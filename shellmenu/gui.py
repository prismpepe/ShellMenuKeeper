"""tkinter の画面。処理は scanner / actions に任せ、ここは呼ぶだけにする。"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox, ttk

from . import __version__, actions
from .registry import Env, default_env
from .scanner import Entry, scan

KIND_LABEL = {"verb": "メニュー項目", "handler": "拡張ハンドラ", "msix": "新メニュー(MSIX)"}
ALL_KINDS = "すべて"
# 呼び先だけが余りの幅を受け取る（対象や名前が広がって呼び先が切れないように）
COLUMNS = (
    ("state", "状態", 110, False),
    ("name", "名前", 230, False),
    ("scope", "対象", 130, False),
    ("kind", "種類", 120, False),
    ("hive", "登録先", 70, False),
    ("target", "呼び先", 420, True),
)


class App(ttk.Frame):
    def __init__(self, master: tk.Misc, env: Env | None = None):
        super().__init__(master, padding=8)
        self.env = env or default_env()
        self.entries: list[Entry] = []
        self.by_iid: dict[str, Entry] = {}
        self.sort_col = "state"
        self.sort_rev = False
        self.broken_only = tk.BooleanVar(value=False)
        self.query = tk.StringVar()
        self.kind_filter = tk.StringVar(value=ALL_KINDS)
        self.status = tk.StringVar()
        self._build()
        self.pack(fill="both", expand=True)
        self.rescan()

    # ---- 画面 ----
    def _build(self) -> None:
        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Button(top, text="再スキャン", command=self.rescan).pack(side="left")
        ttk.Checkbutton(
            top, text="リンク切れだけ表示", variable=self.broken_only, command=self.refresh
        ).pack(side="left", padx=12)
        ttk.Label(top, text="検索:").pack(side="left")
        entry = ttk.Entry(top, textvariable=self.query, width=28)
        entry.pack(side="left", padx=4)
        self.query.trace_add("write", lambda *_: self.refresh())
        ttk.Label(top, text="種類:").pack(side="left", padx=(12, 0))
        kinds = ttk.Combobox(
            top, textvariable=self.kind_filter, state="readonly", width=16,
            values=[ALL_KINDS, *KIND_LABEL.values()],
        )
        kinds.pack(side="left", padx=4)
        kinds.bind("<<ComboboxSelected>>", lambda _e: self.refresh())
        self.admin_label = ttk.Label(top)
        self.admin_label.pack(side="right")
        if not actions.is_admin():
            ttk.Button(top, text="管理者として再起動", command=self.relaunch).pack(side="right", padx=8)
        self.admin_label.configure(
            text="管理者権限: あり" if actions.is_admin() else "管理者権限: なし（HKLM の項目は変更できません）"
        )

        mid = ttk.Frame(self)
        mid.pack(fill="both", expand=True, pady=6)
        self.tree = ttk.Treeview(
            mid, columns=[c[0] for c in COLUMNS], show="headings", selectmode="extended"
        )
        for key, title, width, stretch in COLUMNS:
            self.tree.heading(key, text=title, command=lambda k=key: self.sort_by(k))
            self.tree.column(key, width=width, minwidth=60, anchor="w", stretch=stretch)
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.tag_configure("broken", foreground="#b00020")
        self.tree.tag_configure("disabled", foreground="#808080")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.show_detail())

        self.detail = tk.Text(self, height=5, wrap="word", state="disabled", background="#f6f6f6")
        self.detail.pack(fill="x")

        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(6, 0))
        for text, cmd in (
            ("無効にする", self.do_disable),
            ("有効に戻す", self.do_enable),
            ("削除する（控えを作ります）", self.do_delete),
            ("控えから戻す...", self.do_restore),
            ("Explorer を再起動", self.do_restart),
            ("控えのフォルダを開く", self.open_backups),
        ):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=(0, 6))
        ttk.Label(self, textvariable=self.status, anchor="w").pack(fill="x", pady=(6, 0))

    # ---- 一覧 ----
    def rescan(self) -> None:
        self.entries = scan(self.env)
        self.refresh()

    def _visible(self) -> list[Entry]:
        q = self.query.get().strip().lower()
        out = []
        for e in self.entries:
            if self.broken_only.get() and not e.broken:
                continue
            if self.kind_filter.get() != ALL_KINDS and KIND_LABEL[e.kind] != self.kind_filter.get():
                continue
            if q and q not in " ".join((e.name, e.display, e.scope, e.target, e.key_path)).lower():
                continue
            out.append(e)
        return out

    def _sort_key(self, e: Entry):
        col = self.sort_col
        if col == "state":
            return (not e.broken, e.disabled, e.scope.lower(), e.display.lower())
        value = {
            "name": e.display,
            "scope": e.scope,
            "kind": KIND_LABEL[e.kind],
            "hive": e.hive_name,
            "target": e.target,
        }[col]
        return (value.lower(),)

    def sort_by(self, col: str) -> None:
        self.sort_rev = (not self.sort_rev) if col == self.sort_col else False
        self.sort_col = col
        self.refresh()

    def refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.by_iid = {}
        shown = sorted(self._visible(), key=self._sort_key, reverse=self.sort_rev)
        for i, e in enumerate(shown):
            iid = str(i)
            self.by_iid[iid] = e
            tags = ("broken",) if e.broken and not e.disabled else ("disabled",) if e.disabled else ()
            self.tree.insert(
                "",
                "end",
                iid=iid,
                tags=tags,
                values=(e.state_text, e.display, e.scope, KIND_LABEL[e.kind], e.hive_name, e.target),
            )
        broken = sum(1 for e in self.entries if e.broken)
        self.status.set(f"全 {len(self.entries)} 件 / 表示 {len(shown)} 件 / リンク切れ {broken} 件")
        self.show_detail()

    def selected(self) -> list[Entry]:
        return [self.by_iid[i] for i in self.tree.selection() if i in self.by_iid]

    def show_detail(self) -> None:
        sel = self.selected()
        text = ""
        if len(sel) == 1:
            e = sel[0]
            text = f"{e.full_key}\n呼び先: {e.target or '（なし）'}"
            if e.detail:
                text += f"\n{e.detail}"
            if e.note:
                text += f"\n{e.note}"
            if e.kind in ("handler", "msix") and e.guid:
                text += f"\nCLSID: {e.guid}（無効化はこの CLSID を使う全ての場所に効きます）"
            if e.system:
                text += "\n※ Windows 標準の場所を指しています。削除は推奨しません。"
        elif len(sel) > 1:
            text = f"{len(sel)} 件を選択中"
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")

    # ---- 操作 ----
    def _apply(self, entries: list[Entry], func, done: str) -> None:
        errors, ok = [], 0
        for e in entries:
            try:
                func(self.env, e)
                ok += 1
            except actions.ActionError as ex:
                errors.append(f"{e.display}: {ex}")
        self.rescan()
        self.status.set(f"{done}: {ok} 件" + (f"（失敗 {len(errors)} 件）" if errors else ""))
        if errors:
            messagebox.showerror("一部の操作に失敗しました", "\n".join(errors[:10]))

    def do_disable(self) -> None:
        sel = [e for e in self.selected() if not e.disabled]
        if not sel:
            self.status.set("無効にする項目を選んでください（選択中はすべて無効です）")
            return
        self._apply(sel, actions.disable, "無効にしました")

    def do_enable(self) -> None:
        sel = [e for e in self.selected() if e.disabled]
        if not sel:
            self.status.set("有効に戻す項目を選んでください（選択中はすべて有効です）")
            return
        self._apply(sel, actions.enable, "有効に戻しました")

    def do_delete(self) -> None:
        sel = self.selected()
        if not sel:
            self.status.set("削除する項目を選んでください")
            return
        packaged = [e for e in sel if e.kind == "msix"]
        if packaged:  # パッケージアプリの項目は削除できない（無効化は可能）
            sel = [e for e in sel if e.kind != "msix"]
            self.status.set(f"新メニュー(MSIX)の {len(packaged)} 件は削除できません（「無効にする」を使ってください）")
            if not sel:
                return
        names = "\n".join(f"  ・{e.display}（{e.scope} / {e.hive_name}）" for e in sel[:8])
        more = f"\n  …ほか {len(sel) - 8} 件" if len(sel) > 8 else ""
        warn = ""
        if any(e.system for e in sel):
            warn = "\n\n※ Windows 標準の場所を指す項目を含みます。本当に不要か確認してください。"
        ok = messagebox.askyesno(
            "削除の確認",
            f"{len(sel)} 件を削除します。\n{names}{more}\n\n"
            f"削除前に控え（.reg）を作ります。「控えから戻す」で元に戻せます。{warn}\n\n"
            "削除しますか？",
            icon="warning",
        )
        if ok:
            self._apply(sel, actions.delete, "削除しました")

    def do_restore(self) -> None:
        backups = actions.list_backups(self.env)
        if not backups:
            self.status.set("控えはまだありません")
            return
        win = tk.Toplevel(self)
        win.title("控えから戻す")
        win.geometry("760x320")
        win.transient(self.winfo_toplevel())
        tv = ttk.Treeview(win, columns=("time", "key"), show="headings", selectmode="browse")
        tv.heading("time", text="作成日時")
        tv.heading("key", text="キー")
        tv.column("time", width=150)
        tv.column("key", width=560)
        tv.pack(fill="both", expand=True, padx=8, pady=8)
        for i, b in enumerate(backups):
            tv.insert("", "end", iid=str(i), values=(b.mtime.strftime("%Y-%m-%d %H:%M:%S"), b.key))

        def go() -> None:
            sel = tv.selection()
            if not sel:
                return
            b = backups[int(sel[0])]
            try:
                actions.restore(self.env, b.path)
            except actions.ActionError as ex:
                messagebox.showerror("戻せませんでした", str(ex), parent=win)
                return
            win.destroy()
            self.rescan()
            self.status.set(f"戻しました: {b.key}")

        ttk.Button(win, text="選んだ控えを戻す", command=go).pack(pady=(0, 8))

    def do_restart(self) -> None:
        if messagebox.askyesno(
            "Explorer の再起動",
            "メニューの変更を反映するため Explorer を再起動します。\n"
            "開いているフォルダ窓は閉じます（作業中のファイルは影響しません）。\n\n再起動しますか？",
        ):
            actions.restart_explorer()
            self.status.set("Explorer を再起動しました")

    def open_backups(self) -> None:
        self.env.backup_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(self.env.backup_dir)

    def relaunch(self) -> None:
        if actions.relaunch_as_admin():
            self.winfo_toplevel().destroy()
        else:
            self.status.set("管理者として起動できませんでした")


def main() -> None:
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    root.title(f"ShellMenuKeeper {__version__} — 右クリックメニューの保守")
    root.geometry("1180x640")
    App(root)
    root.mainloop()
