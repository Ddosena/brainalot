"""Synthetic course source contracts; no files, services or owner data read."""
from copy import deepcopy

import pytest

from megamozg.work_period import course_rules
from megamozg.core import ValidationError


def state():
    return {"projects": [{"id": "p"}, {"id": "q"}], "items": [{"id": "raw"}],
            "planning": {"tasks": [{"id": "native", "project_id": "p"},
                                   {"id": "setup", "project_id": "p"}],
                         "completed_cubes": {"fact": {"percent": 100}},
                         "course_catalog": {"items": [{"id": "reference", "owned_by_user": False}]}},
            "period_planner": {"context": {"task_id": "native", "cube_id": "fact"},
                               "visibility": {"p": False},
                               "periods": {"setup": {"start": "2027-02-01", "end": "2027-02-02"}},
                               "placements": {"fact": {"date": "2027-02-01", "start": "12:00"}}}}


def source():
    package = {"id": "package", "project_id": "p", "full_name": "Example screencast package",
               "short_name": "Example", "source_work_task_id": "native",
               "window_start": "2027-02-09", "window_end": "2027-02-15", "deadline": "2027-02-15",
               "window_source": "synthetic-supplied-source", "window_source_locator": "row 2",
               "full_completion": {"value": None, "reason": "No full completion recorded"}}
    component = {"id": "slides", "project_id": "p", "package_id": "package",
                 "kind": "presentation", "full_name": "Example presentation", "short_name": "Slides",
                 "source_work_task_id": "native", "due": None, "individual_due": None,
                 "window_start": "2027-02-09", "window_end": "2027-02-15",
                 "full_completion": {"value": False}, "evidence": [{"source": "synthetic brief", "locator": "section 2"}]}
    material = {"id": "material", "project_id": "q", "kind": "longread",
                "full_name": "Example authored material", "short_name": "Material", "due": "2027-01-20",
                "due_status": "source_sheet", "full_completion": {"value": True, "reason": "Current source ready"},
                "completion_basis": "latest_primary_sheet_and_owner_confirmed",
                "evidence": [{"source": "synthetic current sheet", "status": "ready"}]}
    return {"schema": course_rules.SCHEMA, "private": True, "checked_on": "2027-01-01",
            "packages": [package], "rows": [component, material], "auxiliary": [{"id": "setup"}]}


def projects():
    return [{"id": "p", "visible": False, "title": "Project P", "start": "2026-01-01", "end": "2029-12-31",
             "tasks": [{"id": "native", "title": "Native task", "owned": True, "work_progress_percent": 20},
                       {"id": "setup", "title": "Auxiliary preparation", "owned": True, "date": "2027-02-01", "auxiliary": True},
                       {"id": "reference", "title": "Outside owner scope", "owned": False, "date": "2029-12-31"}]},
            {"id": "q", "visible": True, "title": "Project Q", "tasks": []}]


def imported():
    return course_rules.apply_source(state(), source())


def test_two_official_package_events_and_components_only_in_details():
    result = course_rules.enrich(imported(), projects())[0]
    assert [(e["event_kind"], e["date"]) for e in result["timeline_entries"]] == [
        ("start", "2027-02-09"), ("deadline", "2027-02-15")]
    assert all(e["task_id"] == "package" and e["source_refs"] for e in result["timeline_entries"])
    assert all(e["read_only"] for e in result["timeline_entries"])
    slides = next(t for t in result["tasks"] if t["id"] == "slides")
    assert slides["individual_due"] is None and slides["deadline"] is None
    assert slides["window_start"] == "2027-02-09" and slides["window_end"] == "2027-02-15"
    assert slides["timeline_visible"] is False
    package = next(t for t in result["tasks"] if t["id"] == "package")
    assert package["component_ids"] == ["slides"]
    assert package["components"][0] == slides
    assert package["factual_work_progress"]["percent"] == 20
    assert package["full_completion"] is None and package["full_checkmark"] is False


def test_latest_source_material_completion_does_not_change_native_work_progress():
    current = imported()
    output = course_rules.enrich(current, projects())
    material = output[1]["tasks"][0]
    assert material["full_completion"] is True and material["full_checkmark"] is True
    assert material["completion_basis"] == "latest_primary_sheet_and_owner_confirmed"
    assert material["work_progress_percent"] is None
    assert output[1]["timeline_entries"][0]["event_kind"] == "deadline"
    assert len(output[1]["timeline_entries"]) == 1
    assert current["planning"] == state()["planning"]


@pytest.mark.parametrize("percent, expected", [(None, "available"), (0, "available"), (20, "started")])
def test_incomplete_assignment_is_started_only_with_positive_factual_work_progress(percent, expected):
    src = source()
    src["packages"][0].update(full_completion={"value": False, "reason": "Incomplete assignment"},
                                status="source_partial_annotation")
    original_projects = projects()
    original_projects[0]["tasks"][0]["work_progress_percent"] = percent
    output = course_rules.enrich(course_rules.apply_source(state(), src), original_projects)
    package = next(t for t in output[0]["tasks"] if t["id"] == "package")
    assert package["status"] == expected
    assert package["source_status"] == "source_partial_annotation"
    assert package["status_note"] == "Incomplete assignment"
    assert package["full_completion"] is False and package["full_checkmark"] is False


def test_partial_first_package_and_historical_dates_never_make_current_events():
    src = source()
    package = src["packages"][0]
    package.update(window_start=None, window_end=None, deadline=None,
                   historical_window={"start": "2026-09-29", "end": "2026-10-13", "crossed_out": True},
                   full_completion={"value": False, "reason": "Only analysis and slides are done"})
    src["rows"][0].update(window_start=None, window_end=None)
    result = course_rules.enrich(course_rules.apply_source(state(), src), projects())[0]
    assert result["timeline_entries"] == []
    assert result["start"] is None and result["end"] is None
    package = next(t for t in result["tasks"] if t["id"] == "package")
    assert package["full_completion"] is False and package["full_checkmark"] is False
    assert package["historical_window"]["end"] == "2026-10-13"
    assert result["undated_task_ids"] == ["package"]


def test_apply_and_enrich_copy_preserve_native_visibility_owner_periods_and_auxiliary():
    original, original_projects, src = state(), projects(), source()
    before, before_projects, before_source = deepcopy(original), deepcopy(original_projects), deepcopy(src)
    saved = course_rules.apply_source(original, src)
    output = course_rules.enrich(saved, original_projects)
    assert original == before and original_projects == before_projects and src == before_source
    assert {k: v for k, v in saved.items() if k != "period_planner"} == {k: v for k, v in before.items() if k != "period_planner"}
    for key in ("periods", "placements", "visibility", "context"):
        assert saved["period_planner"][key] == before["period_planner"][key]
    assert output[0]["visible"] is False and output[1]["visible"] is True
    assert output[0]["start"] == "2027-02-09" and output[0]["end"] == "2027-02-15"
    extras = {t["id"]: t for t in output[0]["tasks"]}
    assert extras["setup"]["auxiliary"] and not extras["setup"]["timeline_visible"]
    assert extras["reference"]["owned"] is False and not extras["reference"]["timeline_visible"]
    assert not any(e["task_id"] in {"setup", "reference", "native"} for e in output[0]["timeline_entries"])
    saved["period_planner"]["course_source"]["packages"][0]["short_name"] = "Mutated saved copy"
    assert src == before_source


@pytest.mark.parametrize("selection", ["native", "setup", "package", "slides", "material", "raw", "reference"])
def test_import_preserves_valid_native_component_reference_context(selection):
    original = state()
    original["period_planner"]["context"]["task_id"] = selection
    result = course_rules.apply_source(original, source())
    assert result["period_planner"]["context"]["task_id"] == selection
    assert result["period_planner"]["context"]["cube_id"] == "fact"


def test_import_clears_only_stale_task_selection():
    original = state()
    original["period_planner"]["context"].update(task_id="removed-source-id", date="2027-02-01")
    result = course_rules.apply_source(original, source())
    assert result["period_planner"]["context"] == {"task_id": None, "cube_id": "fact", "date": "2027-02-01"}


def test_explicit_reference_scope_excludes_canonical_package_and_material_events():
    src = source()
    src["packages"][0]["owned_by_user"] = False
    src["rows"][1]["scope_exempt"] = True
    result = course_rules.enrich(course_rules.apply_source(state(), src), projects())
    assert all(not p["timeline_entries"] for p in result)
    assert all(t["owned"] is False for p in result for t in p["tasks"] if t["id"] in {"package", "slides", "material"})


@pytest.mark.parametrize("bad", ["true", 1, 0, [], {"value": "true"}])
def test_completion_rejects_truthy_values_instead_of_bool(bad):
    src = source()
    src["packages"][0]["full_completion"] = bad
    with pytest.raises(ValidationError):
        course_rules.apply_source(state(), src)


@pytest.mark.parametrize("mutation", [
    lambda s: s.update(private=False),
    lambda s: s["rows"].append(deepcopy(s["rows"][0])),
    lambda s: s["packages"][0].update(window_start="2027-02-31"),
    lambda s: s["packages"][0].update(window_start="2027-02-16"),
    lambda s: s["packages"][0].update(window_start=None),
    lambda s: s["rows"][0].update(due="2027-02-14"),
    lambda s: s["rows"][0].update(window_end="2027-02-14"),
    lambda s: s["rows"][0].update(package_id="missing"),
    lambda s: s["rows"][0].update(project_id="q"),
    lambda s: s["rows"][0].update(project_id=[]),
    lambda s: s["rows"][0].update(package_id=[]),
    lambda s: s["rows"][0].update(source_work_task_id="missing"),
])
def test_invalid_source_rejected_without_mutation(mutation):
    original = state()
    before, src = deepcopy(original), source()
    mutation(src)
    with pytest.raises(ValidationError):
        course_rules.apply_source(original, src)
    assert original == before


def test_existing_owner_parent_name_is_used_without_renaming_components():
    current = imported()
    current["period_planner"]["task_titles"] = {"native": "Owner parent name"}
    output = course_rules.enrich(current, projects())[0]
    parent = next(t for t in output["tasks"] if t["id"] == "package")
    child = next(t for t in output["tasks"] if t["id"] == "slides")
    assert parent["title"] == "Owner parent name"
    assert all(e["label"] == "Owner parent name" for e in output["timeline_entries"])
    assert child["title"] == "Example presentation"


def test_no_source_leaves_projects_equal_and_independent():
    original = projects()
    result = course_rules.enrich(state(), original)
    assert result == original and result is not original
