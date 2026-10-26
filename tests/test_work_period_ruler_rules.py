"""Synthetic owner ruler contracts; no private data or external services."""
from copy import deepcopy

import pytest

from megamozg.work_period import ruler_rules as rules
from megamozg.core import ValidationError


def state():
    return {"projects": [{"id": "course", "name": "Official course"}],
        "planning": {"tasks": [{"id": "native", "deadline": "2027-04-20"}],
                     "facts": {"cube": {"actual_minutes": 10}}},
        "items": [{"id": "item", "title": "Preserved"}],
        "period_planner": {"placements": {"cube": {"date": "2027-04-10"}},
            "periods": {"native": {"start": "2027-04-01", "end": "2027-04-02"}},
            "context": {"project_id": "course", "task_id": "package", "cube_id": "cube"}}}


def projects():
    child = {"id": "slides", "project_id": "course", "package_id": "package", "title": "Slides",
        "window_start": "2027-04-09", "window_end": "2027-04-15", "date": "2027-04-09",
        "end": "2027-04-15", "deadline": None, "individual_due": None}
    task = {"id": "package", "project_id": "course", "kind": "package", "title": "Official assignment",
        "window_start": "2027-04-09", "window_end": "2027-04-15", "date": "2027-04-09",
        "end": "2027-04-15", "deadline": "2027-04-15", "full_completion": None,
        "work_progress_percent": 20, "components": [deepcopy(child)]}
    task.update(deadline_kind="source_sheet", status_label="Задание ещё не закрыто")
    entries = [{"id": "package:" + kind, "task_id": "package", "project_id": "course",
        "event_kind": kind, "date": day, "title": "Official assignment", "label": "Assignment",
        "window_start": "2027-04-09", "window_end": "2027-04-15", "full_completion": None,
        "source_refs": [{"source": "Synthetic brief", "locator": "row 2"}], "read_only": True}
        for kind, day in (("start", "2027-04-09"), ("deadline", "2027-04-15"))]
    return [{"id": "course", "title": "Official course", "visible": True, "start": "2027-04-09",
             "end": "2027-04-15", "tasks": [task, child], "timeline_entries": entries}]


def command(current, action, **data):
    return rules.apply(current, action, data, {"projects": rules.enrich(current, projects())})


def create(current=None, **data):
    return command(current or state(), "ruler_create", ruler_id="goal", title="Personal sculpture",
                   **data)


def test_create_reopen_with_two_edges_and_one_documentary_parent():
    original = state()
    saved = create(original, start="2027-05-01", end="2027-05-10", description="Owner supplied")
    output = rules.enrich(deepcopy(saved), projects())
    goal = output[-1]
    assert goal["kind"] == "goal" and goal["description"] == "Owner supplied"
    assert [e["event_kind"] for e in goal["timeline_entries"]] == ["start", "deadline"]
    assert {e["task_id"] for e in goal["timeline_entries"]} == {"goal:goal"}
    assert len(goal["tasks"]) == 1 and goal["tasks"][0]["documentary_only"]
    assert all(e["full_completion"] is None for e in goal["timeline_entries"])
    assert {k: v for k, v in saved.items() if k != "period_planner"} == {k: v for k, v in original.items() if k != "period_planner"}
    assert original == state()
    assert saved["period_planner"]["placements"] == original["period_planner"]["placements"]
    assert saved["period_planner"]["periods"] == original["period_planner"]["periods"]


def test_undated_goal_remains_visible_and_no_completion_inferred_from_date():
    saved = create()
    goal = rules.enrich(saved, projects())[-1]
    assert goal["start"] is None and goal["end"] is None and goal["visible"]
    assert goal["timeline_entries"] == [] and goal["undated_task_ids"] == ["goal:goal"]
    saved = command(saved, "ruler_update", ruler_id="goal", end="2000-01-01")
    assert rules.enrich(saved, projects())[-1]["timeline_entries"][0]["full_completion"] is None


def test_id_reuse_never_duplicates_even_after_delete():
    saved = create()
    for current in (saved, command(saved, "ruler_delete", ruler_id="goal")):
        with pytest.raises(ValidationError):
            create(current)
    with pytest.raises(ValidationError):
        command(state(), "ruler_create", ruler_id="course", title="Collision")
    with pytest.raises(ValidationError):
        command(state(), "ruler_create", title="Missing explicit stable ID")
    for collision in ("native", "package", "package:start", "a" * 256):
        with pytest.raises(ValidationError):
            command(state(), "ruler_create", ruler_id=collision, title="Collision or generated ID too long")


def test_edit_native_dates_preserves_source_and_syncs_only_parent_and_children():
    original_projects, original_state = projects(), state()
    saved = command(original_state, "ruler_entry_upsert", ruler_id="course", entry_id="package:start", date="2027-04-10", title="Owner label")
    saved = command(saved, "ruler_entry_upsert", ruler_id="course", entry_id="package:deadline", date="2027-04-18")
    project = rules.enrich(saved, original_projects)[0]
    start, deadline = project["timeline_entries"]
    assert start["date"] == "2027-04-10" and start["source_date"] == "2027-04-09"
    assert start["title"] == "Owner label" and start["source_title"] == "Official assignment"
    assert start["source_refs"] == original_projects[0]["timeline_entries"][0]["source_refs"]
    assert deadline["source_date"] == "2027-04-15" and deadline["date"] == "2027-04-18"
    package, child = project["tasks"]
    assert package["title"] == "Official assignment"  # labels do not alias assignments
    assert package["window_start"] == "2027-04-10" and package["window_end"] == "2027-04-18"
    assert package["source_window_start"] == "2027-04-09" and package["source_deadline"] == "2027-04-15"
    assert package["deadline"] == "2027-04-18"
    assert child["window_start"] == "2027-04-10" and child["window_end"] == "2027-04-18"
    assert child["source_window_end"] == "2027-04-15" and child["deadline"] is None
    assert package["components"][0] == child
    assert project["start"] == "2027-04-10" and project["source_start"] == "2027-04-09"
    assert len(saved["period_planner"]["ruler_archive"]) == 2
    assert original_projects == projects() and original_state == state()
    assert saved["planning"] == original_state["planning"]


def test_package_edit_does_not_shift_another_assignment():
    base = projects()
    base[0]["timeline_entries"].append({"id": "other:deadline", "project_id": "course", "task_id": "other",
        "event_kind": "deadline", "date": "2027-04-20", "title": "Other", "source_refs": []})
    saved = rules.apply(state(), "ruler_entry_upsert", {"ruler_id": "course", "entry_id": "package:deadline", "date": "2027-04-17"}, {"projects": base})
    assert rules.enrich(saved, base)[0]["timeline_entries"][-1]["date"] == "2027-04-20"


def test_custom_milestone_add_edit_remove_archives_without_work_task():
    saved = create()
    saved = command(saved, "ruler_entry_upsert", ruler_id="goal", entry_id="sketch", title="Sketch", date="2027-05-03")
    saved = command(saved, "ruler_entry_upsert", ruler_id="goal", entry_id="sketch", title="Final sketch", full_completion=True)
    event = rules.enrich(saved, projects())[-1]["timeline_entries"][0]
    assert event["event_kind"] == "milestone" and event["title"] == "Final sketch" and event["full_checkmark"]
    assert event["task_id"] == "goal:goal"
    saved = command(saved, "ruler_entry_delete", ruler_id="goal", entry_id="sketch")
    assert rules.enrich(saved, projects())[-1]["timeline_entries"] == []
    assert saved["period_planner"]["ruler_archive"][-1]["previous"]["title"] == "Final sketch"
    assert saved["planning"] == state()["planning"]


def test_native_added_milestone_gets_documentary_parent_and_can_hide_restore():
    saved = command(state(), "ruler_entry_upsert", ruler_id="course", entry_id="review", title="Owner review", date=None)
    project = rules.enrich(saved, projects())[0]
    event = next(e for e in project["timeline_entries"] if e["id"] == "review")
    assert event["task_id"] == "course:ruler-notes"
    assert next(t for t in project["tasks"] if t["id"] == event["task_id"])["documentary_only"]
    saved = command(saved, "ruler_entry_delete", ruler_id="course", entry_id="review")
    assert next(e for e in rules.enrich(saved, projects())[0]["timeline_entries"] if e["id"] == "review")["visible"] is False
    saved = command(saved, "ruler_entry_upsert", ruler_id="course", entry_id="review", visible=True)
    assert next(e for e in rules.enrich(saved, projects())[0]["timeline_entries"] if e["id"] == "review")["visible"] is True


@pytest.mark.parametrize("action,extra", [("ruler_entry_delete", {}),
    ("ruler_entry_upsert", {"visible": False})])
def test_last_native_note_hidden_removes_documentary_parent_and_clears_only_its_context(action, extra):
    saved = command(state(), "ruler_entry_upsert", ruler_id="course", entry_id="review",
                    title="Personal note", date=None)
    saved["period_planner"]["context"]["task_id"] = "course:ruler-notes"
    context_before = deepcopy(saved["period_planner"]["context"])
    saved = command(saved, action, ruler_id="course", entry_id="review", **extra)
    project = rules.enrich(saved, projects())[0]
    assert not any(t["id"] == "course:ruler-notes" for t in project["tasks"])
    assert "course:ruler-notes" not in project["undated_task_ids"]
    assert saved["period_planner"]["context"] == {**context_before, "task_id": None}
    assert next(e for e in project["timeline_entries"] if e["id"] == "review")["visible"] is False
    assert saved["planning"] == state()["planning"]


def test_hiding_one_of_two_native_notes_retains_parent_and_context():
    saved = command(state(), "ruler_entry_upsert", ruler_id="course", entry_id="review",
                    title="First", date=None)
    saved = command(saved, "ruler_entry_upsert", ruler_id="course", entry_id="review-two",
                    title="Second", date=None)
    saved["period_planner"]["context"]["task_id"] = "course:ruler-notes"
    saved = command(saved, "ruler_entry_delete", ruler_id="course", entry_id="review")
    project = rules.enrich(saved, projects())[0]
    assert any(t["id"] == "course:ruler-notes" for t in project["tasks"])
    assert saved["period_planner"]["context"]["task_id"] == "course:ruler-notes"


def test_hide_native_preserves_source_and_custom_delete_cleans_only_context():
    saved = create()
    saved = command(saved, "ruler_delete", ruler_id="course")
    assert rules.enrich(saved, projects())[0]["visible"] is False
    assert saved["projects"] == state()["projects"]
    saved["period_planner"]["context"].update(project_id="goal", task_id="goal:goal")
    saved = command(saved, "ruler_delete", ruler_id="goal")
    assert len(rules.enrich(saved, projects())) == 1
    assert saved["period_planner"]["context"] == {"project_id": None, "task_id": None, "cube_id": "cube"}
    assert saved["period_planner"]["placements"] == state()["period_planner"]["placements"]
    saved = command(saved, "ruler_update", ruler_id="course", visible=True)
    assert rules.enrich(saved, projects())[0]["visible"]


def test_native_title_visibility_and_source_reimport_keep_overrides():
    saved = command(state(), "ruler_update", ruler_id="course", title="Owner name", description="Owner note", visible=False)
    saved = command(saved, "ruler_entry_upsert", ruler_id="course", entry_id="package:deadline", date="2027-04-18")
    fresh = projects()
    fresh[0]["title"] = "Fresh official name"
    fresh[0]["timeline_entries"][1]["date"] = "2027-04-16"
    projected = rules.enrich(saved, fresh)[0]
    assert projected["title"] == "Owner name" and projected["source_title"] == "Fresh official name"
    assert projected["description"] == "Owner note" and projected["visible"] is False
    assert projected["timeline_entries"][1]["date"] == "2027-04-18"
    assert projected["timeline_entries"][1]["source_date"] == "2027-04-16"


def test_explicit_native_completion_consistent_with_parent_without_native_fact_changes():
    saved = command(state(), "ruler_entry_upsert", ruler_id="course", entry_id="package:deadline", full_completion=True)
    project = rules.enrich(saved, projects())[0]
    assert all(e["full_completion"] is True for e in project["timeline_entries"])
    assert project["tasks"][0]["full_completion"] is True
    assert project["tasks"][0]["source_full_completion"] is None
    assert project["tasks"][0]["status_label"] == "Задание готово"
    assert project["tasks"][0]["source_status_label"] == "Задание ещё не закрыто"
    saved = command(saved, "ruler_entry_upsert", ruler_id="course", entry_id="package:start", full_completion=False)
    project = rules.enrich(saved, projects())[0]
    assert all(e["full_completion"] is False for e in project["timeline_entries"])
    assert project["tasks"][0]["status"] == "started"
    assert saved["planning"] == state()["planning"]


def test_owner_date_override_is_confirmed_but_preserves_source_kind_and_native_facts():
    base = projects()
    for entry in base[0]["timeline_entries"]:
        entry["deadline_kind"] = "source_screenshot"
    saved = rules.apply(state(), "ruler_entry_upsert", {"ruler_id": "course",
        "entry_id": "package:deadline", "date": "2027-04-17"}, {"projects": base})
    projected = rules.enrich(saved, base)[0]
    task = projected["tasks"][0]
    assert task["deadline_kind"] == "confirmed" and task["owner_date_override"]
    assert task["source_deadline_kind"] == "source_sheet"
    deadline = next(e for e in projected["timeline_entries"] if e["event_kind"] == "deadline")
    assert deadline["deadline_kind"] == "confirmed" and deadline["owner_date_override"]
    assert deadline["source_deadline_kind"] == "source_screenshot"
    assert projected["tasks"][1]["individual_due"] is None
    assert saved["planning"] == state()["planning"]


def test_custom_goal_deadline_is_confirmed_only_when_explicit_date_exists():
    saved = create(end="2027-04-20")
    goal = rules.enrich(saved, projects())[-1]
    assert goal["tasks"][0]["deadline_kind"] == "confirmed"
    assert goal["timeline_entries"][0]["deadline_kind"] == "confirmed"
    saved = command(saved, "ruler_entry_upsert", ruler_id="goal", entry_id="goal:deadline", date=None)
    goal = rules.enrich(saved, projects())[-1]
    assert goal["tasks"][0]["deadline_kind"] == "unknown"
    assert goal["timeline_entries"][0]["deadline_kind"] == "unknown"


@pytest.mark.parametrize("action,data", [
    ("ruler_create", {"ruler_id": "new", "title": "N", "start": "2027-2-01"}),
    ("ruler_create", {"ruler_id": "new", "title": "N", "end": 1}),
    ("ruler_create", {"ruler_id": "new", "title": "N", "start": "2027-04-02", "end": "2027-04-01"}),
    ("ruler_update", {"ruler_id": "course", "visible": 1}),
    ("ruler_update", {"ruler_id": "course", "start": "2027-04-01"}),
    ("ruler_update", {"ruler_id": "course", "title": None}),
    ("ruler_entry_upsert", {"ruler_id": "course", "entry_id": "package:start", "full_completion": "true"}),
    ("ruler_entry_upsert", {"ruler_id": "course", "entry_id": "package:start", "full_completion": 1}),
    ("ruler_entry_upsert", {"ruler_id": "course", "entry_id": "package:start", "date": "2027-04-30"}),
    ("ruler_entry_upsert", {"ruler_id": "course", "entry_id": "new", "title": "Missing date"}),
    ("ruler_entry_upsert", {"ruler_id": "course", "entry_id": "package:start", "event_kind": "milestone"}),
    ("ruler_entry_upsert", {"ruler_id": "course", "entry_id": "new", "title": "N", "date": None, "event_kind": []}),
    ("ruler_entry_delete", {"ruler_id": "course", "entry_id": "missing"}),
    ("ruler_update", {"ruler_id": "course", "unknown": 3}),
])
def test_strict_validation_is_atomic(action, data):
    original = state()
    with pytest.raises(ValidationError):
        rules.apply(original, action, data, {"projects": projects()})
    assert original == state()


def test_custom_boundary_update_and_delete_keep_metadata_in_sync():
    saved = create(start="2027-04-01", end="2027-04-10")
    saved = command(saved, "ruler_entry_upsert", ruler_id="goal", entry_id="goal:deadline", date="2027-04-12")
    assert rules.enrich(saved, projects())[-1]["end"] == "2027-04-12"
    saved = command(saved, "ruler_update", ruler_id="goal", title="New name", start="2027-04-02")
    goal = rules.enrich(saved, projects())[-1]
    assert goal["tasks"][0]["title"] == "New name"
    assert goal["timeline_entries"][0]["title"] == "New name"
    assert goal["timeline_entries"][0]["date"] == "2027-04-02"
    saved = command(saved, "ruler_entry_delete", ruler_id="goal", entry_id="goal:deadline")
    assert rules.enrich(saved, projects())[-1]["end"] is None


def test_enrich_does_not_mutate_inputs():
    saved, base = create(), projects()
    before, prior = deepcopy(saved), deepcopy(base)
    rules.enrich(saved, base)
    assert saved == before and base == prior
