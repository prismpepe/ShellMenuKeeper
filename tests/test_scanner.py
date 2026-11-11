import sys

from shellmenu.scanner import extract_executable, path_exists, scan

from conftest import put

GUID_OK = "{11111111-1111-1111-1111-111111111111}"
GUID_DLL_MISSING = "{22222222-2222-2222-2222-222222222222}"
GUID_UNREGISTERED = "{33333333-3333-3333-3333-333333333333}"


def find(entries, name):
    return next(e for e in entries if e.name == name)


def test_extract_executable_cases():
    assert extract_executable('"C:\\Program Files\\X\\x.exe" "%1"') == "C:\\Program Files\\X\\x.exe"
    assert extract_executable(f"{sys.executable} -c pass") == sys.executable
    assert extract_executable("notepad.exe %1").lower() == "notepad.exe"
    assert extract_executable("") == ""


def test_unquoted_path_with_spaces_is_not_cut_at_a_folder(tmp_path):
    d = tmp_path / "Program Files" / "My App"
    d.mkdir(parents=True)
    dll = d / "ext.dll"
    dll.write_text("x")
    assert extract_executable(str(dll)) == str(dll)
    assert path_exists(str(dll))
    assert not path_exists(str(d))  # フォルダは呼び先ではない
    dll.unlink()
    # DLL が消えたら、フォルダで切れて実在扱いにならず、リンク切れと判定できる
    assert not path_exists(extract_executable(str(dll)))


def test_missing_unquoted_path_with_spaces_is_shown_whole():
    # リンク切れでも、画面に出す呼び先は "C:\Program" ではなく DLL までの全体にする
    assert extract_executable(r"C:\Program Files\SampleShell\ext64.dll") == r"C:\Program Files\SampleShell\ext64.dll"
    assert (
        extract_executable(r"C:\Program Files\Old App\old.exe /add %1")
        == r"C:\Program Files\Old App\old.exe"
    )
    assert extract_executable(r"C:\gone\noext") == r"C:\gone\noext"


def test_path_exists_bare_and_absolute():
    assert path_exists("notepad.exe")
    assert path_exists(sys.executable)
    assert not path_exists(r"C:\no\such\dir\x.exe")
    assert not path_exists("")


def test_verb_ok_and_broken(env):
    put(env, r"*\shell\Alive\command", f'"{sys.executable}" "%1"')
    put(env, r"*\shell\Gone\command", r'"C:\no\such\dir\gone.exe" "%1"')
    entries = scan(env)
    assert not find(entries, "Alive").broken
    gone = find(entries, "Gone")
    assert gone.broken and gone.detail
    assert entries[0].name == "Gone"  # リンク切れが先頭


def test_verb_unquoted_path_with_args(env):
    put(env, r"Directory\shell\Plain\command", f"{sys.executable} --version")
    assert not find(scan(env), "Plain").broken


def test_verb_without_command_is_not_flagged(env):
    put(env, r"*\shell\Sub", SubCommands="a;b")
    e = find(scan(env), "Sub")
    assert not e.broken and "判定できない" in e.detail


def test_empty_command_is_broken(env):
    put(env, r"Directory\shell\Bridge15\command")  # 既定値なし（更新後に残った残骸）
    e = find(scan(env), "Bridge15")
    assert e.broken and "空" in e.detail


def test_submenu_nested_commands_are_checked_case_insensitively(env):
    # 値名が小文字の subcommands でも、中の Shell\CmdN\Command を調べる
    put(env, r"Directory\shell\Alive", subcommands="")
    put(env, r"Directory\shell\Alive\Shell\Cmd1\Command", f'"{sys.executable}" "%1"')
    put(env, r"Directory\shell\Dead", subcommands="")
    put(env, r"Directory\shell\Dead\Shell\Cmd1\Command", r'"C:\gone.exe" "%1"')
    put(env, r"Directory\shell\Dead\Shell\Cmd2\Command", r'"C:\gone.exe" /x "%1"')
    put(env, r"Directory\shell\Half", subcommands="")
    put(env, r"Directory\shell\Half\Shell\Cmd1\Command", f'"{sys.executable}"')
    put(env, r"Directory\shell\Half\Shell\Cmd2\Command", r'"C:\gone.exe"')
    entries = scan(env)
    assert not find(entries, "Alive").broken
    assert find(entries, "Dead").broken
    half = find(entries, "Half")
    assert not half.broken and "一部" in half.detail  # 一部だけ無いものは消させない


def test_extended_sub_commands_key_is_followed(env):
    # PowerShell 7 型: 項目自体は何も持たず、別の場所のサブメニューを指す
    put(env, r"Directory\shell\Ext", ExtendedSubCommandsKey=r"Directory\ContextMenus\Ext")
    put(env, r"Directory\ContextMenus\Ext\shell\a\command", f'"{sys.executable}" "%V"')
    e = find(scan(env), "Ext")
    assert not e.broken and e.target == sys.executable
    put(env, r"Directory\shell\ExtDead", ExtendedSubCommandsKey=r"Directory\ContextMenus\ExtDead")
    put(env, r"Directory\ContextMenus\ExtDead\shell\a\command", r'"C:\gone\x.exe"')
    assert find(scan(env), "ExtDead").broken


def test_submenu_items_using_com_handlers_are_checked(env):
    # ファイルの所有権 型: 子項目が command ではなく DelegateExecute（COM）で動く
    put(env, rf"CLSID\{GUID_OK}\InprocServer32", sys.executable)
    put(env, rf"CLSID\{GUID_DLL_MISSING}\InprocServer32", r"C:\gone\x.dll")
    put(env, r"Directory\shell\ComOk", ExtendedSubCommandsKey=r"Directory\shell\ComOk")
    put(env, r"Directory\shell\ComOk\Shell\d\command", DelegateExecute=GUID_OK)
    put(env, r"Directory\shell\ComDead", ExtendedSubCommandsKey=r"Directory\shell\ComDead")
    put(env, r"Directory\shell\ComDead\Shell\d\command", DelegateExecute=GUID_DLL_MISSING)
    entries = scan(env)
    assert not find(entries, "ComOk").broken
    assert find(entries, "ComDead").broken


def test_drop_target_verb_is_checked(env):
    put(env, rf"CLSID\{GUID_OK}\InprocServer32", sys.executable)
    put(env, r"*\shell\Drop\DropTarget", CLSID=GUID_OK)
    put(env, r"*\shell\DropDead\DropTarget", CLSID=GUID_UNREGISTERED)
    entries = scan(env)
    assert not find(entries, "Drop").broken
    assert find(entries, "DropDead").broken


def test_verb_display_uses_default_value(env):
    put(env, r"*\shell\V1", "表示名(&X)")
    put(env, r"*\shell\V1\command", f'"{sys.executable}"')
    assert find(scan(env), "V1").display == "表示名(&X)".replace("&", "")


def test_handlers(env):
    put(env, rf"CLSID\{GUID_OK}\InprocServer32", sys.executable)
    put(env, rf"CLSID\{GUID_DLL_MISSING}\InprocServer32", r"C:\no\such\ext.dll")
    put(env, rf"*\shellex\ContextMenuHandlers\Good", GUID_OK)
    put(env, rf"*\shellex\ContextMenuHandlers\NoDll", GUID_DLL_MISSING)
    put(env, rf"*\shellex\ContextMenuHandlers\Orphan", GUID_UNREGISTERED)
    put(env, rf"*\shellex\ContextMenuHandlers\Unknown", "not-a-guid")
    entries = scan(env)
    assert not find(entries, "Good").broken
    assert find(entries, "NoDll").broken
    orphan = find(entries, "Orphan")
    assert orphan.broken and "未登録" in orphan.detail
    assert not find(entries, "Unknown").broken  # 判定できないものは壊れ扱いにしない


def test_handler_named_by_guid(env):
    put(env, rf"CLSID\{GUID_DLL_MISSING}", "Fancy Tool")
    put(env, rf"CLSID\{GUID_DLL_MISSING}\InprocServer32", r"C:\no\such\ext.dll")
    put(env, rf"Directory\shellex\ContextMenuHandlers\{GUID_DLL_MISSING}")
    e = find(scan(env), GUID_DLL_MISSING)
    assert e.guid == GUID_DLL_MISSING and e.broken and e.display == "Fancy Tool"


def test_extension_and_system_file_associations_scopes(env):
    put(env, r".zzz\shell\ExtVerb\command", r'"C:\gone\a.exe"')
    put(env, r"SystemFileAssociations\.yyy\shell\SfaVerb\command", r'"C:\gone\b.exe"')
    entries = scan(env)
    assert find(entries, "ExtVerb").scope == ".zzz"
    assert find(entries, "SfaVerb").scope == r"SystemFileAssociations\.yyy"
