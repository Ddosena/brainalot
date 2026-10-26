"""Direct drops and real read-only Library sources; synthetic local stores only."""
from copy import deepcopy
import hashlib
import sqlite3
import pytest
from megamozg.core import Store, ConflictError, ValidationError
from megamozg.work_store import WorkStore
from megamozg.work_period import model
from megamozg import work_planning as wp
from test_work_period import synthetic_state, synthetic_store, command, color_planner, color_source_state
from test_work_period_day import send


@pytest.fixture
def library_planner(tmp_path):
    library = Store(tmp_path)
    notes = {
        'day': library.capture('Due task', kind='task', due='2020-01-01'),
        'week': library.capture('Week task', kind='task', planning_horizon='week'),
        'month': library.capture('Month task', kind='task', planning_horizon='month'),
        'unscheduled': library.capture('Unscheduled task', kind='task'),
    }
    closed = library.capture('Closed task', kind='task')
    library.update(closed['id'], {'status': 'done'}, closed['version'])
    library.capture('Thought', kind='thought')
    library.capture('Purchase', kind='purchase', planning_horizon='month')
    state = synthetic_state()
    return model.PeriodPlanner(synthetic_store(tmp_path, state)), library, notes


def sources(planner):
    return [cube for cube in planner.model()['cubes'] if cube.get('library_source')]


def test_actual_day_week_month_tasks_project_read_only_without_rollover_or_autoselection(library_planner):
    planner, library, notes = library_planner
    before = planner.snapshot()
    files = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in library.vault.rglob('*.md')}
    with sqlite3.connect(library.db_path) as db:
        operation_count = db.execute('SELECT COUNT(*) FROM operations').fetchone()[0]
    rows = sources(planner)
    assert len(rows) == 4 and {row['source_ref'] for row in rows} == {note['id'] for note in notes.values()}
    assert {row['source_horizon'] for row in rows} == {'day', 'week', 'month', 'unscheduled'}
    assert all(row['id'] == 'brainalot-'+row['source_ref'] and row['selected_date'] is None for row in rows)
    assert next(row for row in rows if row['source_ref'] == notes['day']['id'])['source_due'] == '2020-01-01'
    assert all(row['minutes'] == 60 and row['can_resize'] for row in rows)
    assert planner.snapshot() == before
    assert files == {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in library.vault.rglob('*.md')}
    with sqlite3.connect(library.db_path) as db:
        assert db.execute('SELECT COUNT(*) FROM operations').fetchone()[0] == operation_count


def test_existing_native_brainalot_identity_selection_and_facts_win_without_duplicate(tmp_path):
    library = Store(tmp_path)
    note = library.capture('Live week task', kind='task', planning_horizon='week')
    state = synthetic_state()
    state['planning'] = wp.apply_action(state['planning'], 'constructor_cube', {'cube_id': 'native-library-id',
        'kind': 'brainalot', 'source_ref': note['id'], 'title': 'Work alias', 'minutes': 60})
    state['planning'] = wp.apply_action(state['planning'], 'constructor_select', {'cube_id': 'native-library-id', 'date': '2027-01-05'})
    state['planning'] = wp.apply_action(state['planning'], 'cube_progress', {'cube_id': 'native-library-id', 'percent': 25, 'actual_minutes': 12})
    state['period_planner'] = {'placements': {'native-library-id': {'date': '2027-01-05', 'start': '15:00'}}}
    planner = model.PeriodPlanner(synthetic_store(tmp_path, state))
    rows = [row for row in sources(planner) if row['source_ref'] == note['id']]
    assert len(rows) == 1 and rows[0]['id'] == 'native-library-id'
    assert rows[0]['title'] == 'Work alias' and rows[0]['actual_minutes'] == 12 and rows[0]['progress_percent'] == 25
    assert rows[0]['selected_date'] == '2027-01-05' and rows[0]['start'] == '15:00'


def test_library_drop_and_resize_are_namespace_only_same_ids_in_day_and_plan(library_planner):
    planner, library, notes = library_planner
    key = 'brainalot-'+notes['month']['id']
    before = deepcopy(planner.snapshot()['state']['planning'])
    dropped = command(planner, 'cube_drop', {'cube_id': key, 'date': '2027-01-06', 'start': '14:05'})
    assert dropped['revision'] == 2
    row = next(row for row in dropped['cubes'] if row['id'] == key)
    assert row['selected_date'] == '2027-01-06' and row['start'] == '14:05' and row['minutes'] == 60
    command(planner, 'cube_resize', {'cube_id': key, 'date': '2027-01-06', 'start': '14:05', 'minutes': 30})
    raw = planner.snapshot()['state']
    assert raw['planning'] == before and raw['period_planner']['brainalot_sources'][key]['source_ref'] == notes['month']['id']
    day_row = next(row for row in planner.day_model()['planning']['result']['constructor']['cubes'] if row['id'] == key)
    assert day_row['minutes'] == 30 and day_row['selected_date'] == '2027-01-06'
    reopened = model.PeriodPlanner(WorkStore(planner.store.root))
    assert next(row for row in sources(reopened) if row['id'] == key)['start'] == '14:05'
    assert library.list_notes()[0]['kind'] in {'task', 'purchase', 'thought'}


def test_unselected_library_duration_edit_never_autofills_day(library_planner):
    planner, library, notes = library_planner
    key = 'brainalot-'+notes['week']['id']
    command(planner, 'cube_resize', {'cube_id': key, 'date': None, 'start': None, 'minutes': 35})
    row = next(row for row in sources(planner) if row['id'] == key)
    assert row['minutes'] == 35 and row['selected_date'] is None and row['start'] is None
    assert key not in planner.snapshot()['state']['period_planner'].get('placements', {})


def test_closed_library_source_preserves_history_but_cannot_start_new_work(library_planner):
    planner, library, notes = library_planner
    key = 'brainalot-'+notes['day']['id']
    command(planner, 'cube_drop', {'cube_id': key, 'date': '2027-01-06', 'start': '14:00'})
    library.update(notes['day']['id'], {'status': 'done'}, notes['day']['version'])
    row = next(row for row in sources(planner) if row['id'] == key)
    assert not row['available'] and row['source_active'] is False and row['selected_date'] == '2027-01-06'
    before = planner.snapshot()
    with pytest.raises(ValidationError, match='Исходная задача'):
        command(planner, 'cube_drop', {'cube_id': key, 'date': '2027-01-07', 'start': '14:00'})
    assert planner.snapshot() == before


def test_library_fact_materializes_exact_note_identity_and_date_in_one_revision(library_planner):
    planner, library, notes = library_planner
    key = 'brainalot-'+notes['week']['id']
    command(planner, 'cube_drop', {'cube_id': key, 'date': '2027-01-06', 'start': '14:00'})
    before = planner.snapshot()
    saved = send(planner, 'cube_progress', {'cube_id': key, 'percent': 50, 'actual_minutes': 15})
    assert saved['revision'] == before['revision']+1
    native = saved['state']['planning']
    assert native['constructor']['cubes'][key]['source_ref'] == notes['week']['id']
    assert native['constructor']['source_reports'][key] == {'date': '2027-01-06', 'percent': 50, 'actual_minutes': 15}
    assert len([row for row in sources(planner) if row['source_ref'] == notes['week']['id']]) == 1
    assert next(row for row in sources(planner) if row['id'] == key)['actual_minutes'] == 15


def test_external_library_edit_changes_model_and_day_fingerprints_without_work_revision(library_planner):
    planner, library, notes = library_planner
    command(planner, 'cube_rename', {'cube_id': 'geometry', 'title': 'Enable namespace'})
    before_model, before_day = planner.model(), planner.day_model()
    library.update(notes['unscheduled']['id'], {'title': 'Externally renamed task', 'planning_horizon': 'month'}, notes['unscheduled']['version'])
    after_model, after_day = planner.model(), planner.day_model()
    assert before_model['revision'] == after_model['revision'] == before_day['revision'] == after_day['revision']
    assert before_model['source_fingerprint'] != after_model['source_fingerprint']
    assert before_day['fingerprint'] != after_day['fingerprint']
    row = next(row for row in after_model['cubes'] if row.get('source_ref') == notes['unscheduled']['id'])
    assert row['title'] == 'Externally renamed task' and row['source_horizon'] == 'month'


def test_period_context_day_queue_persists_external_cube_focus(library_planner):
    planner, library, notes = library_planner
    key = 'brainalot-'+notes['month']['id']
    saved = send(planner, 'period_context', {'date': '2027-01-06', 'cube_id': key, 'task_id': None})
    assert saved['revision'] == 2
    assert saved['state']['period_planner']['context']['cube_id'] == key
    assert planner.model()['context']['cube_id'] == key


def test_unknown_sage_drop_defaults_local_30_at_exact_pointer_and_is_resizable(color_planner):
    planner, source, ids = color_planner
    command(planner, 'calendar_source_import', {'source': source})
    sage = next(row for row in planner.model()['cubes'] if row.get('calendar_role') == 'evergreen')
    before = planner.snapshot()
    payload = {'request_id': 'sage-drop-once', 'expected_revision': before['revision'], 'action': 'cube_drop',
        'data': {'cube_id': sage['id'], 'date': '2027-01-06', 'start': '14:03'}}
    result = planner.command(payload)
    row = next(row for row in result['cubes'] if row['id'] == sage['id'])
    assert row['minutes'] == 30 and row['selected_date'] == '2027-01-06' and row['start'] == '14:03'
    assert row['duration_provisional'] and row['can_resize'] and not row['duration_unknown'] and not row['requires_estimate']
    assert row['source_duration_minutes'] is None
    assert planner.snapshot()['state']['planning'] == before['state']['planning']
    assert planner.command(payload) == result and planner.snapshot()['revision'] == result['revision']
    command(planner, 'cube_resize', {'cube_id': sage['id'], 'date': '2027-01-06', 'start': '14:03', 'minutes': 45})
    assert next(row for row in planner.model()['cubes'] if row['id'] == sage['id'])['minutes'] == 45


def test_unknown_sage_drop_shrinks_only_at_same_start_and_failure_is_atomic(color_planner):
    planner, source, ids = color_planner
    command(planner, 'calendar_source_import', {'source': source})
    sage = next(row for row in planner.model()['cubes'] if row.get('calendar_role') == 'evergreen')
    # A chosen ordinary task at14:20 bounds the gap; no modal or relocation.
    command(planner, 'cube_select', {'cube_id': 'geometry', 'date': '2027-01-06', 'start': '14:20'})
    result = command(planner, 'cube_drop', {'cube_id': sage['id'], 'date': '2027-01-06', 'start': '14:03'})
    row = next(row for row in result['cubes'] if row['id'] == sage['id'])
    assert row['minutes'] == 15 and row['start'] == '14:03'
    command(planner, 'cube_return', {'cube_id': sage['id']})
    # Remove the local estimate in a synthetic fresh baseline to exercise the
    # default-seed failure, rather than changing an existing chosen duration.
    state = planner.snapshot()['state']
    state['period_planner']['calendar_offer_estimates'].pop(sage['id'])
    other = model.PeriodPlanner(synthetic_store(planner.store.root/'other', state))
    before = other.snapshot()
    with pytest.raises(ValidationError):
        command(other, 'cube_drop', {'cube_id': sage['id'], 'date': '2027-01-06', 'start': '14:18'})
    assert other.snapshot() == before
    with pytest.raises(ConflictError):
        command(planner, 'cube_drop', {'cube_id': sage['id'], 'date': '2027-01-07', 'start': '14:00'}, revision=1)


def test_legacy_virtual_library_inspection_does_not_fabricate_day_or_native_cube(library_planner):
    planner, library, notes = library_planner
    key = 'brainalot-'+notes['month']['id']
    send(planner, 'period_context', {'date': '2027-01-06', 'cube_id': key})
    native = deepcopy(planner.snapshot()['state']['planning'])
    send(planner, 'constructor_cube', {'cube_id': key, 'kind': 'brainalot', 'source_ref': notes['month']['id'],
        'minutes': 60, 'title': 'Local Library alias', 'planned_date': '2027-01-06', 'output': '', 'steps': [],
        'comment': '', 'links': [], 'groups': [], 'resource_drafts': {'links': [], 'groups': []}})
    assert planner.snapshot()['state']['planning'] == native
    row = next(row for row in sources(planner) if row['id'] == key)
    assert row['title'] == 'Local Library alias' and row['selected_date'] is None
    assert key not in planner.snapshot()['state']['period_planner'].get('placements', {})
    # An unmaterialized Work offer is cancelled locally; no Library note is deleted.
    deleted = send(planner, 'cube_delete', {'cube_id': key})
    assert deleted['delete_result']['status'] == 'complete'
    assert any(note['id'] == notes['month']['id'] for note in library.list_notes())


def test_floating_drop_and_duration_edit_preserve_null_time(library_planner):
    planner, library, notes = library_planner
    key = 'brainalot-'+notes['week']['id']
    command(planner, 'cube_drop', {'cube_id': key, 'date': '2027-01-06', 'start': None})
    command(planner, 'cube_resize', {'cube_id': key, 'date': '2027-01-06', 'start': None, 'minutes': 35})
    row = next(row for row in sources(planner) if row['id'] == key)
    assert row['selected_date'] == '2027-01-06' and row['start'] is None and row['minutes'] == 35
    state, source, ids = color_source_state()
    optional = model.PeriodPlanner(synthetic_store(planner.store.root/'calendar', state))
    command(optional, 'calendar_source_import', {'source': source})
    sage = next(row for row in optional.model()['cubes'] if row.get('calendar_role') == 'evergreen')
    result = command(optional, 'cube_drop', {'cube_id': sage['id'], 'date': '2027-01-06', 'start': None})
    row = next(row for row in result['cubes'] if row['id'] == sage['id'])
    assert row['minutes'] == 30 and row['start'] is None and row['selected_date'] == '2027-01-06'


def test_ambiguous_library_note_ids_never_offer_two_executable_sources(library_planner):
    planner, library, notes = library_planner
    note = notes['week']
    original = library.vault/note['path']
    duplicate = original.with_name('Duplicate source.md')
    duplicate.write_bytes(original.read_bytes())
    assert note['id'] not in {row['source_ref'] for row in sources(planner)}
    before = planner.snapshot()
    with pytest.raises(ValidationError):
        command(planner, 'cube_drop', {'cube_id': 'brainalot-'+note['id'], 'date': '2027-01-06', 'start': '14:00'})
    assert planner.snapshot() == before


def test_accepted_day_constructor_drop_uses_same_atomic_unknown_estimate(color_planner):
    planner, source, ids = color_planner
    command(planner, 'calendar_source_import', {'source': source})
    sage = next(row for row in planner.model()['cubes'] if row.get('calendar_role') == 'evergreen')
    before = planner.snapshot()
    saved = send(planner, 'constructor_select', {'cube_id': sage['id'], 'date': '2027-01-06', 'start': '14:03'})
    assert saved['revision'] == before['revision']+1 and saved['state']['planning'] == before['state']['planning']
    row = next(row for row in planner.model()['cubes'] if row['id'] == sage['id'])
    assert row['minutes'] == 30 and row['selected_date'] == '2027-01-06' and row['start'] == '14:03'
    assert row['duration_provisional'] and row['can_resize']
    moved = send(planner, 'constructor_timeline', {'cube_id': sage['id'], 'date': '2027-01-06', 'start': '15:03'})
    assert moved['revision'] == saved['revision']+1
    assert next(row for row in planner.model()['cubes'] if row['id'] == sage['id'])['start'] == '15:03'


def test_current_library_importance_and_version_override_stale_native_metadata(tmp_path):
    library = Store(tmp_path)
    note = library.capture('Important month task', kind='task', planning_horizon='month', importance_score=0)
    state = synthetic_state()
    state['planning'] = wp.apply_action(state['planning'], 'constructor_cube', {'cube_id': 'native-importance',
        'kind': 'brainalot', 'source_ref': note['id'], 'title': 'Work alias', 'minutes': 60})
    state['planning']['constructor']['cubes']['native-importance']['library_importance_score'] = 5
    planner = model.PeriodPlanner(synthetic_store(tmp_path, state))
    initial = next(row for row in sources(planner) if row['source_ref'] == note['id'])
    assert initial['library_importance_score'] == 0 and initial['library_importance'] == 'low'
    assert initial['library_note_version'] == note['version'] and initial['title'] == 'Work alias' and initial['minutes'] == 60
    raw = planner.snapshot()
    changed = library.update(note['id'], {'importance_score': 5}, note['version'])
    current = next(row for row in sources(planner) if row['source_ref'] == note['id'])
    assert current['library_importance_score'] == 5 and current['library_importance'] == 'critical'
    assert current['library_note_version'] == changed['version']
    assert planner.snapshot() == raw
    from megamozg.work_period.library_rules import _source
    legacy = _source({'id': 'legacy', 'title': 'Legacy task', 'importance': 'high', 'version': 'known'}, 'brainalot-legacy')
    assert legacy['library_importance_score'] == 4 and legacy['library_importance'] == 'high'


def test_fresh_native_work_projects_month_source_before_any_namespace_write(library_planner):
    planner, library, notes = library_planner
    before = planner.snapshot()
    assert not before['state'].get('period_planner')
    day = planner.day_model()
    key = 'brainalot-'+notes['month']['id']
    assert day['enabled'] is True
    assert key in day['planning']['result']['constructor']['days']['2027-01-06']['possible_cube_ids']
    assert planner.snapshot() == before
    saved = send(planner, 'period_context', {'date': '2027-01-06', 'cube_id': key, 'task_id': None})
    assert saved['revision'] == before['revision']+1
    assert saved['state']['period_planner']['context']['cube_id'] == key
    assert saved['state']['planning'] == before['state']['planning']


@pytest.mark.parametrize('surface', ['period', 'day'])
@pytest.mark.parametrize('cleanup', ['return', 'delete'])
@pytest.mark.parametrize('lifecycle', ['done', 'deleted'])
def test_inactive_unstarted_library_source_explicit_cleanup_frees_slot_only(library_planner, surface, cleanup, lifecycle):
    planner, library, notes = library_planner
    note = notes['week']
    key = 'brainalot-'+note['id']
    command(planner, 'cube_drop', {'cube_id': key, 'date': '2027-01-06', 'start': '14:00'})
    if lifecycle == 'done':
        library.update(note['id'], {'status': 'done'}, note['version'])
    else:
        library.delete(note['id'], note['version'])
    before = planner.snapshot()
    if surface == 'period':
        command(planner, 'cube_return' if cleanup == 'return' else 'cube_delete', {'cube_id': key})
    else:
        send(planner, 'constructor_return' if cleanup == 'return' else 'cube_delete', {'cube_id': key})
    current = next(row for row in sources(planner) if row['id'] == key)
    assert current['selected_date'] is None and current['source_active'] is False
    assert current['status'] == ('cancelled' if cleanup == 'delete' else 'available')
    assert current['progress_percent'] == 0 and current['actual_minutes'] == 0
    assert planner.snapshot()['state']['planning'] == before['state']['planning']
    if lifecycle == 'done':
        assert next(row for row in library.list_notes() if row['id'] == note['id'])['status'] == 'done'
    else:
        assert note['id'] not in {row['id'] for row in library.list_notes()}


def test_inactive_native_library_source_cleanup_never_deletes_note_or_rewrites_reports(library_planner):
    planner, library, notes = library_planner
    note = notes['week']
    key = 'brainalot-'+note['id']
    command(planner, 'cube_drop', {'cube_id': key, 'date': '2027-01-06', 'start': '14:00'})
    send(planner, 'cube_progress', {'cube_id': key, 'percent': 0, 'actual_minutes': 0})
    library.update(note['id'], {'status': 'done'}, note['version'])
    before = planner.snapshot()
    deleted = send(planner, 'cube_delete', {'cube_id': key})
    assert deleted['delete_result']['status'] == 'complete'
    assert deleted['state']['planning'] == before['state']['planning']
    assert deleted['state']['planning']['constructor']['source_reports'][key] == {'date': '2027-01-06', 'percent': 0, 'actual_minutes': 0}
    assert next(row for row in library.list_notes() if row['id'] == note['id'])['status'] == 'done'


def test_inactive_library_facts_cannot_be_cleaned_moved_or_renamed(library_planner):
    planner, library, notes = library_planner
    note = notes['month']
    key = 'brainalot-'+note['id']
    command(planner, 'cube_drop', {'cube_id': key, 'date': '2027-01-06', 'start': '14:00'})
    send(planner, 'cube_progress', {'cube_id': key, 'percent': 25, 'actual_minutes': 10})
    library.update(note['id'], {'status': 'done'}, note['version'])
    before = planner.snapshot()
    for action, data in [('cube_return', {'cube_id': key}), ('cube_delete', {'cube_id': key}),
                         ('cube_rename', {'cube_id': key, 'title': 'Forbidden rename'}),
                         ('cube_drop', {'cube_id': key, 'date': '2027-01-07', 'start': '14:00'})]:
        with pytest.raises(ValidationError):
            command(planner, action, data)
    for action, data in [('constructor_return', {'cube_id': key}), ('cube_delete', {'cube_id': key}),
                         ('cube_progress', {'cube_id': key, 'percent': 100})]:
        with pytest.raises(ValidationError):
            send(planner, action, data)
    assert planner.snapshot() == before
    assert before['state']['planning']['constructor']['source_reports'][key]['percent'] == 25


def test_virtual_library_work_details_persist_both_models_without_touching_library_or_native(library_planner):
    planner, library, notes = library_planner
    note = notes['month']
    key = 'brainalot-'+note['id']
    send(planner, 'period_context', {'date': '2027-01-06', 'cube_id': key})
    before = planner.snapshot()
    note_bytes = (library.vault/note['path']).read_bytes()
    details = {'output': 'Local work result', 'comment': 'Work-only annotation', 'steps': ['First step', 'Second step'],
        'links': [{'id': 'resource-1', 'title': 'Resource', 'url': 'https://example.com/resource'}],
        'groups': [{'id': 'group-1', 'title': 'Materials', 'links': []}],
        'resource_drafts': {'links': [], 'groups': []}, 'planned_date': '2027-01-06'}
    send(planner, 'constructor_cube', {'cube_id': key, **details})
    saved = planner.snapshot()
    assert saved['state']['planning'] == before['state']['planning']
    assert saved['state']['period_planner']['cube_details'][key] == details
    period = next(row for row in sources(planner) if row['id'] == key)
    day = next(row for row in planner.day_model()['planning']['result']['constructor']['cubes'] if row['id'] == key)
    for field, value in details.items():
        assert period[field] == day[field] == value
    assert period['source_ref'] == note['id'] and period['selected_date'] is None and period['minutes'] == 60
    assert (library.vault/note['path']).read_bytes() == note_bytes
    assert key not in saved['state']['planning']['constructor']['cubes']


@pytest.mark.parametrize('unsafe', [{'task_id': 'b'}, {'kind': 'task'}, {'actual_minutes': 100},
                                    {'progress_percent': 100}, {'source_ref': 'another-source'},
                                    {'links': [{'id': 'bad', 'url': 'javascript:alert(1)'}]}])
def test_virtual_work_details_reject_identity_facts_and_invalid_resources_atomically(library_planner, unsafe):
    planner, library, notes = library_planner
    key = 'brainalot-'+notes['month']['id']
    send(planner, 'period_context', {'date': '2027-01-06', 'cube_id': key})
    before = planner.snapshot()
    with pytest.raises(ValidationError):
        send(planner, 'constructor_cube', {'cube_id': key, 'comment': 'Must not partially persist', **unsafe})
    assert planner.snapshot() == before
