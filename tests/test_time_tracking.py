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
    with pytest.raises(ConflictError):
        b.command({**body, "task": None})
    with pytest.raises(ConflictError):
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
    with pytest.raises(ValidationError, match="Windows time"):
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
