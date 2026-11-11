r"""右クリックメニューの登録を洗い出し、リンク切れを判定する。

扱う登録は2種類。
- verb: `...\shell\<名前>\command` で実行ファイルを呼ぶ項目
- handler: `...\shellex\ContextMenuHandlers\<名前>` が CLSID（COM の DLL）を指す拡張

リンク切れ = 呼び先の実行ファイル／DLL が存在しない、または CLSID が未登録。
判定できないものは「リンク切れ」にしない（誤って消させないため）。
"""

from __future__ import annotations

import ctypes
import os
import re
import shutil
from dataclasses import dataclass

from .registry import (
    KEY_VIEW_32,
    KEY_VIEW_64,
    Env,
    Root,
    default_value,
    join,
    key_exists,
    subkeys,
    values,
)

GUID_RE = re.compile(r"^\{[0-9A-Fa-f]{8}(-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\}$")

FIXED_SCOPES = [
    "*",
    "AllFileSystemObjects",
    "Directory",
    r"Directory\Background",
    "Folder",
    "Drive",
]


@dataclass
class Entry:
    kind: str            # "verb" | "handler"
    hive_name: str       # "HKCU" | "HKLM"
    hive: int
    key_path: str        # hive 直下からの完全なパス
    scope: str           # 例: "*", "Directory", ".txt"
    name: str            # キー名
    display: str         # 表示名
    target: str = ""     # 呼び先の実行ファイル／DLL
    guid: str = ""
    disabled: bool = False
    broken: bool = False
    detail: str = ""
    system: bool = False  # Windows 標準の場所を指している

    @property
    def state_text(self) -> str:
        if self.disabled:
            return "無効（リンク切れ）" if self.broken else "無効"
        return "リンク切れ" if self.broken else "有効"

    @property
    def full_key(self) -> str:
        return f"{self.hive_name}\\{self.key_path}"


@dataclass
class ClsidInfo:
    registered: bool = False
    packaged: bool = False
    server: str = ""
    desc: str = ""


# ---- パス判定 --------------------------------------------------------------

def _extra_dirs() -> list[str]:
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return [os.path.join(root, "SysWOW64"), os.path.join(root, "System32")]


def path_exists(p: str) -> bool:
    p = os.path.expandvars(p.strip().strip('"'))
    if not p:
        return False
    if os.path.isabs(p) or "\\" in p or "/" in p:
        # フォルダは呼び先にならない。"C:\Program Files" で切れて実在扱いになるのを防ぐ
        return os.path.lexists(p) and not os.path.isdir(p)
    if shutil.which(p):
        return True
    return any(os.path.lexists(os.path.join(d, p)) for d in _extra_dirs())


def extract_executable(cmd: str) -> str:
    """コマンド行から実行ファイルのパスを取り出す。引用符なしで空白を含むパスにも対応。"""
    cmd = os.path.expandvars(cmd.strip())
    if not cmd:
        return ""
    if cmd[0] == '"':
        end = cmd.find('"', 1)
        return cmd[1:end] if end > 0 else cmd[1:]
    parts = cmd.split(" ")
    for i in range(1, len(parts) + 1):
        cand = " ".join(parts[:i])
        if path_exists(cand) or path_exists(cand + ".exe"):
            return cand
    return parts[0]


def is_system_path(p: str) -> bool:
    if not p:
        return False
    win = os.environ.get("SystemRoot", r"C:\Windows").lower().rstrip("\\") + "\\"
    return os.path.expandvars(p).lower().startswith(win)


def resolve_resource_string(s: str) -> str:
    """`@shell32.dll,-1234` 形式の文字列を実際の文言にする。失敗したらそのまま返す。"""
    if not s.startswith("@"):
        return s
    try:
        buf = ctypes.create_unicode_buffer(260)
        hr = ctypes.windll.shlwapi.SHLoadIndirectString(s, buf, 260, None)
        if hr == 0 and buf.value:
            return buf.value
    except Exception:
        pass
    return s


# ---- CLSID -----------------------------------------------------------------

def resolve_clsid(env: Env, guid: str) -> ClsidInfo:
    info = ClsidInfo()
    for root in env.roots:
        if key_exists(root.hive, join(root.base, "PackagedCom", "ClassIndex", guid)):
            info.registered = info.packaged = True
            return info
        for view in (KEY_VIEW_64, KEY_VIEW_32):
            ck = join(root.base, "CLSID", guid)
            if not key_exists(root.hive, ck, view):
                continue
            info.registered = True
            if not info.desc:
                info.desc = default_value(root.hive, ck, view) or ""
            for server in ("InprocServer32", "LocalServer32"):
                p = default_value(root.hive, join(ck, server), view)
                if p:
                    info.server = extract_executable(p)
                    return info
    return info


def _check_guid(env: Env, guid: str) -> tuple[bool, str, str, str]:
    """(リンク切れか, 呼び先, 説明, CLSIDの名前)"""
    info = resolve_clsid(env, guid)
    if info.packaged:
        return False, "", "パッケージアプリ", info.desc
    if not info.registered:
        return True, "", "CLSID が未登録", ""
    if info.server and not path_exists(info.server):
        return True, info.server, "DLL/EXE が存在しない", info.desc
    return False, info.server, "", info.desc


# ---- 走査 ------------------------------------------------------------------

def blocked_guids(env: Env) -> set[str]:
    out: set[str] = set()
    for b in env.blocked:
        out.update(n.lower() for n in values(b.hive, b.base) if n)
    return out


def iter_scopes(root: Root) -> list[str]:
    """メニュー登録がありうる場所（ファイル全般・フォルダ・拡張子ごと）を挙げる。"""
    found: list[str] = []

    def has_menu(rel: str) -> bool:
        return key_exists(root.hive, join(root.base, rel, "shell")) or key_exists(
            root.hive, join(root.base, rel, "shellex")
        )

    for s in FIXED_SCOPES:
        if has_menu(s):
            found.append(s)
    for k in subkeys(root.hive, root.base):
        if k.startswith(".") and has_menu(k):
            found.append(k)
    sfa = "SystemFileAssociations"
    for k in subkeys(root.hive, join(root.base, sfa)):
        if has_menu(join(sfa, k)):
            found.append(join(sfa, k))
    return found


def _verb_display(name: str, vals: dict) -> str:
    """vals は値名を小文字にした辞書（レジストリの値名は大文字小文字を区別しない）。"""
    for key in ("muiverb", ""):
        data = vals.get(key)
        if data and isinstance(data[0], str) and data[0].strip():
            return resolve_resource_string(data[0]).replace("&", "")
    return name


COMMAND_STORE = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\CommandStore\shell"


def _lower(vals: dict) -> dict:
    return {k.lower(): v for k, v in vals.items()}


def _child(hive: int, path: str, name: str) -> str | None:
    """大文字小文字を無視して子キーの実名を探す。"""
    for s in subkeys(hive, path):
        if s.lower() == name.lower():
            return s
    return None


def _verb_call(hive: int, verb_path: str) -> tuple[str, str] | None:
    """項目の呼び先を (コマンド行, COMのCLSID) で返す。どちらも無ければ None。"""
    vals = _lower(values(hive, verb_path))
    c = _child(hive, verb_path, "command")
    cmd_vals = _lower(values(hive, join(verb_path, c))) if c else {}
    for source in (cmd_vals, vals):
        for key in ("delegateexecute", "explorercommandhandler"):
            g = source.get(key, (None,))[0]
            if isinstance(g, str) and GUID_RE.match(g):
                return "", g
    cmd = (cmd_vals.get("") or (None,))[0]
    if isinstance(cmd, str) and cmd.strip():
        return cmd, ""
    return None


def _subcommand_items(env: Env, root: Root, kp: str, lower: dict) -> list[tuple[str, str]]:
    """サブメニューの各項目の呼び先 (コマンド行, CLSID) を集める。見つからなければ空。"""
    items: list[tuple[str, str]] = []

    def from_shell_key(hive: int, shell_path: str) -> None:
        for sub in subkeys(hive, shell_path):
            call = _verb_call(hive, join(shell_path, sub))
            if call:
                items.append(call)

    nested = _child(root.hive, kp, "shell")           # 項目の中に Shell\CmdN を持つ形
    if nested:
        from_shell_key(root.hive, join(kp, nested))
    listed = (lower.get("subcommands") or ("",))[0]
    for name in [n.strip() for n in str(listed).split(";") if n.strip()]:
        for hive in {r.hive for r in env.roots}:      # CommandStore に名前で登録された形
            call = _verb_call(hive, join(COMMAND_STORE, name))
            if call:
                items.append(call)
                break
    ext = (lower.get("extendedsubcommandskey") or ("",))[0]
    if ext:
        for r in env.roots:
            from_shell_key(r.hive, join(r.base, str(ext), "shell"))
    return items


def _drop_target_guid(root: Root, kp: str) -> str:
    """`DropTarget\\CLSID` で動く項目（ドラッグ&ドロップ型）の CLSID。"""
    dt = _child(root.hive, kp, "DropTarget")
    if dt:
        g = _lower(values(root.hive, join(kp, dt))).get("clsid", (None,))[0]
        if isinstance(g, str) and GUID_RE.match(g):
            return g
    return ""


def _scan_verbs(env: Env, root: Root, scope: str) -> list[Entry]:
    base = join(root.base, scope, "shell")
    out: list[Entry] = []
    for name in subkeys(root.hive, base):
        kp = join(base, name)
        lower = _lower(values(root.hive, kp))
        e = Entry(
            kind="verb",
            hive_name=root.name,
            hive=root.hive,
            key_path=kp,
            scope=scope,
            name=name,
            display=_verb_display(name, lower),
            disabled="legacydisable" in lower,
        )
        cmd_key = _child(root.hive, kp, "command")
        cmd_vals = _lower(values(root.hive, join(kp, cmd_key))) if cmd_key else {}
        cmd = (cmd_vals.get("") or (None,))[0]
        handler_guid = ""
        if isinstance(cmd_vals.get("delegateexecute", (None,))[0], str):
            handler_guid = cmd_vals["delegateexecute"][0]
        elif isinstance(lower.get("explorercommandhandler", (None,))[0], str):
            handler_guid = lower["explorercommandhandler"][0]
        elif not (isinstance(cmd, str) and cmd.strip()):
            handler_guid = _drop_target_guid(root, kp)

        if handler_guid and GUID_RE.match(handler_guid):
            e.guid = handler_guid
            e.broken, e.target, e.detail, _ = _check_guid(env, handler_guid)
        elif isinstance(cmd, str) and cmd.strip():
            e.target = extract_executable(cmd)
            if not path_exists(e.target):
                e.broken, e.detail = True, "実行ファイルが存在しない"
        elif "subcommands" in lower or "extendedsubcommandskey" in lower:
            results: list[tuple[bool, str]] = []   # (リンク切れか, 呼び先)
            for sub_cmd, sub_guid in _subcommand_items(env, root, kp, lower):
                if sub_guid:
                    b, t, _, _ = _check_guid(env, sub_guid)
                else:
                    t = extract_executable(sub_cmd)
                    b = not path_exists(t)
                results.append((b, t))
            if not results:
                e.detail = "サブメニュー（中身を判定できない）"
            else:
                e.target = next((t for _, t in results if t), "")
                missing = sum(1 for b, _ in results if b)
                if missing == len(results):
                    e.broken, e.detail = True, "サブメニューの呼び先がすべて存在しない"
                elif missing:
                    e.detail = f"サブメニューの一部（{missing}/{len(results)} 件）の呼び先が存在しない"
                else:
                    e.detail = "サブメニュー"
        elif cmd_key:
            e.broken, e.detail = True, "command が空（呼び先がない）"
        else:
            e.detail = "呼び先を判定できない"
        e.system = is_system_path(e.target)
        out.append(e)
    return out


def _scan_handlers(env: Env, root: Root, scope: str, blocked: set[str]) -> list[Entry]:
    base = join(root.base, scope, "shellex", "ContextMenuHandlers")
    out: list[Entry] = []
    for name in subkeys(root.hive, base):
        kp = join(base, name)
        default = default_value(root.hive, kp) or ""
        guid = default if GUID_RE.match(default) else (name if GUID_RE.match(name) else "")
        e = Entry(
            kind="handler",
            hive_name=root.name,
            hive=root.hive,
            key_path=kp,
            scope=scope,
            name=name,
            display=name,
            guid=guid,
        )
        if guid:
            e.disabled = guid.lower() in blocked
            e.broken, e.target, e.detail, desc = _check_guid(env, guid)
            if desc and GUID_RE.match(name):
                e.display = desc
        else:
            e.detail = "CLSID を判定できない"
        e.system = is_system_path(e.target)
        out.append(e)
    return out


def scan(env: Env) -> list[Entry]:
    blocked = blocked_guids(env)
    out: list[Entry] = []
    for root in env.roots:
        for scope in iter_scopes(root):
            out += _scan_verbs(env, root, scope)
            out += _scan_handlers(env, root, scope, blocked)
    out.sort(key=lambda e: (not e.broken, e.scope.lower(), e.display.lower(), e.hive_name))
    return out
