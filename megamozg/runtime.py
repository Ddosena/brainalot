"""Running the local service: port choice, runtime.json, logging, a uvicorn server without a console.

``state/runtime.json`` says which process serves this data root and on which port. The native-messaging
host reads it to hand the port and token to the extension, so nothing depends on 8765 being free.
"""
from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import socket
import sys
import threading
import time
from urllib.request import Request, urlopen

from . import __version__

DEFAULT_PORT = 8765
PORT_SPAN = 20  # 8765..8784
_log = logging.getLogger("brainalot")


def runtime_path(root: Path) -> Path:
    return Path(root) / "state" / "runtime.json"


def read_runtime(root: Path) -> dict | None:
    try:
        value = json.loads(runtime_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) and isinstance(value.get("port"), int) else None


def _write_runtime(root: Path, port: int) -> None:
    path = runtime_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"port": port, "pid": os.getpid(), "version": __version__,
                                     "started": time.time()}), encoding="utf-8")
    os.replace(temporary, path)


def _clear_runtime(root: Path) -> None:
    current = read_runtime(root)
    if current and current.get("pid") == os.getpid():
        runtime_path(root).unlink(missing_ok=True)


def health(port: int, timeout: float = 1.0) -> dict | None:
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as response:
            value = json.load(response)
        return value if isinstance(value, dict) and value.get("status") == "ok" else None
    except Exception:
        return None


def serves_root(port: int, token: str, timeout: float = 1.0) -> bool:
    """True when the service on ``port`` accepts this data root's token, i.e. is our own copy."""
    request = Request(f"http://127.0.0.1:{port}/api/runtime", headers={"Authorization": "Bearer " + token})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        # Without SO_EXCLUSIVEADDRUSE Windows lets a second socket share a listening port.
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def pick_port(preferred: int = DEFAULT_PORT, span: int = PORT_SPAN) -> int:
    for port in range(preferred, preferred + span):
        if port_free(port):
            return port
    raise OSError(f"Нет свободного порта {preferred}–{preferred + span - 1} на 127.0.0.1")


def setup_logging(root: Path) -> Path:
    """Log to state/logs/brainalot.log (1 MB x 3) and, when there is a console, to stderr."""
    directory = Path(root) / "state" / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "brainalot.log"
    logger = logging.getLogger()
    if not any(isinstance(handler, RotatingFileHandler) for handler in logger.handlers):
        handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
        if sys.stderr is not None:
            logger.addHandler(logging.StreamHandler())
        logger.setLevel(logging.INFO)
    return path


class Service:
    """The HTTP service in a background thread; ``stop()`` shuts it down cleanly."""

    def __init__(self, root: Path, port: int):
        import uvicorn

        from .api import create_app

        self.root, self.port = Path(root), port
        # log_config=None: uvicorn's default logging config needs a console and crashes without one
        # (pythonw, windowed builds); our handlers from setup_logging() are used instead.
        config = uvicorn.Config(create_app(root, port=port), host="127.0.0.1", port=port,
                                access_log=False, log_config=None)
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self._run, name="brainalot-http", daemon=True)
        self.error: BaseException | None = None

    def _run(self) -> None:
        try:
            self.server.run()
        except BaseException as exc:  # reported by wait_started / the tray
            self.error = exc
            _log.exception("HTTP service stopped with an error")
        finally:
            _clear_runtime(self.root)

    def start(self, timeout: float = 20.0) -> None:
        self.thread.start()
        deadline = time.monotonic() + timeout
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError(f"Сервис не запустился на порту {self.port}: {self.error or 'нет ответа'}")
            time.sleep(0.05)
        _write_runtime(self.root, self.port)
        _log.info("Brainalot %s serves %s on 127.0.0.1:%d", __version__, self.root, self.port)

    def stop(self, timeout: float = 5.0) -> None:
        self.server.should_exit = True
        self.thread.join(timeout)
        _clear_runtime(self.root)


def serve_forever(root: Path, port: int | None = None) -> None:
    """``megamozg serve``: the service in the foreground until Ctrl+C or the process is stopped."""
    setup_logging(root)
    service = Service(root, port if port is not None else pick_port())
    service.start()
    try:
        while service.thread.is_alive():
            service.thread.join(0.5)
    except KeyboardInterrupt:
        service.stop()
    if service.error is not None:
        raise SystemExit(1)
