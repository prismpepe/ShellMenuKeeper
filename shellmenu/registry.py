"""レジストリの読み取りと、走査・操作の対象範囲（Env）。

走査対象を Env に集めてあるのは、試験で本物のメニュー登録に触れず、
専用の一時キーだけを相手にできるようにするため。
"""

from __future__ import annotations

import os
import winreg
from dataclasses import dataclass, field
from pathlib import Path

KEY_VIEW_64 = winreg.KEY_WOW64_64KEY
KEY_VIEW_32 = winreg.KEY_WOW64_32KEY

HIVE_BY_NAME = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}

# 拡張ハンドラを無効にするための一覧。HKCU 側ならば管理者権限なしで書ける。
BLOCKED_PATH = r"Software\Microsoft\Windows\CurrentVersion\Shell Extensions\Blocked"


@dataclass(frozen=True)
class Root:
    """レジストリ上の1つの起点。name は HKCU / HKLM、base はその下のパス。"""

    name: str
    hive: int
    base: str


@dataclass
class Env:
    roots: list[Root]        # Classes の置き場（HKCU と HKLM）
    blocked: list[Root]      # Shell Extensions\Blocked の置き場。先頭が書き込み先
    backup_dir: Path = field(default_factory=lambda: default_backup_dir())

    @property
    def blocked_write(self) -> Root:
        return self.blocked[0]


def default_backup_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "ShellMenuKeeper" / "backups"


def default_env() -> Env:
    return Env(
        roots=[
            Root("HKCU", winreg.HKEY_CURRENT_USER, r"Software\Classes"),
            Root("HKLM", winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Classes"),
        ],
        blocked=[
            Root("HKCU", winreg.HKEY_CURRENT_USER, BLOCKED_PATH),
            Root("HKLM", winreg.HKEY_LOCAL_MACHINE, BLOCKED_PATH),
        ],
    )


def join(*parts: str) -> str:
    return "\\".join(p.strip("\\") for p in parts if p)


def key_exists(hive: int, path: str, view: int = 0) -> bool:
    try:
        with winreg.OpenKey(hive, path, 0, winreg.KEY_READ | view):
            return True
    except OSError:
        return False


def subkeys(hive: int, path: str, view: int = 0) -> list[str]:
    out: list[str] = []
    try:
        with winreg.OpenKey(hive, path, 0, winreg.KEY_READ | view) as key:
            i = 0
            while True:
                try:
                    out.append(winreg.EnumKey(key, i))
                except OSError:
                    break
                i += 1
    except OSError:
        return []
    return out


def values(hive: int, path: str, view: int = 0) -> dict[str, tuple]:
    """値名 → (データ, 種類)。既定値は空文字の名前で入る。"""
    out: dict[str, tuple] = {}
    try:
        with winreg.OpenKey(hive, path, 0, winreg.KEY_READ | view) as key:
            i = 0
            while True:
                try:
                    name, data, kind = winreg.EnumValue(key, i)
                except OSError:
                    break
                out[name] = (data, kind)
                i += 1
    except OSError:
        return {}
    return out


def default_value(hive: int, path: str, view: int = 0) -> str | None:
    data = values(hive, path, view).get("")
    if data and isinstance(data[0], str) and data[0].strip():
        return data[0]
    return None
