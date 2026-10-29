"""One effective period authority shared by the accepted Day view and new Plan."""
from copy import deepcopy
import pytest
import math
from datetime import datetime
from zoneinfo import ZoneInfo
from megamozg.core import ConflictError, ValidationError
from megamozg.work_period import model
from megamozg import work_planning as wp
from megamozg.work_store import WorkStore
from test_work_period import synthetic_state, synthetic_store, planner, command


def send(planner, action, data, request_id=None, revision=None):
    return planner.day_command({'request_id': request_id or 'day-'+str(planner.snapshot()['revision']),
        'expected_revision': planner.snapshot()['revision'] if revision is None else revision, 'action': action, 'data': data})


def row(planner, key):
    return next(row for row in planner.day_model()['planning']['result']['constructor']['cubes'] if row['id'] == key)


def test_plan_move_day_return_rename_delete_share_one_authority(planner):
    # Native selection before adoption is preserved raw and projected through the overlay.
    planner.store.planning_operation({'request_id': 'native-select', 'expected_revision': 1,
        'action': 'constructor_select', 'data': {'cube_id': 'geometry', 'date': '2027-01-05'}})
    raw = deepcopy(planner.snapshot()['state']['planning'])
    command(planner, 'cube_move', {'cube_id': 'geometry', 'date': '2027-01-06', 'start': '15:00'})
    assert row(planner, 'geometry')['selected_date'] == '2027-01-06'
    assert row(planner, 'geometry')['start'] == '15:00'
    assert planner.snapshot()['state']['planning'] == raw
    returned = send(planner, 'constructor_return', {'cube_id': 'geometry'})
    assert returned['state']['planning'] == raw
    assert next(cube for cube in planner.model()['cubes'] if cube['id'] == 'geometry')['selected_date'] is None
    send(planner, 'constructor_select', {'cube_id': 'geometry', 'date': '2027-01-07', 'start': '15:00'})
    assert next(cube for cube in planner.model()['cubes'] if cube['id'] == 'geometry')['selected_date'] == '2027-01-07'
    send(planner, 'constructor_cube', {'cube_id': 'geometry', 'title': 'One shared title'})
    assert row(planner, 'geometry')['title'] == 'One shared title'
    assert next(cube for cube in planner.model()['cubes'] if cube['id'] == 'geometry')['title'] == 'One shared title'
    deleted = send(planner, 'cube_delete', {'cube_id': 'geometry'})
    assert deleted['delete_result']['status'] == 'complete'
    assert deleted['state']['planning'] == raw
    assert 'geometry' not in {cube['id'] for cube in planner.day_model()['planning']['result']['constructor']['cubes']}
    assert next(cube for cube in planner.model()['cubes'] if cube['id'] == 'geometry')['status'] == 'cancelled'


def test_native_fact_uses_effective_date_and_preserves_raw_selection(planner):
    planner.store.planning_operation({'request_id': 'native-select', 'expected_revision': 1,
        'action': 'constructor_select', 'data': {'cube_id': 'geometry', 'date': '2027-01-05'}})
    command(planner, 'cube_move', {'cube_id': 'geometry', 'date': '2027-01-06', 'start': '15:00'})
    raw = planner.snapshot()['state']['planning']
    saved = send(planner, 'cube_progress', {'cube_id': 'geometry', 'percent': 25, 'actual_minutes': 12})
    native = saved['state']['planning']
    assert native['completed_cubes']['geometry']['date'] == '2027-01-06'
    assert native['completed_cubes']['geometry']['percent'] == 25
    assert native['locks'] == raw['locks']
    assert native['constructor']['days'] == raw['constructor']['days']
    assert row(planner, 'geometry')['report_date'] == '2027-01-06'
    send(planner, 'cube_progress', {'cube_id': 'geometry', 'percent': 100, 'actual_minutes': 60})
    assert planner.snapshot()['state']['planning']['completed_cubes']['geometry']['date'] == '2027-01-06'
    assert row(planner, 'geometry')['progress_percent'] == 100


def test_day_commands_cas_durable_replay_and_namespace_only_deletion(planner):
    command(planner, 'cube_rename', {'cube_id': 'geometry', 'title': 'Start overlay'})
    payload = {'request_id': 'day-once', 'expected_revision': planner.snapshot()['revision'],
        'action': 'constructor_select', 'data': {'cube_id': 'geometry', 'date': '2027-01-06'}}
    result = planner.day_command(payload)
    reopened = model.PeriodPlanner(WorkStore(planner.store.root))
    assert reopened.day_command(payload) == result
    assert reopened.snapshot()['revision'] == result['revision']
    with pytest.raises(ConflictError):
        reopened.day_command({**payload, 'request_id': 'stale'})
    with pytest.raises(ConflictError):
        reopened.day_command({**payload, 'data': {'cube_id': 'geometry', 'date': '2027-01-07'}})


def test_composite_and_retained_display_but_unsafe_facts_do_not_execute(planner):
    merged = command(planner, 'cube_merge', {'cube_ids': ['geometry', 'slides'], 'title': 'Presentation'})
    composed = next(cube for cube in merged['cubes'] if cube['id'].startswith('merged-'))
    send(planner, 'constructor_select', {'cube_id': composed['id'], 'date': '2027-01-06'})
    assert row(planner, composed['id'])['period_can_record_progress'] is True
    planner.store.planning_operation({'request_id': 'divergent-leaf', 'expected_revision': planner.snapshot()['revision'],
        'action': 'cube_progress', 'data': {'cube_id': 'geometry', 'percent': 25}})
    before = planner.snapshot()
    assert row(planner, composed['id'])['period_can_record_progress'] is False
    with pytest.raises(ValidationError, match='исполняемого'):
        send(planner, 'cube_progress', {'cube_id': composed['id'], 'percent': 100})
    with pytest.raises(ValidationError):
        send(planner, 'cube_progress', {'cube_id': 'geometry', 'percent': 100})
    assert planner.snapshot() == before
    assert not {'geometry', 'slides'} & {cube['id'] for cube in planner.day_model()['planning']['result']['constructor']['cubes']}


def test_namespace_empty_day_passes_through_raw(planner):
    before = planner.snapshot()
    assert planner.day_model()['enabled'] is False
    assert planner.day_model()['planning'] == before['state']['planning']
    result = send(planner, 'constructor_select', {'cube_id': 'geometry', 'date': '2027-01-05'})
    assert wp._constructor_selected_date(result['state']['planning'], 'geometry') == '2027-01-05'


def test_day_new_native_creation_and_namespace_selection_are_atomic(planner):
    command(planner, 'cube_rename', {'cube_id': 'geometry', 'title': 'Enable'})
    before = planner.snapshot()
    saved = send(planner, 'constructor_select', {'cube_id': 'new-cube', 'date': '2027-01-06',
        'cube': {'kind': 'task', 'task_id': 'a', 'title': 'New native', 'minutes': 30, 'planned_date': '2027-01-06'}})
    assert saved['revision'] == before['revision']+1
    assert 'new-cube' in saved['state']['planning']['constructor']['cubes']
    assert wp._constructor_selected_date(saved['state']['planning'], 'new-cube') is None
    assert row(planner, 'new-cube')['selected_date'] == '2027-01-06'
    assert row(planner, 'new-cube')['period_can_record_progress'] is True


def test_exact_current_forecast_materializes_only_when_facts_are_committed(planner):
    candidate = next(cube for cube in planner.model()['cubes'] if cube.get('candidate') and cube.get('task_id') == 'a')
    command(planner, 'cube_select', {'cube_id': candidate['id'], 'date': '2027-01-06', 'start': '14:00'})
    assert row(planner, candidate['id'])['period_can_record_progress'] is True
    before = planner.snapshot()
    assert candidate['id'] not in before['state']['planning']['constructor']['cubes']
    saved = send(planner, 'cube_progress', {'cube_id': candidate['id'], 'percent': 25, 'actual_minutes': 11})
    assert saved['revision'] == before['revision']+1
    native = saved['state']['planning']
    assert native['constructor']['cubes'][candidate['id']]['blueprint_ref'] == candidate['blueprint_ref']
    assert native['completed_cubes'][candidate['id']]['date'] == '2027-01-06'
    assert wp._constructor_selected_date(native, candidate['id']) is None
    view = planner.model()
    actual = [cube for cube in view['cubes'] if cube.get('task_id') == 'a' and cube.get('kind') == 'task'
        and cube.get('status') != 'cancelled' and not cube.get('candidate')
        and (not cube['id'].startswith('planned-') or cube['id'] in native['constructor']['cubes'])]
    assert candidate['id'] in {cube['id'] for cube in actual}
    assert set(next(task for task in view['tasks'] if task['id'] == 'a')['readiness_cube_ids']) == {cube['id'] for cube in actual}
    assert next(task for task in view['tasks'] if task['id'] == 'a')['progress_percent'] == math.floor(sum(cube['progress_percent'] for cube in actual) / len(actual) + .5)
    assert sum(cube['id'] == candidate['id'] for cube in planner.model()['cubes']) == 1
    assert sum(cube['id'] == candidate['id'] for cube in planner.day_model()['planning']['result']['constructor']['cubes']) == 1
    # All materialized cubes complete the goal even while other forecasts remain virtual.
    complete = deepcopy(saved['state'])
    for cube in actual:
        complete['planning'].setdefault('completed_cubes', {})[cube['id']] = {
            'task_id': 'a', 'date': '2027-01-06', 'planned_minutes': cube['minutes'],
            'percent': 100, 'actual_minutes': 0}
    closed = model.read_model({'state': complete, 'revision': saved['revision']})
    assert any(c.get('candidate') and c.get('task_id') == 'a' for c in closed['cubes'])
    assert next(task for task in closed['tasks'] if task['id'] == 'a')['status'] == 'completed'


def test_short_forecast_tail_off_the_five_minute_grid_stays_executable(planner):
    # Owner report 2026-10-28: a 24-minute forecast tail blocked readiness and delete.
    from megamozg.work_period import day
    planning = deepcopy(planner.snapshot()['state']['planning'])
    source = next(cube for cube in planning['result']['cubes'] if cube.get('task_id') == 'a' and not cube.get('locked'))
    source.update(minutes=24, is_remainder=True)
    spec = day._proven_task_source(planning, 'planned-'+source['id'])
    assert spec is not None and spec['minutes'] == 25 and spec['kind'] == 'task'
    source['minutes'] = 3
    assert day._proven_task_source(planning, 'planned-'+source['id'])['minutes'] == 5


def test_composite_exact_forecast_leaves_progress_atomically_and_prorata(planner):
    candidates = [cube for cube in planner.model()['cubes'] if cube.get('candidate') and cube.get('task_id') == 'a'][:2]
    assert len(candidates) == 2
    command(planner, 'cube_merge', {'cube_ids': [cube['id'] for cube in candidates], 'title': 'Forecast composition'})
    composed = next(cube for cube in planner.model()['cubes'] if cube['id'].startswith('merged-'))
    # Legacy select may send a redundant derived cube payload; it must not
    # create an invalid native composite with a fabricated task identity.
    send(planner, 'constructor_select', {'cube_id': composed['id'], 'date': '2027-01-06', 'start': '14:00',
        'cube': {'kind': 'task', 'task_id': composed['task_id'], 'title': composed['title'], 'minutes': composed['minutes']}})
    before = planner.snapshot()
    assert row(planner, composed['id'])['period_can_record_progress'] is True
    saved = send(planner, 'cube_progress', {'cube_id': composed['id'], 'percent': 50, 'actual_minutes': 13})
    native = saved['state']['planning']
    assert saved['revision'] == before['revision']+1
    assert composed['id'] not in native['constructor']['cubes']
    reports = [native['completed_cubes'][cube['id']] for cube in candidates]
    assert {report['date'] for report in reports} == {'2027-01-06'}
    assert {report['percent'] for report in reports} == {50}
    assert sum(report['actual_minutes'] for report in reports) == 13
    assert row(planner, composed['id'])['period_can_record_progress'] is True
    assert row(planner, composed['id'])['actual_minutes'] == 13
    assert row(planner, composed['id'])['report_date'] == '2027-01-06'
    send(planner, 'constructor_return', {'cube_id': composed['id']})
    assert row(planner, composed['id'])['selected_date'] is None
    assert row(planner, composed['id'])['report_date'] == '2027-01-06'
    assert planner.snapshot()['state']['planning']['completed_cubes'] == native['completed_cubes']


def test_virtual_composite_full_rename_preserves_native_and_period_duration(planner):
    command(planner, 'cube_merge', {'cube_ids': ['geometry', 'slides'], 'title': 'Presentation'})
    composed = next(cube for cube in planner.model()['cubes'] if cube['id'].startswith('merged-'))
    native = deepcopy(planner.snapshot()['state']['planning'])
    full = {key: deepcopy(composed[key]) for key in ['kind', 'task_id', 'minutes', 'output', 'steps', 'planned_date'] if key in composed}
    send(planner, 'constructor_cube', {'cube_id': composed['id'], **full, 'title': 'Shared virtual title'})
    assert row(planner, composed['id'])['title'] == 'Shared virtual title'
    assert planner.snapshot()['state']['planning'] == native
    assert next(cube for cube in planner.model()['cubes'] if cube['id'] == composed['id'])['title'] == 'Shared virtual title'


def test_native_prep_resources_resize_and_alias_remain_one_effective_source(tmp_path):
    state = synthetic_state()
    prep = next(prep for day in state['planning']['result']['days'] for prep in day.get('prep_cubes', []))
    key = 'constructor:'+prep['id']
    state['planning'] = wp.apply_action(state['planning'], 'constructor_cube', {'cube_id': key, 'kind': 'prep',
        'source_ref': prep['id'], 'title': 'Native preparation', 'minutes': prep['minutes']})
    planner = model.PeriodPlanner(synthetic_store(tmp_path, state))
    command(planner, 'cube_select', {'cube_id': key, 'date': prep['date'], 'start': '17:00'})
    command(planner, 'cube_rename', {'cube_id': key, 'title': 'Period alias'})
    command(planner, 'cube_resize', {'cube_id': key, 'date': prep['date'], 'start': '17:00', 'minutes': 35})
    send(planner, 'constructor_cube', {'cube_id': key, 'title': 'Day alias', 'minutes': 40, 'comment': 'Native detail'})
    native = planner.snapshot()['state']['planning']['constructor']['cubes'][key]
    assert native['minutes'] == prep['minutes'] and native['comment'] == 'Native detail'
    assert row(planner, key)['minutes'] == 40 and row(planner, key)['title'] == 'Day alias'
    effective = next(cube for cube in planner.model()['cubes'] if cube['id'] == key)
    assert effective['minutes'] == 40 and effective['title'] == 'Day alias'


def test_day_goal_close_uses_effective_membership_without_persisting_schedule(planner):
    planner.store.planning_operation({'request_id': 'native-select', 'expected_revision': 1,
        'action': 'constructor_select', 'data': {'cube_id': 'geometry', 'date': '2027-01-05'}})
    command(planner, 'cube_move', {'cube_id': 'geometry', 'date': '2027-01-06'})
    send(planner, 'constructor_goal', {'scope': 'day', 'date': '2027-01-06', 'title': 'Effective goal'})
    locks = deepcopy(planner.snapshot()['state']['planning']['locks'])
    saved = send(planner, 'constructor_goal_status', {'scope': 'day', 'date': '2027-01-06', 'status': 'completed'})
    record = saved['state']['planning']['constructor']['days']['2027-01-06']
    assert record['closed_cube_ids'] == ['geometry']
    assert saved['state']['planning']['locks'] == locks


def test_retained_only_row_rename_and_selection_work_but_no_fabricated_fact(tmp_path):
    state = synthetic_state()
    retained = deepcopy(state['planning']['constructor']['cubes']['other'])
    retained.update(id='retained-only', title='Reviewed retained source')
    state['period_planner'] = {'retained_cubes': {retained['id']: retained}}
    planner = model.PeriodPlanner(synthetic_store(tmp_path, state))
    send(planner, 'constructor_cube', {'cube_id': retained['id'], 'title': 'Retained alias'})
    send(planner, 'constructor_select', {'cube_id': retained['id'], 'date': '2027-01-06',
        'cube': {'kind': 'task', 'task_id': 'a', 'title': 'Ignored duplicate creation', 'minutes': 30}})
    assert row(planner, retained['id'])['title'] == 'Retained alias'
    assert row(planner, retained['id'])['period_can_record_progress'] is False
    before = planner.snapshot()
    with pytest.raises(ValidationError, match='исполняемого'):
        send(planner, 'cube_progress', {'cube_id': retained['id'], 'percent': 100})
    assert planner.snapshot() == before


def test_surviving_composite_does_not_inherit_absorbed_deletion_aliases(tmp_path):
    state = synthetic_state()
    candidates = [cube for cube in model.read_model({'state': state, 'revision': 0})['cubes']
        if cube.get('candidate') and cube.get('task_id') == 'a'][:2]
    state = model.apply_command(state, 'cube_merge', {'cube_ids': [cube['id'] for cube in candidates], 'title': 'Surviving composition'})
    merged_id, saved = next(iter(state['period_planner']['merged_cubes'].items()))
    # Older approved compositions may carry a leaf's inherited aliases.
    saved['blueprint_ref'] = candidates[0]['blueprint_ref']
    saved['canonical_id'] = candidates[0]['id']
    planner = model.PeriodPlanner(synthetic_store(tmp_path, state))
    projection = planner.day_model()['planning']
    composed = next(cube for cube in projection['result']['constructor']['cubes'] if cube['id'] == merged_id)
    aliases = {composed.get('id'), composed.get('canonical_id'), composed.get('blueprint_ref')} - {None}
    assert composed['canonical_id'] == merged_id and 'blueprint_ref' not in composed
    assert composed['source_ids'] == [cube['id'] for cube in candidates]
    assert not any(record['cube_id'] in aliases or record.get('blueprint_ref') in aliases
        for record in projection['cube_deletions'].values())
    assert projection['constructor']['cubes'][merged_id]['canonical_id'] == merged_id
    assert 'blueprint_ref' not in projection['constructor']['cubes'][merged_id]
    # Sanitization belongs only to the Day projection, never to persisted state.
    assert planner.snapshot()['state']['period_planner']['merged_cubes'][merged_id]['blueprint_ref'] == candidates[0]['blueprint_ref']


def test_moved_started_cube_keeps_original_facts_and_report_date(planner):
    command(planner, 'cube_select', {'cube_id': 'geometry', 'date': '2027-01-05', 'start': '15:00'})
    send(planner, 'cube_progress', {'cube_id': 'geometry', 'percent': 20, 'actual_minutes': 12})
    facts = deepcopy(planner.snapshot()['state']['planning']['completed_cubes'])
    moved = command(planner, 'cube_drop', {'cube_id': 'geometry', 'date': '2027-01-06', 'start': '15:00'})
    cube = next(c for c in moved['cubes'] if c['id'] == 'geometry')
    assert (cube['selected_date'], cube['progress_percent'], cube['actual_minutes']) == ('2027-01-06', 20, 12)
    assert planner.snapshot()['state']['planning']['completed_cubes'] == facts
    command(planner, 'cube_return', {'cube_id': 'geometry'})
    assert row(planner, 'geometry')['report_date'] == '2027-01-05'
    assert planner.snapshot()['state']['planning']['completed_cubes'] == facts


def test_completed_merged_goal_uses_leaf_report_dates(planner):
    result = command(planner, 'cube_merge', {'cube_ids': ['geometry', 'slides', 'other'], 'title': 'Combined'})
    cube = next(c for c in result['cubes'] if c['id'].startswith('merged-'))
    command(planner, 'cube_drop', {'cube_id': cube['id'], 'date': '2027-01-05', 'start': '15:00'})
    send(planner, 'cube_progress', {'cube_id': cube['id'], 'percent': 100, 'actual_minutes': 30})
    view = planner.model()
    goal = next(t for t in view['tasks'] if t['id'] == 'a')
    assert goal['status'] == 'completed' and goal['completed_on'] == datetime.now(ZoneInfo('Europe/Moscow')).date().isoformat()
    assert goal['readiness_cube_ids'] == [cube['id']]


def test_resize_started_agent_cube_keeps_native_facts_on_next_progress(planner):
    command(planner, 'cube_select', {'cube_id': 'geometry', 'date': '2027-01-05', 'start': '15:00'})
    send(planner, 'cube_progress', {'cube_id': 'geometry', 'percent': 20, 'actual_minutes': 12})
    before = deepcopy(planner.snapshot()['state']['planning'])
    changed = command(planner, 'cube_resize', {'cube_id': 'geometry', 'date': '2027-01-05',
        'start': '14:45', 'minutes': 75})
    row = next(c for c in changed['cubes'] if c['id'] == 'geometry')
    assert (row['start'], row['minutes'], row['progress_percent'], row['actual_minutes']) == ('14:45', 75, 20, 12)
    assert planner.snapshot()['state']['planning'] == before
    command(planner, 'cube_return', {'cube_id': 'geometry'})
    assert planner.snapshot()['state']['planning'] == before
    send(planner, 'cube_progress', {'cube_id': 'geometry', 'percent': 40, 'actual_minutes': 16})
    report = planner.snapshot()['state']['planning']['completed_cubes']['geometry']
    assert (report['date'], report['percent'], report['actual_minutes'], report['planned_minutes']) == ('2027-01-05', 40, 16, 60)
    assert planner.snapshot()['state']['planning']['constructor']['cubes']['geometry']['minutes'] == 60


def test_resize_started_merged_cube_preserves_leaf_facts(planner):
    result = command(planner, 'cube_merge', {'cube_ids': ['geometry', 'slides'], 'title': 'Combined'})
    cube = next(c for c in result['cubes'] if c['id'].startswith('merged-'))
    command(planner, 'cube_drop', {'cube_id': cube['id'], 'date': '2027-01-05', 'start': '15:00'})
    send(planner, 'cube_progress', {'cube_id': cube['id'], 'percent': 20, 'actual_minutes': 12})
    facts = deepcopy(planner.snapshot()['state']['planning'])
    resized = command(planner, 'cube_resize', {'cube_id': cube['id'], 'date': '2027-01-05', 'start': '14:45', 'minutes': 120})
    assert next(c for c in resized['cubes'] if c['id'] == cube['id'])['minutes'] == 120
    assert planner.snapshot()['state']['planning'] == facts
    send(planner, 'cube_progress', {'cube_id': cube['id'], 'percent': 40, 'actual_minutes': 16})
    reports = planner.snapshot()['state']['planning']['completed_cubes']
    assert reports['geometry']['planned_minutes'] == 60 and reports['slides']['planned_minutes'] == 45
    assert reports['geometry']['percent'] == 40 and reports['slides']['percent'] == 40


def test_reassign_partial_agent_cube_preserves_native_identity_and_next_fact(planner):
    command(planner, 'cube_select', {'cube_id': 'geometry', 'date': '2027-01-05', 'start': '15:00'})
    send(planner, 'cube_progress', {'cube_id': 'geometry', 'percent': 20, 'actual_minutes': 12})
    before = deepcopy(planner.snapshot()['state']['planning'])
    result = command(planner, 'cube_reassign', {'cube_id': 'geometry', 'task_id': 'b'})
    cube = next(c for c in result['cubes'] if c['id'] == 'geometry')
    goal = next(t for t in result['tasks'] if t['id'] == 'b')
    assert (cube['task_id'], cube['source_task_id'], cube['progress_percent']) == ('b', 'a', 20)
    assert goal['progress_percent'] == 20 and 'geometry' in goal['readiness_cube_ids']
    assert planner.snapshot()['state']['planning'] == before
    send(planner, 'cube_progress', {'cube_id': 'geometry', 'percent': 40, 'actual_minutes': 16})
    native = planner.snapshot()['state']['planning']
    assert native['constructor']['cubes']['geometry']['task_id'] == 'a'
    report = native['completed_cubes']['geometry']
    assert (report['task_id'], report['percent'], report['actual_minutes'], report['planned_minutes']) == ('a', 40, 16, 60)
    assert next(t for t in planner.model()['tasks'] if t['id'] == 'b')['progress_percent'] == 40


def test_reassign_partial_merged_cube_keeps_leaf_facts_and_undo(planner):
    merged = command(planner, 'cube_merge', {'cube_ids': ['geometry', 'slides'], 'title': 'Combined'})
    cube = next(c for c in merged['cubes'] if c['id'].startswith('merged-'))
    command(planner, 'cube_drop', {'cube_id': cube['id'], 'date': '2027-01-05', 'start': '15:00'})
    send(planner, 'cube_progress', {'cube_id': cube['id'], 'percent': 20, 'actual_minutes': 12})
    original = deepcopy(planner.snapshot()['state']['planning'])
    moved = command(planner, 'cube_reassign', {'cube_id': cube['id'], 'task_id': 'b'})
    goal = next(t for t in moved['tasks'] if t['id'] == 'b')
    assert goal['progress_percent'] == 20 and cube['id'] in goal['readiness_cube_ids']
    assert planner.snapshot()['state']['planning'] == original
    send(planner, 'cube_progress', {'cube_id': cube['id'], 'percent': 40, 'actual_minutes': 16})
    facts = planner.snapshot()['state']['planning']['completed_cubes']
    assert all(facts[cid]['task_id'] == 'a' and facts[cid]['percent'] == 40 for cid in ('geometry', 'slides'))
    restored = command(planner, 'undo', {'revision': moved['revision']})
    assert next(c for c in restored['cubes'] if c['id'] == cube['id'])['task_id'] == 'a'
    assert planner.snapshot()['state']['planning']['completed_cubes'] == facts


def test_day_view_model_reports_legacy_initialized_without_plan():
    from megamozg.work_period.day import day_model
    snapshot = {'revision': 1, 'initialized': True,
        'state': {'version': 1, 'projects': [], 'items': [], 'boards': [{'id': 'general', 'items': []}]}}
    view = day_model(snapshot)
    assert view['initialized'] is True
    assert view['view_model']['initialized'] is True
    assert view['view_model']['planning_initialized'] is False


def _nested_merge_with_missing_forecasts(tmp_path, shape='pairs'):
    state = synthetic_state()
    candidates = [c for c in model.read_model({'state': state, 'revision': 0})['cubes'] if c.get('task_id') == 'b' and c.get('candidate')][:4]
    assert [c['minutes'] for c in candidates] == [90, 60, 60, 90]
    for cube in candidates[:2]:
        wp._constructor_cube_update(state['planning'], {'cube_id': cube['id'], 'kind': 'task', 'task_id': 'b',
            'title': cube['title'], 'minutes': cube['minutes'], 'blueprint_ref': cube['blueprint_ref'], 'planned_date': cube['planned_date']})
    planner = model.PeriodPlanner(synthetic_store(tmp_path / 'before-recalc', state))
    if shape == 'one_plus_three':
        first = command(planner, 'cube_merge', {'cube_ids': [c['id'] for c in candidates[1:]], 'title': 'First'})
        first_id = next(c['id'] for c in first['cubes'] if c.get('title') == 'First')
        final = command(planner, 'cube_merge', {'cube_ids': [candidates[0]['id'], first_id], 'title': 'All'})
    else:
        first = command(planner, 'cube_merge', {'cube_ids': [c['id'] for c in candidates[:2]], 'title': 'First'})
        first_id = next(c['id'] for c in first['cubes'] if c.get('title') == 'First')
        second = command(planner, 'cube_merge', {'cube_ids': [c['id'] for c in candidates[2:]], 'title': 'Second'})
        second_id = next(c['id'] for c in second['cubes'] if c.get('title') == 'Second')
        final = command(planner, 'cube_merge', {'cube_ids': [first_id, second_id], 'title': 'All'})
    final_id = next(c['id'] for c in final['cubes'] if c.get('title') == 'All')
    recalculated = deepcopy(planner.snapshot()['state'])
    missing_refs = {c['blueprint_ref'] for c in candidates[2:]}
    recalculated['planning']['result']['cubes'] = [c for c in recalculated['planning']['result']['cubes'] if c['id'] not in missing_refs]
    return model.PeriodPlanner(synthetic_store(tmp_path / 'after-recalc', recalculated)), final_id, candidates


def test_nested_merge_recovers_exact_lost_forecasts_only_when_progress_recorded(tmp_path):
    planner, merged_id, candidates = _nested_merge_with_missing_forecasts(tmp_path)
    before = planner.snapshot()
    row = next(c for c in planner.model()['cubes'] if c['id'] == merged_id)
    assert row['minutes'] == 300 and row['available'] and not row.get('allocation_stale')
    assert planner.snapshot() == before
    command(planner, 'cube_drop', {'cube_id': merged_id, 'date': '2027-01-05', 'start': '12:00'})
    saved = send(planner, 'cube_progress', {'cube_id': merged_id, 'percent': 100, 'actual_minutes': 60})
    native = saved['state']['planning']
    assert all(c['id'] in native['constructor']['cubes'] for c in candidates)
    assert all(native['completed_cubes'][c['id']]['percent'] == 100 for c in candidates)
    assert sum(native['completed_cubes'][c['id']]['planned_minutes'] for c in candidates) == 300
    assert wp._remaining_before_deletions(native)['b'] == 0
    goal = next(t for t in planner.model()['tasks'] if t['id'] == 'b')
    assert goal['status'] == 'completed' and goal['completed_on'] == datetime.now(ZoneInfo('Europe/Moscow')).date().isoformat()


def test_unproven_merge_snapshot_does_not_resurrect_or_record_facts(tmp_path):
    planner, merged_id, candidates = _nested_merge_with_missing_forecasts(tmp_path)
    broken = deepcopy(planner.snapshot()['state'])
    records = broken['period_planner']['merges']
    nested = next(r for r in records.values() if candidates[2]['id'] in r['source_ids'])
    nested['sources'][0]['minutes'] += 5
    view = model.read_model({'state': broken, 'revision': 0})
    row = next(c for c in view['cubes'] if c['id'] == merged_id)
    assert row['allocation_stale'] and not row['available']


def test_delete_unstarted_unresolved_nested_merge_preserves_native_and_undo(tmp_path):
    planner, merged_id, candidates = _nested_merge_with_missing_forecasts(tmp_path)
    broken = deepcopy(planner.snapshot()['state'])
    records = broken['period_planner']['merges']
    nested = next(r for r in records.values() if candidates[2]['id'] in r['source_ids'])
    nested['sources'][0]['minutes'] += 5
    planner = model.PeriodPlanner(synthetic_store(tmp_path / 'broken', broken))
    stale = next(c for c in planner.model()['cubes'] if c['id'] == merged_id)
    assert stale['allocation_stale'] and stale['can_delete']
    before = deepcopy(planner.snapshot()['state']['planning'])
    deleted = command(planner, 'cube_delete', {'cube_id': merged_id})
    assert next(c for c in deleted['cubes'] if c['id'] == merged_id)['status'] == 'cancelled'
    assert planner.snapshot()['state']['planning'] == before
    restored = command(planner, 'undo', {'revision': deleted['revision']})
    assert next(c for c in restored['cubes'] if c['id'] == merged_id)['allocation_stale']
    assert planner.snapshot()['state']['planning'] == before


def test_unresolved_merge_with_hidden_alias_fact_cannot_be_deleted(tmp_path):
    planner, merged_id, candidates = _nested_merge_with_missing_forecasts(tmp_path)
    state = deepcopy(planner.snapshot()['state'])
    alias = candidates[2]['blueprint_ref']
    state['planning'].setdefault('completed_cubes', {})[alias] = {
        'task_id': 'b', 'date': '2027-01-05', 'planned_minutes': candidates[2]['minutes'],
        'percent': 20, 'actual_minutes': 4}
    view = model.read_model({'state': state, 'revision': 0})
    row = next(c for c in view['cubes'] if c['id'] == merged_id)
    assert row['allocation_stale'] and not row['can_delete']
    with pytest.raises(ValidationError):
        model.apply_command(state, 'cube_delete', {'cube_id': merged_id})


def test_delete_resolved_legacy_merged_cube_and_undo_preserves_sources(tmp_path):
    planner, merged_id, candidates = _nested_merge_with_missing_forecasts(tmp_path)
    native_before = deepcopy(planner.snapshot()['state']['planning'])
    deleted = command(planner, 'cube_delete', {'cube_id': merged_id})
    assert next(c for c in deleted['cubes'] if c['id'] == merged_id)['status'] == 'cancelled'
    assert planner.snapshot()['state']['planning'] == native_before
    restored = command(planner, 'undo', {'revision': deleted['revision']})
    row = next(c for c in restored['cubes'] if c['id'] == merged_id)
    assert row['available'] and row['minutes'] == 300
    assert planner.snapshot()['state']['planning'] == native_before


def test_delete_resized_merge_keeps_original_allocations_and_native_sources(planner):
    result = command(planner, 'cube_merge', {'cube_ids': ['geometry', 'slides'], 'title': 'Combined'})
    merged = next(c for c in result['cubes'] if c['id'].startswith('merged-'))
    before = deepcopy(planner.snapshot()['state']['planning'])
    command(planner, 'cube_resize', {'cube_id': merged['id'], 'date': None, 'start': None, 'minutes': 120})
    deleted = command(planner, 'cube_delete', {'cube_id': merged['id']})
    assert next(c for c in deleted['cubes'] if c['id'] == merged['id'])['status'] == 'cancelled'
    record = planner.snapshot()['state']['period_planner']['cancellations'][merged['id']]
    assert sum(part['minutes'] for part in record['source_task_allocations']) == 105
    assert planner.snapshot()['state']['planning'] == before


def test_actual_shape_one_native_plus_nested_three_with_two_missing(tmp_path):
    planner, merged_id, candidates = _nested_merge_with_missing_forecasts(tmp_path, shape='one_plus_three')
    before = planner.snapshot()
    row = next(c for c in planner.model()['cubes'] if c['id'] == merged_id)
    assert row['available'] and row['minutes'] == 300 and not row.get('allocation_stale')
    assert planner.snapshot() == before
    command(planner, 'cube_drop', {'cube_id': merged_id, 'date': '2027-01-05', 'start': '12:00'})
    saved = send(planner, 'cube_progress', {'cube_id': merged_id, 'percent': 100, 'actual_minutes': 60})
    assert all(saved['state']['planning']['completed_cubes'][cube['id']]['percent'] == 100 for cube in candidates)
    assert next(task for task in planner.model()['tasks'] if task['id'] == 'b')['status'] == 'completed'


def test_selected_and_returned_forecast_counts_as_owner_stage_without_timer_facts(tmp_path):
    from megamozg.time_tracking import TimeStore
    from test_time_tracking import FakeClock, cmd
    state = synthetic_state()
    for cid, minutes in [('geometry', 60), ('slides', 45), ('other', 30)]:
        state['planning'].setdefault('completed_cubes', {})[cid] = {
            'task_id': 'a', 'date': '2027-01-05', 'planned_minutes': minutes,
            'percent': 100, 'actual_minutes': 0}
    planner = model.PeriodPlanner(synthetic_store(tmp_path, state))
    before = planner.model()
    assert next(t for t in before['tasks'] if t['id'] == 'a')['progress_percent'] == 100
    forecasts = [c for c in before['cubes'] if c.get('task_id') == 'a' and c.get('candidate')]
    assert len(forecasts) > 1
    chosen = forecasts[0]
    selected = command(planner, 'cube_select', {'cube_id': chosen['id'], 'date': '2027-01-06'})
    task = next(t for t in selected['tasks'] if t['id'] == 'a')
    assert task['progress_percent'] == 75 and task['status'] == 'started'
    assert set(task['readiness_cube_ids']) == {'geometry', 'slides', 'other', chosen['id']}
    assert all(c['id'] not in task['readiness_cube_ids'] for c in forecasts[1:])
    assert chosen['id'] not in planner.snapshot()['state']['planning']['constructor']['cubes']
    clock = FakeClock()
    timer = TimeStore(tmp_path, lambda: clock.wall, lambda: clock.mono)
    running = cmd(timer, 'start', 0, 'forecast-timer', task={
        'source': 'work', 'id': 'work-cube:' + chosen['id'], 'title': 'Forecast', 'activity_kind': 'work'})
    clock.advance(5_000)
    assert next(t for t in planner.model()['tasks'] if t['id'] == 'a')['progress_percent'] == 75
    cmd(timer, 'stop', running['revision'], 'forecast-stop')
    returned = command(planner, 'cube_return', {'cube_id': chosen['id']})
    task = next(t for t in returned['tasks'] if t['id'] == 'a')
    assert task['progress_percent'] == 75 and chosen['id'] in task['readiness_cube_ids']
    assert next(c for c in returned['cubes'] if c['id'] == chosen['id'])['selected_date'] is None


def test_merge_rejects_active_source_timer_without_changing_state(planner):
    from megamozg.time_tracking import TimeStore
    from test_time_tracking import FakeClock, cmd
    command(planner, 'cube_select', {'cube_id': 'geometry', 'date': '2027-01-05'})
    clock = FakeClock()
    timer = TimeStore(planner.store.root, lambda: clock.wall, lambda: clock.mono)
    running = cmd(timer, 'start', 0, 'merge-guard-start', task={'source': 'work', 'id': 'work-cube:geometry', 'title': 'Geometry'})
    before, before_timer = planner.snapshot(), timer.state()
    with pytest.raises(ConflictError):
        command(planner, 'cube_merge', {'cube_ids': ['geometry', 'slides'], 'title': 'Combined'})
    assert planner.snapshot() == before and timer.state() == before_timer
    stopped = cmd(timer, 'stop', running['revision'], 'merge-guard-stop')
    result = command(planner, 'cube_merge', {'cube_ids': ['geometry', 'slides'], 'title': 'Combined'})
    assert any(c['id'].startswith('merged-') for c in result['cubes'])
    with pytest.raises(ConflictError):
        cmd(timer, 'start', stopped['revision'], 'merge-guard-stale-start',
            task={'source': 'work', 'id': 'work-cube:geometry', 'title': 'Geometry'})


def test_undo_forecast_materialization_keeps_same_timed_source(planner):
    from megamozg.time_tracking import TimeStore
    from test_time_tracking import FakeClock, cmd
    forecast = next(c for c in planner.model()['cubes'] if c.get('candidate') and c.get('task_id') == 'a')
    command(planner, 'cube_select', {'cube_id': forecast['id'], 'date': '2027-01-06'})
    fact = send(planner, 'cube_progress', {'cube_id': forecast['id'], 'percent': 20, 'actual_minutes': 5})
    clock = FakeClock()
    timer = TimeStore(planner.store.root, lambda: clock.wall, lambda: clock.mono)
    running = cmd(timer, 'start', 0, 'forecast-guard-start', task={'source': 'work', 'id': 'work-cube:' + forecast['id'], 'title': forecast['title']})
    before_timer = timer.state()
    undone = command(planner, 'undo', {'revision': fact['revision']})
    row = next(c for c in undone['cubes'] if c['id'] == forecast['id'])
    assert row['selected_date'] == '2027-01-06' and row['progress_percent'] == 0
    assert forecast['id'] not in planner.snapshot()['state']['planning']['constructor']['cubes']
    assert timer.state() == before_timer and timer.state()['revision'] == running['revision']
