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
        repo=Root("HKCU", HKCU, join(base, "Repo")),
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


def put_package(env: Env, full: str, folder, manifest_xml: str, dll_rel: str = "") -> None:
    """インストール済みパッケージ（一覧とマニフェスト）と COM 登録を作る。"""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "AppxManifest.xml").write_text(manifest_xml, encoding="utf-8")
    with winreg.CreateKey(env.repo.hive, join(env.repo.base, full)) as k:
        winreg.SetValueEx(k, "PackageRootFolder", 0, winreg.REG_SZ, str(folder))
    if dll_rel:
        guid = "{AAAAAAAA-0000-0000-0000-000000000001}"
        put(env, rf"PackagedCom\Package\{full}\Class\{guid}", DllPath=dll_rel)
