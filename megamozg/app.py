"""Brainalot.exe: the installed program. Runs the local service with a tray icon, one copy per user.

Started by the installer, at logon (``--background``, see ``integrate.py``) and by the extension via the
native-messaging host. Velopack calls it with ``--veloapp-*`` arguments on install, update and uninstall;
``velopack.App().run()`` handles those first and exits.
"""
from __future__ import annotations

import ctypes
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time
from urllib.request import Request, urlopen
import webbrowser

from . import __version__, integrate, paths
from .runtime import Service, pick_port, port_free, read_runtime, serves_root, setup_logging

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


def _desktop_service_ready(port: int, token: str) -> bool:
    """The checkout supervisor may still serve an older release on this root."""
    base = f"http://127.0.0.1:{port}"
    try:
        with urlopen(Request(base + "/api/time/state", headers={"Authorization": "Bearer " + token}),
                     timeout=2) as response:
            state = json.load(response)
            if response.status != 200 or not isinstance(state.get("revision"), int) or \
                    state.get("status") not in {"idle", "running", "paused", "finished"}:
                return False
        with urlopen(base + "/desktop/desktop.html", timeout=2) as response:
            page = response.read(65536)
            return response.status == 200 and b'id="desktop-content"' in page and b'desktop.js' in page
    except Exception as exc:
        _log.warning("external service cannot serve desktop: %s", exc)
        return False


def _desktop_port_file(root: Path) -> Path:
    return Path(root) / "state" / "desktop-port.json"


def _desktop_port(root: Path) -> int | None:
    file = _desktop_port_file(root)
    if not file.exists():
        return None
    try:
        port = json.loads(file.read_text(encoding="utf-8"))["port"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("Сохранённый порт Brainalot повреждён; проверьте state/desktop-port.json") from exc
    if type(port) is not int or not 1024 <= port <= 65535:
        raise RuntimeError("Сохранённый порт Brainalot некорректен; проверьте state/desktop-port.json")
    return port


def _remember_desktop_port(root: Path, port: int) -> None:
    existing = _desktop_port(root)
    if existing is not None:
        if existing != port:
            raise RuntimeError(f"Brainalot должен использовать прежний порт {existing}; сейчас сервис на {port}. "
                               "Остановите другой экземпляр и перезапустите локальный сервис.")
        return
    file = _desktop_port_file(root)
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_suffix(".tmp")
    temporary.write_text(json.dumps({"port": port}), encoding="utf-8")
    os.replace(temporary, file)


def _port_for_start(root: Path) -> int:
    preferred = _desktop_port(root)
    if preferred is None:
        return pick_port()
    if not port_free(preferred):
        raise RuntimeError(f"Порт Brainalot {preferred} занят. Закройте занявшую его программу и повторите запуск; "
                           "данные Work привязаны к этому адресу.")
    return preferred


class App:
    def __init__(self, root: Path, external_port: int | None = None):
        self.root = root
        self.external_port = external_port
        self.service: Service | None = None
        self.desktop = None
        self.tray = None
        self.update = None  # velopack UpdateInfo once downloaded
        self.lock = threading.Lock()

    # The service -------------------------------------------------------------------------------
    def start_service(self) -> None:
        if self.external_port is not None:
            return
        with self.lock:
            self.service = Service(self.root, _port_for_start(self.root))
            self.service.start()
            _remember_desktop_port(self.root, self.service.port)

    def restart_service(self) -> None:
        if self.external_port is not None:
            _log.info("the checkout supervisor owns the service; restart from its scheduled task")
            return
        with self.lock:
            if self.service:
                self.service.stop()
            self.service = Service(self.root, _port_for_start(self.root))
            self.service.start()
            _remember_desktop_port(self.root, self.service.port)
        if self.tray:
            self.tray.set_tooltip(self.tooltip())

    def welcome_url(self) -> str:
        port = self.service.port if self.service else self.external_port or 8765
        return f"http://127.0.0.1:{port}/welcome"

    def tooltip(self) -> str:
        port = self.service.port if self.service else self.external_port or "—"
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
                 ("Открыть Brainalot", lambda: self.desktop.window_action("full")),
                 ("Плашка", lambda: self.desktop.window_action("bar")),
                 ("Карточка", lambda: self.desktop.window_action("card")),
                 ("Скрыть окна", lambda: self.desktop.window_action("hide")), None,
                 ("Как начать", lambda: webbrowser.open(self.welcome_url())),
                 ("Открыть папку заметок", lambda: _open(paths.vault_path(self.root))),
                 ("Открыть журнал", lambda: _open(self.root / "state" / "logs"))]
        if self.external_port is None:
            items.append(("Перезапустить сервис", self.restart_service))
        if self.update:
            items.append(("Перезапустить и обновить", self.apply_update))
        items += [None, ("Выход", self.quit)]
        return items

    def quit(self) -> None:
        from .desktop import pause_on_quit
        if self.desktop:
            try:
                pause_on_quit(self.desktop.port, self.desktop.token)
            except Exception as exc:
                _log.warning("cannot pause time before quitting: %s", exc)
                if self.tray:
                    self.tray.notify("Brainalot", "Не удалось поставить таймер на паузу. Проверьте сервис и повторите выход.")
                return False
        if self.desktop:
            self.desktop.destroy()
        if self.service:
            self.service.stop()
        if self.tray:
            self.tray.quit()
        return True

    def run(self, background: bool) -> int:
        from .desktop import DesktopHost
        from .tray import Tray

        self.start_service()
        port = self.service.port if self.service else self.external_port
        if port is None:
            raise RuntimeError("No Brainalot service is available")
        token = (self.root / "state" / "api-token").read_text(encoding="utf-8").strip()
        self.desktop = DesktopHost(self.root, port, token, self.quit)
        stop = threading.Event()
        self.tray = Tray(self.menu, self.tooltip(), _icon_path(),
                         on_open=lambda: self.desktop.window_action("full"))
        threading.Thread(target=self._check_updates, args=(stop,), name="brainalot-updates", daemon=True).start()
        tray_thread = threading.Thread(target=self.tray.run, name="brainalot-tray", daemon=True)
        tray_thread.start()
        try:
            self.desktop.run(background)
        finally:
            stop.set()
            if self.tray:
                self.tray.quit()
            tray_thread.join(timeout=3)
            if self.service:
                self.service.stop()
        return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--desktop-smoke" in argv:
        if not os.environ.get("BRAINALOT_HOME"):
            return 2  # diagnostic must never touch an installed user's data
        from .desktop_smoke import run as run_desktop_smoke
        return run_desktop_smoke(paths.default_root())
    _velopack_hooks()
    background = "--background" in argv
    root = paths.prepare_installed_root(paths.default_root())
    setup_logging(root)
    if not _single_instance():
        if not background:
            from .tray import signal_open_desktop
            for _ in range(20):
                if signal_open_desktop():
                    break
                time.sleep(0.1)  # first instance may still be creating its tray window
            else:
                _log.warning("existing Brainalot instance did not accept the open-window message")
        return 0
    info = read_runtime(root)
    token_file = root / "state" / "api-token"
    if info and token_file.exists() and serves_root(info["port"], token_file.read_text(encoding="utf-8").strip()):
        # Attach the desktop to the scheduled-task service without taking ownership of it.
        _log.info("service already runs on %s", info["port"])
        try:
            previous_port = _desktop_port(root)
            if previous_port is not None and previous_port != info["port"]:
                raise RuntimeError(f"Brainalot должен использовать прежний порт {previous_port}; "
                                   f"сейчас сервис на {info['port']}. Перезапустите локальный сервис.")
        except RuntimeError as exc:
            ctypes.windll.user32.MessageBoxW(None, str(exc), "Brainalot", 0x30)
            return 1
        if not _desktop_service_ready(info["port"], token_file.read_text(encoding="utf-8").strip()):
            ctypes.windll.user32.MessageBoxW(None,
                "Перезапустите локальный сервис Brainalot, чтобы открыть новую версию.",
                "Brainalot", 0x30)
            return 1
        _remember_desktop_port(root, info["port"])
        return App(root, external_port=info["port"]).run(background)
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
        ctypes.windll.user32.MessageBoxW(None, "Brainalot не запустился: " + str(sys.exc_info()[1])
                                         + "\nПодробности — в журнале:\n"
                                         + str(root / "state" / "logs" / "brainalot.log"), "Brainalot", 0x10)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
