"""Synthetic Work planning contract tests; never load the user's course data."""
import base64
import hashlib
import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from megamozg import features
from megamozg.api import create_app
from megamozg.core import ConflictError, ValidationError, Store
from megamozg.work_store import WorkStore
from megamozg.work_planning import _remaining


def seed(root):
    work = WorkStore(root)
    state = {
        "version": 1,
        "projects": [{"id": "primary", "name": "A"}, {"id": "secondary", "name": "B"}],
        "items": [{"id": "legacy", "project": "primary", "title": "Existing task"}],
        "boards": [{"id": "general", "items": [{"id": "note", "type": "note", "text": "keep"}]}],
        "unknown_future_field": {"keep": True},
    }
    raw = json.dumps(state).encode()
    digest = hashlib.sha256(raw).hexdigest()
    work.upload_chunk(digest, {"index": 0, "total": 1, "content_base64": base64.b64encode(raw).decode()})
    work.commit({"request_id": "initial", "expected_revision": 0, "state_blob": digest, "images": []})
    return work


def planning():
    return {
        "version": 1,
        "settings": {
            "start": "2027-01-04", "end": "2027-01-17", "timezone": "Europe/Moscow",
            "day_limit_minutes": 300, "work_start": "11:00", "work_end": "20:00",
            "prep_minutes": 25, "break_minutes": 15, "workdays": 6,
            "priority_course": "alpha", "priority_until": "2027-01-11",
            "secondary_early_prep_minutes_per_week": 90,
        },
        "tasks": [
            {"id": "a", "course": "alpha", "project_id": "primary", "title": "Material A",
             "kind": "video", "due": "2027-01-12", "due_status": "confirmed",
             "estimate_minutes": {"lower": 720, "central": 900, "upper": 1080},
             "review_minutes": 120, "resources": []},
            {"id": "b", "course": "beta", "project_id": "secondary", "title": "Small preparation",
             "kind": "other", "due": "2027-01-17", "due_status": "confirmed", "early_prep": True,
             "estimate_minutes": {"lower": 120, "central": 120, "upper": 120},
             "review_minutes": 0, "resources": []},
            {"id": "c", "course": "beta", "project_id": "secondary", "title": "Main B",
             "kind": "video", "due": "2027-01-17", "due_status": "confirmed",
             "estimate_minutes": {"lower": 300, "central": 360, "upper": 420},
             "review_minutes": 60, "resources": []},
        ],
        "calendar": {
            "events": [{"id": "lesson", "title": "Lesson", "start": "2027-01-04T11:00:00+03:00",
                        "end": "2027-01-04T13:00:00+03:00", "category": "lesson"},
                       {"id": "homework", "title": "Homework", "start": "2027-01-05",
                        "end": "2027-01-06", "all_day": True, "category": "homework"},
                       {"id": "ignored", "title": "Marker", "start": "2027-01-05",
                        "end": "2027-01-06", "all_day": True, "category": "ignore"}],
            "fetched_at": "2027-01-03T12:00:00+03:00", "start": "2027-01-04",
            "end": "2027-01-17", "stale": False, "configured": True,
        },
        "completed_cubes": {}, "day_outcomes": [], "locks": [], "result": {}, "history": [],
    }


def op(work, request_id, action, data):
    return work.planning_operation({"request_id": request_id, "expected_revision": work.read()["revision"],
                                    "action": action, "data": data})


def upload_state(work, state):
    raw = json.dumps(state).encode()
    digest = hashlib.sha256(raw).hexdigest()
    work.upload_chunk(digest, {"index": 0, "total": 1, "content_base64": base64.b64encode(raw).decode()})
    return digest


def test_import_preserves_work_and_plans_five_hour_total(tmp_path):
    work = seed(tmp_path)
    command = {"request_id": "import-one", "expected_revision": 1, "action": "import",
               "data": {"planning": planning()}}
    snapshot = work.planning_operation(command)
    assert work.planning_operation(command) == snapshot
    assert snapshot["revision"] == 2
    state = snapshot["state"]
    assert state["unknown_future_field"] == {"keep": True}
    assert state["boards"][0]["items"][0]["text"] == "keep"
    assert state["items"][0]["id"] == "legacy"
    result = state["planning"]["result"]
    assert all(d["total_work_minutes"] <= 300 for d in result["days"])
    lesson_day = next(d for d in result["days"] if d["date"] == "2027-01-04")
    assert lesson_day["fixed_minutes"] == 145
    assert lesson_day["lesson_count"] == 1
    homework_day = next(d for d in result["days"] if d["date"] == "2027-01-05")
    assert homework_day["all_day_assumed_minutes"] == 30
    assert all(c["task_id"] != "c" for c in result["cubes"] if c["date"] < "2027-01-11")
    early = [c for c in result["cubes"] if c["task_id"] == "b" and c["date"] < "2027-01-11"]
    assert len(early) == 1 and early[0]["minutes"] == 60
    mixed = next(d for d in result["days"] if d["date"] == early[0]["date"])
    assert mixed["focus_task_id"] == "a" and mixed["secondary_early_prep_exception"] is True
    assert any(c["task_id"] == "a" and c["date"] == early[0]["date"] for c in result["cubes"])
    with pytest.raises(ConflictError):
        work.planning_operation({"request_id": "different", "expected_revision": 1,
                                 "action": "recalculate", "data": {}})


def test_progress_replaces_credit_and_day_outcome_proposes_recovery(tmp_path):
    work = seed(tmp_path)
    first = op(work, "import", "import", {"planning": planning()})
    cube = first["state"]["planning"]["result"]["cubes"][0]
    data = {"cube_id": cube["id"], "percent": 50, "actual_minutes": 37}
    command = {"request_id": "half", "expected_revision": first["revision"],
               "action": "cube_progress", "data": data}
    half = work.planning_operation(command)
    assert work.planning_operation(command) == half
    assert half["state"]["planning"]["completed_cubes"][cube["id"]]["actual_minutes"] == 37
    three_quarters = op(work, "more", "cube_progress", {"cube_id": cube["id"], "percent": 75})
    assert three_quarters["state"]["planning"]["completed_cubes"][cube["id"]]["percent"] == 75
    assert three_quarters["state"]["planning"]["completed_cubes"][cube["id"]]["actual_minutes"] == 37
    outcome = op(work, "outcome", "day_outcome",
                 {"task_id": cube["task_id"], "date": cube["date"], "remaining_minutes": 45,
                  "done": False, "note": "partial"})
    recovery = outcome["state"]["planning"]["result"]["recovery_proposals"]
    assert recovery["task_id"] == cube["task_id"]
    assert recovery["status"] == "proposal_only_replan_explicit"
    replanned = op(work, "replan", "recalculate", {"from_date": "2027-01-06"})
    assert replanned["state"]["planning"]["day_outcomes"][0]["remaining_minutes"] == 45
    assert replanned["state"]["planning"]["result"]["cubes"][0]["id"] == cube["id"]
    corrected = planning()
    corrected["tasks"][0]["estimate_minutes"] = {"lower": 800, "central": 840, "upper": 900}
    merged = op(work, "corrected-import", "import", {"planning": corrected})
    restored = merged["state"]["planning"]
    assert restored["completed_cubes"][cube["id"]]["percent"] == 75
    assert restored["completed_cubes"][cube["id"]]["actual_minutes"] == 37
    assert restored["day_outcomes"][0]["remaining_minutes"] == 45
    assert len(restored["result"]["days"]) == 14
    assert next(day for day in restored["result"]["days"] if day["date"] == cube["date"])["reported_actual_minutes"] == 37


def test_invalid_lock_rejected_and_explicit_lesson_day_override_allowed(tmp_path):
    work = seed(tmp_path)
    snapshot = op(work, "import", "import", {"planning": planning()})
    cube = snapshot["state"]["planning"]["result"]["cubes"][0]
    before = work.read()
    with pytest.raises(ValidationError):
        op(work, "lock-bad", "place_cube",
           {"cube_id": cube["id"], "date": "2027-01-04", "start": "11:00", "locked": True})
    assert work.read() == before
    lesson_override = op(work, "override-lesson", "capacity_override",
                         {"date": "2027-01-04", "minutes": 420, "reason": "explicit", "approved": True})
    lesson_day = next(d for d in lesson_override["state"]["planning"]["result"]["days"]
                      if d["date"] == "2027-01-04")
    assert lesson_day["day_limit_minutes"] == 420 and lesson_day["raised_load"] is True
    approved = op(work, "override-free", "capacity_override",
                  {"date": "2027-01-06", "minutes": 420, "reason": "explicit", "approved": True})
    day = next(d for d in approved["state"]["planning"]["result"]["days"] if d["date"] == "2027-01-06")
    assert day["emergency_approved"] is True and day["day_limit_minutes"] == 420


def test_secondary_lock_can_share_primary_day_and_homework_is_daily_reserve(tmp_path):
    work = seed(tmp_path)
    document = planning()
    document["calendar"]["events"].append({
        "id": "second-homework", "title": "Homework", "start": "2027-01-05",
        "end": "2027-01-06", "all_day": True, "category": "homework"})
    snapshot = op(work, "import", "import", {"planning": document})
    result = snapshot["state"]["planning"]["result"]
    homework = next(d for d in result["days"] if d["date"] == "2027-01-05")
    assert homework["all_day_assumed_minutes"] == 30
    early = next(c for c in result["cubes"] if c["task_id"] == "b" and c["date"] < "2027-01-11")
    locked = op(work, "lock-early", "place_cube",
                {"cube_id": early["id"], "date": early["date"], "start": early["start"], "locked": True})
    day = next(d for d in locked["state"]["planning"]["result"]["days"] if d["date"] == early["date"])
    assert day["focus_task_id"] == "a" and day["secondary_early_prep_exception"]
    assert any(c["id"] == early["id"] and c["locked"] for c in locked["state"]["planning"]["result"]["cubes"])


def test_planning_endpoint_returns_snapshot_and_validates_full_state(tmp_path, monkeypatch):
    monkeypatch.setattr(features, "WORK_ENABLED", True)
    work = seed(tmp_path)
    token = Store(tmp_path).token()
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765") as client:
        client.headers.update({"Authorization": "Bearer " + token})
        response = client.post("/api/work/planning", json={
            "request_id": "api-import", "expected_revision": 1, "action": "import",
            "data": {"planning": planning()}})
        assert response.status_code == 200
        assert response.json()["revision"] == 2
        stale = client.post("/api/work/planning", json={
            "request_id": "api-stale", "expected_revision": 1, "action": "recalculate", "data": {}})
        assert stale.status_code == 409
    assert work.read()["state"]["planning"]["version"] == 1


def test_calendar_refresh_is_read_only_and_preserves_category(tmp_path, monkeypatch):
    from megamozg.calendar import CalendarFeed
    from megamozg.google_calendar_api import GoogleCalendarApi

    work = seed(tmp_path)
    snapshot = op(work, "import", "import", {"planning": planning()})
    original_prep = next(day for day in snapshot["state"]["planning"]["result"]["days"]
                         if day["date"] == "2027-01-04")["prep_cubes"][0]["id"]
    calls = []
    def read_events(_self, first, last, *, force=False, google_calendar=None):
        calls.append((first.isoformat(), last.isoformat(), force))
        return {"configured": True, "calendar_count": 1, "stale": False, "error": None,
                "events": [{"id": "lesson", "title": "Lesson", "start": "2027-01-04T11:00:00+03:00",
                            "end": "2027-01-04T13:00:00+03:00", "occurrence_id": "stable"}]}
    monkeypatch.setattr(CalendarFeed, "events", read_events)
    monkeypatch.setattr(GoogleCalendarApi, "status", lambda _self: {"authorized": False})
    refreshed = op(work, "refresh", "recalculate", {"refresh_calendar": True})
    assert calls == [("2027-01-04", "2027-01-17", True)]
    event = refreshed["state"]["planning"]["calendar"]["events"][0]
    # An event without an occurrence_id keeps its original id as the match key.
    assert event["category"] == "lesson"
    assert refreshed["state"]["planning"]["calendar"]["source"] == "Brainalot Calendar read-only"
    refreshed_prep = next(day for day in refreshed["state"]["planning"]["result"]["days"]
                          if day["date"] == "2027-01-04")["prep_cubes"][0]["id"]
    assert refreshed_prep == original_prep


def test_calendar_sync_adds_new_google_events_and_writes_only_on_change(tmp_path, monkeypatch):
    from megamozg.calendar import CalendarFeed
    from megamozg.google_calendar_api import GoogleCalendarApi

    work = seed(tmp_path)
    op(work, "import", "import", {"planning": planning()})
    feed = [{"id": "lesson", "title": "Lesson", "start": "2027-01-04T11:00:00+03:00",
             "end": "2027-01-04T13:00:00+03:00"},
            {"id": "homework", "title": "Homework", "start": "2027-01-05", "end": "2027-01-06", "all_day": True}]
    calls = []
    def read_events(_self, first, last, *, force=False, google_calendar=None):
        calls.append(force)
        return {"configured": True, "calendar_count": 1, "stale": False, "error": None, "events": deepcopy(feed)}
    monkeypatch.setattr(CalendarFeed, "events", read_events)
    monkeypatch.setattr(GoogleCalendarApi, "status", lambda _self: {"authorized": False})
    work.sync_calendar()
    revision = work.read()["revision"]
    assert work.sync_calendar() is None and work.read()["revision"] == revision, "unchanged feed writes nothing"
    # An event added in Google after the plan was built reaches Day and Week
    # without an agent running the CLI recalculation.
    feed.append({"id": "webinar", "title": "Webinar 2", "start": "2027-01-09T12:00:00+03:00",
                 "end": "2027-01-09T13:00:00+03:00"})
    synced = work.sync_calendar()
    assert synced["revision"] == revision + 1
    events = synced["state"]["planning"]["calendar"]["events"]
    assert [event["id"] for event in events if event["title"] == "Webinar 2"] == ["webinar"]
    assert next(event for event in events if event["id"] == "lesson")["category"] == "lesson"
    assert synced["state"]["planning"]["last_update"]["reason"] == "calendar-sync"
    assert work.sync_calendar() is None and work.read()["revision"] == revision + 1
    assert calls[0] is False and True in calls, "comparison reads the cache; the write refreshes the feed"
    feed.append({"id": "late", "title": "Late", "start": "2027-01-10T12:00:00+03:00",
                 "end": "2027-01-10T13:00:00+03:00"})
    monkeypatch.setattr(CalendarFeed, "events", lambda *_args, **_kwargs: {
        "configured": True, "calendar_count": 1, "stale": True, "error": "offline", "events": []})
    assert work.sync_calendar() is None and work.read()["revision"] == revision + 1, "a stale feed never shrinks the plan"


def test_week_done_and_reopen_use_native_day_outcome_through_the_day_coordinator(tmp_path):
    from megamozg.work_period import PeriodPlanner

    work = seed(tmp_path)
    # An active library task once made the very first plan import fail (KeyError 'planning').
    Store(tmp_path).capture("Library task", kind="task", title="Library task")
    period = PeriodPlanner(work)
    def planning_command(request_id, action, data):
        return period.day_command({"request_id": request_id, "expected_revision": work.read()["revision"],
                                   "action": action, "data": data})
    planning_command("import", "import", {"planning": planning()})
    task = next(row for row in period.model()["tasks"] if row["id"] == "a")
    assert task["status"] != "completed" and task["completed_on"] is None
    planning_command("done", "day_outcome", {"task_id": "a", "date": "2027-01-05", "done": True})
    task = next(row for row in period.model()["tasks"] if row["id"] == "a")
    assert (task["status"], task["completed_on"], task["effective_remaining_minutes"]) == ("completed", "2027-01-05", 0)
    planning_command("reopen", "day_outcome", {"task_id": "a", "date": task["completed_on"], "done": False})
    task = next(row for row in period.model()["tasks"] if row["id"] == "a")
    assert task["status"] != "completed" and task["completed_on"] is None and task["effective_remaining_minutes"] > 0


def test_live_override_is_three_hours_without_calendar_double_count(tmp_path):
    work = seed(tmp_path)
    document = planning()
    document["tasks"][0]["work_item_id"] = "created-item"
    document["calendar"]["events"].append({
        "id": "calendar-short", "title": "Webinar", "start": "2027-01-06T21:00:00+03:00",
        "end": "2027-01-06T22:00:00+03:00", "category": "lesson"})
    document["live_events"] = [
        {"id": "dated", "task_id": "a", "title": "Webinar", "duration_minutes": 180,
         "date": "2027-01-06", "start": "19:00", "calendar_event_id": "calendar-short"},
        {"id": "undated", "task_id": "c", "title": "Another webinar", "duration_minutes": 180,
         "date": None, "start": None},
    ]
    snapshot = op(work, "live-import", "import", {"planning": document})
    state = snapshot["state"]
    assert state["items"][0]["id"] == "legacy"
    assert any(x["id"] == "created-item" and x["planning_task_id"] == "a" for x in state["items"])
    result = state["planning"]["result"]
    assert result["unscheduled_live_minutes"] == 180 and result["unscheduled_live_count"] == 1
    day = next(d for d in result["days"] if d["date"] == "2027-01-06")
    assert day["calendar_minutes"] == 180 and day["lesson_prep_minutes"] == 25
    assert day["fixed_minutes"] == 205 and day["total_work_minutes"] <= 300
    assert any(r["code"] == "calendar_live_duration_mismatch" for r in result["risks"])
    assert any(r["code"] == "live_event_date_unknown" for r in result["risks"])


def test_later_progress_reduces_evening_remaining_override():
    document = planning()
    document["completed_cubes"] = {
        "morning": {"task_id": "a", "date": "2027-01-04", "planned_minutes": 90,
                    "percent": 50, "actual_minutes": 70},
        "next-day": {"task_id": "a", "date": "2027-01-05", "planned_minutes": 60,
                     "percent": 100, "actual_minutes": 85},
    }
    document["day_outcomes"] = [{"task_id": "a", "date": "2027-01-04",
                                  "remaining_minutes": 120, "done": False, "note": ""}]
    assert _remaining(document)["a"] == 60


def test_cli_status_uses_compact_local_snapshot(tmp_path, monkeypatch, capsys):
    from megamozg.cli import main

    monkeypatch.setattr(features, "WORK_ENABLED", True)
    work = seed(tmp_path)
    op(work, "import", "import", {"planning": planning()})
    assert main(["--root", str(tmp_path), "work", "plan", "status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["available"] and status["revision"] == 2
    assert status["progress"]["reported_cubes"] == 0
    assert len(status["tasks"]) == 3
    assert status["next_cubes"]


def test_weekly_review_reserves_one_hour_with_fallback_and_explicit_completion(tmp_path):
    work = seed(tmp_path)
    document = planning()
    document["settings"]["weekly_review"] = {"enabled": True, "minutes": 60, "weekday": 6}
    document["calendar"]["events"].append({
        "id": "sunday-busy", "title": "Private appointment",
        "start": "2027-01-10T11:00:00+03:00", "end": "2027-01-10T20:00:00+03:00",
        "category": "busy"})
    snapshot = op(work, "import-weekly", "import", {"planning": document})
    result = snapshot["state"]["planning"]["result"]
    assert len(result["weekly_reviews"]) == 2
    first = result["weekly_reviews"][0]
    assert first["week_end"] == "2027-01-10" and first["date"] < first["week_end"]
    assert len(first["checklist"]) == 5 and first["minutes"] == 60
    selected = next(d for d in result["days"] if d["date"] == first["date"])
    assert selected["weekly_review_minutes"] == 60
    assert selected["fixed_minutes"] + selected["weekly_review_minutes"] + selected["development_minutes"] == selected["total_work_minutes"] <= 300
    done = op(work, "done-weekly", "weekly_review_done", {"id": first["id"], "done": True})
    shown = done["state"]["planning"]["result"]["weekly_reviews"][0]
    assert shown["status"] == "done"
    assert done["state"]["planning"]["weekly_review_done"][first["id"]] is True
    refreshed = op(work, "replan-weekly", "recalculate", {})
    assert refreshed["state"]["planning"]["result"]["weekly_reviews"][0]["status"] == "done"
    later = op(work, "later-weekly", "recalculate", {"from_date": "2027-01-12"})
    retained = later["state"]["planning"]["result"]["weekly_reviews"][0]
    assert retained["date"] == first["date"] and retained["status"] == "done"


def test_weekly_review_unplaced_is_risk_not_fake_booking(tmp_path):
    work = seed(tmp_path)
    document = planning()
    document["settings"]["weekly_review"] = {"enabled": True, "minutes": 60, "weekday": 6}
    for day in range(4, 11):
        document["calendar"]["events"].append({
            "id": f"busy-{day}", "title": "Unavailable",
            "start": f"2027-01-{day:02d}T11:00:00+03:00",
            "end": f"2027-01-{day:02d}T20:00:00+03:00",
            "category": "busy"})
    result = op(work, "unplaced", "import", {"planning": document})["state"]["planning"]["result"]
    first = result["weekly_reviews"][0]
    assert first["status"] == "unplaced" and first["date"] is None
    assert any(r["code"] == "weekly_review_unplaced" and r["week_end"] == first["week_end"]
               for r in result["risks"])
    assert all(day["weekly_review_minutes"] == 0 for day in result["days"][:7])
    with pytest.raises(ValidationError):
        op(work, "cannot-done", "weekly_review_done", {"id": first["id"], "done": True})


def test_calendar_refresh_chunks_horizons_longer_than_api_window(tmp_path, monkeypatch):
    from megamozg.calendar import CalendarFeed
    from megamozg.google_calendar_api import GoogleCalendarApi

    work = seed(tmp_path)
    document = planning()
    document["settings"]["end"] = "2027-03-14"
    op(work, "long-import", "import", {"planning": document})
    calls = []
    def read_events(_self, first, last, *, force=False, google_calendar=None):
        calls.append((first, last))
        return {"configured": True, "calendar_count": 1, "stale": False, "error": None, "events": []}
    monkeypatch.setattr(CalendarFeed, "events", read_events)
    monkeypatch.setattr(GoogleCalendarApi, "status", lambda _self: {"authorized": False})
    op(work, "long-refresh", "recalculate", {"refresh_calendar": True})
    assert len(calls) == 2 and calls[0][1] < calls[1][0]
    assert all((end-start).days <= 62 for start, end in calls)


def test_stale_calendar_refresh_keeps_last_known_busy_time(tmp_path, monkeypatch):
    from megamozg.calendar import CalendarFeed
    from megamozg.google_calendar_api import GoogleCalendarApi

    work = seed(tmp_path)
    op(work, "import", "import", {"planning": planning()})
    monkeypatch.setattr(CalendarFeed, "events", lambda *_args, **_kwargs: {
        "configured": True, "calendar_count": 1, "stale": True,
        "error": "temporarily unavailable", "events": [
            {"id": "new-opaque-id", "title": "Lesson", "start": "2027-01-04T11:00:00+03:00",
             "end": "2027-01-04T13:00:00+03:00"}]})
    monkeypatch.setattr(GoogleCalendarApi, "status", lambda _self: {"authorized": False})
    snapshot = op(work, "stale-refresh", "recalculate", {"refresh_calendar": True})
    current = snapshot["state"]["planning"]
    assert current["calendar"]["stale"] is True
    assert current["calendar"]["coverage_status"] == "stale_merged"
    assert len([event for event in current["calendar"]["events"]
                if event.get("title") == "Lesson"]) == 1
    assert next(event for event in current["calendar"]["events"]
                if event.get("title") == "Lesson")["category"] == "lesson"
    assert any(event["id"] == "homework" for event in current["calendar"]["events"])
    day = next(x for x in current["result"]["days"] if x["date"] == "2027-01-04")
    assert day["fixed_minutes"] == 145 and day["needs_review"] is True


def test_live_calendar_link_survives_refresh_id_change_by_unique_fingerprint():
    from megamozg.work_planning import calculate, merge_calendar_refresh

    document = planning()
    document["calendar"]["events"].append({
        "id": "source-id", "title": "Webinar", "start": "2027-01-06T21:00:00+03:00",
        "end": "2027-01-06T22:00:00+03:00", "category": "lesson"})
    document["live_events"] = [{
        "id": "live", "title": "Webinar", "duration_minutes": 180,
        "date": "2027-01-06", "start": "19:00", "calendar_event_id": "source-id"}]
    merge_calendar_refresh(document, {
        "configured": True, "calendar_count": 1, "stale": False, "error": None,
        "events": [{"id": "opaque-id", "title": "Webinar",
                    "start": "2027-01-06T21:00:00+03:00",
                    "end": "2027-01-06T22:00:00+03:00"}]})
    result = calculate(document)["result"]
    day = next(d for d in result["days"] if d["date"] == "2027-01-06")
    assert day["calendar_minutes"] == 180
    assert any(r["code"] == "live_calendar_link_inferred" for r in result["risks"])


def test_ordinary_work_commit_preserves_but_cannot_mutate_planning(tmp_path):
    work = seed(tmp_path)
    snapshot = op(work, "import", "import", {"planning": planning()})
    changed = deepcopy(snapshot["state"])
    changed["planning"]["settings"]["day_limit_minutes"] = 240
    digest = upload_state(work, changed)
    with pytest.raises(ValidationError):
        work.commit({"request_id": "bypass", "expected_revision": snapshot["revision"],
                     "state_blob": digest, "images": []})
    assert work.read()["revision"] == snapshot["revision"]
    ordinary = deepcopy(snapshot["state"])
    ordinary["boards"][0]["items"][0]["text"] = "edited board"
    digest = upload_state(work, ordinary)
    accepted = work.commit({"request_id": "normal-work", "expected_revision": snapshot["revision"],
                            "state_blob": digest, "images": []})
    assert accepted["state"]["boards"][0]["items"][0]["text"] == "edited board"
    assert accepted["state"]["planning"] == snapshot["state"]["planning"]


def test_explicit_full_json_restore_keeps_images_and_receipt_cas(tmp_path, monkeypatch):
    monkeypatch.setattr(features, "WORK_ENABLED", True)
    work = seed(tmp_path)
    image = b"synthetic image bytes"
    image_hash = hashlib.sha256(image).hexdigest()
    work.upload_chunk(image_hash, {"index": 0, "total": 1,
                                   "content_base64": base64.b64encode(image).decode()})
    with_image = deepcopy(work.read()["state"])
    with_image["boards"][0]["items"].append({"id": "picture", "type": "image", "imageId": "keep-image"})
    state_hash = upload_state(work, with_image)
    descriptor = {"id": "keep-image", "blob": image_hash, "mime": "image/png"}
    work.commit({"request_id": "add-image", "expected_revision": 1,
                 "state_blob": state_hash, "images": [descriptor]})
    backup = op(work, "import", "import", {"planning": planning()})
    cube = backup["state"]["planning"]["result"]["cubes"][0]
    progressed = op(work, "progress", "cube_progress",
                    {"cube_id": cube["id"], "percent": 50, "actual_minutes": 40})
    assert progressed["revision"] == backup["revision"] + 1
    old_blob = upload_state(work, backup["state"])
    restore_command = {"request_id": "full-json-restore", "expected_revision": progressed["revision"],
                       "state_blob": old_blob, "images": [descriptor], "restore_import": True}
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765") as client:
        client.headers.update({"Authorization": "Bearer " + Store(tmp_path).token()})
        assert client.post("/api/work/commit", json={
            **restore_command, "request_id": "no-intent", "restore_import": False}).status_code == 422
        assert client.post("/api/work/commit", json={
            **restore_command, "request_id": "stale", "expected_revision": backup["revision"]}).status_code == 409
        response = client.post("/api/work/commit", json=restore_command)
        assert response.status_code == 200
        restored = response.json()
        assert restored["revision"] == progressed["revision"] + 1
        assert restored["state"]["planning"]["completed_cubes"] == backup["state"]["planning"]["completed_cubes"]
        assert restored["state"]["boards"] == backup["state"]["boards"]
        assert restored["images"][0]["id"] == "keep-image"
        assert client.get("/api/work/images/keep-image").content == image
        assert client.post("/api/work/commit", json=restore_command).json()["revision"] == restored["revision"]
        assert client.post("/api/work/commit", json={
            **restore_command, "expected_revision": restored["revision"]}).status_code == 409
        # Revision restore remains a separate explicit path and can recover
        # the progress-bearing revision after the JSON import.
        previous = client.post("/api/work/restore", json={
            "request_id": "restore-revision", "expected_revision": restored["revision"],
            "revision": progressed["revision"]})
        assert previous.status_code == 200
        assert previous.json()["state"]["planning"]["completed_cubes"][cube["id"]]["percent"] == 50


def test_reported_day_freezes_actual_minutes_and_future_replan_without_id_reuse(tmp_path):
    work = seed(tmp_path)
    initial = op(work, "import", "import", {"planning": planning()})["state"]["planning"]["result"]
    first = initial["cubes"][0]
    original_ids = {cube["id"] for cube in initial["cubes"] if cube["date"] == first["date"]}
    op(work, "half", "cube_progress",
       {"cube_id": first["id"], "percent": 50, "actual_minutes": 40})
    snapshot = op(work, "replan-reported", "recalculate", {"from_date": first["date"]})
    result = snapshot["state"]["planning"]["result"]
    provenance = result["provenance"]
    assert provenance["requested_from"] == first["date"]
    assert provenance["effective_from"] == "2027-01-05"
    assert provenance["report_warning"]
    frozen = next(day for day in result["days"] if day["date"] == first["date"])
    assert frozen["reported"] is True and frozen["reported_actual_minutes"] == 40
    assert frozen["total_work_minutes"] == frozen["fixed_minutes"] + frozen["weekly_review_minutes"] + 40
    assert frozen["unused_capacity_minutes"] == 0
    assert {cube["id"] for cube in result["cubes"] if cube["date"] == first["date"]} == original_ids
    assert all(cube["id"] not in original_ids for cube in result["cubes"] if cube["date"] > first["date"])
    assert next(cube for cube in result["cubes"] if cube["id"] == first["id"])["progress_percent"] == 50
    task = next(task for task in result["tasks"] if task["id"] == first["task_id"])
    future_scheduled = sum(cube["minutes"] for cube in result["cubes"]
                           if cube["task_id"] == first["task_id"]
                           and provenance["effective_from"] <= cube["date"] <= task["due"])
    assert task["scheduled_by_due_minutes"] == future_scheduled
    assert task["deficit_by_due_minutes"] == max(0, 900-45-future_scheduled)


def test_reported_over_cap_is_visible_and_started_cube_cannot_move(tmp_path):
    work = seed(tmp_path)
    first = op(work, "import", "import", {"planning": planning()})["state"]["planning"]["result"]["cubes"][0]
    op(work, "overwork", "cube_progress",
       {"cube_id": first["id"], "percent": 50, "actual_minutes": 200})
    before = work.read()
    with pytest.raises(ValidationError, match="Начатый кубик"):
        op(work, "move-started", "place_cube",
           {"cube_id": first["id"], "date": "2027-01-05", "start": "14:00", "locked": True})
    assert work.read() == before
    result = op(work, "replan", "recalculate", {"from_date": first["date"]})["state"]["planning"]["result"]
    day = next(day for day in result["days"] if day["date"] == first["date"])
    assert day["reported_actual_minutes"] == 200
    assert day["total_work_minutes"] == day["fixed_minutes"] + day["weekly_review_minutes"] + 200
    assert any(risk["code"] == "reported_work_exceeds_day_limit" and risk["date"] == first["date"]
               for risk in result["risks"])


def test_replan_after_report_on_horizon_last_day_has_empty_future(tmp_path):
    work = seed(tmp_path)
    op(work, "import", "import", {"planning": planning()})
    op(work, "last-report", "day_outcome",
       {"task_id": "a", "date": "2027-01-17", "remaining_minutes": 120, "done": False})
    result = op(work, "after-end", "recalculate", {"from_date": "2027-01-17"})["state"]["planning"]["result"]
    assert result["provenance"]["effective_from"] == "2027-01-18"
    assert result["totals"]["future_development_hours"] == 0
    assert all(day["date"] <= "2027-01-17" for day in result["days"])
    assert next(task for task in result["tasks"] if task["id"] == "a")["deficit_by_due_minutes"] == 120


def test_compact_status_uses_planning_zone_for_next_days(tmp_path, monkeypatch):
    from datetime import datetime as RealDateTime
    from megamozg import work_planning

    work = seed(tmp_path)
    snapshot = op(work, "import", "import", {"planning": planning()})
    class MoscowClock:
        @staticmethod
        def now(zone):
            assert zone.key == "Europe/Moscow"
            return RealDateTime(2027, 1, 5, 0, 30, tzinfo=zone)
    monkeypatch.setattr(work_planning, "datetime", MoscowClock)
    status = work_planning.compact_status(snapshot)
    assert status["next_days"][0]["date"] == "2027-01-05"


def test_reported_locked_cube_no_longer_reserves_full_minutes_in_future(tmp_path):
    work = seed(tmp_path)
    first = op(work, "import", "import", {"planning": planning()})["state"]["planning"]["result"]["cubes"][0]
    op(work, "lock", "place_cube",
       {"cube_id": first["id"], "date": first["date"], "start": first["start"], "locked": True})
    op(work, "half-locked", "cube_progress",
       {"cube_id": first["id"], "percent": 50, "actual_minutes": 40})
    result = op(work, "replan-lock", "recalculate", {"from_date": first["date"]})["state"]["planning"]["result"]
    assert result["provenance"]["effective_from"] == "2027-01-05"
    assert any(cube["id"] == first["id"] and cube["locked"] for cube in result["cubes"])


def test_partial_credit_creates_exact_tail_and_short_lock_must_match_tail(tmp_path):
    from megamozg.work_planning import calculate

    work = seed(tmp_path)
    document = planning()
    document["tasks"] = [document["tasks"][0]]
    document["tasks"][0]["estimate_minutes"] = {"lower": 180, "central": 180, "upper": 180}
    document["tasks"][0]["review_minutes"] = 0
    document["tasks"][0]["due"] = "2027-01-17"
    first_plan = op(work, "import-tail", "import", {"planning": document})["state"]["planning"]["result"]
    first = first_plan["cubes"][0]
    assert first["minutes"] == 90
    op(work, "half-tail", "cube_progress",
       {"cube_id": first["id"], "percent": 50, "actual_minutes": 40})
    snapshot = op(work, "replan-tail", "recalculate", {"from_date": first["date"]})
    result = snapshot["state"]["planning"]["result"]
    future = [cube for cube in result["cubes"] if cube["task_id"] == "a"
              and cube["date"] >= result["provenance"]["effective_from"]]
    assert sorted(cube["minutes"] for cube in future) == [45, 90]
    assert [cube["minutes"] for cube in future if cube["is_remainder"]] == [45]
    task = next(task for task in result["tasks"] if task["id"] == "a")
    assert task["remaining_at_horizon_minutes"] == 0
    assert task["deficit_by_due_minutes"] == 0
    tail = next(cube for cube in future if cube["is_remainder"])
    locked = op(work, "lock-tail", "place_cube",
                {"cube_id": tail["id"], "date": tail["date"], "start": tail["start"], "locked": True})
    assert any(cube["id"] == tail["id"] and cube["locked"] and cube["minutes"] == 45
               for cube in locked["state"]["planning"]["result"]["cubes"])
    tampered = deepcopy(snapshot["state"]["planning"])
    minute = int(tail["start"][:2]) * 60 + int(tail["start"][3:]) + 30
    wrong = {"cube_id": "invalid-short", "task_id": "a", "date": tail["date"],
             "start": tail["start"], "end": f"{minute // 60:02d}:{minute % 60:02d}", "minutes": 30,
             "phase": "preparation", "locked": True, "is_remainder": True}
    tampered["locks"] = [wrong]
    with pytest.raises(ValidationError):
        calculate(tampered, from_date=result["provenance"]["effective_from"])


def test_small_daily_budget_does_not_create_short_cube_for_long_task():
    from megamozg.work_planning import calculate

    document = planning()
    document["settings"].update(start="2027-01-04", end="2027-01-04", prep_minutes=0)
    document["tasks"] = [document["tasks"][0]]
    document["tasks"][0]["estimate_minutes"] = {"lower": 180, "central": 180, "upper": 180}
    document["tasks"][0]["review_minutes"] = 0
    document["calendar"]["events"] = [{
        "id": "work-255", "title": "Existing work",
        "start": "2027-01-04T11:00:00+03:00", "end": "2027-01-04T15:15:00+03:00",
        "category": "work"}]
    result = calculate(document)["result"]
    assert result["days"][0]["fixed_minutes"] == 255
    assert result["days"][0]["unused_capacity_minutes"] == 45
    assert result["cubes"] == []
    assert result["tasks"][0]["remaining_at_horizon_minutes"] == 180


def test_linked_imported_work_item_done_and_drawer_title_sync_on_replan(tmp_path):
    work = seed(tmp_path)
    document = planning()
    document["tasks"][0]["work_item_id"] = "a-planning-item"
    imported = op(work, "import", "import", {"planning": document})
    cube = next(cube for cube in imported["state"]["planning"]["result"]["cubes"]
                if cube["task_id"] == "a")
    progressed = op(work, "cube-not-whole-task", "cube_progress",
                    {"cube_id": cube["id"], "percent": 100, "actual_minutes": 90})
    linked = next(item for item in progressed["state"]["items"] if item["id"] == "a-planning-item")
    assert linked["status"] == "todo"
    completed = op(work, "whole-task-done", "day_outcome",
                   {"task_id": "a", "date": cube["date"], "done": True, "remaining_minutes": 0})
    linked = next(item for item in completed["state"]["items"] if item["id"] == "a-planning-item")
    assert linked["status"] == "done"
    assert completed["state"]["items"][0] == imported["state"]["items"][0]
    reopened = op(work, "whole-task-reopened", "day_outcome",
                  {"task_id": "a", "date": cube["date"], "done": False, "remaining_minutes": 60})
    assert next(item for item in reopened["state"]["items"] if item["id"] == "a-planning-item")["status"] == "todo"
    zero = op(work, "zero-remaining", "day_outcome",
              {"task_id": "a", "date": cube["date"], "remaining_minutes": 0})
    assert next(item for item in zero["state"]["items"] if item["id"] == "a-planning-item")["status"] == "done"
    changed = deepcopy(zero["state"])
    next(item for item in changed["items"] if item["id"] == "a-planning-item")["title"] = "Renamed in Projects"
    digest = upload_state(work, changed)
    work.commit({"request_id": "drawer-rename", "expected_revision": zero["revision"],
                 "state_blob": digest, "images": []})
    replanned = op(work, "sync-title", "recalculate", {"from_date": "2027-01-06"})
    task = next(task for task in replanned["state"]["planning"]["tasks"] if task["id"] == "a")
    assert task["title"] == "Renamed in Projects"
    assert next(item for item in replanned["state"]["items"] if item["id"] == "legacy")["title"] == "Existing task"


def test_describe_cubes_retains_orphaned_detail_after_replan(tmp_path):
    work = seed(tmp_path)
    snapshot = op(work, "import", "import", {"planning": planning()})
    cube = snapshot["state"]["planning"]["result"]["cubes"][0]
    detail = {"cube_id": cube["id"], "title": "Specific work", "output": "Saved artifact",
              "steps": ["Open source", "Check one result", "Save draft"]}
    described = op(work, "detail", "describe_cubes", {"entries": [detail]})
    shown = next(x for x in described["state"]["planning"]["result"]["cubes"] if x["id"] == cube["id"])
    assert shown["steps"] == detail["steps"] and shown["title"] == detail["title"]
    changed_calendar = planning()
    changed_calendar["calendar"]["events"].append({
        "id": "new-busy", "title": "Unexpected meeting",
        "start": f"{cube['date']}T{cube['start']}:00+03:00",
        "end": f"{cube['date']}T{cube['end']}:00+03:00", "category": "busy"})
    replanned = op(work, "changed-calendar", "import", {"planning": changed_calendar})
    current = replanned["state"]["planning"]
    assert current["cube_details"][cube["id"]]["steps"] == detail["steps"]
    assert current["result"]["needs_detail_review"] is True
    assert any(r["code"] == "cube_detail_orphaned" for r in current["result"]["risks"])


def test_cli_full_command_uses_same_receipt_and_cas(tmp_path, monkeypatch, capsys):
    from megamozg.cli import main

    monkeypatch.setattr(features, "WORK_ENABLED", True)
    work = seed(tmp_path)
    snapshot = op(work, "import", "import", {"planning": planning()})
    cube = snapshot["state"]["planning"]["result"]["cubes"][0]
    command = {"request_id": "cli-detail", "expected_revision": snapshot["revision"],
               "action": "describe_cubes",
               "data": {"entries": [{"cube_id": cube["id"], "title": "One step",
                                     "output": "Saved", "steps": ["Do it"]}]}}
    path = tmp_path / "command.json"
    path.write_text(json.dumps(command), encoding="utf-8")
    assert main(["--root", str(tmp_path), "work", "plan", "command", "--file", str(path)]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["revision"] == snapshot["revision"] + 1
    assert main(["--root", str(tmp_path), "work", "plan", "command", "--file", str(path)]) == 0
    repeated = json.loads(capsys.readouterr().out)
    assert repeated["revision"] == first["revision"]
