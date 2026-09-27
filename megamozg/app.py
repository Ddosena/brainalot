"""Brainalot.exe: the installed program. Runs the local service with a tray icon, one copy per user.

Started by the installer, at logon (``--background``, see ``integrate.py``) and by the extension via the
native-messaging host. Velopack calls it with ``--veloapp-*`` arguments on install, update and uninstall;
``velopack.App().run()`` handles those first and exits.
"""
from __future__ import annotations

import ctypes
import logging
import os
from pathlib import Path
import sys
import threading
import webbrowser

from . import __version__, integrate, paths
from .runtime import Service, pick_port, read_runtime, serves_root, setup_logging

_log = logging.getLogger("brainalot.app")
MUTEX_NAME = "Local\\Brainalot.App"
# GitHub releases are the default feed. BRAINALOT_UPDATE_URL points to a Velopack HTTP feed
# (releases.win.json and packages), for example when a mirror is needed.
UPDATE_REPOSITORY = "https://github.com/Ddosena/brainalot"
UPDATE_EVERY = 6 * 3600


def _update_manager(velopack):
    feed_url = os.environ.get("BRAINALOT_UPDATE_URL")
    if feed_url is not None:
        if not feed_url.strip().startswith(("https://", "http://")):
            raise ValueError("BRAINALOT_UPDATE_URL must be an HTTP(S) Velopack feed URL")
        source = velopack.HttpSource(feed_url.strip())
    else:
        source = velopack.GithubSource(UPDATE_REPOSITORY)
    return velopack.UpdateManager(source)


def _velopack_hooks() -> None:
    """Installer hooks. They must finish in seconds and cannot show a window."""
    try:
        import velopack
    except ImportError:
        return
    (velopack.App()
     .on_after_install_fast_callback(lambda version: integrate.install())
     .on_after_update_fast_callback(lambda version: integrate.install())
     .on_before_uninstall_fast_callback(lambda version: integrate.uninstall())
     .run())


def _single_instance() -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    _single_instance.handle = handle  # keep it open for the life of the process
    return bool(handle) and ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


def _icon_path() -> Path | None:
    for base in (Path(getattr(sys, "_MEIPASS", "")), paths.project_root()):
        candidate = base / "packaging" / "brand" / "brainalot.ico"
        if candidate.is_file():
            return candidate
    return None


def _open(target) -> None:
    try:
        os.startfile(str(target))
    except OSError:
        _log.exception("could not open %s", target)


class App:
    def __init__(self, root: Path):
        self.root = root
        self.service: Service | None = None
        self.tray = None
        self.update = None  # velopack UpdateInfo once downloaded
        self.lock = threading.Lock()

    # The service -------------------------------------------------------------------------------
    def start_service(self) -> None:
        with self.lock:
            self.service = Service(self.root, pick_port())
            self.service.start()

    def restart_service(self) -> None:
        with self.lock:
            if self.service:
                self.service.stop()
            self.service = Service(self.root, pick_port())
            self.service.start()
        if self.tray:
            self.tray.set_tooltip(self.tooltip())

    def welcome_url(self) -> str:
        port = self.service.port if self.service else 8765
        return f"http://127.0.0.1:{port}/welcome"

    def tooltip(self) -> str:
        port = self.service.port if self.service else "—"
        return f"Brainalot {__version__} · 127.0.0.1:{port}"

    # Updates -----------------------------------------------------------------------------------
    def _check_updates(self, stop: threading.Event) -> None:
        if not paths.frozen():
            return
        try:
            import velopack
            manager = _update_manager(velopack)
            if manager.get_is_portable():
                _log.info("portable Brainalot: automatic updates are unavailable")
                return
        except Exception as exc:
            _log.warning("automatic updates could not start: %s", exc)
            if self.tray:
                self.tray.notify("Brainalot", "Проверка обновлений недоступна. Подробности — в журнале.")
            return
        delay = 60  # the first check a minute after start, then every few hours
        failure_reported = False
        while self.update is None and not stop.wait(delay):
            delay = UPDATE_EVERY
            try:
                info = manager.check_for_updates()
                if info is None:
                    continue
                manager.download_updates(info)
                self.update = (manager, info)
                failure_reported = False
                if self.tray:
                    self.tray.notify("Brainalot", "Готово обновление. Меню значка → «Перезапустить и обновить».")
            except Exception as exc:
                _log.warning("update check or download failed: %s", exc)
                if self.tray and not failure_reported:
                    self.tray.notify("Brainalot", "Не удалось проверить обновления. Подробности — в журнале.")
                failure_reported = True

    def apply_update(self) -> None:
        if self.update:
            manager, info = self.update
            if self.service:
                self.service.stop()
            manager.apply_updates_and_restart(info)

    # The tray ----------------------------------------------------------------------------------
    def menu(self):
        items = [(self.tooltip(), None), None,
                 ("Как начать", lambda: webbrowser.open(self.welcome_url())),
                 ("Открыть папку заметок", lambda: _open(paths.vault_path(self.root))),
                 ("Открыть журнал", lambda: _open(self.root / "state" / "logs")),
                 ("Перезапустить сервис", self.restart_service)]
        if self.update:
            items.append(("Перезапустить и обновить", self.apply_update))
        items += [None, ("Выход", self.quit)]
        return items

    def quit(self) -> None:
        if self.service:
            self.service.stop()
        if self.tray:
            self.tray.quit()

    def run(self, background: bool) -> int:
        from .tray import Tray

        self.start_service()
        config = paths.read_config(self.root)
        if not background or not config.get("welcome_shown"):
            webbrowser.open(self.welcome_url())
            paths.write_config(self.root, {**config, "welcome_shown": True})
        stop = threading.Event()
        self.tray = Tray(self.menu, self.tooltip(), _icon_path())
        threading.Thread(target=self._check_updates, args=(stop,), name="brainalot-updates", daemon=True).start()
        self.tray.run()
        stop.set()
        if self.service:
            self.service.stop()
        return 0


def main(argv: list[str] | None = None) -> int:
    _velopack_hooks()
    argv = sys.argv[1:] if argv is None else argv
    background = "--background" in argv
    root = paths.prepare_installed_root(paths.default_root())
    setup_logging(root)
    if not _single_instance():
        # Already running for this user: show where it is instead of starting a second copy.
        info = read_runtime(root)
        if info and not background:
            webbrowser.open(f"http://127.0.0.1:{info['port']}/welcome")
        return 0
    info = read_runtime(root)
    token_file = root / "state" / "api-token"
    if info and token_file.exists() and serves_root(info["port"], token_file.read_text(encoding="utf-8").strip()):
        # A service of this root runs outside the app (a checkout supervisor): do not start another.
        _log.info("service already runs on %s", info["port"])
        if not background:
            webbrowser.open(f"http://127.0.0.1:{info['port']}/welcome")
        return 0
    if paths.frozen() and not os.environ.get("BRAINALOT_HOME"):  # BRAINALOT_HOME: tests, never the registry
        # Keep Chrome's host entry pointing at this copy: a portable zip skips the installer hooks,
        # and a moved or updated installation changes the path.
        try:
            integrate.install(root, autostart=False, path=False)
        except OSError:
            _log.exception("could not register the Chrome native-messaging host")
    try:
        return App(root).run(background)
    except Exception:
        _log.exception("Brainalot stopped with an error")
        ctypes.windll.user32.MessageBoxW(None, "Brainalot не запустился. Подробности — в журнале:\n"
                                         + str(root / "state" / "logs" / "brainalot.log"), "Brainalot", 0x10)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
