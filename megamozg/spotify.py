"""Local Spotify controls through Windows SMTC; no accounts or web requests.

The WinRT projection is provided by Windows PowerShell 5.1 in one hidden helper.
Only explicit ``command`` calls can change playback. Failed commands are never
retried automatically: a timeout can mean Windows accepted the request.
"""

from __future__ import annotations

import atexit
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
from typing import Any


_ACTIONS = frozenset({"previous", "play_pause", "next", "shuffle", "repeat", "open", "volume"})
_SOURCE = re.compile(r"(?:Spotify\.exe|SpotifyAB\.SpotifyMusic_[a-z0-9]+!Spotify)", re.I)


def _empty_state(*, windows: bool, error: str | None = None) -> dict:
    return {
        "available": False, "connected": False, "source_id": None,
        "title": "", "artist": "", "playing": False,
        "shuffle": None, "repeat": None, "volume": None,
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
        volume = value.get("volume")
        if type(volume) in (int, float) and 0 <= volume <= 1 and math.isfinite(volume):
            state["volume"] = float(volume)
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

    def __init__(self, *, adapter=None, snapshot_adapter=None, platform=None, clock=time.monotonic,
                 cache_seconds: float = 1.5, retry_seconds: float = 10.0, opener=None):
        self._windows = (platform or sys.platform) == "win32"
        self._adapter = adapter
        self._snapshot_adapter = snapshot_adapter or adapter
        self._injected = adapter is not None
        self._clock = clock
        self._cache_seconds = cache_seconds
        self._retry_seconds = retry_seconds
        self._opener = opener
        self._lock = threading.RLock()
        self._commands = threading.Lock()
        self._snapshots = threading.Lock()
        self._generation = 0
        self._command_active = False
        self._warm_thread = None
        self._cached = _empty_state(windows=self._windows)
        self._refresh_at = float("-inf")
        self._closed = False

    def _request(self, action, value=None, *, snapshot=False):
        with self._lock:
            name = "_snapshot_adapter" if snapshot else "_adapter"
            if getattr(self, name) is None:
                setattr(self, name, _PowerShellBridge())
            adapter = getattr(self, name)
        return adapter.request(action, value)

    def _save(self, value):
        # Lightweight command replies omit metadata/audio fields instead of
        # pretending that the current title or mixer level is unknown.
        if isinstance(value, dict) and value.get("source_id") == self._cached.get("source_id"):
            value = deepcopy(value)
            for key in ("title", "artist", "volume"):
                if key not in value:
                    value[key] = self._cached[key]
            if isinstance(value.get("controls"), dict) and "volume" not in value["controls"]:
                value["controls"]["volume"] = self._cached["controls"]["volume"]
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
            if self._command_active or self._clock() < self._refresh_at:
                return deepcopy(self._cached)
            generation = self._generation
        # Optional polls coalesce; none wait behind another metadata request.
        if not self._snapshots.acquire(blocking=False):
            with self._lock:
                return deepcopy(self._cached)
        try:
            with self._lock:
                if self._closed:
                    return _empty_state(windows=False)
            result = self._request("snapshot", snapshot=True)["state"]
            with self._lock:
                state = deepcopy(self._cached) if generation != self._generation or self._closed else self._save(result)
            self._warm_commands()
            return state
        except (OSError, RuntimeError, ValueError, KeyError, TypeError):
            with self._lock:
                return deepcopy(self._cached) if generation != self._generation or self._closed else self._failed("Spotify local integration is unavailable")
        finally:
            self._snapshots.release()

    def _warm_commands(self):
        # Read-only and optional; a cold first click remains bounded by RPC timeout.
        with self._lock:
            if self._injected or self._closed or self._warm_thread is not None:
                return
            def warm():
                if not self._commands.acquire(blocking=False):
                    return
                try:
                    with self._lock:
                        if self._closed:
                            return
                    self._request("state")
                except (OSError, RuntimeError, ValueError):
                    pass
                finally:
                    self._commands.release()
            self._warm_thread = threading.Thread(target=warm, name="spotify-warm", daemon=True)
            self._warm_thread.start()

    def command(self, action: str, value=None) -> dict:
        with self._commands:
            return self._command(action, value)

    def _command(self, action: str, value=None) -> dict:
        with self._lock:
            error = None
            if not isinstance(action, str) or action not in _ACTIONS:
                error = "Unknown Spotify command"
            elif action == "shuffle" and type(value) is not bool:
                error = "Shuffle requires a boolean value"
            elif action == "repeat" and value not in ("off", "track", "list"):
                error = "Repeat requires off, track or list"
            elif action == "volume" and (type(value) not in (int, float) or not 0 <= value <= 1 or not math.isfinite(value)):
                error = "Volume requires a number between 0 and 1"
            elif action not in ("shuffle", "repeat", "volume") and value is not None:
                error = "This Spotify command does not accept a value"
            elif self._closed or not self._windows:
                error = "Spotify local integration is unavailable"
            if error:
                return {"ok": False, "error": error, "state": deepcopy(self._cached)}
            self._generation += 1
            self._command_active = True
        try:
            if action == "open":
                try:
                    (self._opener or os.startfile)("spotify:")
                    with self._lock:
                        self._refresh_at = float("-inf")
                        return {"ok": True, "state": deepcopy(self._cached)}
                except (OSError, AttributeError):
                    with self._lock:
                        return {"ok": False, "error": "Could not open Spotify", "state": deepcopy(self._cached)}
            try:
                result = self._request(action, value)
                with self._lock:
                    state = self._save(result["state"])
                    # An accepted SMTC request may publish state asynchronously.
                    # Never freeze its immediate response for the normal 1.5s TTL.
                    if state["available"]:
                        self._refresh_at = float("-inf")
                if result.get("ok") is True:
                    return {"ok": True, "state": state, "confirmed": result.get("confirmed")}
                message = result.get("error")
                return {"ok": False, "error": message[:160] if isinstance(message, str) else "Spotify rejected the command", "state": state}
            except (OSError, RuntimeError, ValueError, KeyError, TypeError):
                with self._lock:
                    return {"ok": False, "error": "Spotify command failed; outcome may be unknown",
                            "state": self._failed("Spotify local integration is unavailable")}
        finally:
            with self._lock:
                self._command_active = False

    def close(self):
        with self._lock:
            self._closed = True
        with self._commands, self._snapshots:
            if self._adapter is not None:
                self._adapter.close()
            if self._snapshot_adapter is not None and self._snapshot_adapter is not self._adapter:
                self._snapshot_adapter.close()
