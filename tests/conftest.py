"""本物のメニュー登録には触れず、HKCU の専用の一時キーだけを相手にする。"""

import os
import winreg

import pytest

from shellmenu.registry import Env, Root, join, subkeys

HKCU = winreg.HKEY_CURRENT_USER


def _wipe(path: str) -> None:
    for s in subkeys(HKCU, path):
        _wipe(join(path, s))
    try:
        winreg.DeleteKey(HKCU, path)
    except OSError:
        pass


@pytest.fixture
def env(tmp_path):
    base = rf"Software\ShellMenuKeeperTest_{os.getpid()}"
    classes = join(base, "Classes")
    winreg.CreateKey(HKCU, classes).Close()
    e = Env(
        roots=[Root("HKCU", HKCU, classes)],
        blocked=[Root("HKCU", HKCU, join(base, "Blocked"))],
        backup_dir=tmp_path / "backups",
    )
    yield e
    _wipe(base)


def put(env: Env, rel: str, value: str = "", **named) -> None:
    """Classes 配下にキーを作り、既定値と名前つき値を書く。"""
    root = env.roots[0]
    with winreg.CreateKey(root.hive, join(root.base, rel)) as k:
        if value:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, value)
        for n, v in named.items():
            winreg.SetValueEx(k, n, 0, winreg.REG_SZ, v)
