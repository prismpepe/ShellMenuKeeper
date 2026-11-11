r"""右クリックメニューの登録を洗い出し、リンク切れを判定する。

扱う登録は3種類。
- verb: `...\shell\<名前>\command` で実行ファイルを呼ぶ項目
- handler: `...\shellex\ContextMenuHandlers\<名前>` が CLSID（COM の DLL）を指す拡張
- msix: Windows 11 の新しいメニュー。インストール済みパッケージのマニフェストが持つ

場所は、ファイル全般・フォルダ・拡張子ごと・拡張子が指す ProgID（txtfile など）。

リンク切れ = 呼び先の実行ファイル／DLL が存在しない、または CLSID が未登録。
判定できないものは「リンク切れ」にしない（誤って消させないため）。
"""

from __future__ import annotations

import ctypes
import os
import re
import shutil
import winreg
import xml.etree.ElementTree as ET
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

DEFAULT_VERBS = {"open", "edit", "print", "play", "opennew", "runas"}

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
    kind: str            # "verb" | "handler" | "msix"
    hive_name: str       # "HKCU" | "HKLM" | "パッケージ"
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
    note: str = ""        # 画面の詳細欄に出す補足（ProgID の拡張子など）

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


APP_PATHS = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
# コマンドの先頭が %1 など（ファイル自身を実行する形。exefile / batfile など）
PLACEHOLDER_RE = re.compile(r"^%[0-9*LlVvWw]")


def _in_app_paths(name: str) -> bool:
    """パスなしの名前は、App Paths に登録された場所からも解決される。"""
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for n in (name, name + ".exe"):
            target = default_value(hive, join(APP_PATHS, n))
            if target and os.path.lexists(os.path.expandvars(target.strip('"'))):
                return True
    return False


def path_exists(p: str) -> bool:
    p = os.path.expandvars(p.strip().strip('"'))
    if not p:
        return False
    if os.path.isabs(p) or "\\" in p or "/" in p:
        # フォルダは呼び先にならない。"C:\Program Files" で切れて実在扱いになるのを防ぐ
        # 拡張子のない指定は、Windows が .exe を補って起動する
        return any(os.path.lexists(c) and not os.path.isdir(c) for c in (p, p + ".exe"))
    if shutil.which(p):
        return True
    return any(os.path.lexists(os.path.join(d, p)) for d in _extra_dirs()) or _in_app_paths(p)


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


def progid_map(env: Env) -> dict[str, list[str]]:
    """ProgID → それを既定にしている拡張子。拡張子の既定値が指す先にもメニューは登録される。"""
    out: dict[str, list[str]] = {}
    for root in env.roots:
        for k in subkeys(root.hive, root.base):
            if not k.startswith("."):
                continue
            prog = default_value(root.hive, join(root.base, k))
            if prog and not prog.startswith("."):
                exts = out.setdefault(prog, [])
                if k not in exts:
                    exts.append(k)
    return out


def iter_scopes(root: Root, progids: dict[str, list[str]] | None = None) -> list[str]:
    """メニュー登録がありうる場所（ファイル全般・フォルダ・拡張子ごと・ProgID）を挙げる。"""
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
    for prog in progids or {}:
        if prog not in found and "\\" not in prog and has_menu(prog):
            found.append(prog)
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
            if PLACEHOLDER_RE.match(e.target):
                e.target, e.detail = "", "ファイル自身を実行する項目"
            elif not path_exists(e.target):
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


def installed_packages(env: Env) -> dict[str, str]:
    """インストール済みパッケージの フル名 → インストール先。"""
    out: dict[str, str] = {}
    if not env.repo:
        return out
    for name in subkeys(env.repo.hive, env.repo.base):
        folder = values(env.repo.hive, join(env.repo.base, name)).get("PackageRootFolder")
        if folder and isinstance(folder[0], str):
            out[name] = folder[0]
    return out


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def manifest_verbs(manifest: str) -> list[tuple[str, str, str]]:
    """マニフェストの windows.fileExplorerContextMenus から (対象の種類, 動詞ID, CLSID) を取る。"""
    try:
        tree = ET.parse(manifest)
    except (OSError, ET.ParseError):
        return []
    out: list[tuple[str, str, str]] = []
    for ext in tree.iter():
        if _local(ext.tag) != "Extension" or ext.get("Category") != "windows.fileExplorerContextMenus":
            continue
        for item in ext.iter():
            if _local(item.tag) != "ItemType":
                continue
            for verb in item:
                if _local(verb.tag) == "Verb" and verb.get("Clsid"):
                    out.append((item.get("Type", ""), verb.get("Id", ""), verb.get("Clsid", "")))
    return out


def _short_types(types: list[str], limit: int = 3) -> str:
    if not types:
        return "*"
    return ", ".join(types[:limit]) + (f" ほか{len(types) - limit}種" if len(types) > limit else "")


def _scan_msix(env: Env, blocked: set[str]) -> list[Entry]:
    """Windows 11 の新しいメニュー（インストール済みの MSIX パッケージが持つ項目）。

    インストール済みの一覧に無いパッケージの登録（旧版の残骸）はメニューに出ないので扱わない。
    """
    packages = installed_packages(env)
    seen: set[tuple[str, str, str]] = set()
    out: list[Entry] = []
    for root in env.roots:
        pc = join(root.base, "PackagedCom", "Package")
        for full in subkeys(root.hive, pc):
            folder = packages.get(full)
            if not folder:
                continue
            verbs = manifest_verbs(os.path.join(folder, "AppxManifest.xml"))
            grouped: dict[tuple[str, str], list[str]] = {}
            for item_type, verb_id, clsid in verbs:
                guid = "{" + clsid.strip("{}").upper() + "}"
                types = grouped.setdefault((verb_id, guid), [])
                if item_type and item_type not in types:
                    types.append(item_type)
            for (verb_id, guid), types in grouped.items():
                if (full, verb_id, guid) in seen:
                    continue
                seen.add((full, verb_id, guid))
                dll_rel = (values(root.hive, join(pc, full, "Class", guid)).get("DllPath") or ("",))[0]
                target = os.path.join(folder, dll_rel) if dll_rel else ""
                e = Entry(
                    kind="msix",
                    hive_name="パッケージ",
                    hive=0,
                    key_path=full,
                    scope=_short_types(types),
                    note="対象: " + (", ".join(types) or "*"),
                    name=verb_id,
                    display=f"{full.split('_')[0]}：{verb_id}",
                    target=target,
                    guid=guid,
                    disabled=guid.lower() in blocked,
                    detail="Windows 11 の新しいメニュー（パッケージアプリ）",
                )
                if target and not os.path.lexists(target):
                    e.broken, e.detail = True, "パッケージ内の DLL が存在しない"
                out.append(e)
    return out


def scan(env: Env) -> list[Entry]:
    blocked = blocked_guids(env)
    progids = progid_map(env)
    out: list[Entry] = []
    for root in env.roots:
        for scope in iter_scopes(root, progids):
            found = _scan_verbs(env, root, scope) + _scan_handlers(env, root, scope, blocked)
            if scope in progids:
                exts = " ".join(progids[scope][:8]) + (" ほか" if len(progids[scope]) > 8 else "")
                for e in found:
                    e.note = f"ProgID「{scope}」の項目。対象の拡張子: {exts}"
                    if e.name.lower() in DEFAULT_VERBS:
                        e.note += "\n※ ダブルクリックなどの既定の操作かもしれません。消す前に確認してください。"
            out += found
    out += _scan_msix(env, blocked)
    out.sort(key=lambda e: (not e.broken, e.scope.lower(), e.display.lower(), e.hive_name))
    return out
