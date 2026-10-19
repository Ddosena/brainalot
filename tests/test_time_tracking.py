"""Behavioral tests for the durable time log, without a live service."""
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from megamozg.core import ConflictError, ValidationError
from megamozg.time_tracking import TimeStore


TASK = {"source": "library", "id": "stable-1", "title": "Original title"}


class FakeClock:
    def __init__(self, wall=1_800_000_000_000):
        self.wall = wall
        self.mono = 100_000

    def advance(self, ms):
        self.wall += ms
        self.mono += ms


def store(tmp_path, clock):
    return TimeStore(tmp_path, lambda: clock.wall, lambda: clock.mono)


def cmd(timer, action, revision, rid, **fields):
    return timer.command({"request_id": rid, "expected_revision": revision,
                          "action": action, **fields})


def day(clock):
    return datetime.fromtimestamp(clock.wall / 1000, timezone.utc).date().isoformat()


def advance_active(timer, clock, milliseconds):
    assert milliseconds % 10_000 == 0
    for _ in range(milliseconds // 10_000):
        clock.advance(10_000)
        timer.checkpoint()


def test_stopwatch_plus_pomodoro_one_work_log(tmp_path):
    c = FakeClock()
    t = store(tmp_path, c)
    s = cmd(t, "start", 0, "start", task=TASK)
    advance_active(t, c, 10 * 60_000)
    s = cmd(t, "set_mode", s["revision"], "pom", mode="pomodoro", duration_ms=15 * 60_000)
    advance_active(t, c, 15 * 60_000)
    assert t.state()["status"] == "finished"
    report = t.summary(day(c), "UTC")
    assert report["total_ms"] == 25 * 60_000
    assert report["rows"] == [{"task": TASK, "total_ms": 25 * 60_000}]


def test_two_clients_cas_replay_and_changed_id(tmp_path):
    c = FakeClock()
    a, b = store(tmp_path, c), store(tmp_path, c)
    body = {"request_id": "same", "expected_revision": 0, "action": "start", "task": TASK}
    first = a.command(body)
    assert b.command(body) == first
    with pytest.raises(ConflictError, match="другой команды таймера"):
        b.command({**body, "task": None})
    # The 409 detail is shown in the panel as is (translated for English).
    with pytest.raises(ConflictError, match="Таймер изменился в другом окне"):
        cmd(b, "start", 0, "another", task=TASK)
    c.advance(9_000)
    assert b.state()["elapsed_ms"] == 9_000
    assert b.summary(day(c), "UTC")["total_ms"] == 9_000
    s = cmd(b, "pause", first["revision"], "pause")
    assert s["elapsed_ms"] == 9_000
    assert a.summary(day(c), "UTC")["total_ms"] == 9_000


def test_simultaneous_clients_only_one_start(tmp_path):
    c = FakeClock()
    a, b = store(tmp_path, c), store(tmp_path, c)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(cmd, timer, "start", 0, rid, task=TASK)
                   for timer, rid in ((a, "a"), (b, "b"))]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result())
            except ConflictError:
                outcomes.append("conflict")
    assert len([result for result in outcomes if result != "conflict"]) == 1
    c.advance(10_000)
    a.checkpoint()
    assert b.summary(day(c), "UTC")["total_ms"] == 10_000


def test_switch_pause_break_and_snapshot_identity(tmp_path):
    c = FakeClock()
    t = store(tmp_path, c)
    s = cmd(t, "start", 0, "a", task=TASK)
    c.advance(8_000)
    renamed = {**TASK, "title": "Renamed"}
    s = cmd(t, "switch_task", s["revision"], "b", task=renamed)
    assert s["elapsed_ms"] == 8_000
    c.advance(2_000)
    s = cmd(t, "pause", s["revision"], "c")
    c.advance(10_000)
    s = cmd(t, "resume", s["revision"], "d")
    c.advance(3_000)
    s = cmd(t, "set_mode", s["revision"], "e", mode="break")
    assert s["elapsed_ms"] == 0
    c.advance(5_000)
    s = cmd(t, "stop", s["revision"], "f")
    assert s["status"] == "idle"
    assert s["elapsed_ms"] == 5_000
    # One identity, preserving the first title snapshot in the aggregate.
    assert t.summary(day(c), "UTC")["rows"] == [{"task": TASK, "total_ms": 13_000}]


def test_midnight_and_dst_boundaries(tmp_path):
    wall = int(datetime(2026, 10, 24, 23, 59, 30, tzinfo=timezone.utc).timestamp() * 1000)
    c = FakeClock(wall)
    t = store(tmp_path, c)
    s = cmd(t, "start", 0, "a")
    advance_active(t, c, 60_000)
    s = cmd(t, "stop", s["revision"], "b")
    assert t.summary("2026-10-24", "UTC")["total_ms"] == 30_000
    assert t.summary("2026-10-25", "UTC")["total_ms"] == 30_000
    assert t.summary("2026-10-25", "Europe/Berlin")["total_ms"] == 60_000
    # Fall-back day is 25 hours: both occurrences of 02:30 are the same day.
    c.wall = int(datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc).timestamp() * 1000)
    c.mono += 1_000_000
    s = cmd(t, "start", s["revision"], "c")
    advance_active(t, c, 2 * 60 * 60_000)
    cmd(t, "stop", s["revision"], "d")
    assert t.summary("2026-10-25", "Europe/Berlin")["total_ms"] == 2 * 60 * 60_000 + 60_000


def test_gap_clock_jump_and_restart_keep_checkpoint(tmp_path):
    c = FakeClock()
    t = store(tmp_path, c)
    s = cmd(t, "start", 0, "a", task=TASK)
    c.advance(10_000)
    t.checkpoint()
    c.advance(40_000)
    assert t.state()["status"] == "paused"
    assert t.state()["interruption"] == "clock_gap"
    assert t.summary(day(c), "UTC")["total_ms"] == 10_000
    s = cmd(t, "resume", t.state()["revision"], "b")
    c.advance(10_000)
    t.checkpoint()
    c.wall += 6_000
    assert t.state()["status"] == "paused"
    assert t.summary(day(c), "UTC")["total_ms"] == 20_000
    s = cmd(t, "resume", t.state()["revision"], "c")
    c.advance(10_000)
    t.checkpoint()
    # A second instance opening the database does not imply a service restart.
    assert store(tmp_path, c).state()["status"] == "running"
    restarted = store(tmp_path, c)
    restarted.start_service()
    try:
        assert restarted.state()["status"] == "paused"
        assert restarted.state()["interruption"] == "service_restart"
        assert restarted.summary(day(c), "UTC")["total_ms"] == 30_000
    finally:
        restarted.stop_service()


def test_cli_constructor_and_early_checkpoint_do_not_change_running_state(tmp_path):
    c = FakeClock()
    service = store(tmp_path, c)
    cmd(service, "start", 0, "a", task=TASK)
    c.advance(2_000)
    other = store(tmp_path, c)  # ordinary CLI request, not service startup
    assert other.state()["status"] == "running"
    assert other.checkpoint()["elapsed_ms"] == 2_000
    with sqlite3.connect(service.db_path) as db:
        assert db.execute("SELECT count(*) FROM intervals").fetchone()[0] == 0
        assert db.execute("SELECT anchor_at FROM timer").fetchone()[0] == c.wall - 2_000
    c.advance(8_000)
    assert service.checkpoint()["elapsed_ms"] == 10_000
    with sqlite3.connect(service.db_path) as db:
        assert db.execute("SELECT count(*) FROM intervals").fetchone()[0] == 1


def test_short_suspend_callback_pauses_before_sleep(tmp_path):
    c = FakeClock()
    t = store(tmp_path, c)
    cmd(t, "start", 0, "a", task=TASK)
    c.advance(4_000)
    t._power_event(4)  # PBT_APMSUSPEND, injected instead of sleeping Windows
    assert t.state()["status"] == "paused"
    assert t.state()["elapsed_ms"] == 4_000
    c.wall += 4_000
    c.mono += 1_000  # only one second of awake time during a short sleep
    t._power_event(18)  # PBT_APMRESUMEAUTOMATIC
    assert t.state()["status"] == "paused"
    assert t.summary(day(c), "UTC")["total_ms"] == 4_000


def test_resume_without_suspend_discards_even_short_uncertain_tail(tmp_path):
    c = FakeClock()
    t = store(tmp_path, c)
    cmd(t, "start", 0, "a", task=TASK)
    c.advance(10_000)
    t.checkpoint()
    c.wall += 4_000
    c.mono += 1_000  # 3s discrepancy is below the ordinary 5s tolerance
    t._power_event(18)
    assert t.state()["status"] == "paused"
    assert t.state()["interruption"] == "system_resume"
    assert t.summary(day(c), "UTC")["total_ms"] == 10_000


def test_windows_clock_and_callback_both_unavailable_block_start(tmp_path, monkeypatch):
    t = TimeStore(tmp_path)
    t._native_clock = True

    def unavailable():
        raise OSError("unbiased clock unavailable")

    monkeypatch.setattr("megamozg.time_tracking._windows_awake_ms", unavailable)
    t.monotonic = t._awake_ms
    with pytest.raises(ValidationError, match="уведомления о сне"):
        cmd(t, "start", 0, "a")
    assert t.state()["status"] == "idle"


def test_validation(tmp_path):
    c = FakeClock()
    t = store(tmp_path, c)
    with pytest.raises(ValidationError):
        cmd(t, "start", 0, "x", task={"source": "library", "id": "1"})
    with pytest.raises(ValidationError):
        cmd(t, "start", 0, "y", mode="pomodoro", duration_ms=1)
    for field, value in (("action", []), ("mode", {}), ("task", {"source": [], "id": "1", "title": "A"})):
        body = {"request_id": "bad", "expected_revision": 0, "action": "start", field: value}
        with pytest.raises(ValidationError):
            t.command(body)
    with pytest.raises(ValidationError):
        cmd(t, "start", 0, "long", task={"source": "calendar", "id": "x" * 2049, "title": "A"})


def test_reset_pomodoro_keeps_work_history(tmp_path):
    c = FakeClock()
    t = store(tmp_path, c)
    s = cmd(t, "start", 0, "a", mode="pomodoro", task=TASK)
    c.advance(20_000)
    s = cmd(t, "reset_pomodoro", s["revision"], "b")
    assert s["status"] == "paused"
    assert s["elapsed_ms"] == 0
    assert s["duration_ms"] == 1_500_000
    assert t.summary(day(c), "UTC")["total_ms"] == 20_000

def test_lifetime_totals_midnight_rename_live_tail_and_break(tmp_path):
    c = FakeClock(int(datetime(2026, 10, 18, 23, 59, 50, tzinfo=timezone.utc).timestamp() * 1000))
    t = store(tmp_path, c)
    s = cmd(t, "start", 0, "life-start", task=TASK)
    advance_active(t, c, 20_000)
    c.advance(5_000)
    snapshot = t.totals()
    assert snapshot["rows"] == [{"task": TASK, "total_ms": 25_000}]
    assert t.totals() == snapshot
    assert t.summary("2026-10-18", "UTC")["total_ms"] == 10_000
    assert t.summary("2026-10-19", "UTC")["total_ms"] == 15_000
    renamed = {**TASK, "title": "Renamed"}
    s = cmd(t, "start", s["revision"], "life-rename", task=renamed, mode="pomodoro", duration_ms=60_000)
    advance_active(t, c, 60_000)
    s = t.state()
    s = cmd(t, "start", s["revision"], "life-break", task=renamed, mode="break", duration_ms=60_000)
    advance_active(t, c, 60_000)
    assert t.totals()["rows"] == [{"task": renamed, "total_ms": 85_000}]
    s = t.state()
    c.advance(86_400_000)
    s = cmd(t, "start", s["revision"], "life-next-day", task=renamed)
    c.advance(6_000)
    stopped = cmd(t, "stop", s["revision"], "life-stop")
    assert t.totals()["rows"] == [{"task": renamed, "total_ms": 91_000}]
    assert t.summary("2026-10-20", "UTC")["total_ms"] == 6_000
    assert stopped["status"] == "idle"


def test_totals_competing_requests_replay_and_source_identity(tmp_path):
    c = FakeClock()
    a, b = store(tmp_path, c), store(tmp_path, c)
    body = {"request_id": "life-concurrent", "expected_revision": 0, "action": "start", "task": TASK}
    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(lambda timer: timer.command(body), [a, b]))
    assert answers[0] == answers[1]
    c.advance(7_000)
    with ThreadPoolExecutor(max_workers=2) as pool:
        snapshots = list(pool.map(lambda timer: timer.totals(), [a, b]))
    assert snapshots[0]["rows"] == snapshots[1]["rows"] == [{"task": TASK, "total_ms": 7_000}]
    other = {**TASK, "source": "calendar"}
    s = cmd(a, "start", answers[0]["revision"], "life-other", task=other)
    c.advance(3_000)
    cmd(b, "stop", s["revision"], "life-other-stop")
    assert {row["task"]["source"]: row["total_ms"] for row in a.totals()["rows"]} == {"library": 7_000, "calendar": 3_000}


def test_time_save_failure_rolls_back_interval_and_receipt(tmp_path, monkeypatch):
    c = FakeClock()
    t = store(tmp_path, c)
    s = cmd(t, "start", 0, "life-rollback-start", task=TASK)
    c.advance(8_000)
    save = t._save
    def fail(*_args):
        raise sqlite3.OperationalError("simulated disk full")
    monkeypatch.setattr(t, "_save", fail)
    with pytest.raises(sqlite3.OperationalError):
        cmd(t, "stop", s["revision"], "life-rollback-stop")
    monkeypatch.setattr(t, "_save", save)
    result = cmd(t, "stop", s["revision"], "life-rollback-stop")
    assert result["status"] == "idle"
    assert t.totals()["rows"] == [{"task": TASK, "total_ms": 8_000}]
    assert cmd(t, "stop", s["revision"], "life-rollback-stop") == result
    assert t.totals()["rows"][0]["total_ms"] == 8_000

@pytest.mark.parametrize("operation, expected_status, interruption", [
    ("state", "running", None), ("totals", "running", None),
    ("summary", "running", None), ("checkpoint", "running", None),
    ("command", "idle", None), ("stop_service", "paused", "service_stop"),
    ("power_suspend", "paused", "system_suspend"),
])
def test_clock_is_sampled_after_transaction_wait(tmp_path, operation, expected_status, interruption):
    """A blocked reader must use a clock newer than the writer's new anchor."""
    from contextlib import contextmanager
    from threading import Event

    c = FakeClock()
    a, b = store(tmp_path, c), store(tmp_path, c)
    waiting, release = Event(), Event()
    original = a._connect

    @contextmanager
    def delayed_connect():
        waiting.set()
        assert release.wait(5), "writer did not release transaction gate"
        with original() as db:
            yield db

    a._connect = delayed_connect
    def invoke():
        if operation == "summary":
            return a.summary(day(c), "UTC")
        if operation == "command":
            return cmd(a, "stop", 1, "waiting-stop")
        if operation == "power_suspend":
            return a._power_event(4)
        return getattr(a, operation)()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(invoke)
        assert waiting.wait(5)
        try:
            c.advance(1_000)
            started = cmd(b, "start", 0, "concurrent-start", task=TASK)
            c.advance(2_000)
        finally:
            release.set()
        future.result(timeout=5)
    state = b.state()
    assert state["status"] == expected_status
    assert state["interruption"] == interruption
    assert state["elapsed_ms"] == 2_000
    if expected_status == "running":
        assert state["revision"] == started["revision"]
    assert b.totals()["rows"][0]["total_ms"] == 2_000


def _event(key, start, end, title=None):
    return {"key": key, "title": title or key, "start_ms": start, "end_ms": end}


def _local_days(*stamps):
    return sorted({datetime.fromtimestamp(ms / 1000).date() for ms in stamps})


def _row(report, source, key):
    return next((row for row in report["rows"] if row["task"] and row["task"]["source"] == source
                 and row["task"]["id"] == key), None)


def test_calendar_events_count_past_part_once_and_timer_wins(tmp_path):
    c = FakeClock()
    base, hour, minute = c.wall, 3_600_000, 60_000
    t = store(tmp_path, c)
    events = [_event("cal:a", base + 2 * hour, base + 3 * hour),
              _event("cal:b", base + 2 * hour + 30 * minute, base + 3 * hour + 30 * minute),
              _event("cal:c", base + 5 * hour, base + 6 * hour)]
    c.advance(2 * hour + 15 * minute)
    s = cmd(t, "start", 0, "work", task=TASK)
    advance_active(t, c, 5 * minute)
    cmd(t, "stop", s["revision"], "stop")
    c.advance(4 * hour - (2 * hour + 20 * minute))  # now: base + 4h
    days = _local_days(*(e["start_ms"] for e in events))
    t.sync_calendar(days, events)
    t.sync_calendar(days, events)  # idempotent: rows of a day are recomputed, never added twice
    totals = {(r["task"]["source"], r["task"]["id"]): r["total_ms"] for r in t.totals()["rows"] if r["task"]}
    assert totals[("calendar", "cal:a")] == 55 * minute  # 10:00-11:00 minus the tracked 10:15-10:20
    assert totals[("calendar", "cal:b")] == 30 * minute  # overlap with A counted once
    assert ("calendar", "cal:c") not in totals  # future part never counts
    assert totals[("library", "stable-1")] == 5 * minute
    report = t.summary(day(c), "UTC")
    assert report["total_ms"] == 90 * minute
    assert _row(report, "calendar", "cal:a")["calendar_ms"] == 55 * minute
    assert "calendar_ms" not in _row(report, "library", "stable-1")


def test_calendar_event_marked_did_not_happen_is_removed_until_restored(tmp_path):
    c = FakeClock()
    base, hour = c.wall, 3_600_000
    t = store(tmp_path, c)
    events = [_event("cal:a", base + hour, base + 2 * hour)]
    c.advance(3 * hour)
    days = _local_days(base + hour)
    t.sync_calendar(days, events)
    assert t.set_calendar_skip("cal:a", True) == {"id": "cal:a", "skipped": True}
    t.sync_calendar(days, events)
    assert all(r["task"]["id"] != "cal:a" for r in t.totals()["rows"] if r["task"])
    assert t.calendar_skips() == ["cal:a"]
    t.set_calendar_skip("cal:a", False)
    t.sync_calendar(days, events)
    assert any(r["task"]["id"] == "cal:a" and r["total_ms"] == hour for r in t.totals()["rows"] if r["task"])
    with pytest.raises(ValidationError):
        t.set_calendar_skip("cal:a", "yes")


def test_calendar_time_yields_to_a_timer_started_during_the_event(tmp_path):
    c = FakeClock()
    base, minute = c.wall, 60_000
    t = store(tmp_path, c)
    events = [_event("cal:a", base, base + 60 * minute)]
    days = _local_days(base)
    c.advance(10 * minute)
    s = cmd(t, "start", 0, "work", task=TASK)
    advance_active(t, c, 20 * minute)  # still running at base + 30 min
    t.sync_calendar(days, events)
    rows = {r["task"]["id"]: r["total_ms"] for r in t.totals()["rows"] if r["task"]}
    assert rows["cal:a"] == 10 * minute and rows["stable-1"] == 20 * minute


def test_backfill_assigns_untracked_stretch_without_overlap_or_replay(tmp_path):
    c = FakeClock()
    base, minute = c.wall, 60_000
    t = store(tmp_path, c)
    s = cmd(t, "start", 0, "first", task=TASK)
    advance_active(t, c, 5 * minute)
    s = cmd(t, "stop", s["revision"], "stop")
    c.advance(25 * minute)  # untracked: base + 5 .. base + 30 min
    other = {"source": "library", "id": "other", "title": "Other"}
    body = {"request_id": "answer", "expected_revision": s["revision"], "action": "start",
            "mode": "stopwatch", "task": other, "backfill_from": base}  # asks too early on purpose
    started = t.command(body)
    assert started["status"] == "running" and started["task"] == other
    assert t.command(body) == started  # the same answer delivered twice
    rows = {r["task"]["id"]: r["total_ms"] for r in t.totals()["rows"] if r["task"]}
    assert rows == {"stable-1": 5 * minute, "other": 25 * minute}  # clipped to the end of tracked work
    with pytest.raises(ValidationError, match="уже учитывается"):
        cmd(t, "start", started["revision"], "again", task=other, backfill_from=c.wall - minute)
    with pytest.raises(ValidationError):
        cmd(t, "pause", started["revision"], "bad", backfill_from=c.wall)


def test_backfill_is_limited_to_twelve_hours_and_rest_is_a_label(tmp_path):
    c = FakeClock()
    hour = 3_600_000
    t = store(tmp_path, c)
    c.advance(20 * hour)
    rest = {"source": "label", "id": "rest", "title": "Отдых"}
    cmd(t, "start", 0, "rest", task=rest, backfill_from=c.wall - 20 * hour)
    rows = {r["task"]["id"]: r["total_ms"] for r in t.totals()["rows"] if r["task"]}
    assert rows["rest"] == 12 * hour
    with pytest.raises(ValidationError):
        cmd(t, "switch_task", 1, "bad-label", task={"source": "label", "id": "nap", "title": "Сон"})
