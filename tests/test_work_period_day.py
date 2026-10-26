"""One effective period authority shared by the accepted Day view and new Plan."""
from copy import deepcopy
import pytest
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
    assert sum(cube['id'] == candidate['id'] for cube in planner.model()['cubes']) == 1
    assert sum(cube['id'] == candidate['id'] for cube in planner.day_model()['planning']['result']['constructor']['cubes']) == 1


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
