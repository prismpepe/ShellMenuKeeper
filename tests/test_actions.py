import sys

import pytest

from shellmenu import actions
from shellmenu.registry import key_exists
from shellmenu.scanner import scan

from conftest import HKCU, put

GUID = "{44444444-4444-4444-4444-444444444444}"


def find(env, name):
    return next(e for e in scan(env) if e.name == name)


def test_disable_and_enable_verb(env):
    put(env, r"*\shell\Old\command", r'"C:\gone\old.exe"')
    actions.disable(env, find(env, "Old"))
    assert find(env, "Old").disabled
    actions.enable(env, find(env, "Old"))
    assert not find(env, "Old").disabled


def test_disable_and_enable_handler(env):
    put(env, rf"CLSID\{GUID}\InprocServer32", r"C:\gone\x.dll")
    put(env, r"*\shellex\ContextMenuHandlers\H", GUID)
    actions.disable(env, find(env, "H"))
    assert find(env, "H").disabled
    actions.enable(env, find(env, "H"))
    assert not find(env, "H").disabled


def test_delete_makes_backup_first_and_removes_tree(env):
    put(env, r"*\shell\Old\command", r'"C:\gone\old.exe"')
    put(env, r"*\shell\Old\sub\deeper", "x")
    entry = find(env, "Old")
    saved = actions.delete(env, entry)
    assert saved.exists()
    assert not key_exists(HKCU, entry.key_path)
    lines = (env.backup_dir / "journal.log").read_text(encoding="utf-8").splitlines()
    assert [ln.split("\t")[1] for ln in lines] == ["delete"]


def test_restore_brings_entry_back(env):
    put(env, r"*\shell\Old\command", r'"C:\gone\old.exe"')
    entry = find(env, "Old")
    actions.delete(env, entry)
    backups = actions.list_backups(env)
    assert len(backups) == 1 and "Old" in backups[0].key
    actions.restore(env, backups[0].path)
    assert key_exists(HKCU, entry.key_path)
    assert find(env, "Old").broken


def test_delete_missing_key_is_reported(env):
    put(env, r"*\shell\Old\command", r'"C:\gone\old.exe"')
    entry = find(env, "Old")
    actions.delete(env, entry)
    with pytest.raises(actions.ActionError):
        actions.delete(env, entry)


def test_delete_aborts_when_backup_fails(env, monkeypatch):
    put(env, r"*\shell\Old\command", r'"C:\gone\old.exe"')
    entry = find(env, "Old")

    def boom(*a, **k):
        raise actions.ActionError("控えの作成に失敗")

    monkeypatch.setattr(actions, "backup", boom)
    with pytest.raises(actions.ActionError):
        actions.delete(env, entry)
    assert key_exists(HKCU, entry.key_path)  # 控えが作れなければ消さない
