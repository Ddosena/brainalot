from datetime import datetime, timezone

from megamozg.work_period import model
from test_work_period import planner, command, synthetic_state
from test_work_period_day import send


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 28, 22, 30, tzinfo=timezone.utc).astimezone(tz)


def goal(planner):
    return next(t for t in planner.model()['tasks'] if t['id'] == 'a')


def test_ready_transition_uses_plan_timezone_reopen_and_undo(planner, monkeypatch):
    monkeypatch.setattr(model, 'datetime', FrozenDatetime)
    merged = command(planner, 'cube_merge', {'cube_ids': ['geometry', 'slides', 'other'], 'title': 'All'})
    cube = next(c for c in merged['cubes'] if c['id'].startswith('merged-'))
    command(planner, 'cube_drop', {'cube_id': cube['id'], 'date': '2027-01-05', 'start': '15:00'})
    done = send(planner, 'cube_progress', {'cube_id': cube['id'], 'percent': 100})
    assert goal(planner)['completed_on'] == '2026-10-29'
    reopened = send(planner, 'cube_progress', {'cube_id': cube['id'], 'percent': 50})
    assert goal(planner)['completed_on'] is None
    command(planner, 'undo', {'revision': reopened['revision']})
    assert goal(planner)['completed_on'] == '2026-10-29'


def test_legacy_future_dates_are_capped_at_today(planner, monkeypatch):
    monkeypatch.setattr(model, 'datetime', FrozenDatetime)
    merged = command(planner, 'cube_merge', {'cube_ids': ['geometry', 'slides', 'other'], 'title': 'All'})
    cube = next(c for c in merged['cubes'] if c['id'].startswith('merged-'))
    command(planner, 'cube_drop', {'cube_id': cube['id'], 'date': '2027-01-05', 'start': '15:00'})
    send(planner, 'cube_progress', {'cube_id': cube['id'], 'percent': 100})
    snapshot = planner.snapshot()
    snapshot['state'].get('period_planner', {}).pop('goal_completed_on', None)
    task = next(t for t in model.read_model(snapshot)['tasks'] if t['id'] == 'a')
    assert task['completed_on'] == '2026-10-29'


def test_integer_rounding_and_incomplete_cap(planner):
    for cid, percent, start in [('geometry', 100, '15:00'), ('slides', 100, '16:00'), ('other', 99, '17:00')]:
        command(planner, 'cube_drop', {'cube_id': cid, 'date': '2027-01-05', 'start': start})
        send(planner, 'cube_progress', {'cube_id': cid, 'percent': percent})
    assert goal(planner)['progress_percent'] == 99
    send(planner, 'cube_progress', {'cube_id': 'other', 'percent': 100})
    assert goal(planner)['progress_percent'] == 100



def test_half_rounds_up_like_javascript(planner):
    merged = command(planner, 'cube_merge', {'cube_ids': ['geometry', 'slides'], 'title': 'Pair'})
    cube = next(c for c in merged['cubes'] if c['id'].startswith('merged-'))
    command(planner, 'cube_drop', {'cube_id': cube['id'], 'date': '2027-01-05', 'start': '15:00'})
    send(planner, 'cube_progress', {'cube_id': cube['id'], 'percent': 1})
    assert goal(planner)['progress_percent'] == 1


def test_legacy_past_report_date_is_retained(planner, monkeypatch):
    monkeypatch.setattr(model, 'datetime', FrozenDatetime)
    merged = command(planner, 'cube_merge', {'cube_ids': ['geometry', 'slides', 'other'], 'title': 'All'})
    cube = next(c for c in merged['cubes'] if c['id'].startswith('merged-'))
    command(planner, 'cube_drop', {'cube_id': cube['id'], 'date': '2027-01-05', 'start': '15:00'})
    send(planner, 'cube_progress', {'cube_id': cube['id'], 'percent': 100})
    snapshot = planner.snapshot()
    snapshot['state']['period_planner'].pop('goal_completed_on', None)
    for report in snapshot['state']['planning']['completed_cubes'].values():
        report['date'] = '2026-10-20'
    task = next(t for t in model.read_model(snapshot)['tasks'] if t['id'] == 'a')
    assert task['completed_on'] == '2026-10-20'


def test_direct_native_progress_records_actual_date_and_read_is_pure(planner, monkeypatch):
    from copy import deepcopy
    monkeypatch.setattr(model, 'datetime', FrozenDatetime)
    snapshot = planner.snapshot()
    untouched = deepcopy(snapshot)
    planner._model(snapshot, [])
    assert snapshot == untouched
    for cid in ('geometry', 'slides', 'other'):
        for action, data in [('constructor_select', {'cube_id': cid, 'date': '2027-01-05'}),
                             ('cube_progress', {'cube_id': cid, 'percent': 100})]:
            planner.store.planning_operation({'request_id': action + cid, 'expected_revision': planner.snapshot()['revision'],
                                               'action': action, 'data': data})
    assert goal(planner)['completed_on'] == '2026-10-29'
    assert planner.snapshot()['state']['period_planner']['goal_completed_on']['a'] == '2026-10-29'


def test_regular_commit_preserves_completion_dates_from_older_client(planner, monkeypatch):
    from copy import deepcopy
    from test_work_planning import upload_state
    monkeypatch.setattr(model, 'datetime', FrozenDatetime)
    merged = command(planner, 'cube_merge', {'cube_ids': ['geometry', 'slides', 'other'], 'title': 'All'})
    cube = next(c for c in merged['cubes'] if c['id'].startswith('merged-'))
    command(planner, 'cube_drop', {'cube_id': cube['id'], 'date': '2027-01-05', 'start': '15:00'})
    send(planner, 'cube_progress', {'cube_id': cube['id'], 'percent': 100})
    snapshot = planner.snapshot()
    old_client = deepcopy(snapshot['state'])
    old_client['period_planner'].pop('goal_completed_on')
    digest = upload_state(planner.store, old_client)
    result = planner.store.commit({'request_id': 'old-client', 'expected_revision': snapshot['revision'],
                                   'state_blob': digest, 'images': []})
    assert result['state']['period_planner']['goal_completed_on']['a'] == '2026-10-29'


def test_imported_completed_facts_freeze_legacy_fallback(tmp_path, monkeypatch):
    from copy import deepcopy
    from megamozg import work_planning as wp
    from test_work_period import synthetic_store
    monkeypatch.setattr(model, 'datetime', FrozenDatetime)
    state = synthetic_state()
    planning = state.pop('planning')
    for cid in ('geometry', 'slides', 'other'):
        planning = wp.apply_action(planning, 'constructor_select', {'cube_id': cid, 'date': '2027-01-05'})
        planning = wp.apply_action(planning, 'cube_progress', {'cube_id': cid, 'percent': 100})
    store = synthetic_store(tmp_path, state)
    result = store.planning_operation({'request_id': 'import-complete', 'expected_revision': 1,
                                       'action': 'import', 'data': {'planning': planning}})
    assert result['state']['period_planner']['goal_completed_on']['a'] == '2026-10-29'


def test_fallback_percent_rounds_only_once(planner, monkeypatch):
    monkeypatch.setattr(model, '_real_task_cube', lambda state, cube: False)
    monkeypatch.setattr(model.wp, '_remaining_before_deletions', lambda planning: {'a': 303.3, 'b': 300})
    assert goal(planner)['progress_percent'] == 49


def test_stored_completion_date_is_capped_on_timezone_change(planner, monkeypatch):
    monkeypatch.setattr(model, 'datetime', FrozenDatetime)
    merged = command(planner, 'cube_merge', {'cube_ids': ['geometry', 'slides', 'other'], 'title': 'All'})
    cube = next(c for c in merged['cubes'] if c['id'].startswith('merged-'))
    command(planner, 'cube_drop', {'cube_id': cube['id'], 'date': '2027-01-05', 'start': '15:00'})
    send(planner, 'cube_progress', {'cube_id': cube['id'], 'percent': 100})
    snapshot = planner.snapshot()
    snapshot['state']['planning']['settings']['timezone'] = 'America/Los_Angeles'
    task = next(t for t in model.read_model(snapshot)['tasks'] if t['id'] == 'a')
    assert task['completed_on'] == '2026-10-28'
    assert snapshot['state']['period_planner']['goal_completed_on']['a'] == '2026-10-29'
