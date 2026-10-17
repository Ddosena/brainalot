"""Durable, single-interval time tracking shared by desktop and browser clients."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .core import ConflictError, ValidationError


CHECKPOINT_MS = 10_000
MAX_GAP_MS = 30_000
MAX_DRIFT_MS = 5_000
UNREGISTERED_DRIFT_MS = 100
POMODORO_MS = 1_500_000
MODES = {"stopwatch", "pomodoro", "break"}
SOURCES = {"library", "work", "calendar"}
LOGGER = logging.getLogger(__name__)
PBT_APMSUSPEND = 4
PBT_APMRESUMESUSPEND = 7
PBT_APMRESUMEAUTOMATIC = 18
DEVICE_NOTIFY_CALLBACK = 2


@lru_cache(maxsize=1)
def _unbiased_query():
    """Microsoft's awake-time clock; unlike uptime, it excludes sleep."""
    import ctypes

    function = ctypes.WinDLL("kernel32", use_last_error=True).QueryUnbiasedInterruptTime
    function.argtypes = [ctypes.POINTER(ctypes.c_ulonglong)]
    function.restype = ctypes.c_int  # BOOL
    return function


def _windows_awake_ms():
    import ctypes

    ticks = ctypes.c_ulonglong()
    if not _unbiased_query()(ctypes.byref(ticks)):
        raise OSError(ctypes.get_last_error(), "QueryUnbiasedInterruptTime failed")
    return ticks.value // 10_000  # 100 ns units to milliseconds


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _task(value):
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {"source", "id", "title"}
            or not isinstance(value.get("source"), str) or value["source"] not in SOURCES
            or any(not isinstance(value.get(key), str) or not value[key].strip()
                   or len(value[key]) > limit or any(c in value[key] for c in "\r\n\x00")
                   for key, limit in (("id", 2048), ("title", 512)))):
        raise ValidationError("task must be null or a TaskRef with source, id and title")
    return {key: value[key] for key in ("source", "id", "title")}


class TimeStore:
    """SQLite time log. ``root`` is the data directory, never a database path.

    ``clock`` and ``monotonic`` return milliseconds. The latter must advance on
    the same scale as the wall clock and is injectable for deterministic tests.
    """

    def __init__(self, root, clock=None, monotonic=None):
        self.db_path = Path(root) / "state" / "time.sqlite3"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock or (lambda: time.time() * 1000)
        self._unbiased_failed = False
        self._native_clock = monotonic is None and os.name == "nt"
        self.monotonic = monotonic or (self._awake_ms if self._native_clock
                                       else lambda: time.monotonic() * 1000)
        self._thread = None
        self._stop = threading.Event()
        self._power_registered = False
        self._power_handle = None
        self._power_callback = None
        self._power_parameters = None
        self._power_dll = None
        self._suspend_seen = False
        self._last_auto_resume = None
        self._init_db()

    def _awake_ms(self):
        try:
            return _windows_awake_ms()
        except (OSError, AttributeError):
            if not self._unbiased_failed:
                LOGGER.exception("Windows awake-time clock unavailable; timer requires power callbacks")
            self._unbiased_failed = True
            return int(time.monotonic() * 1000)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=30000")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _init_db(self):
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS timer (
                    id INTEGER PRIMARY KEY CHECK (id=1),
                    revision INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    task TEXT,
                    elapsed_ms INTEGER NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    started_at INTEGER,
                    anchor_at INTEGER,
                    anchor_mono INTEGER,
                    interruption TEXT
                );
                CREATE TABLE IF NOT EXISTS intervals (
                    id INTEGER PRIMARY KEY,
                    start_ms INTEGER NOT NULL,
                    end_ms INTEGER NOT NULL,
                    task TEXT,
                    mode TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_time_intervals_end_start
                    ON intervals(end_ms, start_ms);
                CREATE TABLE IF NOT EXISTS receipts (
                    request_id TEXT PRIMARY KEY,
                    body_hash TEXT NOT NULL,
                    response TEXT NOT NULL
                );
            """)
            db.execute("""INSERT OR IGNORE INTO timer VALUES
                (1,0,'idle','stopwatch',NULL,0,0,NULL,NULL,NULL,NULL)""")

    def _now(self):
        return int(self.clock()), int(self.monotonic())

    @staticmethod
    def _record(db):
        return dict(db.execute("SELECT * FROM timer WHERE id=1").fetchone())

    @staticmethod
    def _save(db, row):
        keys = [key for key in row if key != "id"]
        db.execute("UPDATE timer SET " + ",".join(key + "=?" for key in keys)
                   + " WHERE id=1", [row[key] for key in keys])

    def _healthy(self, row, wall, mono):
        if row["status"] != "running":
            return True
        dw, dm = wall - row["anchor_at"], mono - row["anchor_mono"]
        drift_limit = (UNREGISTERED_DRIFT_MS if self._native_clock and not self._power_registered
                       else MAX_DRIFT_MS)
        return 0 <= dw <= MAX_GAP_MS and 0 <= dm <= MAX_GAP_MS and abs(dw - dm) <= drift_limit

    @staticmethod
    def _tail(row, wall, mono):
        if row["status"] != "running":
            return 0
        # Use wall time for both the displayed counter and UTC interval bounds;
        # monotonic time is an independent guard against sleep/clock changes.
        delta = max(0, wall - row["anchor_at"])
        if row["duration_ms"] and row["mode"] in {"pomodoro", "break"}:
            delta = min(delta, max(0, row["duration_ms"] - row["elapsed_ms"]))
        return delta

    @staticmethod
    def _append(db, row, end):
        if row["mode"] != "break" and end > row["anchor_at"]:
            db.execute("INSERT INTO intervals(start_ms,end_ms,task,mode) VALUES(?,?,?,?)",
                       (row["anchor_at"], end, row["task"], row["mode"]))

    def _settle(self, db, row, wall, mono, *, force=False):
        """Persist an active tail, or discard it after an uncertain gap."""
        if row["status"] != "running":
            return
        if not self._healthy(row, wall, mono):
            row.update(status="paused", started_at=None, anchor_at=None,
                       anchor_mono=None, interruption="clock_gap")
            row["revision"] += 1
            self._save(db, row)
            return
        delta = self._tail(row, wall, mono)
        due = (row["duration_ms"] > 0 and row["mode"] in {"pomodoro", "break"}
               and row["elapsed_ms"] + delta >= row["duration_ms"])
        if not force and delta < CHECKPOINT_MS and not due:
            return
        end = min(wall, row["anchor_at"] + delta)
        self._append(db, row, end)
        row["elapsed_ms"] += delta
        row["anchor_at"] = end
        row["anchor_mono"] = mono
        if row["duration_ms"] and row["mode"] in {"pomodoro", "break"} and row["elapsed_ms"] >= row["duration_ms"]:
            row.update(status="finished", started_at=None, anchor_at=None, anchor_mono=None)
            row["revision"] += 1
        self._save(db, row)

    def _state(self, row, wall, mono):
        elapsed = row["elapsed_ms"] + self._tail(row, wall, mono)
        return {"revision": row["revision"], "status": row["status"],
                "mode": row["mode"], "task": json.loads(row["task"]) if row["task"] else None,
                "elapsed_ms": elapsed, "duration_ms": row["duration_ms"],
                "started_at": row["started_at"], "server_now": wall,
                "interruption": row["interruption"]}

    def state(self):
        wall, mono = self._now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._record(db)
            if row["status"] == "running" and (not self._healthy(row, wall, mono)
                    or row["duration_ms"] and row["mode"] in {"pomodoro", "break"}
                    and row["elapsed_ms"] + self._tail(row, wall, mono) >= row["duration_ms"]):
                self._settle(db, row, wall, mono, force=True)
            return self._state(row, wall, mono)

    @staticmethod
    def _duration(mode, value):
        if mode == "stopwatch":
            if value is not None:
                raise ValidationError("stopwatch has no duration_ms")
            return 0
        if value is None:
            return POMODORO_MS if mode == "pomodoro" else 0
        if isinstance(value, bool) or not isinstance(value, int) or not 60_000 <= value <= 10_800_000:
            raise ValidationError("duration_ms must be between 60000 and 10800000")
        return value

    def command(self, body: dict):
        if not isinstance(body, dict):
            raise ValidationError("command must be an object")
        rid = body.get("request_id")
        expected = body.get("expected_revision")
        action = body.get("action")
        if not isinstance(rid, str) or not rid.strip() or len(rid) > 256:
            raise ValidationError("request_id is required")
        if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
            raise ValidationError("expected_revision must be a nonnegative integer")
        if (not isinstance(action, str) or action not in
                {"start", "pause", "resume", "stop", "switch_task", "set_mode", "reset_pomodoro"}):
            raise ValidationError("unknown time action")
        allowed = {"request_id", "expected_revision", "action", "task", "mode", "duration_ms"}
        if set(body) - allowed:
            raise ValidationError("unknown time command fields")
        if "task" in body:
            _task(body["task"])
        if "mode" in body and (not isinstance(body["mode"], str) or body["mode"] not in MODES):
            raise ValidationError("unknown mode")
        if "duration_ms" in body and action not in {"start", "set_mode"}:
            raise ValidationError("duration_ms is only valid for start or set_mode")
        digest = hashlib.sha256(_json(body).encode("utf-8")).hexdigest()
        wall, mono = self._now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            receipt = db.execute("SELECT body_hash,response FROM receipts WHERE request_id=?", (rid,)).fetchone()
            if receipt:
                if receipt["body_hash"] != digest:
                    raise ConflictError("request_id was already used for a different command")
                return json.loads(receipt["response"])
            if (action in {"start", "resume"} and self._native_clock
                    and self._unbiased_failed and not self._power_registered):
                raise ValidationError("Windows time and suspend notification are unavailable")
            row = self._record(db)
            self._settle(db, row, wall, mono, force=True)
            if row["revision"] != expected:
                raise ConflictError("time state revision changed")
            if action == "start":
                mode = body.get("mode", "stopwatch")
                row.update(status="running", mode=mode,
                           task=_json(_task(body.get("task"))) if body.get("task") is not None else None,
                           elapsed_ms=0, duration_ms=self._duration(mode, body.get("duration_ms")),
                           started_at=wall, anchor_at=wall, anchor_mono=mono, interruption=None)
            elif action == "pause":
                if row["status"] != "running":
                    raise ValidationError("timer is not running")
                row.update(status="paused", started_at=None, anchor_at=None, anchor_mono=None)
            elif action == "resume":
                if row["status"] != "paused":
                    raise ValidationError("timer is not paused")
                row.update(status="running", started_at=wall, anchor_at=wall,
                           anchor_mono=mono, interruption=None)
            elif action == "stop":
                row.update(status="idle", started_at=None,
                           anchor_at=None, anchor_mono=None, interruption=None)
            elif action == "switch_task":
                if "task" not in body:
                    raise ValidationError("switch_task requires task, which may be null")
                row["task"] = _json(_task(body["task"])) if body["task"] is not None else None
                if row["status"] == "running":
                    row.update(started_at=wall, anchor_at=wall, anchor_mono=mono)
            elif action == "set_mode":
                if "mode" not in body:
                    raise ValidationError("set_mode requires mode")
                mode = body["mode"]
                row.update(mode=mode, elapsed_ms=0,
                           duration_ms=self._duration(mode, body.get("duration_ms")),
                           interruption=None)
                if row["status"] == "running":
                    row.update(started_at=wall, anchor_at=wall, anchor_mono=mono)
                elif row["status"] == "finished":
                    row["status"] = "paused"
            else:
                if row["mode"] != "pomodoro":
                    raise ValidationError("reset_pomodoro requires pomodoro mode")
                row.update(status="paused", elapsed_ms=0, duration_ms=POMODORO_MS,
                           started_at=None, anchor_at=None, anchor_mono=None, interruption=None)
            row["revision"] += 1
            self._save(db, row)
            result = self._state(row, wall, mono)
            db.execute("INSERT INTO receipts VALUES(?,?,?)", (rid, digest, _json(result)))
            return result

    def summary(self, day, timezone="Europe/Moscow"):
        try:
            selected = date.fromisoformat(day)
            if selected.isoformat() != day:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise ValidationError("day must be YYYY-MM-DD") from exc
        try:
            zone = ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
            raise ValidationError("invalid IANA timezone") from exc
        start = int(datetime.combine(selected, datetime.min.time(), zone).timestamp() * 1000)
        end = int(datetime.combine(selected + timedelta(days=1), datetime.min.time(), zone).timestamp() * 1000)
        wall, mono = self._now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._record(db)
            if row["status"] == "running" and (not self._healthy(row, wall, mono)
                    or row["duration_ms"] and row["mode"] in {"pomodoro", "break"}
                    and row["elapsed_ms"] + self._tail(row, wall, mono) >= row["duration_ms"]):
                self._settle(db, row, wall, mono, force=True)
            intervals = [tuple(item) for item in db.execute(
                "SELECT start_ms,end_ms,task FROM intervals WHERE start_ms<? AND end_ms>? ORDER BY id",
                (end, start))]
            if row["status"] == "running" and row["mode"] != "break":
                intervals.append((row["anchor_at"],
                                  min(wall, row["anchor_at"] + self._tail(row, wall, mono)), row["task"]))
        totals = {}
        for left, right, task in intervals:
            amount = max(0, min(right, end) - max(left, start))
            if amount:
                ref = json.loads(task) if task else None
                key = (ref["source"], ref["id"]) if ref else None
                if key not in totals:
                    totals[key] = {"task": ref, "total_ms": 0}
                totals[key]["total_ms"] += amount
        rows = sorted(totals.values(), key=lambda item: -item["total_ms"])
        return {"day": day, "timezone": timezone,
                "total_ms": sum(item["total_ms"] for item in rows), "rows": rows}

    def checkpoint(self):
        wall, mono = self._now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._record(db)
            self._settle(db, row, wall, mono)
            return self._state(row, wall, mono)

    def _power_event(self, kind):
        """Handle a native power event; also called with fake clocks in tests."""
        if kind not in {PBT_APMSUSPEND, PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND}:
            return
        wall, mono = self._now()
        # Windows normally sends automatic resume before optional user resume.
        if (kind == PBT_APMRESUMESUSPEND and self._last_auto_resume is not None
                and 0 <= wall - self._last_auto_resume < 60_000):
            return
        if kind == PBT_APMSUSPEND:
            self._suspend_seen = True
            self._last_auto_resume = None
        elif kind == PBT_APMRESUMEAUTOMATIC:
            self._last_auto_resume = wall
        if kind != PBT_APMSUSPEND and self._suspend_seen:
            self._suspend_seen = False
            return  # The suspend callback already saved and paused the timer.
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._record(db)
            if row["status"] != "running":
                return
            if kind == PBT_APMSUSPEND:
                self._settle(db, row, wall, mono, force=True)
            # An unmatched resume gives no trustworthy boundary for the tail.
            if row["status"] == "running":
                row.update(status="paused", started_at=None, anchor_at=None,
                           anchor_mono=None,
                           interruption="system_suspend" if kind == PBT_APMSUSPEND else "system_resume")
                row["revision"] += 1
                self._save(db, row)

    def _register_power(self):
        if os.name != "nt" or self._power_handle is not None:
            return
        import ctypes
        from ctypes import wintypes

        callback_type = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p,
                                           ctypes.c_ulong, ctypes.c_void_p)

        class Parameters(ctypes.Structure):
            _fields_ = [("Callback", callback_type), ("Context", ctypes.c_void_p)]

        def callback(_context, kind, _setting):
            try:
                self._power_event(kind)
            except Exception:
                self._power_registered = False
                LOGGER.exception("Windows suspend/resume callback failed")
            return 0

        dll = ctypes.WinDLL("powrprof", use_last_error=True)
        register = dll.PowerRegisterSuspendResumeNotification
        register.argtypes = [wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(wintypes.HANDLE)]
        register.restype = wintypes.DWORD
        unregister = dll.PowerUnregisterSuspendResumeNotification
        unregister.argtypes = [wintypes.HANDLE]
        unregister.restype = wintypes.DWORD
        native_callback = callback_type(callback)
        parameters = Parameters(native_callback, None)
        handle = wintypes.HANDLE()
        code = register(DEVICE_NOTIFY_CALLBACK, ctypes.cast(ctypes.pointer(parameters), wintypes.HANDLE),
                        ctypes.byref(handle))
        if code:
            raise OSError(code, "PowerRegisterSuspendResumeNotification failed")
        # ctypes callbacks and their parameter block must outlive registration.
        self._power_dll = dll
        self._power_callback = native_callback
        self._power_parameters = parameters
        self._power_handle = handle
        self._power_registered = True

    def _unregister_power(self):
        if self._power_handle is None:
            return
        code = self._power_dll.PowerUnregisterSuspendResumeNotification(self._power_handle)
        if code:
            LOGGER.error("PowerUnregisterSuspendResumeNotification failed: %s", code)
            return  # Retain the callback reference while Windows may call it.
        self._power_registered = False
        self._power_handle = self._power_callback = self._power_parameters = self._power_dll = None

    def start_service(self):
        if self._thread and self._thread.is_alive():
            return
        # A constructor may be used by a short-lived CLI process. Only the
        # actual service startup is evidence that a former runner has died.
        wall, mono = self._now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._record(db)
            if row["status"] == "running":
                row.update(status="paused", started_at=None, anchor_at=None,
                           anchor_mono=None, interruption="service_restart")
                row["revision"] += 1
                self._save(db, row)
        try:
            self._register_power()
        except Exception:
            LOGGER.exception("Windows power notifications unavailable; using strict awake-time drift limit")
        self._stop.clear()
        self._thread = threading.Thread(target=self._service_loop, daemon=True, name="MegaMozgTimeCheckpoint")
        self._thread.start()

    def _service_loop(self):
        while not self._stop.wait(CHECKPOINT_MS / 1000):
            try:
                self.checkpoint()
            except Exception:
                # The next successful tick can still detect an unsafe gap.
                LOGGER.exception("Time checkpoint failed")

    def stop_service(self):
        self._unregister_power()
        thread = self._thread
        if thread:
            self._stop.set()
            thread.join(timeout=5)
            self._thread = None
        wall, mono = self._now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._record(db)
            if row["status"] == "running":
                self._settle(db, row, wall, mono, force=True)
                if row["status"] == "running":
                    row.update(status="paused", started_at=None, anchor_at=None,
                               anchor_mono=None, interruption="service_stop")
                    row["revision"] += 1
                    self._save(db, row)
