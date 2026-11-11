"""画面が組み上がるかだけを確かめる。ダイアログは一切出さない。"""

import sys
import tkinter as tk

import pytest

from shellmenu.gui import App

from conftest import put


@pytest.fixture
def root():
    try:
        r = tk.Tk()
    except tk.TclError:
        pytest.skip("画面を作れない環境")
    r.withdraw()
    yield r
    r.destroy()


def test_builds_and_lists_entries(env, root):
    put(env, r"*\shell\Alive\command", f'"{sys.executable}"')
    put(env, r"*\shell\Gone\command", r'"C:\gone\g.exe"')
    app = App(root, env)
    assert len(app.tree.get_children()) == 2
    app.broken_only.set(True)
    app.refresh()
    assert len(app.tree.get_children()) == 1
    app.query.set("zzz-no-match")
    assert len(app.tree.get_children()) == 0
