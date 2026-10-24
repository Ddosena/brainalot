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
from . import features


CHECKPOINT_MS = 10_000
MAX_GAP_MS = 30_000
MAX_DRIFT_MS = 5_000
UNREGISTERED_DRIFT_MS = 100
POMODORO_MS = 1_500_000
MODES = {"stopwatch", "pomodoro", "break"}
SOURCES = {"library", "work", "calendar", "label"}
# Time categories that are not library tasks (owner decision 2026-10-19).
LABELS = {"rest": "Отдых"}
# Past parts of timed calendar events, recomputed from the calendar cache.
CALENDAR_MODE = "calendar"
BACKFILL_LIMIT_MS = 12 * 3_600_000
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
    if value["source"] == "label" and value["id"] not in LABELS:
        raise ValidationError("Неизвестная метка времени")
    return {key: value[key] for key in ("source", "id", "title")}


def _free(segment, busy):
    """Parts of ``segment`` not covered by any (start, end) in ``busy``."""
    left, right = segment
    parts = []
    for start, end in sorted(busy):
        if end <= left or start >= right:
            continue
        if start > left:
            parts.append((left, start))
        left = max(left, end)
        if left >= right:
            break
    if left < right:
        parts.append((left, right))
    return parts


def _day_window(day):
    """Local-time day [start, end) in ms. Calendar rows never cross it, so every
    recomputation of a day replaces exactly the rows it created."""
    start = datetime.combine(day, datetime.min.time()).astimezone()
    end = datetime.combine(day + timedelta(days=1), datetime.min.time()).astimezone()
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


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
                CREATE TABLE IF NOT EXISTS calendar_skips (
                    task_id TEXT PRIMARY KEY,
                    at INTEGER NOT NULL
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
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # Sample after acquiring the write lock: another client may have
            # advanced the anchor while this transaction was waiting.
            wall, mono = self._now()
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
        if isinstance(value, bool) or not isinstance(value, int) or not 60_000 <= value <= 14_400_000:
            raise ValidationError("duration_ms must be between 60000 and 14400000")
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
        allowed = {"request_id", "expected_revision", "action", "task", "mode", "duration_ms", "backfill_from"}
        if set(body) - allowed:
            raise ValidationError("unknown time command fields")
        if "backfill_from" in body and (action != "start" or isinstance(body["backfill_from"], bool)
                                        or not isinstance(body["backfill_from"], int)):
            raise ValidationError("backfill_from is only valid for start")
        if "task" in body:
            _task(body["task"])
        if "mode" in body and (not isinstance(body["mode"], str) or body["mode"] not in MODES):
            raise ValidationError("unknown mode")
        if "duration_ms" in body and action not in {"start", "set_mode"}:
            raise ValidationError("duration_ms is only valid for start or set_mode")
        digest = hashlib.sha256(_json(body).encode("utf-8")).hexdigest()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # Sample after acquiring the write lock: another client may have
            # advanced the anchor while this transaction was waiting.
            wall, mono = self._now()
            receipt = db.execute("SELECT body_hash,response FROM receipts WHERE request_id=?", (rid,)).fetchone()
            if receipt:
                if receipt["body_hash"] != digest:
                    raise ConflictError("request_id уже использован для другой команды таймера")
                return json.loads(receipt["response"])
            if (action in {"start", "resume"} and self._native_clock
                    and self._unbiased_failed and not self._power_registered):
                raise ValidationError("Windows не даёт время и уведомления о сне; таймер не запущен")
            row = self._record(db)
            self._settle(db, row, wall, mono, force=True)
            if row["revision"] != expected:
                raise ConflictError("Таймер изменился в другом окне. Показано актуальное состояние")
            task = body.get("task") if action in {"start", "switch_task"} else json.loads(row["task"]) if row["task"] else None
            if action in {"start", "resume", "switch_task"}:
                from .work_delete import guard_timer_target
                guard_timer_target(self.db_path.parent.parent, task)
            if not features.WORK_ENABLED and action in {"start", "switch_task", "resume"} and task and task.get("source") == "work":
                raise ValidationError(features.WORK_PAUSED_MESSAGE)
            if action == "start" and "backfill_from" in body:
                # The answer to "what are you doing?": the untracked active stretch
                # before now belongs to the chosen task. Never over tracked time.
                if row["status"] == "running":
                    raise ValidationError("Время уже учитывается; дозапись не нужна")
                last = db.execute("SELECT MAX(end_ms) FROM intervals WHERE mode!=?",
                                  (CALENDAR_MODE,)).fetchone()[0] or 0
                since = max(body["backfill_from"], last, wall - BACKFILL_LIMIT_MS)
                if since < wall - 1000:
                    task_json = _json(_task(body.get("task"))) if body.get("task") is not None else None
                    db.execute("INSERT INTO intervals(start_ms,end_ms,task,mode) VALUES(?,?,?,?)",
                               (since, wall, task_json, "stopwatch"))
            if action == "start":
                mode = body.get("mode", "stopwatch")
                row.update(status="running", mode=mode,
                           task=_json(_task(body.get("task"))) if body.get("task") is not None else None,
                           elapsed_ms=0, duration_ms=self._duration(mode, body.get("duration_ms")),
                           started_at=wall, anchor_at=wall, anchor_mono=mono, interruption=None)
            elif action == "pause":
                if row["status"] != "running":
                    raise ValidationError("Таймер уже не идёт")
                row.update(status="paused", started_at=None, anchor_at=None, anchor_mono=None)
            elif action == "resume":
                if row["status"] != "paused":
                    raise ValidationError("Таймер не на паузе")
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
                row.update(status="paused", elapsed_ms=0,
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
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # Sample after acquiring the write lock: another client may have
            # advanced the anchor while this transaction was waiting.
            wall, mono = self._now()
            row = self._record(db)
            if row["status"] == "running" and (not self._healthy(row, wall, mono)
                    or row["duration_ms"] and row["mode"] in {"pomodoro", "break"}
                    and row["elapsed_ms"] + self._tail(row, wall, mono) >= row["duration_ms"]):
                self._settle(db, row, wall, mono, force=True)
            intervals = [tuple(item) for item in db.execute(
                "SELECT start_ms,end_ms,task,mode FROM intervals WHERE start_ms<? AND end_ms>? ORDER BY id",
                (end, start))]
            if row["status"] == "running" and row["mode"] != "break":
                intervals.append((row["anchor_at"], min(wall, row["anchor_at"] + self._tail(row, wall, mono)),
                                  row["task"], row["mode"]))
        totals = {}
        for left, right, task, mode in intervals:
            amount = max(0, min(right, end) - max(left, start))
            if amount:
                ref = json.loads(task) if task else None
                key = (ref["source"], ref["id"]) if ref else None
                if key not in totals:
                    totals[key] = {"task": ref, "total_ms": 0, "calendar_ms": 0}
                totals[key]["total_ms"] += amount
                if mode == CALENDAR_MODE:
                    totals[key]["calendar_ms"] += amount
        # calendar_ms only where part of the row came from the calendar.
        rows = sorted(({k: v for k, v in item.items() if k != "calendar_ms" or v} for item in totals.values()),
                      key=lambda item: -item["total_ms"])
        return {"day": day, "timezone": timezone,
                "total_ms": sum(item["total_ms"] for item in rows), "rows": rows}

    def sync_calendar(self, days, events):
        """Record the past part of timed calendar events for whole local ``days``.

        ``days`` must be fully covered by ``events`` (dicts: key, title,
        start_ms, end_ms), or their earlier rows would be lost. Tracked timer
        time wins, overlapping events count once (earlier start first) and
        events marked "did not happen" never count. Idempotent per day.
        """
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            wall, mono = self._now()
            row = self._record(db)
            skips = {item["task_id"] for item in db.execute("SELECT task_id FROM calendar_skips")}
            written = 0
            for day in days:
                start, end = _day_window(day)
                db.execute("DELETE FROM intervals WHERE mode=? AND start_ms>=? AND start_ms<?",
                           (CALENDAR_MODE, start, end))
                busy = [tuple(item) for item in db.execute(
                    "SELECT start_ms,end_ms FROM intervals WHERE start_ms<? AND end_ms>?", (end, start))]
                if row["status"] == "running" and row["mode"] != "break" and row["anchor_at"] is not None:
                    busy.append((row["anchor_at"], max(wall, row["anchor_at"])))
                for event in sorted(events, key=lambda item: (item["start_ms"], item["key"])):
                    if event["key"] in skips:
                        continue
                    segment = (max(event["start_ms"], start), min(event["end_ms"], end, wall))
                    if segment[1] - segment[0] < 1000:
                        continue
                    task = _json({"source": "calendar", "id": event["key"], "title": event["title"]})
                    for left, right in _free(segment, busy):
                        if right - left >= 1000:
                            db.execute("INSERT INTO intervals(start_ms,end_ms,task,mode) VALUES(?,?,?,?)",
                                       (left, right, task, CALENDAR_MODE))
                            written += right - left
                        busy.append((left, right))
            return {"days": [day.isoformat() for day in days], "calendar_ms": written}

    def set_calendar_skip(self, task_id, skipped):
        """Mark a calendar occurrence as "did not happen" (or undo it)."""
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 2048:
            raise ValidationError("Некорректный ID события")
        if not isinstance(skipped, bool):
            raise ValidationError("skipped must be true or false")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if skipped:
                wall, _ = self._now()
                db.execute("INSERT OR REPLACE INTO calendar_skips VALUES(?,?)", (task_id, wall))
                for item in db.execute("SELECT id,task FROM intervals WHERE mode=?", (CALENDAR_MODE,)).fetchall():
                    if json.loads(item["task"])["id"] == task_id:
                        db.execute("DELETE FROM intervals WHERE id=?", (item["id"],))
            else:
                db.execute("DELETE FROM calendar_skips WHERE task_id=?", (task_id,))
            return {"id": task_id, "skipped": skipped}

    def calendar_skips(self):
        with self._connect() as db:
            return sorted(item["task_id"] for item in db.execute("SELECT task_id FROM calendar_skips"))

    def totals(self):
        """One snapshot of lifetime work by stable source/id, including live tail once.

        Checkpointed time already lives in intervals. Only anchor_at..now is
        appended here, never the session's elapsed_ms (which overlaps history).
        """
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # Sample after acquiring the write lock: another client may have
            # advanced the anchor while this transaction was waiting.
            wall, mono = self._now()
            row = self._record(db)
            self._settle(db, row, wall, mono)
            intervals = db.execute(
                "SELECT task,SUM(end_ms-start_ms) AS amount FROM intervals "
                "WHERE mode != 'break' GROUP BY task ORDER BY MAX(id)").fetchall()
            totals = {}
            for item in intervals:
                ref = json.loads(item["task"]) if item["task"] else None
                key = (ref["source"], ref["id"]) if ref else None
                entry = totals.setdefault(key, {"task": ref, "total_ms": 0})
                entry["task"] = ref  # Most recently recorded name for this id.
                entry["total_ms"] += item["amount"]
            if row["status"] == "running" and row["mode"] != "break":
                ref = json.loads(row["task"]) if row["task"] else None
                key = (ref["source"], ref["id"]) if ref else None
                entry = totals.setdefault(key, {"task": ref, "total_ms": 0})
                entry["task"] = ref
                entry["total_ms"] += self._tail(row, wall, mono)
            return {"rows": sorted(totals.values(), key=lambda item: -item["total_ms"]),
                    "server_now": wall, "state": self._state(row, wall, mono)}

    def checkpoint(self):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # Sample after acquiring the write lock: another client may have
            # advanced the anchor while this transaction was waiting.
            wall, mono = self._now()
            row = self._record(db)
            self._settle(db, row, wall, mono)
            return self._state(row, wall, mono)

    def _power_event(self, kind):
        """Handle a native power event; also called with fake clocks in tests."""
        if kind not in {PBT_APMSUSPEND, PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND}:
            return
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
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
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # Sample after acquiring the write lock: another client may have
            # advanced the anchor while this transaction was waiting.
            wall, mono = self._now()
            row = self._record(db)
            if row["status"] == "running":
                self._settle(db, row, wall, mono, force=True)
                if row["status"] == "running":
                    row.update(status="paused", started_at=None, anchor_at=None,
                               anchor_mono=None, interruption="service_stop")
                    row["revision"] += 1
                    self._save(db, row)
