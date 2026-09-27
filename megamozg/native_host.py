"""Chrome native-messaging host: the panel's way to reach the local service without a pasted token.

Chrome starts this program when the extension calls ``chrome.runtime.sendNativeMessage``; it reads one
message (4-byte little-endian length + JSON), answers with one, and exits. It never opens a port.
Actions:

- ``hello``: make sure the service of this data root runs (start it if needed) and return its port,
  token and version. The extension saves them and talks HTTP from then on;
- ``status``, ``start``, ``restart``: the buttons in the panel settings.

Only extensions listed in the host manifest (``integrate.allowed_origins``) can start it, and the token
it hands out is the one any program of this Windows user can already read from ``state/api-token``.
"""
from __future__ import annotations

import json
import os
import re
import struct
import subprocess
import sys
import time
from pathlib import Path

from . import API_VERSION, __version__, paths
from .runtime import DEFAULT_PORT, health, read_runtime, serves_root

TASK_NAME = "MegaMozg Local Service"  # the autostart task of a source checkout
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
BREAKAWAY = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB: survive Chrome closing the host's job
ACTIONS = ("hello", "status", "start", "restart")


def _root() -> Path:
    return paths.default_root()


def _token(root: Path) -> str | None:
    try:
        return (root / "state" / "api-token").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def running(root: Path) -> dict | None:
    """runtime.json of a live service that accepts this root's token, else None."""
    info, token = read_runtime(root), _token(root)
    if info and token and health(info["port"]) and serves_root(info["port"], token):
        return info
    # A checkout service started before runtime.json existed still listens on 8765.
    if token and not info and serves_root(DEFAULT_PORT, token):
        return {"port": DEFAULT_PORT}
    return None


def listener_pids(netstat_output: str, port: int = DEFAULT_PORT) -> list[int]:
    """PIDs listening on 127.0.0.1:<port>, from ``netstat -ano`` output."""
    found: list[int] = []
    for line in netstat_output.splitlines():
        match = re.match(rf"\s*TCP\s+\S+:{port}\s+\S+\s+LISTENING\s+(\d+)\s*$", line)
        if match and int(match.group(1)) not in found:
            found.append(int(match.group(1)))
    return found


def _run(command: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW,
                          errors="replace")


def _spawn(command: list[str]) -> None:
    # The host lives in Chrome's job object; the service must outlive both the host and that job.
    for flags in (DETACHED | BREAKAWAY | NO_WINDOW, DETACHED | NO_WINDOW):
        try:
            subprocess.Popen(command, creationflags=flags, close_fds=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except OSError:
            continue
    raise RuntimeError("Не удалось запустить Brainalot")


def stop_service(root: Path) -> None:
    info = read_runtime(root)
    if paths.frozen():
        if info and isinstance(info.get("pid"), int):
            _run(["taskkill", "/F", "/PID", str(info["pid"])], 10)
    else:
        # A task that still says "Running" while its supervisor is gone would ignore a new start.
        _run(["schtasks", "/End", "/TN", TASK_NAME], 10)
        port = info["port"] if info else DEFAULT_PORT
        for pid in listener_pids(_run(["netstat", "-ano", "-p", "TCP"], 10).stdout, port):
            _run(["taskkill", "/F", "/PID", str(pid)], 10)
    for _ in range(20):
        if running(root) is None:
            return
        time.sleep(0.25)


def start_service(root: Path, timeout: float = 25.0) -> dict:
    if paths.frozen():
        _spawn([str(Path(sys.executable).resolve().parent / "Brainalot.exe"), "--background"])
    else:
        launcher = paths.project_root() / "scripts" / "Launch-MegaMozg-Hidden.ps1"
        result = _run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                       "-File", str(launcher)], 40)
        if result.returncode != 0 and running(root) is None:
            raise RuntimeError((result.stderr or result.stdout or "the launcher failed").strip()[:300])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        info = running(root)
        if info:
            return info
        time.sleep(0.25)
    raise RuntimeError("Сервис не ответил. Журнал: " + str(root / "state" / "logs"))


def handle(message: dict, root: Path | None = None) -> dict:
    action = message.get("action") if isinstance(message, dict) else None
    if action not in ACTIONS:
        return {"ok": False, "error": "unknown action"}
    root = root or _root()
    try:
        info = running(root)
        if action == "restart":
            stop_service(root)
            info = start_service(root)
        elif action in {"start", "hello"} and info is None:
            stop_service(root)
            info = start_service(root)
    except Exception as error:  # reported to the panel, never raised into Chrome
        return {"ok": False, "running": running(root) is not None, "error": str(error)}
    answer = {"ok": True, "running": info is not None, "host_version": __version__, "api_version": API_VERSION}
    if action == "hello" and info:
        answer.update(port=info["port"], token=_token(root), version=info.get("version"))
    return answer


def read_message(stream) -> dict | None:
    header = stream.read(4)
    if len(header) < 4:
        return None
    (length,) = struct.unpack("<I", header)
    if length > 4096:
        return None
    return json.loads(stream.read(length).decode("utf-8"))


def write_message(stream, payload: dict) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    stream.write(struct.pack("<I", len(data)) + data)
    stream.flush()


def main() -> int:
    try:
        message = read_message(sys.stdin.buffer)
    except Exception:
        message = None
    write_message(sys.stdout.buffer, handle(message) if message is not None else {"ok": False, "error": "bad message"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
