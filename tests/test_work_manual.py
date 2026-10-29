"""Manual Work plan without an agent (owner request 2026-10-27).

The owner must be able to start from an empty vault and build project →
tasks → task days → cubes by hand; agent imports keep those manual tasks.
"""
from datetime import date, timedelta
from copy import deepcopy

import pytest

from megamozg.core import ConflictError, ValidationError
from megamozg.work_period import model
from megamozg.work_store import WorkStore
from test_work_planning import op, planning, seed


def day_op(planner, action, data, request_id=None):
    current = planner.store.read()["revision"]
    return planner.day_command({"request_id": request_id or f"{action}-{current}", "expected_revision": current,
                                "action": action, "data": data})


def week_op(planner, action, data):
    current = planner.store.read()["revision"]
    return planner.command({"request_id": f"week-{action}-{current}", "expected_revision": current,
                            "action": action, "data": data})


@pytest.fixture
def planner(tmp_path):
    return model.PeriodPlanner(WorkStore(tmp_path))


def started(planner):
    day_op(planner, "manual_start", {"timezone": "Europe/Moscow"})
    day_op(planner, "manual_project", {"name": "3D-печать", "color": "#d50000"})
    view = planner.model()
    today = date.fromisoformat(view["today"])
    monday = today + timedelta(days=7 - today.weekday())
    return view["projects"][0], monday


def test_empty_vault_builds_project_task_period_and_cubes_by_hand(planner):
    assert planner.store.read()["initialized"] is False
    day_op(planner, "manual_start", {"timezone": "Europe/Moscow"})
    assert planner.model()["projects"] == [] and planner.model()["tasks"] == []
    with pytest.raises(ConflictError):
        day_op(planner, "manual_start", {"timezone": "Europe/Moscow"})
    day_op(planner, "manual_project", {"name": "3D-печать", "color": "#d50000"})
    project = planner.model()["projects"][0]
    assert project["title"] == "3D-печать" and project["color"].startswith("#")
    today = date.fromisoformat(planner.model()["today"])
    first = today + timedelta(days=7 - today.weekday())
    start, end = first.isoformat(), (first + timedelta(days=4)).isoformat()
    day_op(planner, "manual_task", {"project_id": project["id"], "title": "Подставка", "minutes": 120,
                                    "start": start, "end": end, "deadline": end})
    task = planner.model()["tasks"][0]
    assert (task["title"], task["start"], task["end"], task["deadline"], task["minutes"]) == ("Подставка", start, end, end, 120)
    day_op(planner, "manual_cube", {"task_id": task["id"], "title": "Моделирование", "minutes": 60, "date": start})
    cubes = [cube for cube in planner.model()["cubes"] if cube["task_id"] == task["id"]]
    assert [(cube["title"], cube["minutes"], cube["planned_date"]) for cube in cubes] == [("Моделирование", 60, start)]
    # The owner sizes a manual task by its cubes: 60 + 90 > 120 grows the volume.
    day_op(planner, "manual_cube", {"task_id": task["id"], "title": "Печать", "minutes": 90, "date": start})
    assert planner.model()["tasks"][0]["minutes"] == 150
    cube = next(cube for cube in planner.model()["cubes"] if cube["title"] == "Печать")
    week_op(planner, "cube_select", {"cube_id": cube["id"], "date": start})
    week_op(planner, "cube_time", {"cube_id": cube["id"], "start": "12:00"})
    placed = next(row for row in planner.model()["cubes"] if row["id"] == cube["id"])
    assert (placed["selected_date"], placed["start"], placed["end"]) == (start, "12:00", "13:30")


def test_manual_task_edit_readiness_and_delete(planner):
    project, monday = started(planner)
    day_op(planner, "manual_task", {"project_id": project["id"], "title": "Черновик", "minutes": 60})
    task = planner.model()["tasks"][0]
    assert task["start"] is None and task["deadline"] is None
    end = (monday + timedelta(days=2)).isoformat()
    day_op(planner, "manual_task", {"task_id": task["id"], "project_id": project["id"], "title": "Чистовик",
                                    "minutes": 100, "start": monday.isoformat(), "end": end, "deadline": None})
    task = planner.model()["tasks"][0]
    assert (task["title"], task["minutes"], task["start"], task["end"]) == ("Чистовик", 100, monday.isoformat(), end)
    today = planner.model()["today"]
    day_op(planner, "day_outcome", {"task_id": task["id"], "date": today, "remaining_minutes": 50, "done": False})
    assert planner.model()["tasks"][0]["progress_percent"] == 50
    day_op(planner, "day_outcome", {"task_id": task["id"], "date": today, "remaining_minutes": 0, "done": True})
    assert planner.model()["tasks"][0]["status"] == "completed"
    # Done work stays in history: the task only leaves the work ahead.
    day_op(planner, "manual_task_delete", {"task_id": task["id"]})
    assert not planner.model()["tasks"]
    assert any(row["id"] == task["id"] for row in planner.store.read()["state"]["planning"]["tasks"])
    day_op(planner, "manual_task", {"project_id": project["id"], "title": "Лишняя", "minutes": 30})
    spare = next(row for row in planner.model()["tasks"] if row["title"] == "Лишняя")
    assert spare["origin"] == "manual"
    # A cube added on a day makes that day part of the task, so the cube is visible there.
    day_op(planner, "manual_cube", {"task_id": spare["id"], "title": "Кусочек", "minutes": 30, "date": end})
    spare = next(row for row in planner.model()["tasks"] if row["id"] == spare["id"])
    assert (spare["start"], spare["end"]) == (end, end)
    day_op(planner, "manual_task_delete", {"task_id": spare["id"]})
    view = planner.model()
    assert spare["id"] not in {row["id"] for row in view["tasks"]}
    assert not [cube for cube in view["cubes"] if cube["task_id"] == spare["id"]]


def test_project_rename_and_delete_only_without_tasks(planner):
    project, _ = started(planner)
    day_op(planner, "manual_project", {"project_id": project["id"], "name": "Печать", "color": "#f4511e"})
    renamed = planner.model()["projects"][0]
    assert (renamed["title"], renamed["color"]) == ("Печать", "#f4511e")
    day_op(planner, "manual_task", {"project_id": project["id"], "title": "Задача", "minutes": 30})
    deleted = day_op(planner, "manual_project_delete", {"project_id": project["id"]})
    assert planner.model()["projects"] == []
    assert planner.model()["tasks"] == []
    week_op(planner, "undo", {"revision": deleted["revision"]})
    assert len(planner.model()["projects"]) == 1 and len(planner.model()["tasks"]) == 1


def test_rejects_bad_manual_input(planner):
    project, monday = started(planner)
    with pytest.raises(ValidationError, match="Конец периода"):
        day_op(planner, "manual_task", {"project_id": project["id"], "title": "Задача", "minutes": 30,
                                        "start": monday.isoformat(), "end": (monday - timedelta(days=1)).isoformat()})
    with pytest.raises(ValidationError, match="шаг 5"):
        day_op(planner, "manual_task", {"project_id": project["id"], "title": "Задача", "minutes": 33})
    with pytest.raises(ValidationError, match="Проект не найден"):
        day_op(planner, "manual_task", {"project_id": "missing", "title": "Задача", "minutes": 30})


def test_agent_import_keeps_manual_tasks_and_agent_tasks_stay_agent_owned(tmp_path):
    work = seed(tmp_path)
    op(work, "first", "import", {"planning": planning()})
    planner = model.PeriodPlanner(work)
    day_op(planner, "manual_task", {"project_id": "primary", "title": "Своя задача", "minutes": 45})
    day_op(planner, "manual_task", {"task_id": "a", "project_id": "primary", "title": "A", "minutes": 30})
    op(work, "second", "import", {"planning": planning()})
    titles = {row["title"] for row in planner.model()["tasks"]}
    assert {"Своя задача", "A"} <= titles
    assert next(row for row in planner.model()["tasks"] if row["id"] == "a")["minutes"] == 30


def test_cubes_beyond_a_goal_volume_grow_it_with_a_warning_instead_of_an_error(planner):
    # Owner decision 2026-10-28: no blocking "budget"/"remaining" errors.
    project, monday = started(planner)
    day_op(planner, "manual_task", {"project_id": project["id"], "title": "Цель", "minutes": 60,
                                    "start": monday.isoformat(), "end": monday.isoformat()})
    task = planner.model()["tasks"][0]
    result = day_op(planner, "constructor_cube", {"cube_id": "extra-cube", "kind": "task", "task_id": task["id"],
                                                  "title": "Лишний час", "minutes": 120})
    assert planner.model()["tasks"][0]["minutes"] == 120
    assert any("вырос" in warning for warning in result["warnings"])


def test_calendar_palette_required_and_quick_volume(planner):
    day_op(planner, 'manual_start', {'timezone': 'Europe/Moscow'})
    with pytest.raises(ValidationError):
        day_op(planner, 'manual_project', {'name': 'No color'})
    with pytest.raises(ValidationError):
        day_op(planner, 'manual_project', {'name': 'Bad color', 'color': '#123456'})
    day_op(planner, 'manual_project', {'name': 'Project', 'color': '#d50000'})
    project = planner.model()['projects'][0]
    day_op(planner, 'manual_task', {'project_id': project['id'], 'title': 'Quick', 'minutes': 5, 'volume_by_cubes': True})
    task = planner.model()['tasks'][0]
    day_op(planner, 'manual_cube', {'task_id': task['id'], 'title': 'A', 'minutes': 30})
    day_op(planner, 'manual_cube', {'task_id': task['id'], 'title': 'B', 'minutes': 45})
    task = planner.model()['tasks'][0]
    assert task['volume_by_cubes'] is True and task['minutes'] == 75


def test_batch_create_place_and_undo_is_atomic(planner):
    project, monday = started(planner)
    day_op(planner, 'manual_task', {'project_id': project['id'], 'title': 'Task', 'minutes': 60})
    task = planner.model()['tasks'][0]
    result = week_op(planner, 'ui_batch', {'commands': [
        {'route': 'planning', 'action': 'manual_cube', 'data': {'task_id': task['id'], 'title': 'New', 'minutes': 30}},
        {'route': 'period', 'action': 'cube_drop', 'data': {'cube_id': '$created_cube', 'date': monday.isoformat(), 'start': '12:00'}}]})
    cube = next(c for c in result['cubes'] if c['title'] == 'New')
    assert (cube['selected_date'], cube['start']) == (monday.isoformat(), '12:00')
    week_op(planner, 'undo', {'revision': result['revision']})
    assert not any(c['title'] == 'New' for c in planner.model()['cubes'])


def test_batch_failure_does_not_create_cube(planner):
    project, monday = started(planner)
    day_op(planner, 'manual_task', {'project_id': project['id'], 'title': 'Task', 'minutes': 60})
    task = planner.model()['tasks'][0]
    before = planner.store.read()
    with pytest.raises(ValidationError):
        week_op(planner, 'ui_batch', {'commands': [
            {'route': 'planning', 'action': 'manual_cube', 'data': {'task_id': task['id'], 'title': 'New', 'minutes': 30}},
            {'route': 'period', 'action': 'cube_drop', 'data': {'cube_id': '$created_cube', 'date': monday.isoformat(), 'start': '99:00'}}]})
    assert planner.store.read() == before


def test_reassign_manual_cube_to_other_goal(planner):
    project, monday = started(planner)
    for title in ('First', 'Second'):
        day_op(planner, 'manual_task', {'project_id': project['id'], 'title': title, 'minutes': 60})
    first, second = planner.model()['tasks']
    day_op(planner, 'manual_cube', {'task_id': first['id'], 'title': 'Cube', 'minutes': 30})
    cube = next(c for c in planner.model()['cubes'] if c['title'] == 'Cube')
    week_op(planner, 'cube_select', {'cube_id': cube['id'], 'date': monday.isoformat()})
    week_op(planner, 'cube_reassign', {'cube_id': cube['id'], 'task_id': second['id']})
    view = planner.model()
    assert next(c for c in view['cubes'] if c['id'] == cube['id'])['task_id'] == second['id']
    assert next(t for t in view['tasks'] if t['id'] == second['id'])['start'] == monday.isoformat()


def test_unplaced_cube_completed_becomes_today_trophy(planner):
    project, _ = started(planner)
    day_op(planner, 'manual_task', {'project_id': project['id'], 'title': 'Task', 'minutes': 30})
    task = planner.model()['tasks'][0]
    day_op(planner, 'manual_cube', {'task_id': task['id'], 'title': 'Cube', 'minutes': 30})
    cube = next(c for c in planner.model()['cubes'] if c['title'] == 'Cube')
    day_op(planner, 'cube_progress', {'cube_id': cube['id'], 'percent': 100})
    view = planner.model()
    cube = next(c for c in view['cubes'] if c['title'] == 'Cube')
    assert cube['selected_date'] == view['today'] and cube['status'] == 'completed'
    assert next(t for t in view['tasks'] if t['id'] == task['id'])['status'] == 'completed'


def test_quick_volume_persists_and_shrinks_silently_after_cube_delete(planner):
    project, _ = started(planner)
    day_op(planner, 'manual_task', {'project_id': project['id'], 'title': 'Quick', 'minutes': 5, 'volume_by_cubes': True})
    task = planner.model()['tasks'][0]
    a = day_op(planner, 'manual_cube', {'task_id': task['id'], 'title': 'A', 'minutes': 30})
    b = day_op(planner, 'manual_cube', {'task_id': task['id'], 'title': 'B', 'minutes': 45})
    assert not a.get('warnings') and not b.get('warnings')
    native = next(t for t in planner.store.read()['state']['planning']['tasks'] if t['id'] == task['id'])
    assert native['estimate_minutes']['central'] == 75
    cube = next(c for c in planner.model()['cubes'] if c['title'] == 'B')
    week_op(planner, 'cube_delete', {'cube_id': cube['id']})
    native = next(t for t in planner.store.read()['state']['planning']['tasks'] if t['id'] == task['id'])
    assert native['estimate_minutes']['central'] == 30


def test_agent_goal_edit_and_delete_preserve_native_facts_and_undo(tmp_path):
    work = seed(tmp_path)
    op(work, 'first', 'import', {'planning': planning()})
    planner = model.PeriodPlanner(work)
    before = next(t.copy() for t in work.read()['state']['planning']['tasks'] if t['id'] == 'a')
    day_op(planner, 'manual_task', {'task_id': 'a', 'project_id': 'primary', 'title': 'Owner', 'minutes': 90,
        'start': '2027-01-05', 'end': '2027-01-06', 'deadline': '2027-01-07'})
    task = next(t for t in planner.model()['tasks'] if t['id'] == 'a')
    assert (task['title'], task['minutes'], task['deadline']) == ('Owner', 90, '2027-01-07')
    assert next(t for t in work.read()['state']['planning']['tasks'] if t['id'] == 'a') == before
    result = day_op(planner, 'manual_task_delete', {'task_id': 'a'})
    assert all(t['id'] != 'a' for t in planner.model()['tasks'])
    assert next(t for t in work.read()['state']['planning']['tasks'] if t['id'] == 'a') == before
    week_op(planner, 'undo', {'revision': result['revision']})
    assert any(t['id'] == 'a' for t in planner.model()['tasks'])


def test_goal_progress_with_cubes_ignores_legacy_done_outcome(planner):
    project, _ = started(planner)
    day_op(planner, 'manual_task', {'project_id': project['id'], 'title': 'Task', 'minutes': 30})
    task = planner.model()['tasks'][0]
    day_op(planner, 'manual_cube', {'task_id': task['id'], 'title': 'Cube', 'minutes': 30})
    day_op(planner, 'day_outcome', {'task_id': task['id'], 'date': planner.model()['today'],
        'remaining_minutes': 0, 'done': True})
    view = planner.model()
    goal = next(row for row in view['tasks'] if row['id'] == task['id'])
    assert goal['progress_percent'] == 0 and goal['status'] == 'available'
    assert any(row['task_id'] == task['id'] and row['done'] for row in planner.store.read()['state']['planning']['day_outcomes'])


def test_project_tombstone_survives_agent_import(tmp_path):
    work = seed(tmp_path)
    op(work, 'initial-agent', 'import', {'planning': planning()})
    planner = model.PeriodPlanner(work)
    deleted = day_op(planner, 'manual_project_delete', {'project_id': 'primary'})
    op(work, 'next-agent', 'import', {'planning': planning()})
    view = planner.model()
    assert all(p['id'] != 'primary' for p in view['projects'])
    assert all(t['project_id'] != 'primary' for t in view['tasks'])
    week_op(planner, 'undo', {'revision': deleted['revision']})
    assert any(p['id'] == 'primary' for p in planner.model()['projects'])


def test_goal_deadline_edit_after_shift_and_undo_keeps_shifted_cube(planner):
    project, monday = started(planner)
    first = monday.isoformat()
    end = (monday + timedelta(days=6)).isoformat()
    deadline = (monday + timedelta(days=13)).isoformat()
    day_op(planner, 'manual_task', {'project_id': project['id'], 'title': 'Task', 'minutes': 30,
        'start': first, 'end': end, 'deadline': deadline})
    task = planner.model()['tasks'][0]
    day_op(planner, 'manual_cube', {'task_id': task['id'], 'title': 'Cube', 'minutes': 30})
    cube = next(c for c in planner.model()['cubes'] if c['title'] == 'Cube')
    week_op(planner, 'cube_select', {'cube_id': cube['id'], 'date': first, 'start': '12:00'})
    shifted = week_op(planner, 'period_shift', {'task_id': task['id'],
        'start': (monday + timedelta(days=1)).isoformat(), 'end': (monday + timedelta(days=7)).isoformat()})
    expected_shift = (monday + timedelta(days=1)).isoformat()
    shifted_deadline = (monday + timedelta(days=14)).isoformat()
    assert next(t for t in shifted['tasks'] if t['id'] == task['id'])['deadline'] == shifted_deadline
    edited = day_op(planner, 'manual_task', {'task_id': task['id'], 'project_id': project['id'], 'title': 'Task',
        'minutes': 30, 'start': expected_shift, 'end': (monday + timedelta(days=7)).isoformat(), 'deadline': deadline})
    assert next(t for t in planner.model()['tasks'] if t['id'] == task['id'])['deadline'] == deadline
    week_op(planner, 'undo', {'revision': edited['revision']})
    view = planner.model()
    assert next(t for t in view['tasks'] if t['id'] == task['id'])['deadline'] == shifted_deadline
    placed = next(c for c in view['cubes'] if c['id'] == cube['id'])
    assert (placed['selected_date'], placed['start']) == (expected_shift, '12:00')


def test_shelf_cube_partial_fact_then_completion_and_reopen(planner):
    project, _ = started(planner)
    day_op(planner, 'manual_task', {'project_id': project['id'], 'title': 'Task', 'minutes': 30})
    task = planner.model()['tasks'][0]
    day_op(planner, 'manual_cube', {'task_id': task['id'], 'title': 'Cube', 'minutes': 30})
    cube = next(c for c in planner.model()['cubes'] if c['title'] == 'Cube')
    cube_id = cube['id']
    before = planner.store.read()['state']['planning']['constructor']['cubes'][cube_id].copy()
    day_op(planner, 'cube_progress', {'cube_id': cube_id, 'percent': 20, 'actual_minutes': 3})
    view = planner.model()
    row = next(c for c in view['cubes'] if c['id'] == cube_id)
    assert row['selected_date'] is None and row['progress_percent'] == 20
    day_row = next(c for c in planner.day_model()['planning']['result']['constructor']['cubes'] if c['id'] == cube_id)
    assert day_row['report_date'] == view['today']
    native = planner.store.read()['state']['planning']
    assert native['completed_cubes'][cube_id]['date'] == view['today']
    assert native['constructor']['cubes'][cube_id] == before
    assert not any(lock['cube_id'] == cube_id for lock in native['locks'])
    day_op(planner, 'cube_progress', {'cube_id': cube_id, 'percent': 100, 'actual_minutes': 10})
    row = next(c for c in planner.model()['cubes'] if c['id'] == cube_id)
    assert row['selected_date'] == view['today'] and row['status'] == 'completed'
    day_op(planner, 'cube_progress', {'cube_id': cube_id, 'percent': 80, 'actual_minutes': 10})
    row = next(c for c in planner.model()['cubes'] if c['id'] == cube_id)
    assert row['selected_date'] == view['today'] and row['progress_percent'] == 80


def test_manual_cube_resize_uses_final_interval_and_grows_volume(planner):
    project, monday = started(planner)
    day_op(planner, 'manual_task', {'project_id': project['id'], 'title': 'Task', 'minutes': 120})
    task = planner.model()['tasks'][0]
    for title in ('First', 'Next'):
        day_op(planner, 'manual_cube', {'task_id': task['id'], 'title': title, 'minutes': 60})
    cubes = {c['title']: c for c in planner.model()['cubes']}
    for title, start in (('First', '13:00'), ('Next', '14:00')):
        week_op(planner, 'cube_drop', {'cube_id': cubes[title]['id'], 'date': monday.isoformat(), 'start': start})
    assert next(c for c in planner.model()['cubes'] if c['id'] == cubes['First']['id'])['can_resize']
    result = week_op(planner, 'cube_resize', {'cube_id': cubes['First']['id'], 'date': monday.isoformat(),
        'start': '12:45', 'minutes': 75})
    cube = next(c for c in result['cubes'] if c['id'] == cubes['First']['id'])
    assert (cube['start'], cube['end'], cube['minutes']) == ('12:45', '14:00', 75)
    assert next(t for t in result['tasks'] if t['id'] == task['id'])['minutes'] == 135
    assert any('вырос' in warning for warning in result['warnings'])
    state = planner.store.read()
    for minutes in (0, 3, 16, 255):
        with pytest.raises(ValidationError):
            week_op(planner, 'cube_resize', {'cube_id': cube['id'], 'date': monday.isoformat(), 'start': '12:45', 'minutes': minutes})
        assert planner.store.read() == state
    with pytest.raises(ValidationError):
        week_op(planner, 'cube_resize', {'cube_id': cube['id'], 'date': monday.isoformat(), 'start': '12:45', 'minutes': 90})
    assert planner.store.read() == state


def test_manual_cube_resize_shelf_bounds_and_factual_protection(planner):
    project, _ = started(planner)
    day_op(planner, 'manual_task', {'project_id': project['id'], 'title': 'Task', 'minutes': 30})
    task = planner.model()['tasks'][0]
    day_op(planner, 'manual_cube', {'task_id': task['id'], 'title': 'Cube', 'minutes': 30})
    cube = next(c for c in planner.model()['cubes'] if c['title'] == 'Cube')
    for minutes in (240, 15):
        resized = week_op(planner, 'cube_resize', {'cube_id': cube['id'], 'date': None, 'start': None, 'minutes': minutes})
        assert next(c for c in resized['cubes'] if c['id'] == cube['id'])['minutes'] == minutes
    day_op(planner, 'cube_progress', {'cube_id': cube['id'], 'percent': 20, 'actual_minutes': 3})
    before = planner.store.read()
    resized = week_op(planner, 'cube_resize', {'cube_id': cube['id'], 'date': None, 'start': None, 'minutes': 30})
    assert next(row for row in resized['cubes'] if row['id'] == cube['id'])['minutes'] == 30
    assert planner.store.read()['state']['planning'] == before['state']['planning']


def test_delete_original_goal_keeps_reassigned_cube_and_native_source(planner):
    project, _ = started(planner)
    for title in ('Original', 'Destination'):
        day_op(planner, 'manual_task', {'project_id': project['id'], 'title': title, 'minutes': 30})
    original, target = planner.model()['tasks']
    day_op(planner, 'manual_cube', {'task_id': original['id'], 'title': 'Cube', 'minutes': 30})
    cube = next(c for c in planner.model()['cubes'] if c['title'] == 'Cube')
    week_op(planner, 'cube_reassign', {'cube_id': cube['id'], 'task_id': target['id']})
    day_op(planner, 'manual_task_delete', {'task_id': original['id']})
    assert next(c for c in planner.model()['cubes'] if c['id'] == cube['id'])['task_id'] == target['id']
    native = planner.store.read()['state']['planning']
    assert native['constructor']['cubes'][cube['id']]['task_id'] == original['id']
    assert any(t['id'] == original['id'] for t in native['tasks'])


def test_delete_source_goal_preserves_reassigned_merge_and_later_fact(planner):
    project, monday = started(planner)
    for title in ('Original', 'Destination'):
        day_op(planner, 'manual_task', {'project_id': project['id'], 'title': title, 'minutes': 60})
    original, target = planner.model()['tasks']
    for title in ('First', 'Second'):
        day_op(planner, 'manual_cube', {'task_id': original['id'], 'title': title, 'minutes': 30})
    leaves = [cube['id'] for cube in planner.model()['cubes'] if cube['task_id'] == original['id']]
    combined = week_op(planner, 'cube_merge', {'cube_ids': leaves, 'title': 'Combined'})
    cube = next(row for row in combined['cubes'] if row['id'].startswith('merged-'))
    week_op(planner, 'cube_reassign', {'cube_id': cube['id'], 'task_id': target['id']})
    before = planner.store.read()['state']['planning']
    leaf_sources = {cid: before['constructor']['cubes'][cid] for cid in leaves}
    deleted = day_op(planner, 'manual_task_delete', {'task_id': original['id']})
    native = planner.store.read()['state']['planning']
    assert {cid: native['constructor']['cubes'][cid] for cid in leaves} == leaf_sources
    assert any(task['id'] == original['id'] for task in native['tasks'])
    moved = next(row for row in planner.model()['cubes'] if row['id'] == cube['id'])
    assert moved['task_id'] == target['id'] and moved['available'] and not moved.get('allocation_stale')
    day_op(planner, 'cube_progress', {'cube_id': cube['id'], 'percent': 20, 'actual_minutes': 12})
    facts = planner.store.read()['state']['planning']['completed_cubes']
    assert all(facts[cid]['task_id'] == original['id'] and facts[cid]['percent'] == 20 for cid in leaves)
    week_op(planner, 'undo', {'revision': deleted['revision']})
    assert any(task['id'] == original['id'] for task in planner.model()['tasks'])
    assert planner.store.read()['state']['planning']['completed_cubes'] == facts


def test_repeatable_template_makes_independent_instances_and_preserves_facts(planner):
    _, monday = started(planner)
    view = planner.model()
    builtin = next(r for r in view['repeatables'] if r['id'] == 'prep-universal')
    assert builtin['title'] == 'П. к уроку' and builtin['minutes'] == 25
    first = week_op(planner, 'repeatable_drop', {'repeatable_id': builtin['id'], 'date': monday.isoformat(), 'start': '12:00'})
    c1 = next(c for c in first['cubes'] if c.get('repeatable_id') == builtin['id'])
    second = week_op(planner, 'repeatable_drop', {'repeatable_id': builtin['id'], 'date': monday.isoformat(), 'start': '13:00'})
    rows = [c for c in second['cubes'] if c.get('repeatable_id') == builtin['id']]
    assert len(rows) == 2 and len({c['id'] for c in rows}) == 2
    c2 = next(c for c in rows if c['id'] != c1['id'])
    assert all(c['kind'] == 'repeatable' and c['minutes'] == builtin['minutes'] for c in rows)
    day_op(planner, 'cube_progress', {'cube_id': c1['id'], 'percent': 20, 'actual_minutes': 5})
    view = planner.model()
    assert next(c for c in view['cubes'] if c['id'] == c1['id'])['progress_percent'] == 20
    assert next(c for c in view['cubes'] if c['id'] == c2['id'])['progress_percent'] == 0
    day_op(planner, 'cube_progress', {'cube_id': c1['id'], 'percent': 100, 'actual_minutes': 25})
    assert next(c for c in planner.model()['cubes'] if c['id'] == c1['id'])['status'] == 'completed'
    week_op(planner, 'cube_return', {'cube_id': c2['id']})
    assert all(c['id'] != c2['id'] for c in planner.model()['cubes'])
    assert any(r['id'] == builtin['id'] for r in planner.model()['repeatables'])


def test_repeatable_instance_resizes_freely_without_changing_template(planner):
    # Owner report 2026-10-28: stretching «П. к уроку» was refused as a task stage.
    _, monday = started(planner)
    builtin = next(r for r in planner.model()['repeatables'] if r['id'] == 'prep-universal')
    dropped = week_op(planner, 'repeatable_drop', {'repeatable_id': builtin['id'], 'date': monday.isoformat(), 'start': '17:15'})
    instance = next(c for c in dropped['cubes'] if c.get('repeatable_id') == builtin['id'])
    assert instance['can_resize'] is True
    for start, minutes in (('17:00', 40), ('17:00', 20)):
        week_op(planner, 'cube_resize', {'cube_id': instance['id'], 'date': monday.isoformat(), 'start': start, 'minutes': minutes})
        row = next(c for c in planner.model()['cubes'] if c['id'] == instance['id'])
        assert (row['minutes'], row['start']) == (minutes, start)
    assert next(r for r in planner.model()['repeatables'] if r['id'] == builtin['id'])['minutes'] == 25


def test_make_repeatable_from_existing_cube_copies_details_without_facts(planner):
    project, monday = started(planner)
    day_op(planner, 'manual_task', {'project_id': project['id'], 'title': 'Task', 'minutes': 30})
    task = planner.model()['tasks'][0]
    day_op(planner, 'manual_cube', {'task_id': task['id'], 'title': 'Reusable', 'minutes': 30})
    cube = next(c for c in planner.model()['cubes'] if c['title'] == 'Reusable')
    day_op(planner, 'constructor_cube', {'cube_id': cube['id'], 'sources': [{'id': 's1', 'url': '', 'text': 'note'}]})
    day_op(planner, 'cube_progress', {'cube_id': cube['id'], 'percent': 20, 'actual_minutes': 5})
    saved = week_op(planner, 'repeatable_save', {'cube_id': cube['id']})
    template = next(r for r in saved['repeatables'] if r['title'] == 'Reusable')
    assert template['minutes'] == 30 and template['sources'][0]['text'] == 'note'
    dropped = week_op(planner, 'repeatable_drop', {'repeatable_id': template['id'], 'date': monday.isoformat(), 'start': '15:00'})
    instance = next(c for c in dropped['cubes'] if c.get('repeatable_id') == template['id'])
    assert instance['progress_percent'] == 0 and instance['actual_minutes'] == 0
    assert instance['sources'][0]['text'] == 'note'


def test_repeatable_partial_return_is_reselectable_without_duplicate_template(planner):
    _, monday = started(planner)
    saved = week_op(planner, 'repeatable_save', {'title': 'Water', 'minutes': 10})
    template = next(t for t in saved['repeatables'] if t['title'] == 'Water')
    dropped = week_op(planner, 'repeatable_drop', {'repeatable_id': template['id'], 'date': monday.isoformat(), 'start': '12:00'})
    cube = next(c for c in dropped['cubes'] if c.get('repeatable_id') == template['id'])
    day_op(planner, 'cube_progress', {'cube_id': cube['id'], 'percent': 20, 'actual_minutes': 2})
    facts = planner.store.read()['state']['planning']['constructor']['source_reports'][cube['id']].copy()
    returned = week_op(planner, 'cube_return', {'cube_id': cube['id']})
    row = next(c for c in returned['cubes'] if c['id'] == cube['id'])
    assert not row['available'] and row['reselectable'] and row['selected_date'] is None
    moved = week_op(planner, 'cube_drop', {'cube_id': cube['id'], 'date': monday.isoformat(), 'start': '13:00'})
    assert next(c for c in moved['cubes'] if c['id'] == cube['id'])['selected_date'] == monday.isoformat()
    assert planner.store.read()['state']['planning']['constructor']['source_reports'][cube['id']] == facts
    assert len([t for t in moved['repeatables'] if t['id'] == template['id']]) == 1


def test_repeatable_conversion_is_idempotent_and_undo_safe(planner):
    project, _ = started(planner)
    day_op(planner, 'manual_task', {'project_id': project['id'], 'title': 'Task', 'minutes': 30})
    task = planner.model()['tasks'][0]
    day_op(planner, 'manual_cube', {'task_id': task['id'], 'title': 'Reusable', 'minutes': 30})
    cube = next(c for c in planner.model()['cubes'] if c['title'] == 'Reusable')
    first = week_op(planner, 'repeatable_save', {'cube_id': cube['id']})
    first_id = next(r['id'] for r in first['repeatables'] if r['title'] == 'Reusable')
    second = week_op(planner, 'repeatable_save', {'cube_id': cube['id']})
    assert [r['id'] for r in second['repeatables'] if r['title'] == 'Reusable'] == [first_id]
    deleted = week_op(planner, 'repeatable_delete', {'repeatable_id': first_id})
    assert all(r['id'] != first_id for r in deleted['repeatables'])
    week_op(planner, 'undo', {'revision': deleted['revision']})
    assert any(r['id'] == first_id for r in planner.model()['repeatables'])


def test_pin_cube_and_template_shared_day_week_and_undo(planner):
    project, _ = started(planner)
    day_op(planner, 'manual_task', {'project_id': project['id'], 'title': 'Task', 'minutes': 30})
    task = planner.model()['tasks'][0]
    day_op(planner, 'manual_cube', {'task_id': task['id'], 'title': 'Cube', 'minutes': 30})
    cube = next(c for c in planner.model()['cubes'] if c['title'] == 'Cube')
    before = planner.store.read()['state']['planning']
    saved = week_op(planner, 'cube_pin', {'cube_id': cube['id'], 'pinned': True})
    assert next(c for c in saved['cubes'] if c['id'] == cube['id'])['pinned'] is True
    assert next(c for c in planner.day_model()['view_model']['cubes'] if c['id'] == cube['id'])['pinned'] is True
    week_op(planner, 'cube_pin', {'cube_id': 'repeatable:prep-universal', 'pinned': True})
    assert next(r for r in planner.model()['repeatables'] if r['id'] == 'prep-universal')['pinned'] is True
    week_op(planner, 'undo', {'revision': saved['revision']})
    view = planner.model()
    assert not next(c for c in view['cubes'] if c['id'] == cube['id'])['pinned']
    assert next(r for r in view['repeatables'] if r['id'] == 'prep-universal')['pinned']
    assert planner.store.read()['state']['planning'] == before


def test_pin_survives_agent_import_and_does_not_restore_deleted_cube(tmp_path):
    work = seed(tmp_path)
    op(work, 'initial-pin-import', 'import', {'planning': planning()})
    planner = model.PeriodPlanner(work)
    day_op(planner, 'manual_task', {'project_id': 'primary', 'title': 'Own', 'minutes': 30})
    task = next(t for t in planner.model()['tasks'] if t['title'] == 'Own')
    day_op(planner, 'manual_cube', {'task_id': task['id'], 'title': 'Pinned', 'minutes': 30})
    cube = next(c for c in planner.model()['cubes'] if c['title'] == 'Pinned')
    week_op(planner, 'cube_pin', {'cube_id': cube['id'], 'pinned': True})
    op(work, 'next-pin-import', 'import', {'planning': planning()})
    assert next(c for c in planner.model()['cubes'] if c['id'] == cube['id'])['pinned'] is True
    week_op(planner, 'cube_delete', {'cube_id': cube['id']})
    assert next(c for c in planner.model()['cubes'] if c['id'] == cube['id'])['status'] == 'cancelled'


def test_repeatable_drop_idempotent_replay_and_collision_is_atomic(planner):
    _, monday = started(planner)
    before = planner.store.read()['revision']
    payload = {'request_id': 'repeat-drop-once', 'expected_revision': before,
        'action': 'repeatable_drop', 'data': {'repeatable_id': 'prep-universal', 'date': monday.isoformat(), 'start': '12:00'}}
    first = planner.command(payload)
    assert planner.command(payload) == first
    assert planner.store.read()['revision'] == before + 1
    prior = planner.store.read()
    with pytest.raises(ValidationError):
        week_op(planner, 'repeatable_drop', {'repeatable_id': 'prep-universal', 'date': monday.isoformat(), 'start': '12:00'})
    assert planner.store.read() == prior


def test_repeatable_conversion_hides_only_unselected_source_and_restores_on_delete(planner):
    project, _ = started(planner)
    day_op(planner, 'manual_task', {'project_id': project['id'], 'title': 'Task', 'minutes': 30})
    task = planner.model()['tasks'][0]
    day_op(planner, 'manual_cube', {'task_id': task['id'], 'title': 'Convert', 'minutes': 30})
    cube = next(c for c in planner.model()['cubes'] if c['title'] == 'Convert')
    native_before = planner.store.read()['state']['planning']['constructor']['cubes'][cube['id']]
    saved = week_op(planner, 'repeatable_save', {'cube_id': cube['id']})
    template = next(t for t in saved['repeatables'] if t['title'] == 'Convert')
    assert all(c['id'] != cube['id'] for c in saved['cubes'])
    assert planner.store.read()['state']['planning']['constructor']['cubes'][cube['id']] == native_before
    week_op(planner, 'repeatable_delete', {'repeatable_id': template['id']})
    assert any(c['id'] == cube['id'] for c in planner.model()['cubes'])


def test_convert_selected_cube_keeps_existing_placement(planner):
    project, monday = started(planner)
    day_op(planner, 'manual_task', {'project_id': project['id'], 'title': 'Task', 'minutes': 30})
    task = planner.model()['tasks'][0]
    day_op(planner, 'manual_cube', {'task_id': task['id'], 'title': 'Selected', 'minutes': 30})
    cube = next(c for c in planner.model()['cubes'] if c['title'] == 'Selected')
    week_op(planner, 'cube_drop', {'cube_id': cube['id'], 'date': monday.isoformat(), 'start': '12:00'})
    before = planner.store.read()['state']['planning']
    saved = week_op(planner, 'repeatable_save', {'cube_id': cube['id']})
    row = next(c for c in saved['cubes'] if c['id'] == cube['id'])
    assert (row['selected_date'], row['start']) == (monday.isoformat(), '12:00')
    assert any(t['title'] == 'Selected' for t in saved['repeatables'])
    assert planner.store.read()['state']['planning'] == before


def test_repeatable_survives_reopen_and_builtin_duration_stays_fixed(planner):
    _, monday = started(planner)
    cleaned = week_op(planner, 'prep_repeatable_cleanup', {})
    assert next(t for t in cleaned['repeatables'] if t['id'] == 'prep-universal')['minutes'] == 25
    changed = deepcopy(planner.store.read()['state'])
    changed['planning']['settings']['prep_minutes'] = 45
    assert next(t for t in model.read_model({'state': changed, 'revision': 0})['repeatables'] if t['id'] == 'prep-universal')['minutes'] == 25
    result = week_op(planner, 'repeatable_drop', {'repeatable_id': 'prep-universal', 'date': monday.isoformat(), 'start': '12:00'})
    cube = next(c for c in result['cubes'] if c.get('repeatable_id') == 'prep-universal')
    day_op(planner, 'cube_progress', {'cube_id': cube['id'], 'percent': 20, 'actual_minutes': 3})
    reopened = model.PeriodPlanner(WorkStore(planner.store.root))
    assert next(c for c in reopened.model()['cubes'] if c['id'] == cube['id'])['progress_percent'] == 20
    assert next(t for t in reopened.model()['repeatables'] if t['id'] == 'prep-universal')['minutes'] == 25


def test_activity_kind_defaults_overrides_and_repeatable_snapshot(planner):
    project, monday = started(planner)
    day_op(planner, 'manual_task', {'project_id': project['id'], 'title': 'Task', 'minutes': 30})
    task = planner.model()['tasks'][0]
    day_op(planner, 'manual_cube', {'task_id': task['id'], 'title': 'Cube', 'minutes': 30})
    cube = next(c for c in planner.model()['cubes'] if c['title'] == 'Cube')
    assert cube['activity_kind'] == 'work'
    week_op(planner, 'cube_activity_set', {'cube_id': cube['id'], 'activity_kind': 'personal'})
    assert next(c for c in planner.model()['cubes'] if c['id'] == cube['id'])['activity_kind'] == 'personal'
    saved = week_op(planner, 'repeatable_save', {'cube_id': cube['id']})
    template = next(t for t in saved['repeatables'] if t['title'] == 'Cube')
    assert template['activity_kind'] == 'personal'
    instance = week_op(planner, 'repeatable_drop', {'repeatable_id': template['id'], 'date': monday.isoformat(), 'start': '12:00'})
    cube_id = next(c['id'] for c in instance['cubes'] if c.get('repeatable_id') == template['id'])
    week_op(planner, 'repeatable_save', {'repeatable_id': template['id'], 'title': 'Cube', 'minutes': 30, 'activity_kind': 'work'})
    view = planner.model()
    assert next(t for t in view['repeatables'] if t['id'] == template['id'])['activity_kind'] == 'work'
    assert next(c for c in view['cubes'] if c['id'] == cube_id)['activity_kind'] == 'personal'
    assert next(t for t in view['repeatables'] if t['id'] == 'prep-universal')['activity_kind'] == 'work'
    with pytest.raises(ValidationError):
        week_op(planner, 'cube_activity_set', {'cube_id': cube_id, 'activity_kind': 'sleep'})


def test_early_preparation_stays_independent_when_future_goal_moves_or_shrinks(planner):
    project, monday = started(planner)
    first = monday + timedelta(days=14)
    last = first + timedelta(days=6)
    day_op(planner, 'manual_task', {'project_id': project['id'], 'title': 'Future', 'minutes': 30,
        'start': first.isoformat(), 'end': last.isoformat(), 'deadline': last.isoformat()})
    task = planner.model()['tasks'][0]
    day_op(planner, 'manual_cube', {'task_id': task['id'], 'title': 'Preparation', 'minutes': 30, 'date': monday.isoformat()})
    view = planner.model()
    cube = next(c for c in view['cubes'] if c['title'] == 'Preparation')
    goal = next(t for t in view['tasks'] if t['id'] == task['id'])
    assert (goal['start'], goal['end']) == (first.isoformat(), last.isoformat())
    assert cube['independent'] is True
    week_op(planner, 'cube_drop', {'cube_id': cube['id'], 'date': monday.isoformat(), 'start': '12:00'})
    facts_before = deepcopy(planner.store.read()['state']['planning'].get('completed_cubes', {}))
    shifted = week_op(planner, 'period_shift', {'task_id': task['id'],
        'start': (first + timedelta(days=1)).isoformat(), 'end': (last + timedelta(days=1)).isoformat()})
    goal = next(t for t in shifted['tasks'] if t['id'] == task['id'])
    cube = next(c for c in shifted['cubes'] if c['id'] == cube['id'])
    assert (goal['start'], goal['end']) == ((first + timedelta(days=1)).isoformat(), (last + timedelta(days=1)).isoformat())
    assert (cube['selected_date'], cube['start'], cube['independent']) == (monday.isoformat(), '12:00', True)
    narrowed = week_op(planner, 'period_set', {'task_id': task['id'],
        'start': (first + timedelta(days=2)).isoformat(), 'end': (last + timedelta(days=1)).isoformat()})
    cube = next(c for c in narrowed['cubes'] if c['id'] == cube['id'])
    assert (cube['selected_date'], cube['independent']) == (monday.isoformat(), True)
    assert planner.store.read()['state']['planning'].get('completed_cubes', {}) == facts_before


def test_period_cube_delete_cannot_race_shared_timer(planner):
    from megamozg.time_tracking import TimeStore
    from test_time_tracking import FakeClock, cmd
    project, monday = started(planner)
    day_op(planner, 'manual_task', {'project_id': project['id'], 'title': 'Task', 'minutes': 30})
    task = planner.model()['tasks'][0]
    day_op(planner, 'manual_cube', {'task_id': task['id'], 'title': 'Cube', 'minutes': 30})
    cube = next(c for c in planner.model()['cubes'] if c['title'] == 'Cube')
    week_op(planner, 'cube_drop', {'cube_id': cube['id'], 'date': monday.isoformat(), 'start': '12:00'})
    clock = FakeClock()
    timer = TimeStore(planner.store.root, lambda: clock.wall, lambda: clock.mono)
    ref = {'source': 'work', 'id': 'work-cube:' + cube['id'], 'title': 'Cube', 'activity_kind': 'work'}
    running = cmd(timer, 'start', 0, 'cube-timer-start', task=ref, mode='pomodoro', duration_ms=1_800_000)
    before = planner.store.read()
    with pytest.raises(ConflictError):
        week_op(planner, 'cube_delete', {'cube_id': cube['id']})
    assert planner.store.read() == before
    stopped = cmd(timer, 'stop', running['revision'], 'cube-timer-stop')
    week_op(planner, 'cube_delete', {'cube_id': cube['id']})
    with pytest.raises(ConflictError):
        cmd(timer, 'start', stopped['revision'], 'cube-timer-restart', task=ref)


def test_pending_core_delete_blocks_work_and_library_timer_until_recovery(tmp_path, monkeypatch):
    from megamozg.time_tracking import TimeStore
    from test_time_tracking import FakeClock, cmd
    from megamozg.core import Store
    planner = model.PeriodPlanner(WorkStore(tmp_path))
    created = planner.command({'request_id': 'pending-create', 'expected_revision': 0, 'action': 'standalone_create',
        'data': {'title': 'Pending', 'date': '2027-01-05', 'minutes': 30, 'activity_kind': 'personal'}})
    cube = next(c for c in created['cubes'] if c.get('standalone'))
    note_id = cube['source_ref']
    data = {'cube_id': cube['id'], 'expected_note_version': cube['library_note_version']}
    original = Store._apply
    def fail_delete(self, db, operation):
        if operation['kind'] == 'delete':
            raise OSError('synthetic crash after durable intent')
        return original(self, db, operation)
    clock = FakeClock()
    timer = TimeStore(tmp_path, lambda: clock.wall, lambda: clock.mono)
    with monkeypatch.context() as patch:
        patch.setattr(Store, '_apply', fail_delete)
        with pytest.raises(OSError):
            planner.command({'request_id': 'pending-delete', 'expected_revision': 0, 'action': 'standalone_delete', 'data': data})
        for source, target in (('work', 'work-cube:' + cube['id']), ('library', note_id)):
            with pytest.raises(ConflictError, match='изменяется или удаляется'):
                cmd(timer, 'start', 0, 'pending-' + source, task={'source': source, 'id': target, 'title': 'Pending'})
        assert timer.state()['status'] == 'idle'
    # Recovery is allowed only before the timer starts; a deleted linked source
    # stays rejected by both canonical Work and Library identities afterwards.
    assert Store(tmp_path).list_notes() == []
    for source, target in (('work', 'work-cube:' + cube['id']), ('library', note_id)):
        with pytest.raises(ConflictError):
            cmd(timer, 'start', 0, 'deleted-' + source, task={'source': source, 'id': target, 'title': 'Deleted'})


def test_library_source_timer_blocks_standalone_delete_and_unknown_library_id_remains_valid(tmp_path):
    from megamozg.time_tracking import TimeStore
    from test_time_tracking import FakeClock, cmd
    planner = model.PeriodPlanner(WorkStore(tmp_path))
    created = planner.command({'request_id': 'library-source-create', 'expected_revision': 0, 'action': 'standalone_create',
        'data': {'title': 'Linked', 'date': '2027-01-05', 'minutes': 30, 'activity_kind': 'personal'}})
    cube = next(c for c in created['cubes'] if c.get('standalone'))
    clock = FakeClock()
    timer = TimeStore(tmp_path, lambda: clock.wall, lambda: clock.mono)
    started = cmd(timer, 'start', 0, 'library-source-running', task={'source': 'library', 'id': cube['source_ref'], 'title': 'Linked'})
    paused = cmd(timer, 'pause', started['revision'], 'library-source-paused')
    with pytest.raises(ConflictError):
        planner.command({'request_id': 'library-source-delete', 'expected_revision': 0, 'action': 'standalone_delete',
            'data': {'cube_id': cube['id'], 'expected_note_version': cube['library_note_version']}})
    stopped = cmd(timer, 'stop', paused['revision'], 'library-source-stop')
    legacy = cmd(timer, 'start', stopped['revision'], 'unknown-legacy-library',
        task={'source': 'library', 'id': 'unknown-legacy-test', 'title': 'Legacy'})
    assert legacy['status'] == 'running'


def test_active_repeatable_instance_cannot_be_returned_until_timer_stops(planner):
    from megamozg.time_tracking import TimeStore
    from test_time_tracking import FakeClock, cmd
    _, monday = started(planner)
    dropped = week_op(planner, 'repeatable_drop', {'repeatable_id': 'prep-universal', 'date': monday.isoformat(), 'start': '12:00'})
    cube = next(c for c in dropped['cubes'] if c.get('repeatable_id') == 'prep-universal')
    clock = FakeClock()
    timer = TimeStore(planner.store.root, lambda: clock.wall, lambda: clock.mono)
    running = cmd(timer, 'start', 0, 'repeat-running', task={'source': 'work', 'id': 'work-cube:' + cube['id'], 'title': cube['title']})
    before = planner.store.read()
    with pytest.raises(ConflictError):
        week_op(planner, 'cube_return', {'cube_id': cube['id']})
    assert planner.store.read() == before
    cmd(timer, 'stop', running['revision'], 'repeat-stop')
    week_op(planner, 'cube_return', {'cube_id': cube['id']})
    assert all(c['id'] != cube['id'] for c in planner.model()['cubes'])


def test_pending_note_operation_blocks_resume_for_library_source(tmp_path):
    import sqlite3
    from megamozg.time_tracking import TimeStore
    from test_time_tracking import FakeClock, cmd
    planner = model.PeriodPlanner(WorkStore(tmp_path))
    result = planner.command({'request_id': 'resume-create', 'expected_revision': 0, 'action': 'standalone_create',
        'data': {'title': 'Note', 'date': '2027-01-05', 'minutes': 30, 'activity_kind': 'personal'}})
    cube = next(c for c in result['cubes'] if c.get('standalone'))
    clock = FakeClock()
    timer = TimeStore(tmp_path, lambda: clock.wall, lambda: clock.mono)
    started = cmd(timer, 'start', 0, 'resume-start', task={'source': 'library', 'id': cube['source_ref'], 'title': 'Note'})
    paused = cmd(timer, 'pause', started['revision'], 'resume-pause')
    with sqlite3.connect(tmp_path/'state'/'journal.sqlite3') as db:
        db.execute("INSERT INTO operations(id,note_id,kind,changes,status,created) VALUES(?,?,?,?,?,?)",
            ('synthetic-pending', cube['source_ref'], 'delete', '[]', 'pending', '2027-01-05'))
    with pytest.raises(ConflictError, match='изменяется или удаляется'):
        cmd(timer, 'resume', paused['revision'], 'resume-blocked')
    assert timer.state()['status'] == 'paused'


def test_timed_repeatable_return_keeps_zero_readiness_instance_for_resume(planner):
    from megamozg.time_tracking import TimeStore
    from test_time_tracking import FakeClock, cmd, advance_active
    _, monday = started(planner)
    dropped = week_op(planner, 'repeatable_drop', {'repeatable_id': 'prep-universal', 'date': monday.isoformat(), 'start': '12:00'})
    cube = next(c for c in dropped['cubes'] if c.get('repeatable_id') == 'prep-universal')
    clock = FakeClock()
    timer = TimeStore(planner.store.root, lambda: clock.wall, lambda: clock.mono)
    ref = {'source': 'work', 'id': 'work-cube:' + cube['id'], 'title': cube['title'], 'activity_kind': 'work'}
    running = cmd(timer, 'start', 0, 'repeat-measure', task=ref, mode='pomodoro', duration_ms=25*60_000)
    advance_active(timer, clock, 2*60_000)
    cmd(timer, 'stop', running['revision'], 'repeat-measure-stop')
    before_native = deepcopy(planner.store.read()['state']['planning'])
    result = week_op(planner, 'cube_return', {'cube_id': cube['id']})
    row = next(c for c in result['cubes'] if c['id'] == cube['id'])
    assert row['time_retained'] and row['reselectable'] and not row['available'] and row['selected_date'] is None
    assert row['progress_percent'] == 0 and row['actual_minutes'] == 0
    assert planner.store.read()['state']['planning'] == before_native
    elapsed = next(r['total_ms'] for r in timer.totals()['rows'] if r['task']['id'] == ref['id'])
    assert 25*60_000 - elapsed == 23*60_000
    moved = week_op(planner, 'cube_drop', {'cube_id': cube['id'], 'date': monday.isoformat(), 'start': '13:00'})
    assert next(c for c in moved['cubes'] if c['id'] == cube['id'])['selected_date'] == monday.isoformat()
    assert planner.store.read()['state']['planning'] == before_native


def test_day_return_retains_repeatable_with_timer_only_fact(planner):
    from megamozg.time_tracking import TimeStore
    from test_time_tracking import FakeClock, cmd
    _, monday = started(planner)
    dropped = week_op(planner, 'repeatable_drop', {'repeatable_id': 'prep-universal', 'date': monday.isoformat(), 'start': '12:00'})
    cube = next(c for c in dropped['cubes'] if c.get('repeatable_id') == 'prep-universal')
    clock = FakeClock()
    timer = TimeStore(planner.store.root, lambda: clock.wall, lambda: clock.mono)
    running = cmd(timer, 'start', 0, 'day-timer', task={'source': 'work', 'id': 'work-cube:' + cube['id'], 'title': cube['title']})
    clock.advance(5_000)
    cmd(timer, 'stop', running['revision'], 'day-timer-stop')
    day_op(planner, 'constructor_return', {'cube_id': cube['id']})
    row = next(c for c in planner.model()['cubes'] if c['id'] == cube['id'])
    assert row['time_retained'] and row['reselectable'] and row['progress_percent'] == 0


@pytest.mark.parametrize('paused', [False, True])
def test_undo_repeatable_creation_requires_its_timer_stopped(planner, paused):
    from megamozg.time_tracking import TimeStore
    from test_time_tracking import FakeClock, cmd
    _, monday = started(planner)
    dropped = week_op(planner, 'repeatable_drop', {'repeatable_id': 'prep-universal', 'date': monday.isoformat(), 'start': '12:00'})
    cube = next(c for c in dropped['cubes'] if c.get('repeatable_id') == 'prep-universal')
    clock = FakeClock()
    timer = TimeStore(planner.store.root, lambda: clock.wall, lambda: clock.mono)
    running = cmd(timer, 'start', 0, 'undo-guard-start', task={'source': 'work', 'id': 'work-cube:' + cube['id'], 'title': cube['title']})
    if paused:
        running = cmd(timer, 'pause', running['revision'], 'undo-guard-pause')
    before_work, before_timer = planner.store.read(), timer.state()
    with pytest.raises(ConflictError):
        week_op(planner, 'undo', {'revision': dropped['revision']})
    assert planner.store.read() == before_work and timer.state() == before_timer
    stopped = cmd(timer, 'stop', running['revision'], 'undo-guard-stop')
    undone = week_op(planner, 'undo', {'revision': dropped['revision']})
    assert all(c['id'] != cube['id'] for c in undone['cubes'])
    assert cube['id'] not in planner.store.read()['state']['planning'].get('constructor', {}).get('cubes', {})
    with pytest.raises(ConflictError):
        cmd(timer, 'start', stopped['revision'], 'undo-guard-stale-start',
            task={'source': 'work', 'id': 'work-cube:' + cube['id'], 'title': cube['title']})


def test_undo_source_removal_does_not_block_unrelated_timer(planner):
    from megamozg.time_tracking import TimeStore
    from test_time_tracking import FakeClock, cmd
    _, monday = started(planner)
    dropped = week_op(planner, 'repeatable_drop', {'repeatable_id': 'prep-universal', 'date': monday.isoformat(), 'start': '12:00'})
    cube = next(c for c in dropped['cubes'] if c.get('repeatable_id') == 'prep-universal')
    clock = FakeClock()
    timer = TimeStore(planner.store.root, lambda: clock.wall, lambda: clock.mono)
    running = cmd(timer, 'start', 0, 'unrelated-start', task={'source': 'work', 'id': 'work-cube:unrelated', 'title': 'Other'})
    before_timer = timer.state()
    undone = week_op(planner, 'undo', {'revision': dropped['revision']})
    assert all(c['id'] != cube['id'] for c in undone['cubes'])
    assert timer.state() == before_timer and timer.state()['revision'] == running['revision']


def test_undo_receipt_replays_after_source_was_removed(planner):
    from megamozg.time_tracking import TimeStore
    from test_time_tracking import FakeClock, cmd
    _, monday = started(planner)
    dropped = week_op(planner, 'repeatable_drop', {'repeatable_id': 'prep-universal', 'date': monday.isoformat(), 'start': '12:00'})
    payload = {'request_id': 'undo-replay', 'expected_revision': dropped['revision'], 'action': 'undo',
        'data': {'revision': dropped['revision']}}
    first = planner.command(payload)
    clock = FakeClock()
    timer = TimeStore(planner.store.root, lambda: clock.wall, lambda: clock.mono)
    cmd(timer, 'start', 0, 'after-undo-unrelated', task={'source': 'work', 'id': 'work-cube:unrelated', 'title': 'Other'})
    assert planner.command(payload) == first
