"""Synthetic day-builder tests. No user course or Calendar data is loaded."""
from copy import deepcopy
from datetime import datetime as RealDateTime, timezone

import pytest

from test_work_planning import op, planning, seed
from megamozg.core import ValidationError
from megamozg import work_planning


@pytest.fixture(autouse=True)
def stable_builder_today(monkeypatch):
    """Keep 2027 synthetic days future without changing the production clock."""
    class FrozenDateTime(RealDateTime):
        @classmethod
        def now(cls, tz=None):
            fixed = cls(2026, 12, 31, 12, 0, tzinfo=timezone.utc)
            return fixed.astimezone(tz) if tz is not None else fixed.replace(tzinfo=None)

    monkeypatch.setattr(work_planning, "datetime", FrozenDateTime)


def three_lesson_day():
    document = planning()
    document["calendar"]["events"] = [
        {"id": f"lesson-{i}", "title": "Teaching", "start": f"2027-01-04T{hour:02d}:00:00+03:00",
         "end": f"2027-01-04T{hour+1:02d}:00:00+03:00", "category": "lesson"}
        for i, hour in enumerate((11, 13, 18))
    ]
    return document


def day(snapshot, date="2027-01-04"):
    return next(row for row in snapshot["state"]["planning"]["result"]["days"] if row["date"] == date)


def cubes(snapshot, date="2027-01-04"):
    return [cube for cube in snapshot["state"]["planning"]["result"]["cubes"] if cube["date"] == date]


def test_three_lessons_allow_manual_45_without_auto_cube_or_hidden_pool(tmp_path):
    work = seed(tmp_path)
    imported = op(work, "import", "import", {"planning": three_lesson_day()})
    assert day(imported)["fixed_minutes"] == 255
    assert cubes(imported) == []
    assert day(imported)["builder_available_minutes"] == 45
    built = op(work, "manual-45", "day_cube_add",
               {"date": "2027-01-04", "task_id": "a", "minutes": 45})
    row = day(built)
    assert row["manual"] and row["budget_minutes"] == 300
    assert row["builder_selected_minutes"] == 45
    assert row["builder_available_minutes"] == 0
    assert row["total_work_minutes"] == 300
    assert len(cubes(built)) == 1
    assert cubes(built)[0]["floating"] is True
    assert cubes(built)[0]["start"] is None and cubes(built)[0]["end"] is None
    assert cubes(built)[0]["order"] == 0
    assert built["state"]["planning"]["result"]["task_pool"]


def test_first_manual_edit_releases_auto_forecast_and_prep_restore_does_not_refill(tmp_path):
    work = seed(tmp_path)
    imported = op(work, "import", "import", {"planning": planning()})
    assert cubes(imported, "2027-01-05")
    edited = op(work, "manualize", "day_budget", {"date": "2027-01-05", "minutes": 300})
    assert day(edited, "2027-01-05")["manual"] is True
    assert cubes(edited, "2027-01-05") == []
    assert day(edited, "2027-01-05")["builder_selected_minutes"] == 0
    assert day(imported, "2027-01-05")["builder_total_work_minutes"] == 30
    assert edited["state"]["planning"]["result"]["task_pool"]
    prep_id = day(edited)["prep_cubes"][0]["id"]
    removed = op(work, "prep-zero", "prep_set", {"prep_id": prep_id, "minutes": 0})
    assert day(removed)["prep_cubes"][0]["minutes"] == 0
    assert day(removed)["lesson_prep_minutes"] == 0
    assert cubes(removed) == []
    restored = op(work, "prep-return", "prep_set", {"prep_id": prep_id, "minutes": 25})
    assert day(restored)["lesson_prep_minutes"] == 25
    assert cubes(restored) == []


def test_budget_rejects_unstarted_but_preserves_accepted_actual_and_allows_next_task(tmp_path):
    work = seed(tmp_path)
    op(work, "import", "import", {"planning": planning()})
    op(work, "manualize", "day_budget", {"date": "2027-01-05", "minutes": 150})
    selected = op(work, "add-90", "day_cube_add",
                  {"date": "2027-01-05", "task_id": "a", "minutes": 90})
    first = cubes(selected, "2027-01-05")[0]
    # A budget below the chosen cubes is only a reading (decision 2026-10-28).
    op(work, "too-low", "day_budget", {"date": "2027-01-05", "minutes": 0})
    assert cubes(work.read(), "2027-01-05")[0]["id"] == first["id"]
    op(work, "restore-budget", "day_budget", {"date": "2027-01-05", "minutes": 150})
    op(work, "half", "cube_progress",
       {"cube_id": first["id"], "percent": 50, "actual_minutes": 40})
    immediate = work.read()["state"]["planning"]["result"]
    assert next(row for row in immediate["days"] if row["date"] == "2027-01-05")["reported_actual_minutes"] == 40
    assert next(row for row in immediate["task_pool"] if row["task_id"] == "a")["remaining_minutes"] == 855
    lowered = op(work, "accepted-low", "day_budget", {"date": "2027-01-05", "minutes": 0})
    assert day(lowered, "2027-01-05")["over_cap_minutes"] == 70
    assert lowered["state"]["planning"]["completed_cubes"][first["id"]]["actual_minutes"] == 40
    raised = op(work, "back-to-150", "day_budget", {"date": "2027-01-05", "minutes": 150})
    another = op(work, "other-task", "day_cube_add",
                 {"date": "2027-01-05", "task_id": "b", "minutes": 60})
    assert day(another, "2027-01-05")["builder_selected_minutes"] == 100
    assert day(another, "2027-01-05")["builder_total_work_minutes"] == day(another, "2027-01-05")["total_work_minutes"]
    assert {cube["task_id"] for cube in cubes(another, "2027-01-05")} == {"a", "b"}
    op(work, "finish-a", "day_outcome",
       {"task_id": "a", "date": "2027-01-05", "done": True, "remaining_minutes": 0})
    third = op(work, "another-after-outcome", "day_cube_add",
               {"date": "2027-01-05", "task_id": "c", "minutes": 15})
    assert len(cubes(third, "2027-01-05")) == 3


def test_floating_reorder_resize_and_started_cube_protection(tmp_path):
    work = seed(tmp_path)
    op(work, "import", "import", {"planning": planning()})
    op(work, "manualize", "day_budget", {"date": "2027-01-05", "minutes": 300})
    first = op(work, "first", "day_cube_add",
               {"date": "2027-01-05", "task_id": "a", "minutes": 15})
    first_id = cubes(first, "2027-01-05")[0]["id"]
    second = op(work, "second", "day_cube_add",
                {"date": "2027-01-05", "task_id": "b", "minutes": 30})
    second_id = cubes(second, "2027-01-05")[1]["id"]
    reordered = op(work, "reorder", "day_cube_move",
                   {"cube_id": second_id, "date": "2027-01-05", "index": 0})
    assert [cube["id"] for cube in cubes(reordered, "2027-01-05")] == [second_id, first_id]
    resized = op(work, "resize", "day_cube_resize", {"cube_id": first_id, "minutes": 45})
    assert next(cube for cube in cubes(resized, "2027-01-05") if cube["id"] == first_id)["minutes"] == 45
    op(work, "started", "cube_progress",
       {"cube_id": first_id, "percent": 50, "actual_minutes": 20})
    with pytest.raises(ValidationError, match="Начатый"):
        op(work, "no-remove", "day_cube_remove", {"cube_id": first_id})
    with pytest.raises(ValidationError, match="Начатый"):
        op(work, "no-resize", "day_cube_resize", {"cube_id": first_id, "minutes": 90})
    with pytest.raises(ValidationError, match="Начатый"):
        op(work, "no-move-date", "day_cube_move",
           {"cube_id": first_id, "date": "2027-01-06"})


def test_prep_progress_is_separate_from_course_remaining_and_cannot_be_erased(tmp_path):
    work = seed(tmp_path)
    imported = op(work, "import", "import", {"planning": planning()})
    prep_id = day(imported)["prep_cubes"][0]["id"]
    before = next(task for task in imported["state"]["planning"]["result"]["tasks"]
                  if task["id"] == "a")["remaining_at_horizon_minutes"]
    saved = op(work, "prep-half", "prep_progress",
               {"prep_id": prep_id, "percent": 50, "actual_minutes": 15})
    assert day(saved)["prep_cubes"][0]["actual_minutes"] == 15
    with pytest.raises(ValidationError, match="Подтверждённую"):
        op(work, "prep-cannot-zero", "prep_set", {"prep_id": prep_id, "minutes": 0})
    assert next(task for task in saved["state"]["planning"]["result"]["tasks"]
                if task["id"] == "a")["remaining_at_horizon_minutes"] == before
    extra = op(work, "prep-overrun", "prep_progress",
               {"prep_id": prep_id, "percent": 100, "actual_minutes": 40})
    assert day(extra)["prep_actual_excess_minutes"] == 15
    assert day(extra)["builder_total_work_minutes"] == day(extra)["fixed_minutes"] + 15


def test_manual_day_and_prep_override_survive_replan_and_corrected_seed_import(tmp_path):
    work = seed(tmp_path)
    imported = op(work, "import", "import", {"planning": planning()})
    prep_id = day(imported)["prep_cubes"][0]["id"]
    op(work, "prep-zero", "prep_set", {"prep_id": prep_id, "minutes": 0})
    op(work, "manualize", "day_budget", {"date": "2027-01-05", "minutes": 330})
    selected = op(work, "select", "day_cube_add",
                  {"date": "2027-01-05", "task_id": "b", "minutes": 60})
    cube_id = cubes(selected, "2027-01-05")[0]["id"]
    replanned = op(work, "replan", "recalculate", {})
    assert day(replanned)["lesson_prep_minutes"] == 0
    assert day(replanned, "2027-01-05")["manual"] is True
    assert next(c for c in cubes(replanned, "2027-01-05") if c["id"] == cube_id)["minutes"] == 60
    corrected = planning()
    corrected["tasks"][0]["estimate_minutes"] = {"lower": 800, "central": 840, "upper": 900}
    merged = op(work, "corrected-import", "import", {"planning": corrected})
    assert merged["state"]["planning"]["prep_overrides"][prep_id] == 0
    assert day(merged, "2027-01-05")["budget_minutes"] == 330
    assert any(c["id"] == cube_id for c in cubes(merged, "2027-01-05"))
    assert sum(s["minutes"] for s in merged["state"]["planning"]["task_segments"]
               if s["task_id"] == "a") == 840


def one_small_task():
    document = planning()
    document["tasks"] = [document["tasks"][0]]
    document["tasks"][0]["estimate_minutes"] = {"lower": 180, "central": 180, "upper": 180}
    document["tasks"][0]["review_minutes"] = 0
    return document


def test_segments_carve_parent_and_atomic_two_hour_add(tmp_path):
    work = seed(tmp_path)
    imported = op(work, "import", "import", {"planning": one_small_task()})
    base = imported["state"]["planning"]
    assert sum(x["minutes"] for x in base["task_segments"] if x["task_id"] == "a") == 180
    created = op(work, "create-stage", "segment_create",
                 {"task_id": "a", "title": "Build sample", "minutes": 120,
                  "output": "Working example", "steps": ["Open file", "Build", "Verify"]})
    new = next(x for x in created["state"]["planning"]["task_segments"] if x["origin"] == "manual")
    assert sum(x["minutes"] for x in created["state"]["planning"]["task_segments"] if x["task_id"] == "a") == 180
    packed = op(work, "pack-stage", "day_segment_add",
                {"date": "2027-01-05", "segment_id": new["id"], "minutes": 120})
    selected = cubes(packed, "2027-01-05")
    assert sorted(cube["minutes"] for cube in selected) == [30, 90]
    assert all(cube["segment_id"] == new["id"] and cube["floating"] for cube in selected)
    pool = packed["state"]["planning"]["result"]["task_pool"][0]
    assert pool["remaining_minutes"] == 180
    assert pool["manual_reserved_minutes"] == 120
    assert pool["available_for_manual_minutes"] == 60
    assert next(x for x in pool["segments"] if x["id"] == new["id"])["available_for_manual_minutes"] == 0
    before = work.read()
    with pytest.raises(ValidationError):
        op(work, "overpack", "day_segment_add",
           {"date": "2027-01-06", "segment_id": new["id"], "minutes": 120})
    assert work.read() == before


def test_segment_progress_releases_only_unstarted_reservations_and_no_double_credit(tmp_path):
    work = seed(tmp_path)
    op(work, "import", "import", {"planning": one_small_task()})
    created = op(work, "create-stage", "segment_create",
                 {"task_id": "a", "title": "Stage", "minutes": 120})
    segment = next(x for x in created["state"]["planning"]["task_segments"] if x["origin"] == "manual")
    packed = op(work, "pack-stage", "day_segment_add",
                {"date": "2027-01-05", "segment_id": segment["id"], "minutes": 120})
    first = next(cube for cube in cubes(packed, "2027-01-05") if cube["minutes"] == 90)
    second = next(cube for cube in cubes(packed, "2027-01-05") if cube["minutes"] == 30)
    half = op(work, "stage-half", "segment_progress",
              {"segment_id": segment["id"], "percent": 50})
    assert len(half["state"]["planning"]["result"]["released_reservations"]) == 1
    assert half["state"]["planning"]["result"]["released_reservations"][0]["cube_id"] == first["id"]
    assert any(cube["id"] == second["id"] for cube in cubes(half, "2027-01-05"))
    assert next(x for x in half["state"]["planning"]["result"]["task_pool"] if x["task_id"] == "a")["remaining_minutes"] == 120
    assert next(x for x in half["state"]["planning"]["result"]["tasks"] if x["id"] == "a")["remaining_minutes"] == 120
    finished_cube = op(work, "remaining-cube-done", "cube_progress",
                       {"cube_id": second["id"], "percent": 100, "actual_minutes": 30})
    assert next(x for x in finished_cube["state"]["planning"]["result"]["task_pool"]
                if x["task_id"] == "a")["remaining_minutes"] == 90
    complete = op(work, "stage-full", "segment_progress",
                  {"segment_id": segment["id"], "percent": 100})
    assert next(x for x in complete["state"]["planning"]["result"]["task_pool"]
                if x["task_id"] == "a")["remaining_minutes"] == 60
    assert next(x for x in complete["state"]["planning"]["result"]["tasks"] if x["id"] == "a")["remaining_minutes"] == 60
    assert all(x["id"] != segment["id"] for x in complete["state"]["planning"]["result"]["task_pool"][0]["segments"])


def test_cube_half_then_segment_half_uses_anchor_without_double_count(tmp_path):
    work = seed(tmp_path)
    op(work, "import", "import", {"planning": one_small_task()})
    created = op(work, "create-stage", "segment_create",
                 {"task_id": "a", "title": "Stage", "minutes": 120})
    segment = next(x for x in created["state"]["planning"]["task_segments"] if x["origin"] == "manual")
    packed = op(work, "pack-stage", "day_segment_add",
                {"date": "2027-01-05", "segment_id": segment["id"], "minutes": 120})
    first = next(cube for cube in cubes(packed, "2027-01-05") if cube["minutes"] == 90)
    op(work, "cube-half", "cube_progress",
       {"cube_id": first["id"], "percent": 50, "actual_minutes": 40})
    half = op(work, "segment-half", "segment_progress",
              {"segment_id": segment["id"], "percent": 50})
    anchor = half["state"]["planning"]["segment_progress"][segment["id"]]["cube_credit_at_report"]
    assert anchor == 45
    assert next(x for x in half["state"]["planning"]["result"]["task_pool"]
                if x["task_id"] == "a")["remaining_minutes"] == 120


def test_small_custom_segment_defaults_to_exact_floating_45(tmp_path):
    work = seed(tmp_path)
    op(work, "import", "import", {"planning": one_small_task()})
    created = op(work, "small-segment", "segment_create",
                 {"task_id": "a", "title": "Small step", "minutes": 45})
    segment = next(x for x in created["state"]["planning"]["task_segments"] if x["origin"] == "manual")
    added = op(work, "small-add", "day_segment_add",
               {"date": "2027-01-05", "segment_id": segment["id"]})
    selected = cubes(added, "2027-01-05")
    assert len(selected) == 1 and selected[0]["minutes"] == 45
    assert selected[0]["floating"] is True and selected[0]["segment_id"] == segment["id"]
    assert added["state"]["planning"]["result"]["tasks"][0]["remaining_minutes"] == 180


def test_plan_version_tracks_manual_prep_and_segment_inputs(tmp_path):
    work = seed(tmp_path)
    imported = op(work, "import", "import", {"planning": planning()})
    before = imported["state"]["planning"]["plan_version"]
    budget = op(work, "budget", "day_budget", {"date": "2027-01-05", "minutes": 315})
    after_budget = budget["state"]["planning"]["plan_version"]
    assert after_budget != before
    prep_id = day(budget)["prep_cubes"][0]["id"]
    prep = op(work, "prep", "prep_set", {"prep_id": prep_id, "minutes": 0})
    after_prep = prep["state"]["planning"]["plan_version"]
    assert after_prep != after_budget
    segment_id = prep["state"]["planning"]["task_segments"][0]["id"]
    progress = op(work, "stage-progress", "segment_progress",
                  {"segment_id": segment_id, "percent": 25})
    assert progress["state"]["planning"]["plan_version"] != after_prep
