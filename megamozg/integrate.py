"""Registering Brainalot in Windows for the current user (no administrator rights).

``install()`` / ``uninstall()`` are called by the installer hooks (``app.py``) and by
``mmm integrate install|uninstall``. Everything lives under HKCU:

- ``Software\\Microsoft\\Windows\\CurrentVersion\\Run\\Brainalot``: start in the background at logon;
- ``Software\\Google\\Chrome\\NativeMessagingHosts\\com.brainalot.host``: lets the extension start the
  service and learn its port and token (``native_host.py``);
- ``Software\\Google\\Chrome\\Extensions\\<id>``: Chrome offers the Web Store extension (only once the
  extension is published and ``STORE_EXTENSION_ID`` is filled in);
- ``Environment\\Path``: the program folder, so agents can run ``mmm``.

Uninstalling removes exactly these entries and never touches notes or state.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys

from . import paths

HOST_NAME = "com.brainalot.host"
RUN_VALUE = "Brainalot"
# Filled in after the first upload to the Chrome Web Store (the dashboard shows the item ID).
# While empty, the welcome page explains how to load the extension bundled with the program.
STORE_EXTENSION_ID = ""
STORE_URL = f"https://chromewebstore.google.com/detail/{STORE_EXTENSION_ID}" if STORE_EXTENSION_ID else ""
UPDATE_URL = "https://clients2.google.com/service/update2/crx"

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
HOST_KEY = rf"Software\Google\Chrome\NativeMessagingHosts\{HOST_NAME}"
EXTENSIONS_KEY = r"Software\Google\Chrome\Extensions"
ENVIRONMENT_KEY = "Environment"


def app_folder() -> Path:
    """The folder with Brainalot.exe and mmm.exe (installed) or the source checkout."""
    return Path(sys.executable).resolve().parent if paths.frozen() else paths.project_root()


def extension_folder() -> Path:
    base = Path(getattr(sys, "_MEIPASS", "")) if paths.frozen() else paths.project_root()
    return (base / "extension").resolve()


def unpacked_extension_id(folder: Path) -> str:
    """The ID Chrome gives an unpacked extension: SHA-256 of its UTF-16 path, nibbles as a..p."""
    digest = hashlib.sha256(str(folder).encode("utf-16-le")).hexdigest()[:32]
    return "".join(chr(ord("a") + int(char, 16)) for char in digest)


def allowed_origins() -> list[str]:
    ids = [STORE_EXTENSION_ID] if STORE_EXTENSION_ID else []
    ids.append(unpacked_extension_id(extension_folder()))
    ids += [value for value in paths.read_config(paths.default_root()).get("extension_ids", [])
            if isinstance(value, str) and len(value) == 32 and set(value) <= set("abcdefghijklmnop")]
    return [f"chrome-extension://{value}/" for value in dict.fromkeys(ids)]


def host_command() -> Path:
    """What Chrome starts for the native-messaging host: mmm.exe (installed) or a .cmd (checkout)."""
    if paths.frozen():
        return app_folder() / "mmm.exe"
    return paths.project_root() / "scripts" / "native-host.cmd"


def host_manifest(root: Path) -> Path:
    return Path(root) / "state" / "native-host" / f"{HOST_NAME}.json"


def write_host_manifest(root: Path) -> Path:
    path = host_manifest(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {"name": HOST_NAME, "description": "Brainalot: start the local service and connect the panel",
                "path": str(host_command()), "type": "stdio", "allowed_origins": allowed_origins()}
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _registry():
    import winreg
    return winreg


def _set(key_path: str, name: str | None, value: str, kind=None) -> None:
    winreg = _registry()
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, kind if kind is not None else winreg.REG_SZ, value)


def _get(key_path: str, name: str | None):
    winreg = _registry()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            return winreg.QueryValueEx(key, name)
    except OSError:
        return None


def _delete_value(key_path: str, name: str) -> None:
    winreg = _registry()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except OSError:
        pass


def _delete_key(key_path: str) -> None:
    winreg = _registry()
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
    except OSError:
        pass


def _broadcast_environment() -> None:
    try:
        import ctypes
        result = ctypes.c_ulong()
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 2000, ctypes.byref(result))
    except (OSError, AttributeError):
        pass


def _path_entries(value: str) -> list[str]:
    return [part for part in value.split(";") if part.strip()]


def _same_folder(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(os.path.expandvars(a))) == os.path.normcase(os.path.normpath(b))


def add_to_path(folder: Path) -> bool:
    current = _get(ENVIRONMENT_KEY, "Path")
    value, kind = (current if current else ("", _registry().REG_EXPAND_SZ))
    entries = _path_entries(value)
    if any(_same_folder(entry, str(folder)) for entry in entries):
        return False
    _set(ENVIRONMENT_KEY, "Path", ";".join(entries + [str(folder)]), kind)
    _broadcast_environment()
    return True


def remove_from_path(folder: Path) -> bool:
    current = _get(ENVIRONMENT_KEY, "Path")
    if not current:
        return False
    value, kind = current
    entries = _path_entries(value)
    kept = [entry for entry in entries if not _same_folder(entry, str(folder))]
    if kept == entries:
        return False
    _set(ENVIRONMENT_KEY, "Path", ";".join(kept), kind)
    _broadcast_environment()
    return True


def install(root: Path | None = None, *, autostart: bool | None = None, path: bool | None = None) -> dict:
    """Register everything. ``autostart`` and ``path`` default to True for the installed program only."""
    if os.name != "nt":
        return {"skipped": "Windows only"}
    root = paths.prepare_installed_root(Path(root) if root else paths.default_root())
    installed = paths.frozen()
    autostart = installed if autostart is None else autostart
    path = installed if path is None else path
    done = {}
    manifest = write_host_manifest(root)
    _set(HOST_KEY, None, str(manifest))
    done["native_host"] = str(manifest)
    if autostart and installed:
        _set(RUN_KEY, RUN_VALUE, f'"{app_folder() / "Brainalot.exe"}" --background')
        done["autostart"] = True
    if path:
        done["path_added"] = add_to_path(app_folder())
    if STORE_EXTENSION_ID:
        _set(rf"{EXTENSIONS_KEY}\{STORE_EXTENSION_ID}", "update_url", UPDATE_URL)
        done["extension_offer"] = STORE_EXTENSION_ID
    return done


def uninstall(root: Path | None = None) -> dict:
    if os.name != "nt":
        return {"skipped": "Windows only"}
    root = Path(root) if root else paths.default_root()
    _delete_value(RUN_KEY, RUN_VALUE)
    _delete_key(HOST_KEY)
    host_manifest(root).unlink(missing_ok=True)
    if STORE_EXTENSION_ID:
        _delete_key(rf"{EXTENSIONS_KEY}\{STORE_EXTENSION_ID}")
    return {"path_removed": remove_from_path(app_folder()), "data_kept": str(root)}


def status(root: Path | None = None) -> dict:
    root = Path(root) if root else paths.default_root()
    host = _get(HOST_KEY, None) if os.name == "nt" else None
    run = _get(RUN_KEY, RUN_VALUE) if os.name == "nt" else None
    return {"root": str(root), "vault": str(paths.vault_path(root)), "app": str(app_folder()),
            "native_host": host[0] if host else None, "autostart": run[0] if run else None,
            "allowed_origins": allowed_origins(), "extension_folder": str(extension_folder())}
