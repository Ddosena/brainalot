"""Synthetic cube composition/delete tests; no owner or external service access."""
from copy import deepcopy

import pytest

from test_work_period import synthetic_state, color_source_state, planner, command, set_context
from megamozg.work_period import calendar_rules
from megamozg.work_period import cube_ops
from megamozg.work_period import model
from megamozg.core import ValidationError
from megamozg import work_planning as wp


def read(state):
    return model.read_model({"state": state, "revision": 0})


def cube(state, cube_id):
    return next(row for row in read(state)["cubes"] if row["id"] == cube_id)


def merged(state):
    rows = [row for row in read(state)["cubes"] if row["id"].startswith("merged-")]
    assert len(rows) == 1
    return rows[0]


def native(state):
    return {key: value for key, value in state.items() if key != "period_planner"}


def merge(state, ids=("geometry", "slides"), title="Presentation"):
    return cube_ops.merge(state, {"cube_ids": list(ids), "title": title}, read(state), model._place)


@pytest.mark.parametrize("start", [None, "14:00"])
def test_selected_target_keeps_its_day_time_and_does_not_double_charge_sources(start):
    state = synthetic_state()
    state["planning"]["settings"]["day_limit_minutes"] = 110
    state["period_planner"] = {"placements": {"geometry": {"date": "2027-01-05", "start": start},
        "slides": {"date": "2027-01-05", "start": "16:00"}}}
    before = deepcopy(state)
    result = merge(state)
    row = merged(result)
    assert (row["selected_date"], row["start"], row["minutes"]) == ("2027-01-05", start, 105)
    assert result["period_planner"]["placements"][row["id"]] == {"date": "2027-01-05", "start": start}
    assert row["end"] == ("15:45" if start else None)
    assert state == before and native(result) == native(before)
    archive = result["period_planner"]["merges"][row["id"]]
    assert archive["prior_placements"]["slides"] == {"date": "2027-01-05", "start": "16:00"}


def test_selected_target_with_pool_source_and_pool_target_with_selected_source():
    state = synthetic_state()
    state["period_planner"] = {"placements": {"geometry": {"date": "2027-01-05", "start": "14:00"}}}
    chosen = merged(merge(state))
    assert (chosen["selected_date"], chosen["start"]) == ("2027-01-05", "14:00")
    pooled_state = merge(state, ("slides", "geometry"))
    pooled = merged(pooled_state)
    assert pooled["selected_date"] is None and pooled["start"] is None
    assert all(pooled_state["period_planner"]["placements"][cid] == {"date": None, "start": None}
        for cid in ("geometry", "slides", pooled["id"]))


def test_mixed_segments_and_nested_merge_keep_original_volume_sources_and_period():
    state = synthetic_state()
    manual = state["planning"]["constructor"]["cubes"]
    manual["geometry"].update(segment_id="prepare", planned_date="2027-01-04")
    manual["slides"].update(segment_id="produce", planned_date="2027-01-06")
    manual["other"].update(segment_id="prepare", planned_date="2027-01-09")
    state["period_planner"] = {"cube_titles": {"geometry": "Named by owner"},
        "context": {"cube_id": "geometry", "task_id": "a", "mode": "month", "date": "2027-01-05"}}
    initial = read(state)
    first_state = merge(state)
    first = merged(first_state)
    assert first["segment_id"] is None and first["planned_date"] is None
    assert first["source_segments"] == [
        {"task_id": "a", "segment_id": "prepare", "minutes": 60, "source_ids": ["geometry"]},
        {"task_id": "a", "segment_id": "produce", "minutes": 45, "source_ids": ["slides"]}]
    result = merge(first_state, (first["id"], "other"), "All slides")
    final = merged(result)
    assert final["minutes"] == final["source_minutes"] == 135
    assert final["source_ids"] == ["geometry", "slides", "other"]
    assert final["source_segments"] == [
        {"task_id": "a", "segment_id": "prepare", "minutes": 90, "source_ids": ["geometry", "other"]},
        {"task_id": "a", "segment_id": "produce", "minutes": 45, "source_ids": ["slides"]}]
    assert not {"geometry", "slides", "other", first["id"]} & {row["id"] for row in read(result)["cubes"]}
    assert sum(row["minutes"] for row in initial["cubes"]) == sum(row["minutes"] for row in read(result)["cubes"])
    assert result["period_planner"]["cube_titles"]["geometry"] == "Named by owner"
    assert result["period_planner"]["context"] == {**state["period_planner"]["context"], "cube_id": None}
    archive = result["period_planner"]["merges"][first["id"]]
    assert archive["sources"][0]["title"] == "Named by owner"
    assert archive["sources"][0]["output"] == "Geometry nodes output"
    assert native(result) == native(state)
    assert [(t["start"], t["end"]) for t in read(result)["tasks"]] == [(t["start"], t["end"]) for t in initial["tasks"]]


@pytest.mark.parametrize("problem", ["collision"])
def test_selected_merge_failure_is_atomic_without_moving_other_blocks(problem):
    state = synthetic_state()
    state["period_planner"] = {"placements": {"geometry": {"date": "2027-01-05", "start": "14:00"}}}
    if problem == "collision":
        state["period_planner"]["placements"]["other"] = {"date": "2027-01-05", "start": "15:15"}
        reason = "пересекается"
    else:
        state["planning"]["settings"]["day_limit_minutes"] = 100
        reason = "лимит"
    before = deepcopy(state)
    with pytest.raises(ValidationError, match=reason):
        merge(state)
    assert state == before


@pytest.mark.parametrize("fact", ["progress", "actual", "history", "started"])
def test_fact_rows_cannot_be_deleted_or_merged_and_source_is_unchanged(fact):
    state = synthetic_state()
    current = read(state)
    row = next(row for row in current["cubes"] if row["id"] == "geometry")
    if fact == "progress":
        row["progress_percent"] = 25
    elif fact == "actual":
        row["actual_minutes"] = 12
    elif fact == "history":
        row["historical_facts"] = [{"cube_id": "legacy", "percent": 25, "actual_minutes": 12}]
    else:
        row["status"] = "started"
    before = deepcopy(state)
    for operation, payload in [(cube_ops.delete, {"cube_id": "geometry"}),
            (cube_ops.merge, {"cube_ids": ["geometry", "slides"], "title": "Invalid"})]:
        with pytest.raises(ValidationError, match="факты"):
            operation(state, payload, current, *([model._place] if operation == cube_ops.merge else []))
    assert state == before


def test_delete_selected_cube_cancels_scope_preserves_parent_siblings_facts_and_replay(planner):
    command(planner, "cube_select", {"cube_id": "geometry", "date": "2027-01-05", "start": "14:00"})
    set_context(planner, {"cube_id": "geometry", "task_id": "a", "date": "2027-01-05"})
    before = planner.store.read()
    payload = {"request_id": "delete-selected-once", "expected_revision": before["revision"],
        "action": "cube_delete", "data": {"cube_id": "geometry"}}
    deleted = planner.command(payload)
    task = next(task for task in deleted["tasks"] if task["id"] == "a")
    assert (task["remaining_minutes"], task["effective_remaining_minutes"], task["progress_percent"], task["cancelled_minutes"]) == (600, 540, 0, 60)
    removed = next(row for row in deleted["cubes"] if row["id"] == "geometry")
    assert (removed["status"], removed["selected_date"], removed["start"], removed["progress_percent"]) == ("cancelled", None, None, 0)
    assert {"slides", "other"} <= {row["id"] for row in deleted["cubes"] if row["available"]}
    after = planner.store.read()
    archive = after["state"]["period_planner"]["cancellations"]["geometry"]
    assert archive["reason"] == cube_ops.DELETE_REASON and archive["minutes"] == 60
    assert archive["source_ids"] == ["geometry"]
    assert archive["source"]["output"] == "Geometry nodes output"
    assert archive["prior_placement"] == {"date": "2027-01-05", "start": "14:00"}
    assert archive["prior_placement_override"] == archive["prior_placement"]
    assert native(after["state"]) == native(before["state"])
    assert deleted["context"]["cube_id"] is None and deleted["context"]["task_id"] == "a"
    assert planner.command(payload) == deleted and planner.store.read() == after
    with pytest.raises(ValidationError, match="уже удалён"):
        command(planner, "cube_delete", {"cube_id": "geometry"})
    assert planner.store.read() == after


@pytest.mark.parametrize("kind", ["prep", "weekly_review", "calendar_optional"])
def test_delete_auxiliary_cube_keeps_original_sources_and_calendar(kind):
    if kind == "calendar_optional":
        state, source, _ = color_source_state()
        state = calendar_rules.apply_import(state, source)
    else:
        state = synthetic_state()
        if kind == "weekly_review":
            state["planning"]["result"].setdefault("weekly_reviews", []).append({"id": "review-qa", "date": "2027-01-10", "minutes": 60})
    candidates = model.cube_rows(state) if kind == "prep" else read(state)["cubes"]
    row = next(row for row in candidates if row["kind"] == kind)
    state.setdefault("period_planner", {}).setdefault("placements", {})[row["id"]] = {"date": row["planned_date"], "start": None}
    before = deepcopy(state)
    result = cube_ops.delete(state, {"cube_id": row["id"]}, read(state))
    cancelled = cube(result, row["id"])
    assert cancelled["status"] == "cancelled" and not cancelled["available"] and cancelled["selected_date"] is None
    assert native(result) == native(before) and state == before
    assert read(result)["events"] == read(before)["events"]
    assert all(task["progress_percent"] == 0 for task in read(result)["tasks"])


def test_delete_masks_native_selection_without_modifying_native_lock():
    state = synthetic_state()
    state["planning"] = wp.apply_action(state["planning"], "constructor_select",
        {"cube_id": "geometry", "date": "2027-01-05", "start": "14:00"})
    before = deepcopy(state)
    assert cube(state, "geometry")["selected_date"] == "2027-01-05"
    result = cube_ops.delete(state, {"cube_id": "geometry"}, read(state))
    assert cube(result, "geometry")["selected_date"] is None
    assert result["period_planner"]["cancellations"]["geometry"]["prior_placement"] == {"date": "2027-01-05", "start": "14:00"}
    assert result["period_planner"]["cancellations"]["geometry"]["prior_placement_override"] is None
    assert native(result) == native(before) and state == before


def test_fixed_calendar_event_delete_and_cancelled_or_non_task_merge_rejected():
    state = synthetic_state()
    before = deepcopy(state)
    with pytest.raises(ValidationError, match="Календарное событие"):
        cube_ops.delete(state, {"cube_id": "calendar-webinar"}, read(state))
    prep_row = next(row for row in model.cube_rows(state) if row["kind"] == "prep")
    prep = prep_row["id"]
    state.setdefault("period_planner", {}).setdefault("placements", {})[prep] = {"date": prep_row["planned_date"], "start": None}
    before = deepcopy(state)
    with pytest.raises(ValidationError, match="только кубики"):
        merge(state, ("geometry", prep))
    cancelled = cube_ops.delete(state, {"cube_id": "geometry"}, read(state))
    with pytest.raises(ValidationError, match="уже удалён"):
        merge(cancelled)
    assert state == before


def test_inferred_task_period_survives_absorbing_forecasts_with_different_dates():
    state = synthetic_state()
    current = read(state)
    forecasts = [row for row in current["cubes"] if row.get("task_id") == "b"]
    assert len({row["planned_date"] for row in forecasts}) > 1
    prior = next(task for task in current["tasks"] if task["id"] == "b")
    result = merge(state, [row["id"] for row in forecasts])
    assert merged(result)["planned_date"] is None
    after = next(task for task in read(result)["tasks"] if task["id"] == "b")
    assert (after["start"], after["end"], after["deadline"], after["minutes"]) == (prior["start"], prior["end"], prior["deadline"], prior["minutes"])
    assert native(result) == native(state)


@pytest.mark.parametrize("data", [{"cube_id": "geometry", "reason": "extra"}, {}, {"cube_id": []}])
def test_delete_has_exact_typed_contract(data):
    state = synthetic_state()
    with pytest.raises(ValidationError):
        cube_ops.delete(state, data, read(state))


@pytest.mark.parametrize("target_selected", [True, False])
def test_cross_parent_merge_keeps_target_placement_and_leaf_task_allocations(target_selected):
    state = synthetic_state()
    other_task = next(row for row in read(state)["cubes"] if row.get("task_id") == "b")
    state["planning"]["settings"]["day_limit_minutes"] = 160
    state["period_planner"] = {"placements": {
        "geometry": {"date": "2027-01-05", "start": "14:00"} if target_selected else {"date": None, "start": None},
        other_task["id"]: {"date": "2027-01-05", "start": "17:00"}}}
    before = deepcopy(state)
    result = merge(state, ("geometry", other_task["id"]))
    composite = merged(result)
    assert composite["task_id"] is None and composite["project_id"] is None
    assert composite["kind"] == "task" and composite["minutes"] == 60 + other_task["minutes"]
    assert (composite["selected_date"], composite["start"]) == (("2027-01-05", "14:00") if target_selected else (None, None))
    assert composite["source_task_allocations"] == [
        {"task_id": "a", "project_id": "print", "minutes": 60, "source_ids": ["geometry"]},
        {"task_id": "b", "project_id": "sculpt", "minutes": other_task["minutes"], "source_ids": [other_task["id"]]}]
    assert composite["source_segments"] == [
        {"task_id": "a", "segment_id": None, "minutes": 60, "source_ids": ["geometry"]},
        {"task_id": "b", "segment_id": None, "minutes": other_task["minutes"], "source_ids": [other_task["id"]]}]
    archive = result["period_planner"]["merges"][composite["id"]]
    assert archive["source_task_allocations"] == composite["source_task_allocations"]
    assert native(result) == native(before) and state == before


def test_nested_cross_parent_composites_deduplicate_allocation_groups_and_preserve_task_ranges():
    state = synthetic_state()
    before = read(state)
    other_task = [row for row in before["cubes"] if row.get("task_id") == "b"]
    assert len(other_task) >= 2
    first_state = merge(state)
    first = merged(first_state)
    cross_state = merge(first_state, (first["id"], other_task[0]["id"]))
    cross = merged(cross_state)
    result = merge(cross_state, (cross["id"], other_task[1]["id"], "other"))
    composite = merged(result)
    b_minutes = other_task[0]["minutes"] + other_task[1]["minutes"]
    assert composite["minutes"] == 135 + b_minutes
    assert composite["source_task_allocations"] == [
        {"task_id": "a", "project_id": "print", "minutes": 135, "source_ids": ["geometry", "slides", "other"]},
        {"task_id": "b", "project_id": "sculpt", "minutes": b_minutes,
            "source_ids": [other_task[0]["id"], other_task[1]["id"]]}]
    assert len(composite["source_ids"]) == len(set(composite["source_ids"])) == 5
    assert {(part["task_id"], part["segment_id"]): part["minutes"] for part in composite["source_segments"]} == {
        ("a", None): 135, ("b", None): b_minutes}
    assert sum(part["minutes"] for part in composite["source_segments"]) == composite["minutes"]
    assert sum(row["minutes"] for row in before["cubes"]) == sum(row["minutes"] for row in read(result)["cubes"])
    assert not {"geometry", "slides", "other", first["id"], cross["id"], *[row["id"] for row in other_task[:2]]} & {row["id"] for row in read(result)["cubes"]}
    assert [(t["id"], t["start"], t["end"], t["minutes"]) for t in read(result)["tasks"]] == [
        (t["id"], t["start"], t["end"], t["minutes"]) for t in before["tasks"]]
    assert native(result) == native(state)


def test_cross_parent_composite_retains_common_project_and_delete_allocation_archive():
    state = synthetic_state()
    next(task for task in state["planning"]["tasks"] if task["id"] == "b")["project_id"] = "print"
    other_task = next(row for row in read(state)["cubes"] if row.get("task_id") == "b")
    combined = merge(state, ("geometry", other_task["id"]))
    composite = merged(combined)
    assert composite["task_id"] is None and composite["project_id"] == "print"
    result = cube_ops.delete(combined, {"cube_id": composite["id"]}, read(combined))
    record = result["period_planner"]["cancellations"][composite["id"]]
    assert record["source_task_allocations"] == record["source"]["source_task_allocations"] == composite["source_task_allocations"]
    assert sum(part["minutes"] for part in record["source_task_allocations"]) == record["minutes"]
    assert record["source_ids"] == composite["source_ids"]
    assert cube(result, composite["id"])["status"] == "cancelled"
    assert all(task["progress_percent"] == 0 for task in read(result)["tasks"])
    assert native(result) == native(state)


def test_older_same_parent_merge_segments_gain_task_identity_when_composed():
    state = merge(synthetic_state())
    first = merged(state)
    saved = state["period_planner"]["merged_cubes"][first["id"]]
    saved.pop("source_task_allocations")
    for segment in saved["source_segments"]:
        segment.pop("task_id")
    other_task = next(row for row in read(state)["cubes"] if row.get("task_id") == "b")
    result = merge(state, (first["id"], other_task["id"]))
    composite = merged(result)
    assert composite["source_task_allocations"][0] == {
        "task_id": "a", "project_id": "print", "minutes": 105, "source_ids": ["geometry", "slides"]}
    assert composite["source_segments"][0] == {"task_id": "a", "segment_id": None,
        "minutes": 105, "source_ids": ["geometry", "slides"]}


def test_cross_parent_absorption_preserves_both_affected_inferred_periods():
    state = synthetic_state()
    state["planning"]["constructor"]["cubes"] = {}
    chosen = [next(row for row in state["planning"]["result"]["cubes"] if row["task_id"] == task_id)
        for task_id in ("a", "b")]
    state["planning"]["result"]["cubes"] = chosen
    before = read(state)
    result = merge(state, [row["id"] for row in before["cubes"] if row["kind"] == "task"])
    assert set(result["period_planner"]["periods"]) == {"a", "b"}
    assert [(task["id"], task["start"], task["end"], task["deadline"], task["minutes"]) for task in read(result)["tasks"]] == [
        (task["id"], task["start"], task["end"], task["deadline"], task["minutes"]) for task in before["tasks"]]
    assert native(result) == native(state)
