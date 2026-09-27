"""Where Brainalot keeps its data.

A source checkout keeps everything in ``data/`` next to the code, as before. The installed program
(a PyInstaller build) must never keep data inside its own folder: updates replace that folder and
uninstalling deletes it. So an installed copy uses

- ``%LOCALAPPDATA%\\Brainalot``: the data root with ``state`` (journal, token, calendar secrets,
  logs, runtime.json) and ``config.json``;
- ``Documents\\Brainalot``: the vault, readable in Obsidian. It lives apart from ``state`` because
  Documents is often synced by OneDrive: Markdown survives that, SQLite and file locks do not, and
  secrets must not go to the cloud.

``BRAINALOT_HOME`` overrides the data root (tests, portable copies).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

APP_NAME = "Brainalot"
CONFIG_NAME = "config.json"


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def documents_folder() -> Path:
    """The user's Documents folder, following a OneDrive or GPO redirection."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            import uuid

            folder_id = uuid.UUID("{FDD39AD0-238F-46AF-ADB4-6C85480369C7}")  # FOLDERID_Documents
            guid = (ctypes.c_ubyte * 16).from_buffer_copy(folder_id.bytes_le)
            buffer = wintypes.LPWSTR()
            shell32 = ctypes.windll.shell32
            if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(buffer)) == 0:
                try:
                    return Path(buffer.value)
                finally:
                    ctypes.windll.ole32.CoTaskMemFree(buffer)
        except (OSError, AttributeError, ValueError):
            pass
    return Path.home() / "Documents"


def default_root() -> Path:
    override = os.environ.get("BRAINALOT_HOME")
    if override:
        return Path(override).expanduser()
    if not frozen():
        return project_root() / "data"
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / APP_NAME


def read_config(root: Path) -> dict:
    try:
        value = json.loads((Path(root) / CONFIG_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def write_config(root: Path, config: dict) -> None:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / (CONFIG_NAME + ".tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, root / CONFIG_NAME)


def vault_path(root: Path) -> Path:
    """The vault of a data root: ``config.json`` may move it out of ``root/vault``."""
    configured = read_config(root).get("vault")
    return Path(configured).expanduser() if isinstance(configured, str) and configured.strip() else Path(root) / "vault"


def prepare_installed_root(root: Path) -> Path:
    """First start of an installed copy: put the vault into Documents unless a choice exists."""
    root = Path(root)
    config = read_config(root)
    if frozen() and not os.environ.get("BRAINALOT_HOME") and not config.get("vault") and not (root / "vault").exists():
        config["vault"] = str(documents_folder() / APP_NAME)
        write_config(root, config)
    return root


# State files that belong to one running copy or can be rebuilt; everything else in state is moved.
_STATE_SKIP = {"writer.lock", "calendar.lock", "runtime.json", "logs", "native-host", "models"}


def migrate(source: Path, target: Path) -> dict:
    """Copy an old data folder (``vault`` + ``state``) into this installation. The source stays as is."""
    import shutil
    import sqlite3
    from contextlib import closing

    from .core import ValidationError
    from .runtime import health, read_runtime

    source, target = Path(source), Path(target)
    source_vault, source_state = vault_path(source), source / "state"
    if not source_vault.is_dir() or not (source_state / "journal.sqlite3").is_file():
        raise ValidationError("В старой папке нет vault и state/journal.sqlite3")
    running = read_runtime(target)
    if running and health(running["port"]):
        raise ValidationError("Сначала закройте Brainalot: значок в трее → «Выход»")
    target_vault = vault_path(target)
    if target_vault.resolve() == source_vault.resolve():
        raise ValidationError("Это та же библиотека")
    existing = [p for p in target_vault.rglob("*.md")
                if p.relative_to(target_vault).parts[0] != "9 Система" and p.name != "Главная.md"] \
        if target_vault.exists() else []
    if existing:
        raise ValidationError(f"В новой библиотеке уже есть записи ({len(existing)}): перенос не смешивает библиотеки")
    shutil.copytree(source_vault, target_vault, dirs_exist_ok=True)
    target_state = target / "state"
    target_state.mkdir(parents=True, exist_ok=True)
    copied = []
    for item in source_state.iterdir():
        if item.name in _STATE_SKIP or item.name.endswith((".lock", ".tmp", "-journal", "-wal", "-shm")):
            continue
        if item.name == "journal.sqlite3":
            # The online backup API gives a consistent copy even if the old service still writes.
            with closing(sqlite3.connect(item)) as old, closing(sqlite3.connect(target_state / item.name)) as new:
                old.backup(new)
        elif item.is_dir():
            shutil.copytree(item, target_state / item.name, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target_state / item.name)
        copied.append(item.name)
    return {"vault": str(target_vault), "state": str(target_state), "copied_state": sorted(copied),
            "source_untouched": str(source)}
