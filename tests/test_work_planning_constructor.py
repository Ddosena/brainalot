"""Constructor CAS/accounting regressions on isolated synthetic Work stores."""
from copy import deepcopy

import pytest

from test_work_planning import op, planning, seed
from test_work_planning_day_builder import stable_builder_today
from megamozg.core import ConflictError, ValidationError, Store
from megamozg.work_planning import _remaining
from megamozg.work_planning import AWARD_VARIANTS, update_constructor_awards


DAY = "2027-01-04"
NEXT = "2027-01-05"


def setup(tmp_path, document=None):
    work = seed(tmp_path)
    op(work, "import", "import", {"planning": document or planning()})
    return work


def view(snapshot, key=DAY):
    return snapshot["state"]["planning"]["result"]["constructor"]["days"][key]


def cube(snapshot, cube_id="draft"):
    return next(c for c in snapshot["state"]["planning"]["result"]["constructor"]["cubes"] if c["id"] == cube_id)


def draft(work, cube_id="draft", **patch):
    return op(work, "draft-"+cube_id, "constructor_cube", {"cube_id": cube_id, "kind": "task", "task_id": "a", "minutes": 60, "title": "Practice", **patch})


def test_explicit_initialization_preserves_work_and_does_not_charge_proposals(tmp_path):
    work = setup(tmp_path)
    original = work.read()
    assert "constructor" not in original["state"]["planning"]
    assert view(original)["initialized"] is False
    assert work.read() == original
    initialized = op(work, "initialize", "constructor_day", {"date": DAY, "main_task_id": "a", "goal": "A result"})
    assert view(initialized)["initialized"] is True
    assert view(initialized)["total_work_minutes"] == 120  # lesson only, no prep proposal charge
    assert any(c["kind"] == "prep" for c in initialized["state"]["planning"]["result"]["constructor"]["cubes"])
    proposed = draft(work, planned_date=NEXT)
    assert "draft" not in view(proposed)["possible_cube_ids"]
    assert "draft" in view(proposed, NEXT)["possible_cube_ids"]
    assert cube(proposed)["selected_date"] is None
    assert proposed["state"]["items"] == initialized["state"]["items"]
    assert proposed["state"]["boards"] == original["state"]["boards"]
    assert proposed["state"]["unknown_future_field"] == {"keep": True}


def test_constructor_cas_receipt_and_rejected_mutation_are_atomic(tmp_path):
    work = setup(tmp_path)
    command = {"request_id": "one", "expected_revision": work.read()["revision"], "action": "constructor_cube",
               "data": {"cube_id": "draft", "kind": "task", "task_id": "a", "minutes": 60, "title": "One"}}
    result = work.planning_operation(command)
    assert work.planning_operation(command) == result
    with pytest.raises(ConflictError):
        work.planning_operation({**command, "data": {**command["data"], "minutes": 30}})
    with pytest.raises(ConflictError):
        work.planning_operation({**command, "request_id": "stale"})
    # Owner decision 2026-10-28: over-allocation grows the task and warns, never refuses.
    grown = op(work, "over-allocation", "constructor_cube", {"cube_id": "too-big", "kind": "task", "task_id": "b", "minutes": 240, "title": "Yes"})
    assert "too-big" in grown["state"]["planning"]["constructor"]["cubes"]
    assert any("вырос" in warning for warning in grown["warnings"])


def test_started_return_hide_restore_preserves_fact_credit_and_id_once(tmp_path):
    work = setup(tmp_path)
    draft(work, planned_date=DAY)
    op(work, "select", "constructor_select", {"cube_id": "draft", "date": DAY})
    progressed = op(work, "progress", "cube_progress", {"cube_id": "draft", "percent": 30, "actual_minutes": 17})
    remaining = _remaining(progressed["state"]["planning"])["a"]
    returned = op(work, "return", "constructor_return", {"cube_id": "draft"})
    assert cube(returned)["selected_date"] is None
    assert cube(returned)["actual_minutes"] == 17 and cube(returned)["progress_percent"] == 30
    assert view(returned)["actual_minutes"] == 17 and view(returned)["credited_minutes"] == 18
    assert _remaining(returned["state"]["planning"])["a"] == remaining
    assert "draft" in view(returned)["possible_cube_ids"]
    hidden = op(work, "hide", "constructor_hide", {"cube_id": "draft", "date": DAY, "hidden": True})
    assert "draft" in view(hidden)["hidden_cube_ids"] and "draft" not in view(hidden)["possible_cube_ids"]
    assert view(hidden)["actual_minutes"] == 17
    restored = op(work, "restore", "constructor_hide", {"cube_id": "draft", "date": DAY, "hidden": False})
    assert cube(restored)["selected_date"] is None
    selected = op(work, "reselect", "constructor_select", {"cube_id": "draft", "date": DAY})
    assert cube(selected)["id"] == "draft" and view(selected)["actual_minutes"] == 17
    before = work.read()
    with pytest.raises(ValidationError, match="другую дату"):
        op(work, "cross-date", "constructor_select", {"cube_id": "draft", "date": NEXT})
    assert work.read() == before


def test_hide_selected_started_cube_removes_slot_but_not_report(tmp_path):
    work = setup(tmp_path)
    draft(work, planned_date=DAY)
    op(work, "select", "constructor_select", {"cube_id": "draft", "date": DAY, "start": "14:00"})
    op(work, "progress", "cube_progress", {"cube_id": "draft", "percent": 10, "actual_minutes": 17})
    hidden = op(work, "hide", "constructor_hide", {"cube_id": "draft", "date": DAY, "hidden": True})
    assert cube(hidden)["start"] is None
    assert view(hidden)["actual_minutes"] == 17
    assert hidden["state"]["planning"]["completed_cubes"]["draft"]["date"] == DAY


def test_split_conserves_estimate_metadata_absolute_date_and_unique_allocation(tmp_path):
    work = setup(tmp_path)
    metadata = {"planned_date": NEXT, "comment": "Keep", "links": [{"id": "l", "title": "Doc", "url": "https://example.org/a"}],
                "groups": [{"id": "g", "title": "Reference", "links": [{"id": "gl", "title": "One", "url": "http://example.org/"}]}]}
    before = draft(work, **metadata)
    split = op(work, "split", "constructor_split", {"cube_id": "draft", "minutes": 25, "new_cube_id": "part2"})
    assert cube(split)["minutes"] == 25 and cube(split, "part2")["minutes"] == 35
    for field in metadata:
        assert cube(split, "part2")[field] == metadata[field]
    assert split["state"]["planning"]["tasks"] == before["state"]["planning"]["tasks"]
    selected = op(work, "select", "constructor_select", {"cube_id": "part2", "date": NEXT})
    assert sum(c["minutes"] for c in selected["state"]["planning"]["constructor"]["cubes"].values() if c["kind"] == "task") == 60
    assert cube(selected, "part2")["planned_date"] == NEXT
    with pytest.raises(ValidationError):
        op(work, "split-selected", "constructor_split", {"cube_id": "part2", "minutes": 15})


def test_timeline_snaps_no_overlap_survives_metadata_and_float(tmp_path):
    work = setup(tmp_path)
    draft(work)
    selected = op(work, "select", "constructor_select", {"cube_id": "draft", "date": DAY, "start": "11:00"})
    assert cube(selected)["start"] == "13:00"  # fixed lesson ends13:00, optional prep not charged
    draft(work, "second", minutes=30)
    second = op(work, "select2", "constructor_select", {"cube_id": "second", "date": DAY, "start": "13:00"})
    assert cube(second, "second")["start"] == "14:00"
    updated = op(work, "metadata", "constructor_cube", {"cube_id": "draft", "comment": "Kept slot"})
    assert cube(updated)["start"] == "13:00"
    floated = op(work, "float", "constructor_timeline", {"cube_id": "draft", "start": None})
    assert cube(floated)["selected_date"] == DAY and cube(floated)["start"] is None


def test_prep_optional_event_and_weekly_sources_charge_once_and_return_facts(tmp_path):
    document = planning()
    document["settings"]["weekly_review"] = {"enabled": True, "minutes": 60, "weekday": 6}
    document["calendar"]["events"].append({"id": "meeting", "occurrence_id": "stable-meeting", "title": "Optional", "category": "busy",
        "start": "2027-01-04T16:00:00+03:00", "end": "2027-01-04T17:00:00+03:00"})
    work = setup(tmp_path, document)
    initialized = op(work, "init", "constructor_day", {"date": DAY})
    all_cubes = initialized["state"]["planning"]["constructor"]["cubes"]
    prep = next(c for c in all_cubes.values() if c["kind"] == "prep")
    optional = next(c for c in all_cubes.values() if c.get("source_ref") == "stable-meeting")
    assert view(initialized)["total_work_minutes"] == 120
    selected = op(work, "select-prep", "constructor_select", {"cube_id": prep["id"], "date": DAY})
    assert view(selected)["total_work_minutes"] == 145
    moved = op(work, "optional", "constructor_select", {"cube_id": optional["id"], "date": DAY, "start": "14:00"})
    assert cube(moved, optional["id"])["start"] == "14:00"
    assert view(moved)["total_work_minutes"] == 205
    required = op(work, "required", "calendar_required", {"event_id": "stable-meeting", "required": True})
    assert view(required)["total_work_minutes"] == 205
    assert cube(required, optional["id"])["start"] is None
    assert required["state"]["planning"]["calendar"] == initialized["state"]["planning"]["calendar"]
    op(work, "prep-progress", "cube_progress", {"cube_id": prep["id"], "percent": 20, "actual_minutes": 17})
    returned = op(work, "prep-return", "constructor_return", {"cube_id": prep["id"]})
    assert view(returned)["actual_minutes"] == 17
    assert view(returned)["total_work_minutes"] == 197  # requiredevents180 + returned prep fact17


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///C:/a", "https:///nohost", "https://user:pass@example.org", "https://bad host/a"])
def test_unsafe_resources_rejected_but_drafts_saved(tmp_path, url):
    work = setup(tmp_path)
    before = draft(work)
    with pytest.raises(ValidationError):
        op(work, "unsafe", "constructor_cube", {"cube_id": "draft", "links": [{"id": "l", "title": "x", "url": url}]})
    assert work.read() == before
    saved = op(work, "draft-resource", "constructor_cube", {"cube_id": "draft", "resource_drafts": {"links": [{"id": "d", "title": "Draft", "url": url}], "groups": []}})
    assert cube(saved)["resource_drafts"]["links"][0]["url"] == url
    assert cube(saved)["links"] == []


def test_positions_dynamic_ids_durable_and_nonfinite_rejected(tmp_path):
    work = setup(tmp_path)
    positioned = op(work, "positions", "constructor_day", {"date": DAY, "positions": {"possible": {"virtual-candidate": {"x": 13.5, "y": 27, "angle": 45}}}})
    assert view(positioned)["positions"]["possible"]["virtual-candidate"]["angle"] == 45
    with pytest.raises(ValidationError):
        op(work, "bad-position", "constructor_day", {"date": DAY, "positions": {"selected": {"x": {"x": float("inf"), "y": 0, "angle": 0}}}})
    assert work.read() == positioned


def test_atomic_main_switch_preserves_started_placed_and_selected_sum(tmp_path):
    work = setup(tmp_path)
    op(work, "init", "constructor_day", {"date": NEXT, "main_task_id": "a"})
    for cube_id in ("started", "placed", "floating"):
        draft(work, cube_id, minutes=30, planned_date=NEXT)
        op(work, "select-"+cube_id, "constructor_select", {"cube_id": cube_id, "date": NEXT, **({"start": "14:00"} if cube_id == "placed" else {})})
    op(work, "progress", "cube_progress", {"cube_id": "started", "percent": 20, "actual_minutes": 17})
    switched = op(work, "switch", "constructor_day", {"date": NEXT, "main_task_id": "c", "replace_unstarted": True})
    assert cube(switched, "started")["selected_date"] == NEXT
    assert cube(switched, "placed")["start"] == "14:00"
    assert cube(switched, "floating")["selected_date"] is None
    selected = [c for c in switched["state"]["planning"]["result"]["constructor"]["cubes"] if c["selected_date"] == NEXT]
    assert sum(c["minutes"] for c in selected) == 90
    assert any(c.get("task_id") == "c" and c["minutes"] == 30 for c in selected)


def test_brainalot_source_validated_no_note_creation_and_single_allocation(tmp_path):
    work = setup(tmp_path)
    store = Store(tmp_path)
    note = store.capture("Real task", kind="task", due=DAY)
    before_notes = deepcopy(store.list_notes())
    selected = op(work, "brainalot", "constructor_select", {"cube_id": "library-task", "date": DAY,
        "cube": {"kind": "brainalot", "source_ref": note["id"], "minutes": 30, "title": note["title"]}})
    assert cube(selected, "library-task")["selected_date"] == DAY
    assert store.list_notes() == before_notes
    with pytest.raises(ValidationError):
        op(work, "duplicate-source", "constructor_cube", {"cube_id": "duplicate", "kind": "brainalot", "source_ref": note["id"], "minutes": 30, "title": "Duplicate"})
    with pytest.raises(ValidationError):
        op(work, "missing-source", "constructor_cube", {"cube_id": "missing", "kind": "brainalot", "source_ref": "missing", "minutes": 30, "title": "Missing"})


def test_legacy_placed_cube_details_and_prep_report_survive_initialization(tmp_path):
    work = setup(tmp_path)
    auto = next(c for c in work.read()["state"]["planning"]["result"]["cubes"] if c["date"] == NEXT)
    locked = op(work, "lock", "place_cube", {"cube_id": auto["id"], "date": NEXT, "start": auto["start"], "locked": True})
    op(work, "details", "describe_cubes", {"entries": [{"cube_id": auto["id"], "title": "Specific", "output": "Result", "steps": ["Do"]}]})
    prep_id = next(d for d in locked["state"]["planning"]["result"]["days"] if d["date"] == DAY)["prep_cubes"][0]["id"]
    op(work, "prep-fact", "prep_progress", {"prep_id": prep_id, "percent": 10, "actual_minutes": 17})
    initialized = op(work, "init-placed", "constructor_day", {"date": NEXT})
    assert cube(initialized, auto["id"])["start"] == auto["start"]
    assert cube(initialized, auto["id"])["title"] == "Specific"
    edited = op(work, "edit-specific", "constructor_cube", {"cube_id": auto["id"], "title": "New specific"})
    assert cube(edited, auto["id"])["title"] == "New specific"
    initialized_prep = op(work, "init-prep", "constructor_day", {"date": DAY})
    assert cube(initialized_prep, "constructor:"+prep_id)["selected_date"] == DAY
    assert view(initialized_prep)["actual_minutes"] == 17


def test_source_slot_resize_and_required_event_collision_rejected_atomically(tmp_path):
    work = setup(tmp_path)
    note = Store(tmp_path).capture("Task", kind="task", due=DAY)
    op(work, "brainalot", "constructor_select", {"cube_id": "lib", "date": DAY, "start": "13:00",
        "cube": {"kind": "brainalot", "source_ref": note["id"], "minutes": 30, "title": "Task"}})
    draft(work, minutes=30)
    before = op(work, "second", "constructor_select", {"cube_id": "draft", "date": DAY, "start": "13:30"})
    with pytest.raises(ValidationError, match="пересечение"):
        op(work, "too-long", "constructor_cube", {"cube_id": "lib", "minutes": 60})
    assert work.read() == before


def test_weekly_review_is_proposal_then_charged_exactly_once(tmp_path):
    document = planning()
    document["settings"]["weekly_review"] = {"enabled": True, "minutes": 60, "weekday": 6}
    work = setup(tmp_path, document)
    initial = work.read()
    review = next(r for r in initial["state"]["planning"]["result"]["weekly_reviews"] if r["date"])
    key = review["date"]
    initialized = op(work, "init-review", "constructor_day", {"date": key})
    source = next(c for c in initialized["state"]["planning"]["constructor"]["cubes"].values() if c["kind"] == "weekly_review")
    before = view(initialized, key)["total_work_minutes"]
    selected = op(work, "select-review", "constructor_select", {"cube_id": source["id"], "date": key})
    assert view(selected, key)["total_work_minutes"] == before+60
    again = op(work, "repeat-select", "constructor_select", {"cube_id": source["id"], "date": key})
    assert view(again, key)["total_work_minutes"] == before+60
    assert next(r for r in again["state"]["planning"]["result"]["weekly_reviews"] if r["id"] == review["id"])["status"] == "constructor_proposal"


def test_prep_zero_and_extended_constructor_duration_do_not_change_legacy_contract(tmp_path):
    work = setup(tmp_path)
    initialized = op(work, "init", "constructor_day", {"date": DAY})
    prep = next(c for c in initialized["state"]["planning"]["constructor"]["cubes"].values() if c["kind"] == "prep")
    zero = op(work, "prep0", "prep_set", {"prep_id": prep["source_ref"], "minutes": 0})
    assert cube(zero, prep["id"])["minutes"] == 0
    op(work, "energy8", "day_budget", {"date": NEXT, "minutes": 480})
    selected = op(work, "duration240", "constructor_select", {"cube_id": "long", "date": NEXT,
        "cube": {"kind": "task", "task_id": "a", "minutes": 240, "title": "Long"}})
    assert cube(selected, "long")["minutes"] == 240
    with pytest.raises(ValidationError):
        op(work, "legacy240", "day_cube_add", {"date": NEXT, "task_id": "a", "minutes": 240})


def test_scope_exemption_releases_only_future_reservations_preserves_estimate_fact_and_items(tmp_path):
    work = setup(tmp_path)
    draft(work, planned_date=DAY)
    op(work, "select", "constructor_select", {"cube_id": "draft", "date": DAY})
    op(work, "progress", "cube_progress", {"cube_id": "draft", "percent": 25, "actual_minutes": 17})
    draft(work, "future", planned_date=NEXT)
    before = op(work, "select-future", "constructor_select", {"cube_id": "future", "date": NEXT})
    command = {"task_ids": ["a"], "exempt": True, "reason": "Owner released module", "from_date": DAY}
    with pytest.raises(ValidationError, match="release_unstarted"):
        op(work, "no-release", "scope_exempt", command)
    assert work.read() == before
    saved = op(work, "exempt", "scope_exempt", {**command, "release_unstarted": True})
    plan = saved["state"]["planning"]
    prior_plan = before["state"]["planning"]
    assert plan["tasks"] == prior_plan["tasks"] and plan["task_segments"] == prior_plan["task_segments"]
    assert plan["completed_cubes"] == prior_plan["completed_cubes"]
    assert plan["day_outcomes"] == prior_plan["day_outcomes"]
    assert saved["state"]["items"] == before["state"]["items"]
    assert _remaining(plan)["a"] == 885
    task = next(t for t in plan["result"]["tasks"] if t["id"] == "a")
    assert task["remaining_minutes"] == 885 and task["active_remaining_minutes"] == 0 and task["scope_exempt"]
    assert not any(p["task_id"] == "a" for p in plan["result"]["task_pool"])
    assert cube(saved, "future")["selected_date"] is None
    assert cube(saved)["selected_date"] == DAY and view(saved)["actual_minutes"] == 17
    assert any(r["code"] == "scope_exempt_started_work_retained" for r in plan["result"]["risks"])
    assert not any(c["task_id"] == "a" and c["date"] >= NEXT for c in plan["result"]["cubes"])
    assert plan["history"][:-1] == prior_plan["history"]
    with pytest.raises(ValidationError, match="освобождена"):
        op(work, "reselect-exempt", "constructor_select", {"cube_id": "future", "date": NEXT})
    restored = op(work, "scope-restore", "scope_exempt", {**command, "exempt": False})
    assert not next(t for t in restored["state"]["planning"]["result"]["tasks"] if t["id"] == "a")["scope_exempt"]
    assert _remaining(restored["state"]["planning"])["a"] == 885


def test_scope_exemption_suppresses_dated_and_unknown_linked_live_demand(tmp_path):
    document = planning()
    document["live_events"] = [
        {"id": "live-a", "task_id": "a", "title": "Webinar A", "duration_minutes": 180, "date": NEXT, "start": "17:00"},
        {"id": "unknown-a", "task_id": "a", "title": "Later A", "duration_minutes": 180, "date": None, "start": None},
        {"id": "unknown-c", "task_id": "c", "title": "Keep final", "duration_minutes": 180, "date": None, "start": None},
    ]
    work = setup(tmp_path, document)
    before = work.read()
    saved = op(work, "exempt", "scope_exempt", {"task_ids": ["a"], "exempt": True, "reason": "Released", "from_date": DAY, "release_unstarted": True})
    plan = saved["state"]["planning"]
    assert plan["live_events"] == before["state"]["planning"]["live_events"]
    assert plan["result"]["unscheduled_live_minutes"] == 180
    assert all(live["status"] == "scope_exempt" for live in plan["result"]["live_events"] if live.get("task_id") == "a")
    after_day = next(d for d in plan["result"]["days"] if d["date"] == NEXT)
    old_day = next(d for d in before["state"]["planning"]["result"]["days"] if d["date"] == NEXT)
    assert old_day["fixed_minutes"]-after_day["fixed_minutes"] == 205
    assert not any(c["task_id"] == "a" for c in plan["result"]["cubes"])
    assert any(c["task_id"] != "a" for c in plan["result"]["cubes"])


def test_snap_to_grid_after_arbitrary_calendar_boundary(tmp_path):
    document = planning()
    document["calendar"]["events"][0]["end"] = "2027-01-04T12:32:00+03:00"
    work = setup(tmp_path, document)
    selected = op(work, "select", "constructor_select", {"cube_id": "boundary", "date": DAY, "start": "12:32",
        "cube": {"kind": "task", "task_id": "a", "minutes": 15, "title": "Grid"}})
    assert cube(selected, "boundary")["start"] == "12:35"


def test_legacy_add_cannot_overallocate_constructor_proposals(tmp_path):
    work = setup(tmp_path)
    draft(work, "all-b", task_id="b", minutes=120)
    # Owner decision 2026-10-28: an extra hour is accepted; the task volume grows with a warning.
    before = next(task for task in work.read()["state"]["planning"]["tasks"] if task["id"] == "b")["estimate_minutes"]["central"]
    added = op(work, "legacy-overflow", "day_cube_add", {"date": NEXT, "task_id": "b", "minutes": 60})
    after = next(task for task in added["state"]["planning"]["tasks"] if task["id"] == "b")["estimate_minutes"]["central"]
    assert after > before and added.get("warnings")


def test_optional_calendar_cannot_select_other_occurrence_date(tmp_path):
    document = planning()
    document["calendar"]["events"].append({"id": "optional", "title": "Optional", "category": "busy",
        "start": "2027-01-04T16:00:00+03:00", "end": "2027-01-04T17:00:00+03:00"})
    work = setup(tmp_path, document)
    initialized = op(work, "init", "constructor_day", {"date": DAY})
    optional = next(c for c in initialized["state"]["planning"]["constructor"]["cubes"].values() if c.get("source_ref") == "optional")
    with pytest.raises(ValidationError, match="исходной дате"):
        op(work, "wrong-date", "constructor_select", {"cube_id": optional["id"], "date": NEXT})
    assert work.read() == initialized


def test_outcome_zero_keeps_stale_candidates_editable_and_returnable(tmp_path):
    work = setup(tmp_path)
    draft(work, planned_date=DAY)
    op(work, "select", "constructor_select", {"cube_id": "draft", "date": DAY})
    outcome = op(work, "outcome", "day_outcome", {"task_id": "a", "date": DAY, "remaining_minutes": 0, "done": True})
    assert outcome["state"]["planning"]["completed_cubes"] == {}
    returned = op(work, "return", "constructor_return", {"cube_id": "draft"})
    assert cube(returned)["allocation_stale"] and cube(returned)["selected_date"] is None
    op(work, "hide", "constructor_hide", {"cube_id": "draft", "date": DAY, "hidden": True})
    op(work, "restore", "constructor_hide", {"cube_id": "draft", "date": DAY, "hidden": False})
    op(work, "metadata", "constructor_cube", {"cube_id": "draft", "comment": "Preserved", "links": [{"id": "l", "title": "Keep", "url": "https://example.org"}]})
    op(work, "goal", "constructor_day", {"date": DAY, "goal": "Next task", "positions": {"possible": {"draft": {"x": 1, "y": 2, "angle": 3}}}})
    before = work.read()
    with pytest.raises(ValidationError, match="устарело"):
        op(work, "stale-select", "constructor_select", {"cube_id": "draft", "date": DAY})
    assert work.read() == before


def test_goal_only_day_and_week_history_without_day_conversion_or_credit(tmp_path):
    work = setup(tmp_path)
    before = work.read()
    past = "2026-09-16"
    daily = op(work, "day-goal", "constructor_goal", {"scope": "day", "date": past, "title": "Past result", "percent": 100})
    weekly = op(work, "week-goal", "constructor_goal", {"scope": "week", "date": past, "title": "Past week", "percent": 70})
    plan = weekly["state"]["planning"]
    assert plan["constructor"]["days"][past] == {"initialized": False, "goal": "Past result", "goal_percent": 100}
    assert plan["constructor"]["weeks"] == {"2026-09-14": {"goal": "Past week", "goal_percent": 70}}
    goals = plan["result"]["constructor"]["goals"]
    assert {row["id"] for row in goals} == {"day:2026-09-16", "week:2026-09-14"}
    assert next(row for row in goals if row["scope"] == "day")["percent"] == 0  # no cubes, legacy manual value is not readiness
    for field in ("tasks", "task_segments", "completed_cubes", "day_outcomes", "locks", "history"):
        assert plan.get(field) == before["state"]["planning"].get(field)
    assert "manual_days" not in plan
    assert plan["result"]["days"] == before["state"]["planning"]["result"]["days"]
    assert weekly["state"]["items"] == before["state"]["items"]
    assert _remaining(plan) == _remaining(before["state"]["planning"])
    assert daily["state"]["planning"]["result"]["constructor"]["days"][DAY]["initialized"] is False
    replanned = op(work, "replan-with-history", "recalculate", {"from_date": DAY})
    assert replanned["state"]["planning"]["result"]["constructor"]["goals"] == goals
    proposed = draft(work)
    assert proposed["state"]["planning"]["result"]["constructor"]["goals"] == goals


def test_goal_percent_survives_init_main_change_recalc_and_import(tmp_path):
    work = setup(tmp_path)
    goal = op(work, "goal", "constructor_goal", {"scope": "day", "date": DAY, "title": "Independent result", "percent": 45})
    assert view(goal)["goal_percent"] == 0 and view(goal)["initialized"] is False
    assert goal["state"]["planning"]["constructor"]["days"][DAY]["goal_percent"] == 45
    assert "manual_days" not in goal["state"]["planning"]
    initialized = op(work, "init", "constructor_day", {"date": DAY, "main_task_id": "a"})
    assert view(initialized)["goal"] == "Independent result" and view(initialized)["goal_percent"] == 0
    renamed = op(work, "title", "constructor_day", {"date": DAY, "main_task_id": "c", "goal": "Compatible title"})
    assert view(renamed)["goal_percent"] == 0
    op(work, "week", "constructor_goal", {"scope": "week", "date": NEXT, "title": "Week result", "percent": 100})
    replanned = op(work, "replan", "recalculate", {"from_date": DAY})
    assert view(replanned)["goal_percent"] == 0
    imported = op(work, "reimport", "import", {"planning": planning()})
    assert view(imported)["goal"] == "Compatible title" and view(imported)["goal_percent"] == 0
    assert imported["state"]["planning"]["constructor"]["days"][DAY]["goal_percent"] == 45
    assert imported["state"]["planning"]["constructor"]["weeks"][DAY]["goal_percent"] == 100
    assert imported["state"]["planning"]["result"]["constructor"]["weeks"][DAY]["goal_percent"] == 0


def test_goal_100_does_not_complete_cube_and_hidden_trophy_keeps_report_date(tmp_path):
    work = setup(tmp_path)
    draft(work, planned_date=DAY)
    op(work, "select", "constructor_select", {"cube_id": "draft", "date": DAY})
    progressed = op(work, "fact", "cube_progress", {"cube_id": "draft", "percent": 40, "actual_minutes": 17})
    done_goal = op(work, "goal100", "constructor_goal", {"scope": "day", "date": DAY, "percent": 100})
    assert cube(done_goal)["progress_percent"] == 40
    assert done_goal["state"]["planning"]["completed_cubes"] == progressed["state"]["planning"]["completed_cubes"]
    assert _remaining(done_goal["state"]["planning"])["a"] == 876
    complete = op(work, "cube100", "cube_progress", {"cube_id": "draft", "percent": 100})
    hidden = op(work, "hide-trophy", "constructor_hide", {"cube_id": "draft", "date": DAY, "hidden": True})
    op(work, "new-day-goal", "constructor_goal", {"scope": "day", "date": NEXT, "percent": 20})
    after = work.read()
    assert cube(after)["report_date"] == DAY and cube(after)["progress_percent"] == 100
    assert cube(after)["selected_date"] is None and view(after, NEXT)["actual_minutes"] == 0
    assert view(after)["goal_percent"] == 100 and view(after, NEXT)["goal_percent"] == 0
    assert hidden["state"]["planning"]["completed_cubes"] == complete["state"]["planning"]["completed_cubes"]


@pytest.mark.parametrize("payload", [
    {"scope": "month", "date": DAY, "percent": 0},
    {"scope": "day", "date": "bad", "percent": 0},
    {"scope": "day", "date": DAY, "percent": 101},
    {"scope": "week", "date": DAY, "percent": True},
    {"scope": "day", "date": DAY, "title": "x"*1001},
    {"scope": "day", "date": DAY},
    {"scope": "day", "date": DAY, "percent": 50, "done": True},
])
def test_invalid_goal_does_not_change_revision(tmp_path, payload):
    work = setup(tmp_path)
    before = work.read()
    with pytest.raises(ValidationError):
        op(work, "invalid-goal", "constructor_goal", payload)
    assert work.read() == before


@pytest.mark.parametrize("kind", ["task", "prep", "brainalot", "calendar", "weekly_review"])
def test_constructor_energy_keeps_incomplete_selection_budget_for_every_source(tmp_path, kind):
    document = planning()
    document["calendar"]["events"] = [{"id": "lesson", "title": "Lesson", "category": "lesson",
        "start": "2027-01-04T11:00:00+03:00", "end": "2027-01-04T12:00:00+03:00"}]
    if kind == "calendar":
        document["calendar"]["events"].append({"id": "optional", "title": "Meeting", "category": "busy",
            "start": "2027-01-04T16:00:00+03:00", "end": "2027-01-04T17:00:00+03:00"})
    if kind == "weekly_review":
        document["settings"]["weekly_review"] = {"enabled": True, "minutes": 60, "weekday": 6}
    work = setup(tmp_path, document)
    initialized = op(work, "init", "constructor_day", {"date": DAY})
    if kind in {"prep", "calendar"}:
        source = next(c for c in initialized["state"]["planning"]["constructor"]["cubes"].values()
                      if c["kind"] == kind and (kind != "calendar" or c["source_ref"] == "optional"))
        cube_id = source["id"]
        if kind == "prep":
            op(work, "prep-duration", "constructor_cube", {"cube_id": cube_id, "minutes": 60})
    else:
        cube_id = "energy"
        candidate = {"cube_id": cube_id, "kind": kind, "title": "Energy", "minutes": 60, "planned_date": DAY}
        if kind == "task":
            candidate["task_id"] = "a"
        elif kind == "brainalot":
            candidate["source_ref"] = Store(tmp_path).capture("Real source", kind="task", due=DAY)["id"]
        else:
            candidate["source_ref"] = initialized["state"]["planning"]["result"]["weekly_reviews"][0]["id"]
        op(work, "make-energy", "constructor_cube", candidate)
    op(work, "select-energy", "constructor_select", {"cube_id": cube_id, "date": DAY})
    halfway = op(work, "half-energy", "cube_progress", {"cube_id": cube_id, "percent": 50, "actual_minutes": 20})
    assert view(halfway)["selected_minutes"] == 60
    assert view(halfway)["total_work_minutes"] == 120 and view(halfway)["available_minutes"] == 180
    assert view(halfway)["actual_minutes"] == 20 and view(halfway)["credited_minutes"] == 30
    hidden = op(work, "hide-energy", "constructor_hide", {"cube_id": cube_id, "date": DAY, "hidden": True})
    assert view(hidden)["total_work_minutes"] == 80 and view(hidden)["available_minutes"] == 220
    op(work, "restore-energy", "constructor_hide", {"cube_id": cube_id, "date": DAY, "hidden": False})
    selected = op(work, "reselect-energy", "constructor_select", {"cube_id": cube_id, "date": DAY})
    assert view(selected)["total_work_minutes"] == 120
    done = op(work, "done-energy", "cube_progress", {"cube_id": cube_id, "percent": 100, "actual_minutes": 20})
    assert view(done)["total_work_minutes"] == 80 and view(done)["available_minutes"] == 220
    overtime = op(work, "overtime-energy", "cube_progress", {"cube_id": cube_id, "percent": 50, "actual_minutes": 90})
    assert view(overtime)["total_work_minutes"] == 150 and view(overtime)["available_minutes"] == 150
    assert _remaining(overtime["state"]["planning"])["a"] == (870 if kind == "task" else 900)


def test_legacy_budget_policy_unchanged_until_explicit_constructor_initialization(tmp_path):
    document = planning()
    document["calendar"]["events"][0]["end"] = "2027-01-04T12:00:00+03:00"
    work = setup(tmp_path, document)
    selected = op(work, "legacy-add", "day_cube_add", {"task_id": "a", "date": DAY, "minutes": 60})
    cube_id = next(c["id"] for c in selected["state"]["planning"]["result"]["cubes"] if c["date"] == DAY)
    legacy = op(work, "legacy-half", "cube_progress", {"cube_id": cube_id, "percent": 50, "actual_minutes": 20})
    # Legacy keeps its existing prep25 + lesson60 + actual20 policy.
    assert next(d for d in legacy["state"]["planning"]["result"]["days"] if d["date"] == DAY)["total_work_minutes"] == 105
    initialized = op(work, "explicit-init", "constructor_day", {"date": DAY})
    # Prep is now merely a proposal; selected unfinished60 retains its budget.
    assert view(initialized)["total_work_minutes"] == 120
    assert initialized["state"]["planning"]["completed_cubes"] == legacy["state"]["planning"]["completed_cubes"]


def test_required_calendar_energy_fixed_once_and_actual_excess_retained(tmp_path):
    document = planning()
    document["calendar"]["events"][0]["end"] = "2027-01-04T12:00:00+03:00"
    document["calendar"]["events"].append({"id": "required", "title": "Meeting", "category": "work",
        "start": "2027-01-04T16:00:00+03:00", "end": "2027-01-04T17:00:00+03:00"})
    work = setup(tmp_path, document)
    initialized = op(work, "init", "constructor_day", {"date": DAY})
    source = next(c for c in initialized["state"]["planning"]["constructor"]["cubes"].values() if c.get("source_ref") == "required")
    op(work, "select-required", "constructor_select", {"cube_id": source["id"], "date": DAY})
    halfway = op(work, "half-required", "cube_progress", {"cube_id": source["id"], "percent": 50, "actual_minutes": 20})
    assert view(halfway)["fixed_minutes"] == 120 and view(halfway)["total_work_minutes"] == 120
    done = op(work, "done-required", "cube_progress", {"cube_id": source["id"], "percent": 100, "actual_minutes": 20})
    assert view(done)["total_work_minutes"] == 120  # fixed occurrence remains required
    overtime = op(work, "overtime-required", "cube_progress", {"cube_id": source["id"], "percent": 100, "actual_minutes": 90})
    assert view(overtime)["total_work_minutes"] == 150
    hidden = op(work, "hide-required", "constructor_hide", {"cube_id": source["id"], "date": DAY, "hidden": True})
    assert view(hidden)["total_work_minutes"] == 150 and view(hidden)["actual_minutes"] == 90


def goal_row(snapshot, scope="day", key=DAY):
    return next(row for row in snapshot["state"]["planning"]["result"]["constructor"]["goals"]
                if row["scope"] == scope and row["date"] == key)


def test_goals_derive_weighted_cube_progress_not_time_or_legacy_manual_percent(tmp_path):
    work = setup(tmp_path)
    for cube_id, minutes in (("sixty", 60), ("thirty", 30)):
        draft(work, cube_id, minutes=minutes, planned_date=DAY)
        op(work, "select-"+cube_id, "constructor_select", {"cube_id": cube_id, "date": DAY})
    partial = op(work, "partial", "cube_progress", {"cube_id": "sixty", "percent": 50, "actual_minutes": 5})
    assert goal_row(partial)["percent"] == 33
    assert goal_row(partial)["completed_minutes"] == 30 and goal_row(partial)["total_minutes"] == 90
    before = op(work, "one-done", "cube_progress", {"cube_id": "sixty", "percent": 100, "actual_minutes": 20})
    assert goal_row(before)["percent"] == 67
    assert goal_row(before)["unfinished_cube_ids"] == ["thirty"]
    assert goal_row(before)["completed_cube_ids"] == ["sixty"]
    legacy = op(work, "legacy-manual", "constructor_goal", {"scope": "day", "date": DAY, "title": "Finish result", "percent": 100})
    assert goal_row(legacy)["percent"] == 67 and goal_row(legacy)["status"] == "active"
    assert legacy["state"]["planning"]["constructor"]["days"][DAY]["goal_percent"] == 100
    assert legacy["state"]["planning"]["completed_cubes"] == before["state"]["planning"]["completed_cubes"]
    more_time = op(work, "more-time", "cube_progress", {"cube_id": "sixty", "percent": 100, "actual_minutes": 90})
    assert goal_row(more_time)["percent"] == 67
    hidden = op(work, "hide-done", "constructor_hide", {"cube_id": "sixty", "date": DAY, "hidden": True})
    assert goal_row(hidden)["percent"] == 67 and "sixty" in goal_row(hidden)["cube_ids"]
    assert hidden["state"]["planning"]["result"]["constructor"]["goal_cubes"]["sixty"]["report_date"] == DAY


def test_goal_close_cancel_and_reopen_snapshot_members_without_fake_completion(tmp_path):
    work = setup(tmp_path)
    for cube_id, minutes in (("done", 60), ("unfinished", 30)):
        draft(work, cube_id, minutes=minutes, planned_date=DAY)
        op(work, "select-"+cube_id, "constructor_select", {"cube_id": cube_id, "date": DAY})
    before = op(work, "progress-done", "cube_progress", {"cube_id": "done", "percent": 100, "actual_minutes": 20})
    closed = op(work, "close", "constructor_goal_status", {"scope": "day", "date": DAY, "status": "completed"})
    assert goal_row(closed)["status"] == "completed" and goal_row(closed)["percent"] == 67
    assert goal_row(closed)["display_status"] == "completed" and goal_row(closed)["unfinished_cube_ids"] == ["unfinished"]
    for field in ("tasks", "task_segments", "completed_cubes", "day_outcomes", "locks", "history"):
        assert closed["state"]["planning"].get(field) == before["state"]["planning"].get(field)
    assert closed["state"]["items"] == before["state"]["items"]
    draft(work, "later", minutes=30, planned_date=DAY)
    added = op(work, "select-later", "constructor_select", {"cube_id": "later", "date": DAY})
    assert goal_row(added)["cube_ids"] == ["done", "unfinished"]
    assert goal_row(added)["percent"] == 67
    completed_cube = op(work, "finish-member", "cube_progress", {"cube_id": "unfinished", "percent": 100, "actual_minutes": 10})
    assert goal_row(completed_cube)["percent"] == 100 and goal_row(completed_cube)["status"] == "completed"
    cancelled = op(work, "cancel", "constructor_goal_status", {"scope": "day", "date": DAY, "status": "cancelled"})
    assert goal_row(cancelled)["cube_ids"] == ["done", "unfinished"]
    reopened = op(work, "reopen", "constructor_goal_status", {"scope": "day", "date": DAY, "status": "active"})
    assert goal_row(reopened)["status"] == "active" and goal_row(reopened)["percent"] == 75
    assert goal_row(reopened)["unfinished_cube_ids"] == ["later"]
    assert cube(reopened, "later")["progress_percent"] == 0


def test_week_goal_uses_user_configuration_and_reports_without_auto_or_fixed_calendar(tmp_path):
    work = setup(tmp_path)
    initial = op(work, "init", "constructor_day", {"date": DAY})
    draft(work, "configured", minutes=60, planned_date=NEXT)
    draft(work, "undated", minutes=30)
    op(work, "week-title", "constructor_goal", {"scope": "week", "date": NEXT, "title": "Week"})
    current = work.read()
    assert goal_row(current, "week")["cube_ids"] == ["configured"]
    prep = next(c for c in initial["state"]["planning"]["constructor"]["cubes"].values() if c["kind"] == "prep")
    selected = op(work, "select-prep", "constructor_select", {"cube_id": prep["id"], "date": DAY})
    assert set(goal_row(selected, "week")["cube_ids"]) == {"configured", prep["id"]}
    assert all(not cube_id.startswith("constructor:event:") for cube_id in goal_row(selected, "week")["cube_ids"])
    closed = op(work, "close-week", "constructor_goal_status", {"scope": "week", "date": NEXT, "status": "cancelled"})
    assert goal_row(closed, "week")["date"] == DAY
    assert goal_row(closed, "week")["status"] == "cancelled"
    assert goal_row(closed, "week")["percent"] == 0


def test_active_achieved_goal_is_computed_without_write_and_blueprint_reference_survives_split(tmp_path):
    work = setup(tmp_path)
    draft(work, planned_date=DAY, blueprint_ref="original-auto-id")
    split = op(work, "split", "constructor_split", {"cube_id": "draft", "minutes": 30, "new_cube_id": "second"})
    assert cube(split, "second")["blueprint_ref"] == "original-auto-id"
    op(work, "select-first", "constructor_select", {"cube_id": "draft", "date": DAY})
    completed = op(work, "done-first", "cube_progress", {"cube_id": "draft", "percent": 100, "actual_minutes": 10})
    row = goal_row(completed)
    assert row["percent"] == 100 and row["status"] == "active" and row["display_status"] == "achieved"
    assert "goal_status" not in completed["state"]["planning"]["constructor"]["days"][DAY]
    assert work.read() == completed


@pytest.mark.parametrize("payload", [
    {"scope": "month", "date": DAY, "status": "completed"},
    {"scope": "day", "date": DAY, "status": "done"},
    {"scope": "day", "date": "bad", "status": "completed"},
    {"scope": "week", "date": DAY, "status": "cancelled", "percent": 100},
])
def test_invalid_goal_status_does_not_change_revision(tmp_path, payload):
    work = setup(tmp_path)
    before = work.read()
    with pytest.raises(ValidationError):
        op(work, "bad-status", "constructor_goal_status", payload)
    assert work.read() == before


def test_historical_legacy_report_outside_horizon_supports_goal_edit_and_status(tmp_path):
    document = planning()
    historical = {"task_id": "a", "date": "2026-09-16", "planned_minutes": 60, "percent": 100, "actual_minutes": 17}
    document["completed_cubes"]["historical-cube"] = deepcopy(historical)
    document["cube_details"] = {"historical-cube": {"title": "Past work", "output": "Preserved result", "steps": ["Real work"]}}
    work = setup(tmp_path, document)
    before = work.read()
    catalog = before["state"]["planning"]["result"]["constructor"]["goal_cubes"]
    assert catalog["historical-cube"]["title"] == "Past work"
    assert catalog["historical-cube"]["report_date"] == "2026-09-16"
    assert goal_row(before, key="2026-09-16")["title"] == ""
    assert goal_row(before, key="2026-09-16")["percent"] == 100
    assert "constructor" not in before["state"]["planning"]
    op(work, "history-title", "constructor_goal", {"scope": "day", "date": "2026-09-16", "title": "Reviewed history"})
    closed = op(work, "history-close", "constructor_goal_status", {"scope": "day", "date": "2026-09-16", "status": "completed"})
    assert goal_row(closed, key="2026-09-16")["cube_ids"] == ["historical-cube"]
    assert goal_row(closed, key="2026-09-16")["status"] == "completed"
    assert closed["state"]["planning"]["completed_cubes"]["historical-cube"] == historical
    assert closed["state"]["planning"]["tasks"] == before["state"]["planning"]["tasks"]
    assert closed["state"]["items"] == before["state"]["items"]
    assert "manual_days" not in closed["state"]["planning"]


def test_future_report_beyond_horizon_rejected_without_cas_change(tmp_path):
    work = setup(tmp_path)
    before = work.read()
    document = planning()
    document["completed_cubes"]["future-cube"] = {"task_id": "a", "date": "2027-02-01", "planned_minutes": 60, "percent": 100, "actual_minutes": 17}
    with pytest.raises(ValidationError, match="вне горизонта"):
        op(work, "future-report-import", "import", {"planning": document})
    assert work.read() == before


def test_near_complete_weighted_goal_never_rounds_to_false_victory(tmp_path):
    work = setup(tmp_path)
    for index in range(3):
        draft(work, "long-"+str(index), minutes=240, planned_date=DAY)
    draft(work, "tiny", minutes=5, planned_date=DAY)
    for index in range(3):
        op(work, "report-long-"+str(index), "cube_progress", {"cube_id": "long-"+str(index), "percent": 100, "actual_minutes": 1})
    almost = op(work, "tiny95", "cube_progress", {"cube_id": "tiny", "percent": 95, "actual_minutes": 1})
    assert goal_row(almost)["percent"] == 99.9
    assert goal_row(almost)["achieved"] is False and goal_row(almost)["display_status"] == "active"
    assert goal_row(almost)["unfinished_cube_ids"] == ["tiny"]
    done = op(work, "tiny100", "cube_progress", {"cube_id": "tiny", "percent": 100})
    assert goal_row(done)["percent"] == 100 and goal_row(done)["achieved"] is True


def test_legacy_prep_alias_counts_once_after_initialization_and_closed_snapshot_stays_valid(tmp_path):
    work = setup(tmp_path)
    initial = work.read()
    prep_id = next(d for d in initial["state"]["planning"]["result"]["days"] if d["date"] == DAY)["prep_cubes"][0]["id"]
    op(work, "legacy-prep", "prep_progress", {"prep_id": prep_id, "percent": 50, "actual_minutes": 17})
    closed = op(work, "legacy-close", "constructor_goal_status", {"scope": "day", "date": DAY, "status": "completed"})
    assert goal_row(closed)["cube_ids"] == [prep_id]
    assert goal_row(closed)["percent"] == 50
    initialized = op(work, "init-with-closed-prep", "constructor_day", {"date": DAY})
    row = goal_row(initialized)
    assert row["cube_ids"] == [prep_id] and row["percent"] == 50 and row["total_minutes"] == 25
    assert row["status"] == "completed"
    catalog = initialized["state"]["planning"]["result"]["constructor"]["goal_cubes"]
    assert catalog[prep_id]["alias_of"] == "constructor:"+prep_id
    reopened = op(work, "reopen-prep", "constructor_goal_status", {"scope": "day", "date": DAY, "status": "active"})
    assert goal_row(reopened)["cube_ids"] == ["constructor:"+prep_id]
    assert goal_row(reopened)["total_minutes"] == 25 and goal_row(reopened)["percent"] == 50
    assert reopened["state"]["planning"]["prep_progress"][prep_id]["actual_minutes"] == 17


def test_closed_goal_resolves_removed_legacy_lock_metadata_after_recalc_and_import(tmp_path):
    work = setup(tmp_path)
    op(work, "resource", "resource", {"task_id": "a", "resource": {"type": "url", "label": "Reference", "url": "https://example.org/reference"}})
    selected = op(work, "legacy-add", "day_cube_add", {"task_id": "a", "date": NEXT, "minutes": 60})
    cube_id = next(c["id"] for c in selected["state"]["planning"]["result"]["cubes"] if c["date"] == NEXT)
    op(work, "describe", "describe_cubes", {"entries": [{"cube_id": cube_id, "title": "Unfinished legacy work", "output": "Keep output", "steps": ["Step one"]}]})
    closed = op(work, "close", "constructor_goal_status", {"scope": "day", "date": NEXT, "status": "completed"})
    assert goal_row(closed, key=NEXT)["total_minutes"] == 60
    removed = op(work, "legacy-remove", "day_cube_remove", {"cube_id": cube_id})
    for snapshot in (removed,
                     op(work, "recalc", "recalculate", {"from_date": DAY}),
                     op(work, "reimport", "import", {"planning": planning()})):
        row = goal_row(snapshot, key=NEXT)
        assert row["cube_ids"] == [cube_id] and row["total_minutes"] == 60
        assert row["percent"] == 0 and row["unfinished_cube_ids"] == [cube_id]
        metadata = snapshot["state"]["planning"]["result"]["constructor"]["goal_cubes"][cube_id]
        assert metadata["title"] == "Unfinished legacy work"
        assert metadata["output"] == "Keep output" and metadata["steps"] == ["Step one"]
        assert metadata["resources"][0]["url"] == "https://example.org/reference"
        assert metadata["selected_date"] is None and metadata["progress_percent"] == 0
    # Recreate the same stable cube through the constructor; real later reports
    # override the closure fallback while its original descriptive data remains.
    op(work, "recreate", "constructor_select", {"cube_id": cube_id, "date": NEXT,
        "cube": {"kind": "task", "task_id": "a", "minutes": 60, "title": "Current live title"}})
    reported = op(work, "real-later-report", "cube_progress", {"cube_id": cube_id, "percent": 100, "actual_minutes": 20})
    assert goal_row(reported, key=NEXT)["percent"] == 100
    assert goal_row(reported, key=NEXT)["total_minutes"] == 60
    assert reported["state"]["planning"]["result"]["constructor"]["goal_cubes"][cube_id]["actual_minutes"] == 20
    reopened = op(work, "reopen", "constructor_goal_status", {"scope": "day", "date": NEXT, "status": "active"})
    assert cube_id in reopened["state"]["planning"]["constructor"]["days"][NEXT]["closed_cube_snapshots"]


def test_award_bags_use_each_variant_once_per_cycle_with_independent_kinds(tmp_path):
    work = setup(tmp_path)
    plan = deepcopy(work.read()["state"]["planning"])
    plan["constructor"] = {"version": 1, "cubes": {}, "days": {}, "event_required": {}, "source_reports": {}}
    from datetime import date, timedelta
    for index in range(30):
        key = (date(2026, 1, 1)+timedelta(days=index)).isoformat()
        plan["constructor"]["days"][key] = {"initialized": False, "goal": "Done", "goal_status": "completed"}
        plan["completed_cubes"][f"award-cube-{index:02d}"] = {"task_id": "a", "date": DAY, "planned_minutes": 5, "percent": 100, "actual_minutes": 1}
    update_constructor_awards(plan, None, backfill=True)
    awards = plan["constructor"]["awards"]
    # Two derived day/week rows also qualify from the reports. The first30
    # explicit historical goal IDs sort first and occupy exactly two cycles.
    for kind in ("goals", "cubes"):
        variants = list(awards[kind].values())[:30]
        assert len(variants) == 30
        assert set(variants[:15]) == set(AWARD_VARIANTS)
        assert set(variants[15:]) == set(AWARD_VARIANTS)
        assert variants[14] != variants[15]
    assert awards["decks"]["cubes"]["cycle"] == 2 and awards["decks"]["cubes"]["remaining"] == []
    assert len(awards["decks"]["goals"]["remaining"]) == 13
    assert awards["decks"]["goals"] is not awards["decks"]["cubes"]


def test_award_assignment_is_durable_across_receipt_drop_reload_recalc_and_import(tmp_path):
    work = setup(tmp_path)
    draft(work, planned_date=DAY)
    op(work, "select", "constructor_select", {"cube_id": "draft", "date": DAY})
    before = op(work, "95", "cube_progress", {"cube_id": "draft", "percent": 95, "actual_minutes": 17})
    assert cube(before)["award_variant"] is None
    command = {"request_id": "complete-with-award", "expected_revision": before["revision"], "action": "cube_progress",
               "data": {"cube_id": "draft", "percent": 100, "actual_minutes": 20}}
    awarded = work.planning_operation(command)
    assert work.planning_operation(command) == awarded
    variant = cube(awarded)["award_variant"]
    assert variant in AWARD_VARIANTS
    awards = deepcopy(awarded["state"]["planning"]["constructor"]["awards"])
    assert work.read() == awarded
    assert type(work)(tmp_path).read() == awarded
    lowered = op(work, "lower-readiness", "cube_progress", {"cube_id": "draft", "percent": 95})
    assert cube(lowered)["award_variant"] == variant
    assert lowered["state"]["planning"]["constructor"]["awards"] == awards
    replan = op(work, "award-recalc", "recalculate", {"from_date": DAY})
    assert replan["state"]["planning"]["constructor"]["awards"] == awards
    imported = op(work, "award-import", "import", {"planning": planning()})
    assert cube(imported)["award_variant"] == variant
    assert imported["state"]["planning"]["constructor"]["awards"] == awards
    with pytest.raises(ConflictError):
        work.planning_operation({**command, "request_id": "stale-award"})
    assert work.read() == imported


def test_explicit_completed_goals_award_at80_cancelled_and95_do_not(tmp_path):
    work = setup(tmp_path)
    draft(work, planned_date=DAY)
    partial = op(work, "partial", "cube_progress", {"cube_id": "draft", "percent": 80, "actual_minutes": 17})
    assert goal_row(partial)["award_variant"] is None and cube(partial)["award_variant"] is None
    cancelled = op(work, "cancel-active", "constructor_goal_status", {"scope": "day", "date": DAY, "status": "cancelled"})
    assert goal_row(cancelled)["award_variant"] is None
    op(work, "reopen", "constructor_goal_status", {"scope": "day", "date": DAY, "status": "active"})
    completed = op(work, "explicit-day-done", "constructor_goal_status", {"scope": "day", "date": DAY, "status": "completed"})
    assert goal_row(completed)["percent"] == 80 and goal_row(completed)["award_variant"] in AWARD_VARIANTS
    week_done = op(work, "explicit-week-done", "constructor_goal_status", {"scope": "week", "date": DAY, "status": "completed"})
    assert goal_row(week_done, "week")["percent"] == 80 and goal_row(week_done, "week")["award_variant"] in AWARD_VARIANTS
    awards = deepcopy(week_done["state"]["planning"]["constructor"]["awards"])
    cancelled_after = op(work, "cancel-awarded", "constructor_goal_status", {"scope": "day", "date": DAY, "status": "cancelled"})
    assert cancelled_after["state"]["planning"]["constructor"]["awards"] == awards
    assert goal_row(cancelled_after)["status"] == "cancelled"
    assert goal_row(cancelled_after)["award_variant"] == goal_row(completed)["award_variant"]
    assert cancelled_after["state"]["planning"]["completed_cubes"] == partial["state"]["planning"]["completed_cubes"]


def test_existing_eligible_awards_backfill_only_explicit_recalc_without_day_conversion(tmp_path):
    document = planning()
    document["completed_cubes"]["historical"] = {"task_id": "a", "date": "2026-09-16", "planned_minutes": 60, "percent": 100, "actual_minutes": 17}
    work = setup(tmp_path, document)
    before = work.read()
    assert "constructor" not in before["state"]["planning"]
    assert goal_row(before, key="2026-09-16")["award_variant"] is None
    assert work.read() == before
    replanned = op(work, "backfill", "recalculate", {"from_date": DAY})
    ctor = replanned["state"]["planning"]["constructor"]
    assert ctor["days"] == {} and "manual_days" not in replanned["state"]["planning"]
    assert ctor["awards"]["cubes"]["historical"] in AWARD_VARIANTS
    assert len(ctor["awards"]["goals"]) == 2  # historical day and week
    assert replanned["state"]["planning"]["completed_cubes"] == before["state"]["planning"]["completed_cubes"]
    assert _remaining(replanned["state"]["planning"]) == _remaining(before["state"]["planning"])


def test_prep_alias_preserves_one_award_when_constructor_wrapper_is_created(tmp_path):
    work = setup(tmp_path)
    before = work.read()
    prep_id = next(d for d in before["state"]["planning"]["result"]["days"] if d["date"] == DAY)["prep_cubes"][0]["id"]
    completed = op(work, "legacy-prep-done", "prep_progress", {"prep_id": prep_id, "percent": 100, "actual_minutes": 17})
    awards = completed["state"]["planning"]["constructor"]["awards"]
    assert len(awards["cubes"]) == 1
    variant = awards["cubes"][prep_id]
    deck = deepcopy(awards["decks"]["cubes"])
    op(work, "close-prep", "constructor_goal_status", {"scope": "day", "date": DAY, "status": "completed"})
    initialized = op(work, "init-prep", "constructor_day", {"date": DAY})
    after_awards = initialized["state"]["planning"]["constructor"]["awards"]
    assert after_awards["cubes"] == {"constructor:"+prep_id: variant}
    assert after_awards["decks"]["cubes"] == deck
    catalog = initialized["state"]["planning"]["result"]["constructor"]["goal_cubes"]
    assert catalog[prep_id]["award_variant"] == variant
    assert catalog["constructor:"+prep_id]["award_variant"] == variant
    assert cube(initialized, "constructor:"+prep_id)["award_variant"] == variant


@pytest.mark.parametrize("defect", ["variant", "repeat", "last", "cycle", "missing_last"])
def test_invalid_awards_deck_import_rejected_without_cas_change(tmp_path, defect):
    work = setup(tmp_path)
    before = work.read()
    document = planning()
    awards = {"version": 1, "goals": {}, "cubes": {}, "decks": {
        kind: {"remaining": [], "last": None, "cycle": 0} for kind in ("goals", "cubes")}}
    if defect == "variant":
        awards["cubes"]["any"] = "unknown-variant"
    elif defect == "repeat":
        awards["decks"]["cubes"]["remaining"] = ["enamel", "enamel"]
    elif defect == "last":
        awards["decks"]["goals"]["last"] = "unknown-variant"
    elif defect == "missing_last":
        del awards["decks"]["goals"]["last"]
    else:
        awards["decks"]["goals"]["cycle"] = True
    document["constructor"] = {"version": 1, "cubes": {}, "days": {}, "event_required": {}, "source_reports": {}, "awards": awards}
    with pytest.raises(ValidationError):
        op(work, "bad-awards", "import", {"planning": document})
    assert work.read() == before
