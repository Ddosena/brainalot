"""Text shortcuts exercise real Work transactions on synthetic local data."""
import json
from copy import deepcopy

from megamozg.cli import main
from megamozg import features
from megamozg.work_period import PeriodPlanner
from test_work_period import synthetic_state, synthetic_store


def invoke(root, capsys, *args):
    status = main(['--root', str(root), 'work', 'edit', *args])
    output = capsys.readouterr()
    return status, json.loads(output.out) if output.out else output.err


def test_short_context_and_rename_preserve_native_and_durable_request(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(features, 'WORK_ENABLED', True)
    store = synthetic_store(tmp_path, synthetic_state())
    planner = PeriodPlanner(store)
    planner.set_context({'request_id': 'focus', 'expected_revision': 1, 'patch': {'cube_id': 'geometry', 'task_id': 'a'}})
    status, context = invoke(tmp_path, capsys, 'context')
    assert status == 0 and context['cube']['id'] == 'geometry' and context['revision'] == 2
    assert 'state' not in context and len(json.dumps(context)) < 5000
    native = deepcopy(planner.snapshot()['state']['planning'])
    args = ('rename', 'Visible agent title', '--id', 'geometry', '--revision', '2', '--request-id', 'short-rename')
    status, first = invoke(tmp_path, capsys, *args)
    assert status == 0 and first['changed']['title'] == 'Visible agent title' and first['revision'] == 3
    assert len(json.dumps(first)) < 700
    assert invoke(tmp_path, capsys, *args) == (0, first)
    assert planner.snapshot()['revision'] == 3 and planner.snapshot()['state']['planning'] == native
    stale = invoke(tmp_path, capsys, 'rename', 'Must not overwrite', '--id', 'geometry', '--revision', '2', '--request-id', 'stale')
    assert stale[0] != 0
    assert next(c for c in planner.model()['cubes'] if c['id'] == 'geometry')['title'] == 'Visible agent title'


def test_text_place_return_and_goal_replay_share_native_store(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(features, 'WORK_ENABLED', True)
    planner = PeriodPlanner(synthetic_store(tmp_path, synthetic_state()))
    before = deepcopy(planner.snapshot()['state']['planning'])
    status, placed = invoke(tmp_path, capsys, 'place', '2027-01-06', '14:05', '--id', 'geometry', '--revision', '1', '--request-id', 'place')
    assert status == 0 and placed['changed']['start'] == '14:05' and placed['changed']['selected_date'] == '2027-01-06'
    status, returned = invoke(tmp_path, capsys, 'return', '--id', 'geometry', '--revision', '2', '--request-id', 'return')
    assert status == 0 and returned['revision'] == 3 and returned['changed'].get('selected_date') is None
    args = ('goal', 'Agent goal', '--start', '2027-01-01', '--end', '2027-01-31', '--revision', '3', '--request-id', 'goal-once')
    status, goal = invoke(tmp_path, capsys, *args)
    assert status == 0 and goal['changed']['title'] == 'Agent goal'
    assert invoke(tmp_path, capsys, *args) == (0, goal)
    assert sum(p['title'] == 'Agent goal' for p in planner.model()['projects']) == 1
    assert planner.snapshot()['state']['planning'] == before


def test_text_duration_resizes_preparation_without_touching_original_volume(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(features, 'WORK_ENABLED', True)
    planner = PeriodPlanner(synthetic_store(tmp_path, synthetic_state()))
    cube = next(c for c in planner.model()['cubes'] if c['kind'] == 'prep')
    before = deepcopy(planner.snapshot()['state']['planning'])
    status, changed = invoke(tmp_path, capsys, 'duration', '35', '--id', cube['id'], '--revision', '1', '--request-id', 'resize-prep')
    assert status == 0 and changed['changed']['minutes'] == 35
    assert planner.snapshot()['state']['planning'] == before


def test_existing_typed_agent_command_uses_the_same_effective_alias(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(features, 'WORK_ENABLED', True)
    planner = PeriodPlanner(synthetic_store(tmp_path, synthetic_state()))
    native = deepcopy(planner.snapshot()['state']['planning'])
    planner.command({'request_id': 'existing-alias', 'expected_revision': 1,
        'action': 'cube_rename', 'data': {'cube_id': 'geometry', 'title': 'Existing visible alias'}})
    path = tmp_path/'edit.json'
    path.write_text(json.dumps({'request_id': 'typed-agent-edit', 'expected_revision': 2,
        'action': 'constructor_cube', 'data': {'cube_id': 'geometry', 'title': 'Agent typed edit'}}), 'utf-8')
    assert main(['--root', str(tmp_path), 'work', 'plan', 'command', '--file', str(path)]) == 0
    capsys.readouterr()
    assert next(c for c in planner.model()['cubes'] if c['id'] == 'geometry')['title'] == 'Agent typed edit'
    assert planner.snapshot()['state']['planning'] == native
