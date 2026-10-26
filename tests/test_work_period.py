"""Synthetic production WorkStore contracts; no personal state or services."""
from copy import deepcopy
import hashlib
import json
import pytest
from megamozg.work_period import model, calendar_rules
from megamozg.core import ConflictError, ValidationError
from megamozg import work_planning as wp
from megamozg.work_store import WorkStore
from megamozg.shared_state import encoded


def synthetic_store(root, state, images=()):
    """Install a validated synthetic baseline, including historical deletions."""
    store = WorkStore(root)
    raw = encoded(state).encode('utf-8')
    digest = hashlib.sha256(raw).hexdigest()
    with store.db() as db:
        db.execute('INSERT INTO work_blobs VALUES(?,?)', (digest, raw))
        descriptors = store._validate(db, digest, list(images))
        db.execute('INSERT INTO work_versions VALUES(?,?,?,?)', (1, 1, digest, encoded(descriptors)))
    return store


def set_context(planner, patch, request_id=None, revision=None):
    return planner.set_context({'request_id': request_id or 'context-'+str(planner.store.read()['revision']),
        'expected_revision': planner.store.read()['revision'] if revision is None else revision, 'patch': patch})


@pytest.fixture
def planner(tmp_path):
    return model.PeriodPlanner(synthetic_store(tmp_path, synthetic_state()))


@pytest.fixture
def color_planner(tmp_path):
    state, source, ids = color_source_state()
    return model.PeriodPlanner(synthetic_store(tmp_path, state)), source, ids


def synthetic_state():
    planning = {"version": 1, "settings": {"start": "2027-01-04", "end": "2027-01-17",
        "timezone": "Europe/Moscow", "day_limit_minutes": 300, "work_start": "11:00", "work_end": "20:00",
        "prep_minutes": 25, "break_minutes": 15, "workdays": 6},
        "tasks": [{"id": key, "project_id": project, "course": project, "title": title, "kind": "other",
            "due": "2027-01-17", "due_status": "confirmed", "estimate_minutes": dict.fromkeys(("lower", "central", "upper"), minutes),
            "resources": [], "review_minutes": 0} for key, project, title, minutes in
            [("a", "print", "Slides and geometry nodes", 600), ("b", "sculpt", "Sculpt", 300)]],
        "calendar": {"start": "2027-01-04", "end": "2027-01-17", "fetched_at": "2027-01-03T12:00:00+03:00",
            "configured": True, "stale": False, "events": [
                {"id": "calendar-webinar", "occurrence_id": "webinar-occ", "calendar_id": "qa", "title": "Webinar",
                    "start": "2027-01-07T21:00:00+03:00", "end": "2027-01-07T22:00:00+03:00", "category": "work"},
                {"id": "all-day", "title": "Homework", "start": "2027-01-06", "end": "2027-01-07", "all_day": True, "category": "homework"}]},
        "live_events": [{"id": "live-webinar", "title": "Webinar", "task_id": "a", "calendar_event_id": "webinar-occ",
            "date": "2027-01-07", "start": "19:00", "duration_minutes": 180}],
        "completed_cubes": {}, "day_outcomes": [], "locks": [], "history": [], "result": {}}
    planning = wp.calculate(planning)
    for cid, title, minutes in [("geometry", "Geometry nodes", 60), ("slides", "Slides", 45), ("other", "Other", 30)]:
        planning = wp.apply_action(planning, "constructor_cube", {"cube_id": cid, "kind": "task", "task_id": "a",
            "title": title, "minutes": minutes, "planned_date": "2027-01-05", "output": title + " output", "steps": [title + " step"]})
    planning["course_catalog"] = {"items": [
        {"id": "done-source", "project_id": "print", "title": "Source done, no Work claim", "status": "done", "due": "2026-09-11", "owned_by_user": True},
        {"id": "a-material", "work_task_id": "a", "project_id": "print", "title": "Material A", "status": "active", "due": "2027-01-17", "owned_by_user": True},
        {"id": "reference-tail", "project_id": "print", "title": "Someone else's tail", "status": "waiting", "due": "2028-12-01", "owned_by_user": False},
        {"id": "b-material", "work_task_id": "b", "project_id": "sculpt", "title": "Material B", "status": "waiting", "due": "2027-01-17", "owned_by_user": True}]}
    # A real /api/work/state snapshot already contains these read-time defaults.
    wp.project_task_importance(planning)
    return {"version": 1, "projects": [{"id": "print", "name": "Print"}, {"id": "sculpt", "name": "Sculpt"}],
        "items": [], "boards": [{"id": "general", "items": []}], "planning": planning,
        "unknown_future_field": {"preserve": ["owner edit", 10]}}




def command(planner, action, data, request_id=None, revision=None):
    return planner.command({"request_id": request_id or "test-" + str(planner.store.read()["revision"]),
        "expected_revision": planner.store.read()["revision"] if revision is None else revision,
        "action": action, "data": data})




def test_period_metadata_only_and_outside_selection_preserved(planner):
    native = deepcopy(planner.store.read()["state"])
    command(planner, "period_set", {"task_id": "a", "start": "2027-01-05", "end": "2027-01-10"})
    after = planner.store.read()["state"]
    assert {k: v for k, v in after.items() if k != model.NAMESPACE} == native
    command(planner, "cube_select", {"cube_id": "geometry", "date": "2027-01-05"})
    before = planner.store.read()
    command(planner, "period_set", {"task_id": "a", "start": "2027-01-06", "end": "2027-01-10"})
    assert next(c for c in planner.model()["cubes"] if c["id"] == "geometry")["selected_date"] == "2027-01-05"
    assert planner.store.read()["state"]["planning"] == before["state"]["planning"]
    command(planner, "period_clear", {"task_id": "a"})
    assert not next(t for t in planner.model()["tasks"] if t["id"] == "a")["period_explicit"]
    assert next(c for c in planner.model()["cubes"] if c["id"] == "geometry")["selected_date"] == "2027-01-05"


def test_durable_cas_replay_collision_and_reopen(planner):
    revision = planner.store.read()["revision"]
    payload = {"request_id": "rename-once", "expected_revision": revision, "action": "cube_rename", "data": {"cube_id": "geometry", "title": "Owner title"}}
    first = planner.command(payload)
    assert first["revision"] == revision + 1
    reopened = model.PeriodPlanner(type(planner.store)(planner.store.root))
    assert reopened.command(payload) == first
    assert reopened.store.read()["revision"] == revision + 1
    with pytest.raises(ConflictError):
        command(reopened, "cube_rename", {"cube_id": "geometry", "title": "Stale"}, revision=revision)
    with pytest.raises(ConflictError):
        reopened.command({**payload, "data": {"cube_id": "geometry", "title": "Changed payload"}})


def test_shared_durable_context_and_clear_stale_selection(planner):
    result = set_context(planner, {"date": "2027-01-05", "task_id": "a", "cube_id": "geometry", "mode": "month", "expanded_week": "2027-01-04"})
    assert result["context"]["week_start"] == "2027-01-04"
    assert result["revision"] == planner.store.read()["revision"]
    reopened = model.PeriodPlanner(type(planner.store)(planner.store.root))
    assert reopened.model()["context"] == result["context"]
    updated = set_context(reopened, {"date": "2027-01-06"})
    assert updated["context"]["cube_id"] is None and updated["context"]["task_id"] is None


def test_historical_timeline_context_is_readable_but_not_mutable(planner):
    selected = set_context(planner, {"task_id": "done-source"})
    assert selected["context"]["task_id"] == "done-source"
    assert model.PeriodPlanner(type(planner.store)(planner.store.root)).model()["context"]["task_id"] == "done-source"
    before = planner.store.read()
    for action, data in [("period_set", {"task_id": "done-source", "start": "2027-01-05", "end": "2027-01-06"}),
                         ("task_rename", {"task_id": "done-source", "title": "Forbidden historical edit"})]:
        with pytest.raises(ValidationError):
            command(planner, action, data)
    assert planner.store.read() == before


def test_context_revision_supports_immediate_command_but_keeps_external_cas(planner):
    before = planner.store.read()["revision"]
    saved = set_context(planner, {"cube_id": "geometry", "date": "2027-01-05"})
    assert saved["revision"] == before + 1
    chosen = command(planner, "cube_select", {"cube_id": "geometry", "date": "2027-01-05"}, revision=saved["revision"])
    assert chosen["revision"] == before + 2
    assert next(c for c in chosen["cubes"] if c["id"] == "geometry")["selected_date"] == "2027-01-05"
    saved = set_context(planner, {"cube_id": "slides"})
    external = model.PeriodPlanner(type(planner.store)(planner.store.root))
    command(external, "cube_rename", {"cube_id": "slides", "title": "External edit"})
    with pytest.raises(ConflictError):
        command(planner, "cube_select", {"cube_id": "slides", "date": "2027-01-05"}, revision=saved["revision"])


def test_select_move_time_return_preserve_identity_effort_facts(planner):
    before = deepcopy(planner.store.read()["state"]["planning"])
    command(planner, "cube_select", {"cube_id": "geometry", "date": "2027-01-05"})
    command(planner, "cube_time", {"cube_id": "geometry", "start": "14:00"})
    command(planner, "cube_move", {"cube_id": "geometry", "date": "2027-01-06", "start": "15:00"})
    row = next(c for c in planner.model()["cubes"] if c["id"] == "geometry")
    assert (row["selected_date"], row["start"], row["minutes"]) == ("2027-01-06", "15:00", 60)
    command(planner, "cube_return", {"cube_id": "geometry"})
    row = next(c for c in planner.model()["cubes"] if c["id"] == "geometry")
    assert row["selected_date"] is None and row["start"] is None and row["available"]
    assert planner.store.read()["state"]["planning"] == before


def test_merge_sum_source_ids_steps_and_nested_merge_do_not_resurrect(planner):
    set_context(planner, {"cube_id": "geometry"})
    before = planner.model()
    command(planner, "cube_merge", {"cube_ids": ["geometry", "slides"], "title": "Create presentation"}, request_id="merge")
    first = next(c for c in planner.model()["cubes"] if c["id"].startswith("merged-"))
    assert first["minutes"] == 105 and set(first["source_ids"]) == {"geometry", "slides"}
    assert len(first["steps"]) == 2 and "Geometry nodes output" in first["output"] and "Slides output" in first["output"]
    assert not {"geometry", "slides"} & {c["id"] for c in planner.model()["cubes"]}
    assert sum(c["minutes"] for c in planner.model()["cubes"]) == sum(c["minutes"] for c in before["cubes"])
    assert planner.model()["context"]["cube_id"] is None
    reopened = model.PeriodPlanner(type(planner.store)(planner.store.root))
    assert set_context(reopened, {"cube_id": first["id"]})["context"]["cube_id"] == first["id"]
    command(planner, "cube_merge", {"cube_ids": [first["id"], "other"], "title": "Whole presentation"})
    assert planner.model()["context"]["cube_id"] is None
    rows = planner.model()["cubes"]
    assert first["id"] not in {c["id"] for c in rows}
    merged = next(c for c in rows if c["id"].startswith("merged-"))
    assert merged["minutes"] == 135 and set(merged["source_ids"]) == {"geometry", "slides", "other"}


def test_unsafe_started_merge_rejected_and_selected_merge_preserves_target(planner):
    state = planner.store.read()["state"]
    state["planning"]["completed_cubes"]["geometry"] = {"task_id": "a", "date": "2027-01-05", "planned_minutes": 60, "percent": 25, "actual_minutes": 12}
    with pytest.raises(ValidationError, match="факты"):
        model.apply_command(deepcopy(state), "cube_merge", {"cube_ids": ["geometry", "slides"], "title": "Unsafe"})
    with pytest.raises(ValidationError, match="факты"):
        model.apply_command(deepcopy(state), "cube_cancel", {"cube_id": "geometry", "reason": "Unsafe"})
    command(planner, "cube_select", {"cube_id": "geometry", "date": "2027-01-05"})
    result = command(planner, "cube_merge", {"cube_ids": ["geometry", "slides"], "title": "Selected merge"})
    merged = next(cube for cube in result["cubes"] if cube["id"].startswith("merged-"))
    assert merged["selected_date"] == "2027-01-05" and merged["start"] is None
    assert merged["minutes"] == 105


def test_cancel_scope_preserved_and_never_completion(planner):
    before = planner.model()
    command(planner, "cube_cancel", {"cube_id": "geometry", "reason": "Owner cancels precisely this scope"})
    after = planner.model()
    task = next(t for t in after["tasks"] if t["id"] == "a")
    assert task["remaining_minutes"] == 600 and task["progress_percent"] == 0 and task["cancelled_minutes"] == 60
    assert task["effective_remaining_minutes"] == 540
    row = next(c for c in after["cubes"] if c["id"] == "geometry")
    assert row["minutes"] == 60 and row["status"] == "cancelled" and row["progress_percent"] == 0
    assert after["totals"]["effective_cube_minutes"] + after["totals"]["cancelled_cube_minutes"] == sum(c["minutes"] for c in before["cubes"])


def test_dedup_candidates_and_calendar_conflict_once(planner):
    state = planner.store.read()["state"]
    p = state["planning"]
    forecast = next(c for c in p["result"]["cubes"] if not c.get("manual") and not c.get("locked"))
    saved = deepcopy(p["constructor"]["cubes"]["geometry"])
    saved.update(id="planned-" + forecast["id"], blueprint_ref=forecast["id"])
    p["constructor"]["cubes"][saved["id"]] = saved
    rows = model.cube_rows(state)
    assert sum(c.get("blueprint_ref") == forecast["id"] for c in rows) == 1
    events = planner.model()["events"]
    webinar = [e for e in events if e["id"] in {"calendar-webinar", "live-webinar"}]
    assert len(webinar) == 1 and webinar[0]["conflict"]
    assert {(a["start"], a["end"]) for a in webinar[0]["alternatives"]} == {("21:00", "22:00"), ("19:00", "22:00")}
    assert next(e for e in events if e["id"] == "all-day")["all_day"]


def test_timeline_full_owned_range_and_independent_source_status(planner):
    p = next(p for p in planner.model()["projects"] if p["id"] == "print")
    assert p["start"] == "2026-09-11" and p["end"] == "2027-01-17"
    assert len(p["tasks"]) == 3 and next(t for t in p["tasks"] if t["id"] == "done-source")["source_status"] == "done"
    assert next(t for t in planner.model()["tasks"] if t["id"] == "a")["progress_percent"] == 0
    command(planner, "project_visibility", {"project_id": "print", "visible": False})
    assert next(p for p in planner.model()["projects"] if p["id"] == "print")["visible"] is False
    assert next(c for c in planner.model()["cubes"] if c["id"] == "geometry")["available"]


@pytest.mark.parametrize("ownership_rule", ["catalog", "scope_exempt"])
def test_reference_and_exempt_tasks_do_not_inflate_personal_workload(ownership_rule):
    state = synthetic_state()
    planning = state["planning"]
    if ownership_rule == "catalog":
        next(row for row in planning["course_catalog"]["items"] if row.get("work_task_id") == "a")["owned_by_user"] = False
    else:
        planning["scope_exemptions"] = {"a": {"exempt": True, "from_date": "2027-01-04", "reason": "Outside owner scope"}}
    before = deepcopy(state)
    result = model.read_model({"state": state, "revision": 10})
    assert state == before
    assert next(task for task in result["tasks"] if task["id"] == "a")["owned"] is False
    assert result["totals"]["source_minutes"] == 300
    assert result["totals"]["source_all_minutes"] == 900 and result["totals"]["reference_minutes"] == 600
    assert result["totals"]["effective_remaining_minutes"] == 300
    personal = [cube for cube in result["cubes"] if cube.get("task_id") != "a"]
    assert result["totals"]["effective_cube_minutes"] == sum(cube["minutes"] for cube in personal if cube["status"] != "cancelled")
    assert result["totals"]["source_all_cube_minutes"] == result["totals"]["effective_cube_minutes"] + result["totals"]["cancelled_cube_minutes"] + result["totals"]["reference_cube_minutes"]
    assert all(not cube["available"] for cube in result["cubes"] if cube.get("task_id") == "a")
    for action, data in [("period_set", {"task_id": "a", "start": "2027-01-05", "end": "2027-01-06"}),
                         ("task_rename", {"task_id": "a", "title": "Reference edit"})]:
        with pytest.raises(ValidationError, match="только чтение"):
            model.apply_command(deepcopy(state), action, data)


def test_conflict_time_and_daily_budget_are_atomic(planner):
    before = planner.store.read()
    with pytest.raises(ValidationError, match="пересекается"):
        command(planner, "cube_select", {"cube_id": "geometry", "date": "2027-01-07", "start": "19:00"})
    assert planner.store.read() == before
    with pytest.raises(ValidationError, match="рабочее окно"):
        command(planner, "cube_select", {"cube_id": "geometry", "date": "2027-01-05", "start": "10:00"})
    assert planner.store.read() == before






def color_source_state():
    state = synthetic_state()
    native_calendar = "synthetic-calendar-hash"
    source = {"captured_at": "2027-01-03T12:00:00+03:00", "calendar_id": "primary", "timezone": "Europe/Moscow",
        "coverage": {"start": "2027-01-01", "end": "2027-01-31"}, "default_background_color": "#8e24aa",
        "labels": [{"id": label, "background_color": color} for label, color in
            [("red", "#ad1457"), ("gray", "#a79b8e"), ("sage", "#33b679"), ("unknown", "#8e24aa"), ("teal", "#009688")]], "events": []}
    ids = {}
    def event(provider_id, first, last, label=None, *, all_day=False, recurring=None, native=True, legacy=None):
        value = {"id": provider_id, "summary": provider_id, "status": "confirmed", "start": first, "end": last,
            "event_label_id": label, "color_id": legacy, "recurring_event_id": recurring,
            "original_start_time": first if recurring else None}
        source["events"].append(value)
        if native:
            zone = calendar_rules.ZoneInfo("Europe/Moscow")
            start = calendar_rules._dt(first, zone).isoformat()
            uid = (recurring.split("_R", 1)[0] if recurring else provider_id) + "@google.com"
            event_id = calendar_rules._digest(native_calendar + "|" + uid + "|" + start)
            slot = start if recurring else "single"
            occurrence_id = calendar_rules._digest(native_calendar + "|" + uid + "|" + slot)
            state["planning"]["calendar"]["events"].append({"id": event_id, "occurrence_id": occurrence_id,
                "calendar_id": native_calendar, "title": provider_id, "start": first + "+03:00" if "+" not in first else first,
                "end": last + "+03:00" if "+" not in last else last, "all_day": all_day, "category": "busy"})
            ids[provider_id] = event_id
    event("fixed", "2027-01-05T11:00:00+03:00", "2027-01-05T12:00:00+03:00")
    next(event for event in state["planning"]["calendar"]["events"] if event["id"] == ids["fixed"])["category"] = "ignore"
    event("flex", "2027-01-08T22:15:00+03:00", "2027-01-08T23:15:00+03:00", "red", legacy="2")
    event("gray", "2027-01-09T15:00:00+03:00", "2027-01-09T15:30:00+03:00", "gray")
    event("gray-all-day", "2027-01-10T00:00:00", "2027-01-11T00:00:00", "gray", all_day=True)
    event("sage-series_20270111", "2027-01-11T00:00:00", "2027-01-12T00:00:00", "sage", all_day=True, recurring="sage-series")
    event("sage-series_20270118", "2027-01-18T00:00:00", "2027-01-19T00:00:00", "sage", all_day=True, recurring="sage-series", native=False)
    event("unknown", "2027-01-12T15:00:00+03:00", "2027-01-12T16:00:00+03:00", "unknown")
    event("teal", "2027-01-13T15:00:00+03:00", "2027-01-13T16:00:00+03:00", "teal")
    next(entry for entry in state["planning"]["result"]["days"] if entry["date"] == "2027-01-08").setdefault("prep_cubes", []).append({
        "id": "linked-prep", "date": "2027-01-08", "calendar_event_id": ids["flex"], "minutes": 25})
    # Same title/date is not an identity. This unmatched fixed provider is ignored.
    source["events"].append({"id": "wrong-provider", "summary": "Lesson", "status": "confirmed",
        "start": "2027-01-07T21:00:00+03:00", "end": "2027-01-07T22:00:00+03:00", "event_label_id": "red", "color_id": None})
    return state, source, ids




def test_calendar_source_exact_identity_roles_and_idempotent_import(color_planner):
    planner, source, ids = color_planner
    before = deepcopy(planner.store.read()["state"])
    result = command(planner, "calendar_source_import", {"source": source}, request_id="colors-once")
    saved = planner.store.read()
    assert {key: value for key, value in saved["state"].items() if key != "period_planner"} == before
    metadata = saved["state"]["period_planner"]["calendar_source"]
    assert metadata["matched_count"] == 7 and metadata["provider_only_offer_count"] == 1
    assert metadata["events"][ids["flex"]]["calendar_role"] == "flexible"  # custom label wins over legacy Sage
    assert metadata["events"][ids["fixed"]]["calendar_role"] == "fixed"  # calendar background is irrelevant
    assert next(event for event in result["events"] if event["id"] == ids["fixed"])["calendar_role"] == "fixed"  # newer colour policy overrides legacy ignore
    assert metadata["events"][ids["unknown"]]["calendar_role"] == "unknown"
    assert "calendar-webinar" in metadata["unmatched_native_ids"]  # title/time match did not join
    replay = planner.command({"request_id": "colors-once", "expected_revision": 1, "action": "calendar_source_import", "data": {"source": source}})
    assert replay == result and planner.store.read()["revision"] == saved["revision"]
    rows = result["cubes"]
    sage = [cube for cube in rows if cube.get("calendar_role") == "evergreen"]
    assert len(sage) == 1 and len(sage[0]["source_occurrences"]) == 2
    assert len([cube for cube in rows if cube.get("calendar_role") == "optional_day"]) == 2
    assert not {ids["gray"], ids["gray-all-day"], ids["sage-series_20270111"]} & {event["id"] for event in result["events"]}
    assert len([event for event in result["events"] if event["id"] in {"calendar-webinar", "live-webinar"}]) == 1
    assert next(event for event in result["events"] if event["id"] == "calendar-webinar")["conflict"]


def test_flexible_move_outside_work_window_and_color_freeze_transition(color_planner):
    planner, source, ids = color_planner
    command(planner, "calendar_source_import", {"source": source})
    command(planner, "calendar_event_move", {"event_id": ids["flex"], "date": "2027-02-02", "start": "23:30"})
    row = next(event for event in planner.model()["events"] if event["id"] == ids["flex"])
    assert (row["date"], row["start"], row["end"], row["end_date"], row["duration_minutes"]) == ("2027-02-02", "23:30", "00:30", "2027-02-03", 60)
    assert row["source_ids"] == [ids["flex"]] and row["movable"]
    updated = deepcopy(source)
    changed = next(event for event in updated["events"] if event["id"] == "flex")
    changed.update(event_label_id=None, color_id=None)
    command(planner, "calendar_source_import", {"source": updated})
    result = planner.model()
    row = next(event for event in result["events"] if event["id"] == ids["flex"])
    assert not row["movable"] and (row["date"], row["start"]) == ("2027-01-08", "22:15")
    assert planner.store.read()["state"]["period_planner"]["calendar_placement_archive"][0]["placement"]["date"] == "2027-02-02"
    assert any("локальный перенос" in warning for warning in result["warnings"])
    with pytest.raises(ValidationError, match="Закреплено"):
        command(planner, "calendar_event_move", {"event_id": ids["flex"], "date": "2027-01-09", "start": "14:00"})


def test_optional_unknown_duration_any_day_gray_date_and_single_busy_copy(color_planner):
    planner, source, ids = color_planner
    command(planner, "calendar_source_import", {"source": source})
    sage = next(cube for cube in planner.model()["cubes"] if cube.get("calendar_role") == "evergreen")
    gray = next(cube for cube in planner.model()["cubes"] if cube.get("source_event_id") == ids["gray"])
    assert sage["duration_unknown"] and sage["minutes"] == 0 and sage["available"] and sage["availability"] == "any_day"
    with pytest.raises(ValidationError, match="длительность"):
        command(planner, "cube_select", {"cube_id": sage["id"], "date": "2027-02-05"})
    command(planner, "calendar_offer_estimate", {"cube_id": sage["id"], "minutes": 90})
    command(planner, "cube_select", {"cube_id": sage["id"], "date": "2027-02-05", "start": "23:00"})
    selected = next(cube for cube in planner.model()["cubes"] if cube["id"] == sage["id"])
    assert selected["end"] == "00:30" and selected["end_date"] == "2027-02-06"
    assert not any(event["id"] == sage["source_event_id"] for event in planner.model()["events"])
    with pytest.raises(ValidationError, match="верните"):
        command(planner, "calendar_offer_estimate", {"cube_id": sage["id"], "minutes": 60})
    command(planner, "cube_return", {"cube_id": sage["id"]})
    assert next(cube for cube in planner.model()["cubes"] if cube["id"] == sage["id"])["selected_date"] is None
    with pytest.raises(ValidationError, match="предложенный"):
        command(planner, "cube_select", {"cube_id": gray["id"], "date": "2027-01-10"})
    with pytest.raises(ValidationError, match="Длительность указана"):
        command(planner, "calendar_offer_estimate", {"cube_id": gray["id"], "minutes": 45})
    command(planner, "cube_select", {"cube_id": gray["id"], "date": "2027-01-09", "start": "15:00"})
    assert next(cube for cube in planner.model()["cubes"] if cube["id"] == gray["id"])["minutes"] == 30


def test_calendar_move_and_optional_choice_obey_budget_conflicts(color_planner):
    planner, source, ids = color_planner
    command(planner, "calendar_source_import", {"source": source})
    before = planner.store.read()
    with pytest.raises(ValidationError, match="пересекается"):
        command(planner, "calendar_event_move", {"event_id": ids["flex"], "date": "2027-01-05", "start": "11:15"})
    assert planner.store.read() == before
    sage = next(cube for cube in planner.model()["cubes"] if cube.get("calendar_role") == "evergreen")
    command(planner, "calendar_offer_estimate", {"cube_id": sage["id"], "minutes": 300})
    before = planner.store.read()
    with pytest.raises(ValidationError, match="Дневной лимит"):
        command(planner, "cube_select", {"cube_id": sage["id"], "date": "2027-01-05"})
    assert planner.store.read() == before
    # Calendar-only freedom does not loosen ordinary Work task placement.
    with pytest.raises(ValidationError, match="горизонта"):
        command(planner, "cube_select", {"cube_id": "geometry", "date": "2027-02-05"})
    with pytest.raises(ValidationError, match="рабочее окно"):
        command(planner, "cube_select", {"cube_id": "geometry", "date": "2027-01-09", "start": "22:15"})


def test_calendar_event_context_persists_and_rejects_unknown_ids(color_planner):
    planner, source, ids = color_planner
    command(planner, "calendar_source_import", {"source": source})
    saved = set_context(planner, {"calendar_event_id": ids["flex"]})
    reopened = model.PeriodPlanner(type(planner.store)(planner.store.root))
    assert reopened.model()["context"]["calendar_event_id"] == ids["flex"]
    command(reopened, "calendar_event_move", {"event_id": ids["flex"], "date": "2027-02-02", "start": "22:15"}, revision=saved["revision"])
    assert reopened.model()["context"]["calendar_event_id"] == ids["flex"]
    before = reopened.store.read()
    with pytest.raises(ValidationError, match="calendar_event_id"):
        set_context(reopened, {"calendar_event_id": "missing"})
    assert reopened.store.read() == before
    changed = set_context(reopened, {"date": "2027-02-03"})
    assert changed["context"]["calendar_event_id"] is None


def test_flex_move_derives_unstarted_prep_but_keeps_selected_and_factual_dates(color_planner):
    planner, source, ids = color_planner
    command(planner, "calendar_source_import", {"source": source})
    command(planner, "calendar_event_move", {"event_id": ids["flex"], "date": "2027-02-02", "start": "22:15"})
    row = next(cube for cube in planner.model()["cubes"] if cube["id"] == "linked-prep")
    assert row["planned_date"] == "2027-02-02" and row["calendar_event_id"] == ids["flex"]
    command(planner, "cube_select", {"cube_id": row["id"], "date": "2027-02-02", "start": "18:00"})
    command(planner, "calendar_event_move", {"event_id": ids["flex"], "date": "2027-02-03", "start": "22:15"})
    selected = next(cube for cube in planner.model()["cubes"] if cube["id"] == "linked-prep")
    assert selected["selected_date"] == "2027-02-02"
    assert any("Подготовка" in warning and "сохранены" in warning for warning in planner.model()["warnings"])
    state = planner.store.read()["state"]
    state["planning"]["prep_progress"] = {"linked-prep": {"percent": 25, "actual_minutes": 5, "date": "2027-01-08"}}
    factual = model.read_model({"state": state, "revision": 0})
    row = next(cube for cube in factual["cubes"] if cube["id"] == "linked-prep")
    assert row["progress_percent"] == 25 and row["actual_minutes"] == 5


def test_missing_provider_rows_keep_prior_offers_and_known_provider_can_become_fixed(color_planner):
    planner, source, ids = color_planner
    command(planner, "calendar_source_import", {"source": source})
    saga = next(cube for cube in planner.model()["cubes"] if cube.get("calendar_role") == "evergreen")
    command(planner, "calendar_offer_estimate", {"cube_id": saga["id"], "minutes": 45})
    command(planner, "cube_select", {"cube_id": saga["id"], "date": "2027-02-02"})
    set_context(planner, {"cube_id": saga["id"]})
    missing = deepcopy(source)
    missing["events"] = [event for event in missing["events"] if not event["id"].startswith("sage-series")]
    command(planner, "calendar_source_import", {"source": missing})
    preserved = next(cube for cube in planner.model()["cubes"] if cube["id"] == saga["id"])
    assert preserved["selected_date"] == "2027-02-02" and len(preserved["source_occurrences"]) == 2
    assert planner.model()["context"]["cube_id"] == saga["id"]
    assert any("прежние данные и выбор сохранены" in warning for warning in planner.model()["warnings"])
    fixed = deepcopy(source)
    for event in fixed["events"]:
        if event["id"].startswith("sage-series"):
            event.update(event_label_id=None, color_id=None)
    command(planner, "calendar_source_import", {"source": fixed})
    result = planner.model()
    assert not any(cube["id"] == saga["id"] for cube in result["cubes"])
    assert result["context"]["cube_id"] is None
    reopened = model.PeriodPlanner(type(planner.store)(planner.store.root))
    assert set_context(reopened, {"calendar_event_id": ids["flex"]})["context"]["cube_id"] is None
    assert len([event for event in result["events"] if (event.get("provider_id") or "").startswith("sage-series")]) == 2
    assert any(row.get("cube_id") == saga["id"] for row in planner.store.read()["state"]["period_planner"]["calendar_placement_archive"])


def test_known_zero_preparation_remains_zero_occupancy_and_unknown_offer_still_requires_estimate():
    state = synthetic_state()
    next(entry for entry in state["planning"]["result"]["days"] if entry["date"] == "2027-01-05").setdefault("prep_cubes", []).append({
        "id": "zero-prep", "date": "2027-01-05", "minutes": 0})
    selected = model.apply_command(deepcopy(state), "cube_select", {"cube_id": "zero-prep", "date": "2027-01-05", "start": "14:00"})
    result = model.read_model({"state": selected, "revision": 0})
    row = next(cube for cube in result["cubes"] if cube["id"] == "zero-prep")
    assert row["minutes"] == 0 and calendar_rules._pieces(row, "2027-01-05", "selected_date") == []
    model.apply_command(selected, "cube_select", {"cube_id": "geometry", "date": "2027-01-05", "start": "14:00"})
    with pytest.raises(ValidationError, match="длительность"):
        calendar_rules.validate_slot(result, "2027-01-05", 0, "14:00")


def test_empty_work_reads_without_seeding_and_writes_have_clear_error(tmp_path):
    p = model.PeriodPlanner(WorkStore(tmp_path))
    before = p.snapshot()
    projected = p.model()
    assert projected['revision'] == 0 and not projected['initialized'] and not projected['planning_initialized']
    assert all(projected[key] == [] for key in ('projects', 'tasks', 'cubes', 'events', 'warnings'))
    assert all(value == 0 for value in projected['totals'].values())
    assert projected['context']['cube_id'] is None
    assert projected['provenance'] == {'source': 'work', 'training_copy': False, 'source_revision': 0}
    with pytest.raises(ValidationError, match='ещё не инициализирован'):
        command(p, 'ruler_create', {'ruler_id': 'custom', 'title': 'Custom goal'})
    with pytest.raises(ValidationError, match='ещё не инициализирован'):
        set_context(p, {'mode': 'month'})
    assert p.snapshot() == before


def test_existing_work_without_planning_is_readable_and_untouched(tmp_path):
    state = {'version': 1, 'projects': [{'id': 'existing', 'name': 'Existing'}],
             'items': [{'id': 'note', 'title': 'Preserved'}], 'boards': []}
    p = model.PeriodPlanner(synthetic_store(tmp_path, state))
    before = p.snapshot()
    assert p.model()['projects'][0]['id'] == 'existing'
    assert not p.model()['planning_initialized']
    with pytest.raises(ValidationError, match='ещё не инициализирован'):
        set_context(p, {'mode': 'month'})
    assert p.snapshot() == before


def test_context_requires_cas_and_receipt_with_durable_replay(planner):
    payload = {'request_id': 'context-once', 'expected_revision': planner.model()['revision'],
               'patch': {'date': '2027-01-05', 'cube_id': 'geometry'}}
    first = planner.set_context(payload)
    reopened = model.PeriodPlanner(WorkStore(planner.store.root))
    assert reopened.set_context(payload) == first
    assert reopened.model()['revision'] == first['revision']
    with pytest.raises(ConflictError):
        reopened.set_context({**payload, 'request_id': 'stale-context', 'patch': {'mode': 'month'}})
    with pytest.raises(ConflictError):
        reopened.set_context({**payload, 'patch': {'mode': 'month'}})
    with pytest.raises(ValidationError):
        reopened.set_context({'mode': 'month'})
    assert reopened.model()['context']['cube_id'] == 'geometry'


def test_native_deletion_and_unknown_metadata_survive_namespace_commit(tmp_path):
    state = synthetic_state()
    p = state['planning']
    p['cube_deletions'] = {'slides': {'cube_id': 'slides', 'request_id': 'owner-deleted',
        'deleted_at': '2026-10-26T09:00:00+00:00', 'blueprint_ref': None,
        'retired_minutes': 45, 'selected_date': None, 'remaining_epoch': 0}}
    planner = model.PeriodPlanner(synthetic_store(tmp_path, state))
    before = planner.snapshot()['state']
    result = command(planner, 'cube_rename', {'cube_id': 'geometry', 'title': 'Owner title'})
    assert next(c for c in result['cubes'] if c['id'] == 'slides')['status'] == 'cancelled'
    after = planner.snapshot()['state']
    assert {key: value for key, value in after.items() if key != 'period_planner'} == before
    assert after['unknown_future_field'] == state['unknown_future_field']
    with pytest.raises(ValidationError):
        command(planner, 'cube_select', {'cube_id': 'slides', 'date': '2027-01-05'})


def test_orphan_and_board_images_survive_both_write_paths(tmp_path):
    import base64
    store = WorkStore(tmp_path)
    images = []
    for key, raw in [('orphan', b'orphan-image'), ('attached', b'board-image')]:
        digest = hashlib.sha256(raw).hexdigest()
        store.upload_chunk(digest, {'index': 0, 'total': 1, 'content_base64': base64.b64encode(raw).decode()})
        images.append({'id': key, 'blob': digest, 'mime': 'image/png'})
    state = synthetic_state()
    state['boards'][0]['items'] = [{'id': 'board-picture', 'type': 'image', 'imageId': 'attached'}]
    raw = encoded(state).encode()
    digest = hashlib.sha256(raw).hexdigest()
    store.upload_chunk(digest, {'index': 0, 'total': 1, 'content_base64': base64.b64encode(raw).decode()})
    initial = store.commit({'request_id': 'initial', 'expected_revision': 0, 'state_blob': digest,
        'images': images, 'restore_import': True})
    p = model.PeriodPlanner(store)
    adoption_blob = upload_adoption(store, {})
    p.adopt({'request_id': 'image-safe-adopt', 'expected_revision': initial['revision'], 'source_blob': adoption_blob})
    command(p, 'cube_rename', {'cube_id': 'geometry', 'title': 'Picture-safe rename'})
    set_context(p, {'date': '2027-01-05'})
    assert p.snapshot()['images'] == initial['images']
    assert store.image('orphan') == (b'orphan-image', 'image/png')
    assert store.image('attached') == (b'board-image', 'image/png')
    assert p.snapshot()['state']['boards'] == state['boards']


def test_pending_native_delete_blocks_commands_and_context(planner, monkeypatch):
    from megamozg import work_delete
    from megamozg.work_delete import DeleteStateError
    calls = []
    monkeypatch.setattr(work_delete, 'recover', lambda store: calls.append(store))
    with planner.store.db() as db:
        version = planner.store._version(db)
        db.execute('INSERT INTO work_cube_deletes VALUES(?,?,?,?,?,?,?,?,?)',
            ('pending-native', encoded({'data': {'cube_id': 'geometry'}}), 'pending', None, None, None,
             version['state_blob'], version['images'], None))
    before = planner.store.read()
    with pytest.raises(DeleteStateError):
        command(planner, 'cube_rename', {'cube_id': 'geometry', 'title': 'Blocked'})
    with pytest.raises(DeleteStateError):
        set_context(planner, {'mode': 'month'})
    assert planner.store.read() == before
    assert len(calls) >= 2


def test_native_facts_after_overlay_merge_are_authoritative_and_protect_composition(planner):
    result = command(planner, 'cube_merge', {'cube_ids': ['geometry', 'slides'], 'title': 'Presentation'})
    merged = next(cube for cube in result['cubes'] if cube['id'].startswith('merged-'))
    native = planner.store.planning_operation({'request_id': 'native-progress',
        'expected_revision': result['revision'], 'action': 'cube_progress',
        'data': {'cube_id': 'geometry', 'percent': 25, 'actual_minutes': 12}})
    current = next(cube for cube in planner.model()['cubes'] if cube['id'] == merged['id'])
    assert current['actual_minutes'] == 12 and current['progress_percent'] > 0
    assert current['historical_facts'][0]['cube_id'] == 'geometry'
    assert current['status'] == 'started' and not current['can_delete']
    for action, data in [('cube_delete', {'cube_id': merged['id']}),
                         ('cube_return', {'cube_id': merged['id']}),
                         ('cube_merge', {'cube_ids': [merged['id'], 'other'], 'title': 'Unsafe'})]:
        with pytest.raises(ValidationError, match='факты'):
            command(planner, action, data)
    assert planner.snapshot() == native


def test_native_delete_after_overlay_merge_does_not_resurrect_source(planner):
    result = command(planner, 'cube_merge', {'cube_ids': ['geometry', 'slides'], 'title': 'Presentation'})
    merged = next(cube for cube in result['cubes'] if cube['id'].startswith('merged-'))
    planner.store.planning_operation({'request_id': 'native-delete', 'expected_revision': result['revision'],
        'action': 'cube_delete', 'data': {'cube_id': 'geometry'}})
    effective = planner.model()
    current = next(cube for cube in effective['cubes'] if cube['id'] == merged['id'])
    assert current['allocation_stale'] and not current['available'] and not current['can_delete']
    assert current['changed_source_ids'] == ['geometry']
    assert any('Состав исходных этапов' in warning for warning in effective['warnings'])
    before = planner.snapshot()
    with pytest.raises(ValidationError):
        command(planner, 'cube_select', {'cube_id': merged['id'], 'date': '2027-01-05'})
    assert planner.snapshot() == before


@pytest.mark.parametrize('action,data', [
    ('calendar_source_import', {'source': {'events': [], 'labels': []}}),
    ('course_source_import', {'source': []}),
    ('cube_select', {'cube_id': 'geometry', 'date': []}),
])
def test_malformed_source_commands_have_validation_errors_and_no_commit(planner, action, data):
    before = planner.snapshot()
    with pytest.raises(ValidationError):
        command(planner, action, data)
    assert planner.snapshot() == before


def test_transaction_boundary_rejects_accidental_native_writes(planner, monkeypatch):
    before = planner.snapshot()
    def wrong_target(state, action, data, library_notes=None):
        state['items'].append({'id': 'unwanted', 'title': 'Should not commit'})
        return state
    monkeypatch.setattr(model, 'apply_command', wrong_target)
    with pytest.raises(ValidationError, match='не может менять исходные'):
        command(planner, 'cube_rename', {'cube_id': 'geometry', 'title': 'Example'})
    assert planner.snapshot() == before


def upload_adoption(store, namespace, *, extra=None):
    import base64
    from megamozg.work_store import CHUNK_SIZE
    packet = {'schema': 'work-period-adopt-v1', 'namespace': namespace,
        'source_revision': 10, 'source_hash': 'a' * 64}
    if extra:
        packet.update(extra)
    raw = encoded(packet).encode()
    digest = hashlib.sha256(raw).hexdigest()
    parts = [raw[index:index+CHUNK_SIZE] for index in range(0, len(raw), CHUNK_SIZE)]
    for index, part in enumerate(parts):
        store.upload_chunk(digest, {'index': index, 'total': len(parts),
            'content_base64': base64.b64encode(part).decode()})
    return digest


def test_typed_adoption_namespace_only_cas_replay_and_native_facts(tmp_path):
    original = synthetic_state()
    proposed = model.apply_command(deepcopy(original), 'cube_merge',
        {'cube_ids': ['geometry', 'slides'], 'title': 'Presentation'})
    proposed = model.apply_command(proposed, 'period_set',
        {'task_id': 'a', 'start': '2027-01-05', 'end': '2027-01-10'})
    current = deepcopy(original)
    current['planning']['completed_cubes']['geometry'] = {'task_id': 'a', 'date': '2027-01-05',
        'planned_minutes': 60, 'percent': 25, 'actual_minutes': 12}
    current['items'].append({'id': 'new-native-item', 'title': 'Current owner work'})
    p = model.PeriodPlanner(synthetic_store(tmp_path, current))
    before = p.snapshot()
    digest = upload_adoption(p.store, proposed['period_planner'])
    payload = {'request_id': 'adopt-once', 'expected_revision': before['revision'], 'source_blob': digest}
    result = p.adopt(payload)
    saved = p.snapshot()
    assert {key: value for key, value in saved['state'].items() if key != 'period_planner'} == before['state']
    merged = next(cube for cube in result['cubes'] if cube['id'].startswith('merged-'))
    assert merged['actual_minutes'] == 12 and merged['status'] == 'started'
    assert saved['state']['period_planner']['adoption']['source_hash'] == 'a' * 64
    reopened = model.PeriodPlanner(WorkStore(tmp_path))
    assert reopened.adopt(payload) == result
    assert reopened.snapshot() == saved
    with pytest.raises(ConflictError):
        reopened.adopt({**payload, 'request_id': 'stale-adoption'})
    different = upload_adoption(p.store, {})
    with pytest.raises(ConflictError):
        reopened.adopt({**payload, 'source_blob': different})
    with pytest.raises(ValidationError, match='уже содержит изменения'):
        reopened.adopt({'request_id': 'replacement', 'expected_revision': saved['revision'], 'source_blob': different})
    assert reopened.snapshot() == saved


@pytest.mark.parametrize('namespace,extra', [
    ({'planning': {}}, None),
    ({'unknown': {}}, None),
    ({'cube_titles': {'unknown': 'Bad source'}}, None),
    ({'periods': {'unknown': {'start': '2027-01-05', 'end': '2027-01-06'}}}, None),
    ({'placements': {'geometry': {'date': None, 'start': '12:00'}}}, None),
    ({'context': {'__proto__': {}}}, None),
    ({}, {'planning': {}}),
])
def test_typed_adoption_rejects_invalid_namespace_atomically(planner, namespace, extra):
    digest = upload_adoption(planner.store, namespace, extra=extra)
    before = planner.snapshot()
    with pytest.raises(ValidationError):
        planner.adopt({'request_id': 'invalid-adoption', 'expected_revision': before['revision'], 'source_blob': digest})
    assert planner.snapshot() == before


def test_adoption_rejects_merged_volume_drift_from_current_native(tmp_path):
    state = synthetic_state()
    proposed = model.apply_command(deepcopy(state), 'cube_merge',
        {'cube_ids': ['geometry', 'slides'], 'title': 'Presentation'})
    state['planning']['constructor']['cubes']['geometry']['minutes'] = 65
    p = model.PeriodPlanner(synthetic_store(tmp_path, state))
    digest = upload_adoption(p.store, proposed['period_planner'])
    before = p.snapshot()
    with pytest.raises(ValidationError, match='объём'):
        p.adopt({'request_id': 'changed-volume', 'expected_revision': before['revision'], 'source_blob': digest})
    assert p.snapshot() == before


def test_adopted_catalog_and_retained_source_fallbacks_never_override_native(tmp_path):
    state = synthetic_state()
    native_geometry = deepcopy(state['planning']['constructor']['cubes']['geometry'])
    native_geometry['title'] = 'Stale title'
    retained = {**native_geometry, 'id': 'retained-source', 'task_id': 'a', 'minutes': 20, 'title': 'Retained source'}
    ext = {'course_catalog': {'items': [
        {'id': 'adopted-a', 'work_task_id': 'a', 'project_id': 'print', 'title': 'Reference old', 'owned_by_user': False},
        {'id': 'adopted-only', 'project_id': 'print', 'title': 'Adopted document', 'owned_by_user': False}]},
        'retained_cubes': {'geometry': native_geometry, 'retained-source': retained}}
    p = model.PeriodPlanner(synthetic_store(tmp_path, state))
    digest = upload_adoption(p.store, ext)
    result = p.adopt({'request_id': 'fallback', 'expected_revision': 1, 'source_blob': digest})
    assert next(cube for cube in result['cubes'] if cube['id'] == 'geometry')['title'] == 'Geometry nodes'
    assert next(task for task in result['tasks'] if task['id'] == 'a')['owned'] is True
    assert any(task['id'] == 'adopted-only' for project in result['projects'] for task in project['tasks'])
    assert next(cube for cube in result['cubes'] if cube['id'] == 'retained-source')['minutes'] == 20
    assert p.snapshot()['state']['planning'] == state['planning']


def test_native_day_specific_budgets_override_default_and_project_without_mutation():
    state = synthetic_state()
    state['planning']['settings']['day_capacity_overrides'] = {'2027-01-05': {'minutes': 400, 'reason': 'Owner', 'approved': True}}
    state['planning']['manual_days'] = {'2027-01-05': {'budget_minutes': 450}}
    before = deepcopy(state)
    projected = model.read_model({'state': state, 'revision': 0})
    assert projected['settings']['daily_limit_minutes'] == 300
    assert projected['settings']['daily_limit_minutes_by_date']['2027-01-05'] == 450
    calendar_rules.validate_slot(projected, '2027-01-05', 420, None)
    with pytest.raises(ValidationError, match='450'):
        calendar_rules.validate_slot(projected, '2027-01-05', 451, None)
    with pytest.raises(ValidationError, match='300'):
        calendar_rules.validate_slot(projected, '2027-01-06', 301, None)
    assert state == before


def test_exact_provider_alias_keeps_canonical_event_and_one_native_conflict():
    state, source, ids = color_source_state()
    state = calendar_rules.apply_import(state, source)
    ext = state['period_planner']
    native = state['planning']['calendar']['events']
    old = ids['flex']
    original = next(event for event in native if event['id'] == old)
    original['id'] = 'raw-google-provider-id'
    original.update(start='2027-01-07T21:00:00+03:00', end='2027-01-07T22:00:00+03:00')
    ext['calendar_source']['events'][old]['native_event_id'] = original['id']
    state['planning']['live_events'] = [{'id': 'live-native-link', 'title': 'One event', 'task_id': 'a',
        'calendar_event_id': original['id'], 'date': '2027-01-07', 'start': '19:00', 'duration_minutes': 180}]
    result = model.read_model({'state': state, 'revision': 0})
    rows = [row for row in result['events'] if row['id'] in {old, original['id'], 'live-native-link'}]
    assert len(rows) == 1 and rows[0]['id'] == old and rows[0]['conflict']
    assert {(part['start'], part['end']) for part in rows[0]['alternatives']} == {('19:00', '22:00'), ('21:00', '22:00')}
    refreshed = calendar_rules.apply_import(state, source)
    refreshed_record = refreshed['period_planner']['calendar_source']['events'][old]
    assert refreshed_record['id'] == old and refreshed_record['native_event_id'] == original['id']
    assert refreshed_record['calendar_role'] == 'flexible'
    assert old not in refreshed['period_planner']['calendar_source']['unmatched_native_ids']


def test_provider_alias_keeps_existing_native_optional_cube_facts():
    state, source, ids = color_source_state()
    state = calendar_rules.apply_import(state, source)
    record = state['period_planner']['calendar_source']['events'][ids['gray']]
    raw_id = 'raw-optional-provider'
    record['native_event_id'] = raw_id
    native_event = next(event for event in state['planning']['calendar']['events'] if event['id'] == ids['gray'])
    native_event['id'] = raw_id
    state['planning'] = wp.apply_action(state['planning'], 'constructor_cube', {'cube_id': 'native-optional',
        'kind': 'calendar', 'source_ref': raw_id, 'title': 'Existing optional', 'minutes': 60, 'planned_date': '2027-01-06'})
    state['planning']['constructor']['source_reports']['native-optional'] = {'percent': 25, 'actual_minutes': 12, 'date': '2027-01-06'}
    result = model.read_model({'state': state, 'revision': 0})
    offer = next(row for row in result['cubes'] if row.get('source_event_id') == ids['gray'])
    assert offer['actual_minutes'] == 12 and offer['progress_percent'] == 25 and offer['historical_facts']
    assert 'native-optional' not in {row['id'] for row in result['cubes']}


def test_overlay_cancel_then_native_delete_retires_scope_only_once(planner):
    command(planner, 'cube_delete', {'cube_id': 'geometry'})
    planner.store.planning_operation({'request_id': 'native-delete-overlap',
        'expected_revision': planner.model()['revision'], 'action': 'cube_delete', 'data': {'cube_id': 'geometry'}})
    task = next(row for row in planner.model()['tasks'] if row['id'] == 'a')
    assert task['remaining_minutes'] == 600 and task['cancelled_minutes'] == 60
    assert task['effective_remaining_minutes'] == 540


def test_effective_parent_scope_prevents_overselecting_superseded_forecasts():
    state = synthetic_state()
    state = model.apply_command(state, 'cube_delete', {'cube_id': 'geometry'})
    # An explicitly retained source cannot authorize more than the current
    # factual/cancelled task remainder, even if old proposals exceed that scope.
    large = deepcopy(state['planning']['constructor']['cubes']['other'])
    large.update(id='large-retained', minutes=550)
    state['period_planner']['retained_cubes'] = {'large-retained': large}
    with pytest.raises(ValidationError, match='действующий остаток'):
        model.apply_command(state, 'cube_select', {'cube_id': 'large-retained', 'date': '2027-01-05'})


def test_moving_existing_selection_does_not_add_parent_allocation_after_drift():
    state = synthetic_state()
    state = model.apply_command(state, 'cube_select', {'cube_id': 'geometry', 'date': '2027-01-05'})
    # Later native reports or scope edits may make a pre-existing selection
    # larger than today's remaining quota; moving that same ID adds no scope.
    state['planning']['day_outcomes'] = [{'task_id': 'a', 'date': '2027-01-04', 'remaining_minutes': 30}]
    moved = model.apply_command(state, 'cube_move', {'cube_id': 'geometry', 'date': '2027-01-06'})
    assert next(row for row in model.cube_rows(moved) if row['id'] == 'geometry')['selected_date'] == '2027-01-06'
    assert moved['planning'] == state['planning']


def test_effective_calendar_coverage_is_native_metadata_intersection():
    state, source, ids = color_source_state()
    source['coverage'] = {'start': '2026-12-01', 'end': '2027-03-01'}
    state = calendar_rules.apply_import(state, source)
    state['period_planner']['context'] = {'date': '2027-02-01'}
    before = deepcopy(state)
    result = model.read_model({'state': state, 'revision': 0})
    assert result['calendar_coverage']['metadata'] == source['coverage']
    assert result['calendar_coverage']['effective'] == {'start': '2027-01-04', 'end': '2027-01-17'}
    assert any('отсутствие событий не означает свободное время' in warning for warning in result['warnings'])
    assert state == before


def test_native_completion_after_overlay_cancellation_keeps_facts_and_only_unfinished_retirement(planner):
    command(planner, 'cube_delete', {'cube_id': 'geometry'})
    planner.store.planning_operation({'request_id': 'later-native-fact', 'expected_revision': planner.model()['revision'],
        'action': 'cube_progress', 'data': {'cube_id': 'geometry', 'percent': 25, 'actual_minutes': 12}})
    result = planner.model()
    task = next(row for row in result['tasks'] if row['id'] == 'a')
    cube = next(row for row in result['cubes'] if row['id'] == 'geometry')
    assert task['remaining_minutes'] == 585 and task['cancelled_minutes'] == 45
    assert task['effective_remaining_minutes'] == 540
    assert cube['actual_minutes'] == 12 and cube['progress_percent'] == 25
    assert cube['status'] == 'cancelled' and not cube['can_delete']
