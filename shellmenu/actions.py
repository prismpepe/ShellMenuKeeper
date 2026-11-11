"""無効化・有効化・削除・控えからの復元。

方針:
- 既定は「無効化」。元に戻せる。
- 削除の前に必ず .reg で控えを作る。控えが作れなければ削除しない。
- 管理者権限が要る操作は、理由を示して失敗させる（黙って無視しない）。
"""

from __future__ import annotations

import ctypes
import os
import re
import subprocess
import winreg
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .registry import Env, join, key_exists, subkeys
from .scanner import GUID_RE, Entry

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ActionError(Exception):
    pass


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _need_admin(entry: Entry) -> str:
    return f"{entry.hive_name} の変更には管理者権限が必要です（「管理者として再起動」を使ってください）"


def _journal(env: Env, action: str, entry: Entry, extra: str = "") -> None:
    try:
        env.backup_dir.mkdir(parents=True, exist_ok=True)
        line = "\t".join([datetime.now().isoformat(timespec="seconds"), action, entry.full_key, extra])
        with open(env.backup_dir / "journal.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass  # 記録できなくても操作そのものは止めない


def _ensure_exists(entry: Entry) -> None:
    if not key_exists(entry.hive, entry.key_path):
        raise ActionError("すでに存在しません（再スキャンしてください）")


# ---- 無効化・有効化 --------------------------------------------------------

def disable(env: Env, entry: Entry) -> None:
    if entry.kind == "verb":
        _ensure_exists(entry)
        try:
            with winreg.OpenKey(entry.hive, entry.key_path, 0, winreg.KEY_SET_VALUE) as k:
                winreg.SetValueEx(k, "LegacyDisable", 0, winreg.REG_SZ, "")
        except PermissionError:
            raise ActionError(_need_admin(entry))
    else:
        if not GUID_RE.match(entry.guid):
            raise ActionError("CLSID が分からないため無効にできません")
        b = env.blocked_write
        try:
            with winreg.CreateKeyEx(b.hive, b.base, 0, winreg.KEY_SET_VALUE) as k:
                winreg.SetValueEx(k, entry.guid, 0, winreg.REG_SZ, entry.display)
        except PermissionError:
            raise ActionError("Blocked 一覧に書き込めません")
    _journal(env, "disable", entry)


def enable(env: Env, entry: Entry) -> None:
    if entry.kind == "verb":
        _ensure_exists(entry)
        try:
            with winreg.OpenKey(entry.hive, entry.key_path, 0, winreg.KEY_SET_VALUE) as k:
                try:
                    winreg.DeleteValue(k, "LegacyDisable")
                except FileNotFoundError:
                    pass
        except PermissionError:
            raise ActionError(_need_admin(entry))
    else:
        for b in env.blocked:
            try:
                with winreg.OpenKey(b.hive, b.base, 0, winreg.KEY_SET_VALUE | winreg.KEY_READ) as k:
                    for i in range(1000):
                        try:
                            name, _, _ = winreg.EnumValue(k, i)
                        except OSError:
                            break
                        if name.lower() == entry.guid.lower():
                            winreg.DeleteValue(k, name)
                            break
            except FileNotFoundError:
                continue
            except PermissionError:
                raise ActionError(f"{b.name} の Blocked 一覧を変更できません（管理者権限が必要です）")
    _journal(env, "enable", entry)


# ---- 控えと削除 ------------------------------------------------------------

def _safe_name(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", s).strip("_")[:60] or "entry"


def backup(env: Env, entry: Entry) -> Path:
    """キー以下を .reg に書き出す。失敗したら ActionError。"""
    env.backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = env.backup_dir / f"{stamp}_{entry.hive_name}_{_safe_name(entry.scope + '_' + entry.name)}.reg"
    n = 1
    while dest.exists():
        n += 1
        dest = dest.with_name(f"{dest.stem}_{n}.reg")
    result = subprocess.run(
        ["reg", "export", entry.full_key, str(dest), "/y"],
        capture_output=True,
        creationflags=NO_WINDOW,
    )
    if result.returncode != 0 or not dest.exists():
        raise ActionError("控えの作成に失敗したため、削除しませんでした")
    return dest


def _delete_tree(hive: int, path: str) -> None:
    for sub in subkeys(hive, path):
        _delete_tree(hive, join(path, sub))
    winreg.DeleteKey(hive, path)


def delete(env: Env, entry: Entry) -> Path:
    if entry.kind == "msix":
        # パッケージの持ち物なので、レジストリからは消せない（消しても更新で復活する）
        raise ActionError("パッケージアプリの項目は削除できません。「無効にする」を使うか、アプリをアンインストールしてください")
    _ensure_exists(entry)
    saved = backup(env, entry)
    try:
        _delete_tree(entry.hive, entry.key_path)
    except PermissionError:
        raise ActionError(_need_admin(entry) + f"（控えは作成済み: {saved.name}）")
    _journal(env, "delete", entry, str(saved))
    return saved


@dataclass
class BackupInfo:
    path: Path
    key: str
    mtime: datetime


def list_backups(env: Env) -> list[BackupInfo]:
    out: list[BackupInfo] = []
    if not env.backup_dir.is_dir():
        return out
    for p in env.backup_dir.glob("*.reg"):
        key = ""
        try:
            for line in p.read_text(encoding="utf-16").splitlines():
                if line.startswith("[") and line.endswith("]"):
                    key = line[1:-1]
                    break
        except (OSError, UnicodeError):
            pass
        out.append(BackupInfo(p, key, datetime.fromtimestamp(p.stat().st_mtime)))
    out.sort(key=lambda b: b.mtime, reverse=True)
    return out


def restore(env: Env, backup_path: Path) -> None:
    result = subprocess.run(
        ["reg", "import", str(backup_path)], capture_output=True, creationflags=NO_WINDOW
    )
    if result.returncode != 0:
        raise ActionError("控えの取り込みに失敗しました（HKLM の控えは管理者権限が必要です）")
    try:
        with open(env.backup_dir / "journal.log", "a", encoding="utf-8") as f:
            f.write("\t".join([datetime.now().isoformat(timespec="seconds"), "restore", "", str(backup_path)]) + "\n")
    except OSError:
        pass


# ---- Explorer --------------------------------------------------------------

def restart_explorer() -> None:
    """メニューの変更を反映するため Explorer を再起動する。"""
    subprocess.run(["taskkill", "/f", "/im", "explorer.exe"], capture_output=True, creationflags=NO_WINDOW)
    subprocess.Popen(["explorer.exe"], creationflags=NO_WINDOW)


def relaunch_as_admin() -> bool:
    """管理者として自分自身を起動し直す。起動できたら True。"""
    import sys

    exe = sys.executable
    if getattr(sys, "frozen", False):  # exe 版は自分自身をそのまま起動し直す
        return ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, None, None, 1) > 32
    pyw = exe[:-10] + "pythonw.exe" if exe.lower().endswith("python.exe") else exe
    if not os.path.exists(pyw):
        pyw = exe
    project = str(Path(__file__).resolve().parent.parent)
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", pyw, "-m shellmenu", project, 1)
    return rc > 32
