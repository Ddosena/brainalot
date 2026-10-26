from copy import deepcopy
import pytest
from megamozg.work_period import model
from test_work_period import synthetic_state, planner, color_planner, synthetic_store, set_context
from megamozg.core import ConflictError, ValidationError


def make(tmp_path):
    return model.PeriodPlanner(synthetic_store(tmp_path, synthetic_state()))


def send(planner, action, data, request="resize-test", revision=None):
    return planner.command({"action": action, "data": data, "request_id": request,
        "expected_revision": planner.model()["revision"] if revision is None else revision})


def test_prep_resize_is_atomic_persistent_and_not_task_volume(tmp_path):
    planner = make(tmp_path)
    before = deepcopy(planner.store.read()["state"])
    prep = next(row for row in planner.model()["cubes"] if row["kind"] == "prep")
    send(planner, "cube_select", {"cube_id": prep["id"], "date": prep["planned_date"], "start": "17:00"}, "select")
    rev = planner.model()["revision"]
    data = {"cube_id": prep["id"], "date": prep["planned_date"], "start": "16:45", "minutes": 40}
    first = send(planner, "cube_resize", data, revision=rev)
    row = next(row for row in first["cubes"] if row["id"] == prep["id"])
    assert (row["start"], row["end"], row["minutes"]) == ("16:45", "17:25", 40)
    assert row["source_minutes"] == 25 and row["can_resize"]
    assert send(planner, "cube_resize", data, revision=rev)["revision"] == first["revision"]
    after = deepcopy(planner.store.read()["state"]); after.pop("period_planner", None); before.pop("period_planner", None)
    assert after == before
    assert next(row for row in model.PeriodPlanner(planner.store).model()["cubes"] if row["id"] == prep["id"])["minutes"] == 40
    frozen = planner.store.read()
    with pytest.raises(ValidationError, match="пересекается"):
        send(planner, "cube_resize", {**data, "start": "18:45"}, "collision")
    assert planner.store.read() == frozen
    with pytest.raises(ConflictError):
        send(planner, "cube_resize", data, "stale", rev)


def test_work_scope_and_facts_cannot_be_resized(tmp_path):
    planner = make(tmp_path)
    cube = next(row for row in planner.model()["cubes"] if row["id"] == "geometry")
    assert not cube["can_resize"] and "Объём" in cube["resize_block_reason"]
    before = planner.store.read()
    with pytest.raises(ValidationError, match="перераспределения"):
        send(planner, "cube_resize", {"cube_id": cube["id"], "date": cube["planned_date"], "start": "15:00", "minutes": 120})
    assert planner.store.read() == before
    prep = next(row for row in planner.model()["cubes"] if row["kind"] == "prep")
    copied = deepcopy(before["state"])
    copied["planning"].setdefault("prep_progress", {})[prep["id"]] = {"percent": 20, "actual_minutes": 5, "date": prep["planned_date"]}
    factual = model.read_model({"state": copied, "revision": 0})
    assert not next(row for row in factual["cubes"] if row["id"] == prep["id"])["can_resize"]


def test_estimated_offer_can_resize_but_calendar_duration_cannot(color_planner):
    p, source, ids = color_planner
    send(p, "calendar_source_import", {"source": source}, "colors")
    sage = next(c for c in p.model()["cubes"] if c.get("calendar_role") == "evergreen")
    gray = next(c for c in p.model()["cubes"] if c.get("source_event_id") == ids["gray"])
    assert not sage["can_resize"] and sage["source_duration_unknown"]
    assert not gray["can_resize"] and "календаре" in gray["resize_block_reason"]
    send(p, "calendar_offer_estimate", {"cube_id": sage["id"], "minutes": 15}, "estimate")
    send(p, "cube_select", {"cube_id": sage["id"], "date": "2027-02-05", "start": "10:00"}, "select-sage")
    resized = send(p, "cube_resize", {"cube_id": sage["id"], "date": "2027-02-05", "start": "09:45", "minutes": 30})
    row = next(c for c in resized["cubes"] if c["id"] == sage["id"])
    assert row["minutes"] == 30 and row["end"] == "10:15" and row["can_resize"]


def test_explicit_zero_prep_preserves_facts(tmp_path):
    p = make(tmp_path); prep = next(c for c in p.model()["cubes"] if c["kind"] == "prep")
    send(p, "cube_select", {"cube_id": prep["id"], "date": prep["planned_date"], "start": "17:00"}, "select")
    result = send(p, "cube_resize", {"cube_id": prep["id"], "date": prep["planned_date"], "start": "17:00", "minutes": 0})
    row = next(c for c in result["cubes"] if c["id"] == prep["id"])
    assert row["minutes"] == 0 and row["source_minutes"] == 25 and row["actual_minutes"] == row["progress_percent"] == 0


def test_documentary_source_command_context_and_receipt_preserve_native(tmp_path):
    p = make(tmp_path); before = deepcopy(p.store.read()["state"])
    source = {"schema": "private-proposed-course-deliverables-v1", "private": True,
        "packages": [{"id": "package-a", "project_id": "print", "full_name": "Video and slides",
            "short_name": "Video", "source_work_task_id": "a", "window_start": "2027-01-05",
            "window_end": "2027-01-09", "deadline": "2027-01-09", "full_completion": None}],
        "rows": [{"id": "slides-a", "project_id": "print", "package_id": "package-a",
            "full_name": "Presentation", "short_name": "Slides", "source_work_task_id": "a",
            "due": None, "individual_due": None, "full_completion": False}]}
    rev = p.model()["revision"]
    result = send(p, "course_source_import", {"source": source}, "course-import", rev)
    assert [(e["event_kind"], e["date"]) for e in result["projects"][0]["timeline_entries"]] == [("start", "2027-01-05"), ("deadline", "2027-01-09")]
    assert send(p, "course_source_import", {"source": source}, "course-import", rev)["revision"] == result["revision"]
    set_context(p, {"task_id": "package-a"})
    assert model.PeriodPlanner(p.store).model()["context"]["task_id"] == "package-a"
    after = deepcopy(p.store.read()["state"]);after.pop("period_planner", None);before.pop("period_planner", None)
    assert after == before


def test_calendar_display_rename_is_local_synced_and_survives_source_import(color_planner):
    p, source, ids = color_planner
    before = deepcopy(p.store.read()["state"])
    send(p, "calendar_source_import", {"source": source}, "source")
    event = next(e for e in p.model()["events"] if e["calendar_role"] == "fixed")
    result = send(p, "calendar_event_rename", {"event_id": event["id"], "title": "  Local display label  "}, "rename-event")
    renamed = next(e for e in result["events"] if e["id"] == event["id"])
    assert renamed["title"] == "Local display label" and renamed["source_title"] == event["source_title"]
    assert not renamed["movable"] and renamed["start"] == event["start"]
    send(p, "calendar_source_import", {"source": source}, "source-repeat")
    assert next(e for e in model.PeriodPlanner(p.store).model()["events"] if e["id"] == event["id"])["title"] == "Local display label"
    after = deepcopy(p.store.read()["state"]);before.pop("period_planner", None);after.pop("period_planner", None)
    assert before == after


def test_calendar_alias_follows_same_source_through_role_changes(color_planner):
    p, source, ids = color_planner
    send(p, "calendar_source_import", {"source": source}, "source")
    cube = next(c for c in p.model()["cubes"] if c.get("source_event_id") == ids["gray"])
    send(p, "cube_rename", {"cube_id": cube["id"], "title": "Optional alias"}, "cube-name")
    changed = deepcopy(source)
    provider = next(e for e in changed["events"] if e["id"] == cube["source_occurrences"][0]["provider_id"])
    provider.update(event_label_id=None, color_id=None)
    send(p, "calendar_source_import", {"source": changed}, "fixed")
    assert next(e for e in p.model()["events"] if e["id"] == ids["gray"])["title"] == "Optional alias"
    send(p, "calendar_event_rename", {"event_id": ids["gray"], "title": "Fixed alias"}, "event-name")
    send(p, "calendar_source_import", {"source": source}, "optional-again")
    assert next(c for c in p.model()["cubes"] if c["id"] == cube["id"])["title"] == "Fixed alias"


def test_documentary_names_sync_entries_components_and_survive_reimport(tmp_path):
    p = make(tmp_path)
    source = {"schema": "private-proposed-course-deliverables-v1", "private": True,
        "packages": [{"id": "package-a", "project_id": "print", "full_name": "Original package", "short_name": "Package",
            "source_work_task_id": "a", "window_start": "2027-01-05", "window_end": "2027-01-09", "deadline": "2027-01-09"}],
        "rows": [{"id": "slides-a", "project_id": "print", "package_id": "package-a", "full_name": "Original slides", "short_name": "Slides", "due": None}]}
    before = deepcopy(p.store.read()["state"])
    send(p, "course_source_import", {"source": source}, "source")
    send(p, "assignment_rename", {"task_id": "package-a", "title": "My package"}, "parent-name")
    send(p, "assignment_rename", {"task_id": "slides-a", "title": "My presentation"}, "child-name")
    send(p, "course_source_import", {"source": source}, "reimport")
    project = model.PeriodPlanner(p.store).model()["projects"][0]
    parent = next(t for t in project["tasks"] if t["id"] == "package-a")
    child = next(t for t in project["tasks"] if t["id"] == "slides-a")
    assert parent["title"] == "My package" and parent["source_title"] == "Original package"
    assert child["title"] == parent["components"][0]["title"] == "My presentation"
    assert all(e["label"] == "My package" for e in project["timeline_entries"])
    assert next(t for t in p.model()["tasks"] if t["id"] == "a")["title"] == "My package"
    send(p, "task_rename", {"task_id": "a", "title": "Renamed from week"}, "week-rename")
    assert next(t for t in p.model()["projects"][0]["tasks"] if t["id"] == "package-a")["title"] == "Renamed from week"
    after = deepcopy(p.store.read()["state"]);before.pop("period_planner", None);after.pop("period_planner", None)
    assert before == after


def test_goal_crud_and_course_reimport_preserve_custom_context_and_native(tmp_path):
    p = make(tmp_path);before = deepcopy(p.store.read()["state"])
    result = send(p, "ruler_create", {"ruler_id": "my-goal", "title": "My goal", "start": "2027-01-05", "end": "2027-01-10"}, "goal-create")
    assert len(result["projects"]) == 3 and len(result["tasks"]) == len(before["planning"]["tasks"])
    goal = next(pr for pr in result["projects"] if pr["id"] == "my-goal")
    assert [(e["event_kind"], e["date"]) for e in goal["timeline_entries"]] == [("start", "2027-01-05"), ("deadline", "2027-01-10")]
    set_context(p, {"project_id": "my-goal", "task_id": "my-goal:goal"})
    source = {"schema": "private-proposed-course-deliverables-v1", "private": True,
        "packages": [], "rows": [{"id": "material-a", "project_id": "print", "full_name": "Material", "short_name": "Material", "due": "2027-01-09"}]}
    result = send(p, "course_source_import", {"source": source}, "course")
    assert result["context"]["task_id"] == "my-goal:goal"
    send(p, "ruler_entry_upsert", {"ruler_id": "my-goal", "entry_id": "custom-check", "title": "Checkpoint", "date": "2027-01-08"}, "check")
    send(p, "assignment_rename", {"task_id": "my-goal:goal", "title": "Updated goal"}, "goal-name")
    assert next(pr for pr in p.model()["projects"] if pr["id"] == "my-goal")["title"] == "Updated goal"
    send(p, "ruler_delete", {"ruler_id": "my-goal"}, "goal-delete")
    assert not any(pr["id"] == "my-goal" for pr in p.model()["projects"])
    assert p.model()["context"]["project_id"] is None and p.model()["context"]["task_id"] is None
    after = deepcopy(p.store.read()["state"]);before.pop("period_planner", None);after.pop("period_planner", None)
    assert before == after


def test_composite_delete_cancels_each_parent_portion_without_completion(tmp_path):
    p = make(tmp_path);initial = p.model();before = deepcopy(p.store.read()["state"])
    a = next(c for c in initial["cubes"] if c["id"] == "geometry")
    b = next(c for c in initial["cubes"] if c.get("task_id") == "b" and c["available"] and not c.get("selected_date"))
    merged = send(p, "cube_merge", {"cube_ids": [a["id"], b["id"]], "title": "Two parent work block"}, "merge-cross")
    cube = next(c for c in merged["cubes"] if c["id"].startswith("merged-"))
    assert cube["task_id"] is None and sum(x["minutes"] for x in cube["source_task_allocations"]) == cube["minutes"]
    deleted = send(p, "cube_delete", {"cube_id": cube["id"]}, "delete-cross")
    tasks = {t["id"]: t for t in deleted["tasks"]}
    assert tasks["a"]["cancelled_minutes"] == a["minutes"] and tasks["b"]["cancelled_minutes"] == b["minutes"]
    assert tasks["a"]["progress_percent"] == tasks["b"]["progress_percent"] == 0
    after = deepcopy(p.store.read()["state"]);before.pop("period_planner", None);after.pop("period_planner", None)
    assert before == after


def test_period_shrink_keeps_placed_cube_and_returned_cube_can_be_chosen(tmp_path):
    p = make(tmp_path);before = deepcopy(p.store.read()["state"])
    send(p, "cube_select", {"cube_id": "geometry", "date": "2027-01-05", "start": "15:00"}, "select")
    send(p, "period_set", {"task_id": "a", "start": "2027-01-08", "end": "2027-01-10"}, "shrink")
    row = next(c for c in p.model()["cubes"] if c["id"] == "geometry")
    assert row["selected_date"] == "2027-01-05" and row["start"] == "15:00"
    send(p, "cube_time", {"cube_id": "geometry", "start": "16:00"}, "time")
    send(p, "cube_return", {"cube_id": "geometry"}, "return")
    assert next(c for c in p.model()["cubes"] if c["id"] == "geometry")["returned"] is True
    send(p, "cube_select", {"cube_id": "geometry", "date": "2027-01-05", "start": "17:00"}, "choose-returned")
    task = next(t for t in p.model()["tasks"] if t["id"] == "a")
    assert task["start"] == "2027-01-08" and task["end"] == "2027-01-10"
    after = deepcopy(p.store.read()["state"]);before.pop("period_planner", None);after.pop("period_planner", None)
    assert before == after
