"""Local Spotify controls through Windows SMTC; no accounts or web requests.

The WinRT projection is provided by Windows PowerShell 5.1 in one hidden helper.
Only explicit ``command`` calls can change playback. Failed commands are never
retried automatically: a timeout can mean Windows accepted the request.
"""

from __future__ import annotations

import atexit
from copy import deepcopy
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
from typing import Any


_ACTIONS = frozenset({"previous", "play_pause", "next", "shuffle", "repeat", "open"})
_SOURCE = re.compile(r"(?:Spotify\.exe|SpotifyAB\.SpotifyMusic_[a-z0-9]+!Spotify)", re.I)


def _empty_state(*, windows: bool, error: str | None = None) -> dict:
    return {
        "available": False, "connected": False, "source_id": None,
        "title": "", "artist": "", "playing": False,
        "shuffle": None, "repeat": None,
        "controls": {action: windows and action == "open" for action in sorted(_ACTIONS)},
        "error": error,
    }


def _validated_state(value: Any, *, windows: bool) -> dict:
    """Treat helper output as a narrow protocol, including the source boundary."""
    if not isinstance(value, dict):
        raise RuntimeError("Invalid Spotify bridge response")
    state = _empty_state(windows=windows)
    state["available"] = value.get("available") is True
    state["connected"] = value.get("connected") is True
    if state["connected"]:
        source_id = value.get("source_id")
        if not isinstance(source_id, str) or not _SOURCE.fullmatch(source_id):
            raise RuntimeError("Invalid Spotify source")
        state.update(
            source_id=source_id,
            title=str(value.get("title") or "")[:2048],
            artist=str(value.get("artist") or "")[:2048],
            playing=value.get("playing") is True,
            shuffle=value.get("shuffle") if type(value.get("shuffle")) is bool else None,
            repeat=value.get("repeat") if value.get("repeat") in ("off", "track", "list") else None,
        )
        controls = value.get("controls")
        if isinstance(controls, dict):
            for action in _ACTIONS - {"open"}:
                state["controls"][action] = controls.get(action) is True
    if isinstance(value.get("error"), str):
        state["error"] = value["error"][:160]
    return state


class _PowerShellBridge:
    """Serialized JSON-lines RPC. A dead/timed-out helper is replaced next call."""

    def __init__(self, *, timeout: float = 5.0):
        self.timeout = timeout
        self._process = None
        self._reader = None
        self._responses = None
        self._sequence = 0
        atexit.register(self.close)

    def _start(self):
        executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        helper = Path(__file__).with_name("spotify_bridge.ps1")
        self._responses = queue.Queue(maxsize=2)
        self._process = subprocess.Popen(
            [str(executable), "-NoLogo", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", str(helper)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            bufsize=0,
        )
        process, responses = self._process, self._responses

        def read_responses():
            try:
                while True:
                    # Bound memory even if the helper ever produces malformed output.
                    line = process.stdout.readline(65537)
                    if not line or len(line) > 65536:
                        break
                    responses.put_nowait(line)
            except (OSError, ValueError, queue.Full):
                pass
            finally:
                try:
                    responses.put_nowait(None)
                except queue.Full:
                    pass

        self._reader = threading.Thread(target=read_responses, name="spotify-smtc", daemon=True)
        self._reader.start()

    def request(self, action: str, value=None) -> dict:
        try:
            if self._process is None or self._process.poll() is not None:
                self._stop()
                self._start()
            self._sequence += 1
            payload = json.dumps({"id": self._sequence, "action": action, "value": value}).encode("utf-8") + b"\n"
            self._process.stdin.write(payload)
            self._process.stdin.flush()
            line = self._responses.get(timeout=self.timeout)
            if line is None:
                raise RuntimeError("Spotify bridge stopped")
            result = json.loads(line.decode("utf-8-sig"))
            if not isinstance(result, dict) or result.get("id") != self._sequence:
                raise RuntimeError("Invalid Spotify bridge response")
            return result
        except queue.Empty:
            self._stop()
            raise RuntimeError("Spotify bridge timed out; command outcome may be unknown") from None
        except (OSError, ValueError, RuntimeError):
            self._stop()
            raise RuntimeError("Spotify local integration is unavailable") from None

    def _stop(self):
        process, self._process = self._process, None
        if process is not None:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=0.5)
            for stream in (process.stdin, process.stdout):
                if stream:
                    stream.close()
        reader, self._reader = self._reader, None
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=0.5)

    def close(self):
        self._stop()
        atexit.unregister(self.close)


class SpotifyController:
    """One controller per API instance; safe for concurrent request threads.

    ``adapter`` is a request(action, value) / close() seam for offline tests.
    Snapshots are copied and briefly cached across the desktop and widget polls.
    Close the controller from the API lifecycle to reap the helper immediately.
    """

    def __init__(self, *, adapter=None, platform=None, clock=time.monotonic,
                 cache_seconds: float = 1.5, retry_seconds: float = 10.0, opener=None):
        self._windows = (platform or sys.platform) == "win32"
        self._adapter = adapter
        self._clock = clock
        self._cache_seconds = cache_seconds
        self._retry_seconds = retry_seconds
        self._opener = opener
        self._lock = threading.RLock()
        self._cached = _empty_state(windows=self._windows)
        self._refresh_at = float("-inf")
        self._closed = False

    def _request(self, action, value=None):
        if self._adapter is None:
            self._adapter = _PowerShellBridge()
        return self._adapter.request(action, value)

    def _save(self, value):
        self._cached = _validated_state(value, windows=self._windows)
        delay = self._cache_seconds if self._cached["available"] else self._retry_seconds
        self._refresh_at = self._clock() + delay
        return deepcopy(self._cached)

    def _failed(self, message):
        self._cached = _empty_state(windows=self._windows, error=message)
        self._refresh_at = self._clock() + self._retry_seconds
        return deepcopy(self._cached)

    def snapshot(self) -> dict:
        with self._lock:
            if self._closed or not self._windows:
                return _empty_state(windows=False)
            if self._clock() < self._refresh_at:
                return deepcopy(self._cached)
            try:
                return self._save(self._request("snapshot")["state"])
            except (OSError, RuntimeError, ValueError, KeyError, TypeError):
                return self._failed("Spotify local integration is unavailable")

    def command(self, action: str, value=None) -> dict:
        with self._lock:
            error = None
            if not isinstance(action, str) or action not in _ACTIONS:
                error = "Unknown Spotify command"
            elif action == "shuffle" and type(value) is not bool:
                error = "Shuffle requires a boolean value"
            elif action == "repeat" and value not in ("off", "track", "list"):
                error = "Repeat requires off, track or list"
            elif action not in ("shuffle", "repeat") and value is not None:
                error = "This Spotify command does not accept a value"
            elif self._closed or not self._windows:
                error = "Spotify local integration is unavailable"
            if error:
                return {"ok": False, "error": error, "state": deepcopy(self._cached)}
            if action == "open":
                try:
                    (self._opener or os.startfile)("spotify:")
                    self._refresh_at = float("-inf")
                    return {"ok": True, "state": deepcopy(self._cached)}
                except (OSError, AttributeError):
                    return {"ok": False, "error": "Could not open Spotify", "state": deepcopy(self._cached)}
            try:
                result = self._request(action, value)
                state = self._save(result["state"])
                if result.get("ok") is True:
                    return {"ok": True, "state": state}
                message = result.get("error")
                return {"ok": False, "error": message[:160] if isinstance(message, str) else "Spotify rejected the command", "state": state}
            except (OSError, RuntimeError, ValueError, KeyError, TypeError):
                return {"ok": False, "error": "Spotify command failed; outcome may be unknown",
                        "state": self._failed("Spotify local integration is unavailable")}

    def close(self):
        with self._lock:
            self._closed = True
            if self._adapter is not None:
                self._adapter.close()
