"""ProgID 配下の項目、`%1` 型、拡張子なしのパス、Windows 11 新メニュー（MSIX）。"""

import sys

import pytest

from shellmenu import actions
from shellmenu.scanner import _short_types, scan

from conftest import put, put_package

GUID = "{AAAAAAAA-0000-0000-0000-000000000001}"

MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
         xmlns:desktop4="http://schemas.microsoft.com/appx/manifest/desktop/windows10/4">
  <Applications><Application Id="App"><Extensions>
    <desktop4:Extension Category="windows.fileExplorerContextMenus">
      <desktop4:FileExplorerContextMenus>
        <desktop4:ItemType Type="*">
          <desktop4:Verb Id="OpenIt" Clsid="aaaaaaaa-0000-0000-0000-000000000001"/>
        </desktop4:ItemType>
        <desktop4:ItemType Type="Directory">
          <desktop4:Verb Id="OpenIt" Clsid="aaaaaaaa-0000-0000-0000-000000000001"/>
        </desktop4:ItemType>
      </desktop4:FileExplorerContextMenus>
    </desktop4:Extension>
  </Extensions></Application></Applications>
</Package>"""


def find(env, name):
    return next(e for e in scan(env) if e.name == name)


def test_placeholder_command_is_not_flagged(env):
    # exefile 型: コマンドの先頭がファイル自身（%1）
    put(env, r".exe", "exefile")
    put(env, r"exefile\shell\open\command", '"%1" %*')
    e = find(env, "open")
    assert not e.broken and e.detail == "ファイル自身を実行する項目"


def test_absolute_path_without_extension_gets_exe_appended(env):
    stem = sys.executable[: -len(".exe")]  # perfmon 型: 拡張子を省いた書き方
    put(env, r"*\shell\NoExt\command", f'"{stem}"')
    assert not find(env, "NoExt").broken


def test_progid_verbs_are_scanned_via_extension_default(env):
    put(env, r".qqq", "qqqfile")
    put(env, r".qqr", "qqqfile")
    put(env, r"qqqfile\shell\Gone\command", r'"C:\gone\g.exe" "%1"')
    put(env, r"qqqfile\shell\open\command", r'"C:\gone\g.exe" "%1"')
    put(env, r"unreferenced\shell\Gone2\command", r'"C:\gone\g.exe"')  # どの拡張子も指さない
    entries = scan(env)
    gone = find(env, "Gone")
    assert gone.broken and gone.scope == "qqqfile" and ".qqq" in gone.note and ".qqr" in gone.note
    assert "既定の操作" in find(env, "open").note
    assert not any(e.name == "Gone2" for e in entries)


def test_msix_menu_items_come_from_installed_package_manifests(env, tmp_path):
    folder = tmp_path / "pkg_ok"
    put_package(env, "Acme.App_1.0_x64__abc", folder, MANIFEST, "Shell.dll")
    (folder / "Shell.dll").write_text("x")
    entries = [e for e in scan(env) if e.kind == "msix"]
    assert len(entries) == 1  # * と Directory は1件にまとめる
    e = entries[0]
    assert e.name == "OpenIt" and e.display.startswith("Acme.App")
    assert e.scope == "*, Directory" and not e.broken and not e.disabled
    assert e.guid == GUID


def test_msix_missing_dll_is_broken_and_uninstalled_package_is_ignored(env, tmp_path):
    put_package(env, "Acme.Dead_1.0_x64__abc", tmp_path / "pkg_dead", MANIFEST, "Missing.dll")
    # 旧版の残骸: COM 登録だけが残り、インストール済み一覧に無い（メニューには出ない）
    put(env, rf"PackagedCom\Package\Acme.Old_0.9_x64__abc\Class\{GUID}", DllPath="x.dll")
    entries = [e for e in scan(env) if e.kind == "msix"]
    assert [e.display.split("：")[0] for e in entries] == ["Acme.Dead"]
    assert entries[0].broken


def test_msix_scope_is_shortened():
    types = [f".e{i}" for i in range(10)]
    assert _short_types(types) == ".e0, .e1, .e2 ほか7種"
    assert _short_types([]) == "*"


def test_msix_can_be_disabled_but_not_deleted(env, tmp_path):
    put_package(env, "Acme.App_1.0_x64__abc", tmp_path / "pkg", MANIFEST, "Shell.dll")
    actions.disable(env, find(env, "OpenIt"))
    assert find(env, "OpenIt").disabled
    with pytest.raises(actions.ActionError):
        actions.delete(env, find(env, "OpenIt"))
    actions.enable(env, find(env, "OpenIt"))
    assert not find(env, "OpenIt").disabled
