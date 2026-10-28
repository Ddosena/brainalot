"""Standalone cube/Library synchronization on synthetic roots only."""
from copy import deepcopy
import sqlite3

import pytest

from megamozg.core import Store, ConflictError, ValidationError
from megamozg.work_store import WorkStore
from megamozg.work_period import PeriodPlanner
from megamozg import work_library_link
from test_work_period import synthetic_store, synthetic_state


def send(planner, action, data, request='request', revision=None):
    return planner.command({'request_id': request, 'expected_revision': planner.snapshot()['revision'] if revision is None else revision,
                            'action': action, 'data': data})


def create(planner, request='create'):
    return send(planner, 'standalone_create', {'title': 'Cook pasta', 'date': '2027-01-05',
                'minutes': 30, 'activity_kind': 'personal'}, request)


def one(model):
    return next(row for row in model['cubes'] if row.get('standalone'))


def update(planner, patch, request='update', row=None):
    row = row or one(planner.model())
    return send(planner, 'standalone_update', {'cube_id': row['id'],
        'expected_note_version': row['library_note_version'], 'patch': patch}, request)


def test_empty_create_retry_single_note_cube_and_no_fake_goal(tmp_path):
    planner = PeriodPlanner(WorkStore(tmp_path))
    result = create(planner)
    repeated = create(planner)
    note = Store(tmp_path).list_notes()[0]
    assert result['revision'] == repeated['revision'] == 0
    assert len(result['tasks']) == 0
    assert one(result)['selected_date'] == '2027-01-05'
    assert one(result)['source_ref'] == note['id']
    assert one(result)['id'] == 'brainalot-'+note['id']
    assert len(Store(tmp_path).list_notes()) == 1
    assert len([row for row in result['cubes'] if row.get('standalone')]) == 1
    assert note['kind'] == 'task' and note['due'] == '2027-01-05'
    assert note['activity_kind'] == one(result)['activity_kind'] == 'personal'
    assert not planner.snapshot()['initialized']
    assert one(PeriodPlanner(WorkStore(tmp_path)).model()) == one(result)


def test_standalone_without_day_then_assign_and_return_uses_one_note(tmp_path):
    planner = PeriodPlanner(WorkStore(tmp_path))
    result = send(planner, 'standalone_create', {'title': 'Unscheduled task', 'date': None,
                  'minutes': 30, 'activity_kind': 'personal'}, 'create-without-day')
    assert one(result)['selected_date'] is None
    assert Store(tmp_path).list_notes()[0]['due'] is None
    update(planner, {'date': '2027-01-05', 'start': '10:00'}, 'assign-day')
    assert one(planner.model())['start'] == '10:00'
    update(planner, {'date': None}, 'return-to-unscheduled')
    note = Store(tmp_path).list_notes()[0]
    assert note['due'] is None and note['work_cube']['start'] is None
    assert len(Store(tmp_path).list_notes()) == 1 and planner.snapshot()['revision'] == 0


def test_two_independent_readers_both_directions_title_date_status_and_activity(tmp_path):
    planner = PeriodPlanner(synthetic_store(tmp_path, synthetic_state()))
    observer = PeriodPlanner(WorkStore(tmp_path))
    created = create(planner)
    changed = update(planner, {'title': 'Pasta dinner', 'date': '2027-01-06',
                              'start': '15:00', 'percent': 40, 'activity_kind': 'work'})
    library = Store(tmp_path)
    note = library.list_notes()[0]
    assert note['title'] == 'Pasta dinner' and note['due'] == '2027-01-06'
    assert note['status'] == 'active' and note['work_cube']['progress_percent'] == 40
    assert one(observer.model())['start'] == '15:00'
    assert one(observer.model())['activity_kind'] == 'work'
    before = observer.model()['source_fingerprint']
    note = library.update(note['id'], {'title': 'Boil pasta', 'due': '2027-01-08', 'status': 'done',
                                     'activity_kind': 'personal'}, note['version'])
    for reader in (planner, observer, PeriodPlanner(WorkStore(tmp_path))):
        projected = one(reader.model())
        assert projected['title'] == 'Boil pasta' and projected['selected_date'] == '2027-01-08'
        assert projected['progress_percent'] == 100 and projected['actual_minutes'] == 0
        assert projected['activity_kind'] == 'personal'
        assert reader.model()['source_fingerprint'] != before
        assert one(reader.day_model()['view_model'])['progress_percent'] == 100
    reopened = library.update(note['id'], {'status': 'active', 'due': None}, note['version'])
    assert reopened['work_cube']['start'] is None
    assert one(observer.model())['progress_percent'] == 0
    assert one(observer.model())['selected_date'] is None
    assert created['revision'] == changed['revision'] == planner.snapshot()['revision']


def test_update_retry_after_foreign_edit_does_not_overwrite_and_stale_cas_fails(tmp_path):
    planner = PeriodPlanner(synthetic_store(tmp_path, synthetic_state()))
    create(planner)
    original = one(planner.model())
    data = {'cube_id': original['id'], 'expected_note_version': original['library_note_version'],
            'patch': {'title': 'First update'}}
    saved = send(planner, 'standalone_update', data, 'stable')
    library = Store(tmp_path)
    note = library.list_notes()[0]
    library.update(note['id'], {'title': 'Independent writer'}, note['version'])
    repeated = send(planner, 'standalone_update', data, 'stable')
    assert one(repeated)['title'] == 'Independent writer'
    assert repeated['source_result']['operation_id'] == saved['source_result']['operation_id']
    with pytest.raises(ConflictError):
        send(planner, 'standalone_update', data, 'new-stale')
    with pytest.raises(ConflictError):
        send(planner, 'standalone_update', {**data, 'patch': {'title': 'Wrong reuse'}}, 'stable')
    with pytest.raises(ConflictError):
        send(planner, 'standalone_create', {'title': 'X', 'date': '2027-01-05', 'minutes': 30,
             'activity_kind': 'personal'}, 'stale-work', revision=0)


@pytest.mark.parametrize('action', ['create', 'update'])
def test_response_crash_replay_is_durable_without_duplicate_or_stale_write(tmp_path, monkeypatch, action):
    planner = PeriodPlanner(WorkStore(tmp_path))
    if action == 'update':
        create(planner)
    original = one(planner.model()) if action == 'update' else None
    def crash(boundary):
        if boundary == 'after_source_write':
            raise OSError('simulated response crash')
    with monkeypatch.context() as patch:
        patch.setattr(work_library_link, '_boundary', crash)
        with pytest.raises(OSError):
            create(planner) if action == 'create' else update(planner, {'title': 'Updated'}, row=original)
    result = create(planner) if action == 'create' else update(planner, {'title': 'Updated'}, row=original)
    assert len(Store(tmp_path).list_notes()) == 1
    assert len([row for row in result['cubes'] if row.get('standalone')]) == 1


def test_partial_core_journal_failure_recovers_same_intent(tmp_path, monkeypatch):
    planner = PeriodPlanner(WorkStore(tmp_path))
    original = Store._apply
    def crash(self, db, operation):
        if operation['kind'] == 'capture':
            raise OSError('disk unavailable after durable intent')
        return original(self, db, operation)
    with monkeypatch.context() as patch:
        patch.setattr(Store, '_apply', crash)
        with pytest.raises(OSError):
            create(planner)
    result = create(PeriodPlanner(WorkStore(tmp_path)))
    assert one(result)['title'] == 'Cook pasta'
    assert len(Store(tmp_path).list_notes()) == 1
    with sqlite3.connect(tmp_path/'state'/'journal.sqlite3') as db:
        assert db.execute('SELECT COUNT(*) FROM captures').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM work_note_requests').fetchone()[0] == 1


def test_undo_source_change_is_durable_and_protects_foreign_edits(tmp_path):
    planner = PeriodPlanner(WorkStore(tmp_path))
    create(planner)
    edited = update(planner, {'title': 'New title', 'percent': 100})
    data = {'operation_id': edited['source_result']['operation_id']}
    undone = send(planner, 'standalone_undo', data, 'undo')
    assert one(undone)['title'] == 'Cook pasta' and one(undone)['progress_percent'] == 0
    assert send(planner, 'standalone_undo', data, 'undo')['source_result'] == undone['source_result']
    edited = update(planner, {'title': 'Second title'}, 'second')
    library = Store(tmp_path)
    note = library.list_notes()[0]
    library.update(note['id'], {'title': 'Foreign title'}, note['version'])
    with pytest.raises(ConflictError):
        send(planner, 'standalone_undo', {'operation_id': edited['source_result']['operation_id']}, 'unsafe-undo')


def test_ordinary_library_task_can_opt_into_exact_sync_without_duplicate(tmp_path):
    library = Store(tmp_path)
    note = library.capture('Pasta', kind='task', due='2027-01-05')
    planner = PeriodPlanner(WorkStore(tmp_path))
    row = next(row for row in planner.model()['cubes'] if row.get('source_ref') == note['id'])
    assert not row.get('standalone')
    result = update(planner, {'title': 'Pasta synced', 'percent': 100}, row=row)
    assert one(result)['source_ref'] == note['id'] and one(result)['progress_percent'] == 100
    assert len(library.list_notes()) == 1


@pytest.mark.parametrize('done', [False, True])
@pytest.mark.parametrize('initialized', [False, True])
def test_delete_and_undo_exact_source_on_empty_and_existing_work(tmp_path, done, initialized):
    planner = PeriodPlanner(synthetic_store(tmp_path, synthetic_state()) if initialized else WorkStore(tmp_path))
    create(planner)
    if done:
        update(planner, {'percent': 100})
    row = one(planner.model())
    before = planner.snapshot()
    data = {'cube_id': row['id'], 'expected_note_version': row['library_note_version']}
    result = send(planner, 'standalone_delete', data, 'delete')
    assert not any(cube.get('standalone') for cube in result['cubes'])
    assert Store(tmp_path).list_notes() == []
    assert planner.snapshot() == before
    assert send(planner, 'standalone_delete', data, 'delete')['source_result'] == result['source_result']
    restored = send(planner, 'standalone_undo', {'operation_id': result['source_result']['operation_id']}, 'undo-delete')
    assert one(restored)['source_ref'] == row['source_ref']
    assert one(restored)['progress_percent'] == (100 if done else 0)
    assert len(Store(tmp_path).list_notes()) == 1
    # Replaying an old deletion after undo is a receipt, never another mutation.
    send(planner, 'standalone_delete', data, 'delete')
    assert len(Store(tmp_path).list_notes()) == 1


def test_library_delete_routes_exact_standalone_and_can_delete_after_undo(tmp_path):
    planner = PeriodPlanner(WorkStore(tmp_path))
    create(planner)
    store = Store(tmp_path)
    note = store.list_notes()[0]
    deleted = store.delete(note['id'], note['version'])
    assert not planner.model()['cubes'] and store.list_notes() == []
    assert store.delete(note['id'], note['version']) == deleted
    restored = store.undo(deleted['operation_id'])
    assert one(planner.model())['source_ref'] == restored['id']
    deleted_again = store.delete(restored['id'], restored['version'])
    assert deleted_again['operation_id'] != deleted['operation_id']
    assert store.list_notes() == []
    restored_again = store.undo(deleted_again['operation_id'])
    deleted_third = store.delete(restored_again['id'], restored_again['version'])
    assert deleted_third['operation_id'] not in {deleted['operation_id'], deleted_again['operation_id']}
    assert store.list_notes() == []


def test_standalone_delete_stale_source_and_active_timer_are_guarded(tmp_path):
    import json
    from megamozg.time_tracking import TimeStore
    planner = PeriodPlanner(WorkStore(tmp_path))
    create(planner)
    original = one(planner.model())
    update(planner, {'title': 'New source'})
    with pytest.raises(ConflictError):
        send(planner, 'standalone_delete', {'cube_id': original['id'],
            'expected_note_version': original['library_note_version']}, 'stale-delete')
    row = one(planner.model())
    TimeStore(tmp_path)
    with sqlite3.connect(tmp_path/'state'/'time.sqlite3') as db:
        db.execute("UPDATE timer SET status='running',task=? WHERE id=1",
                   (json.dumps({'source': 'work', 'id': 'work-cube:'+row['id'], 'title': row['title']}),))
    with pytest.raises(ConflictError):
        send(planner, 'standalone_delete', {'cube_id': row['id'],
            'expected_note_version': row['library_note_version']}, 'active-delete')
    assert len(Store(tmp_path).list_notes()) == 1
    with sqlite3.connect(tmp_path/'state'/'time.sqlite3') as db:
        assert db.execute('SELECT status FROM timer WHERE id=1').fetchone()[0] == 'running'


def test_undo_create_cannot_archive_an_active_timer_source(tmp_path):
    import json
    from megamozg.time_tracking import TimeStore
    planner = PeriodPlanner(WorkStore(tmp_path))
    created = create(planner)
    row = one(created)
    TimeStore(tmp_path)
    with sqlite3.connect(tmp_path/'state'/'time.sqlite3') as db:
        db.execute("UPDATE timer SET status='paused',task=? WHERE id=1",
                   (json.dumps({'source': 'work', 'id': 'work-cube:'+row['id'], 'title': row['title']}),))
    with pytest.raises(ConflictError):
        send(planner, 'standalone_undo', {'operation_id': created['source_result']['operation_id']}, 'undo-active-create')
    assert Store(tmp_path).list_notes()[0]['status'] == 'active'


def test_delete_native_alias_keeps_elapsed_history_and_undo_restores_one_identity(tmp_path):
    from megamozg import work_planning as wp
    store = Store(tmp_path)
    note = store.capture('Source title', kind='task', due='2027-01-05', activity_kind='personal',
        work_cube={'minutes': 30, 'progress_percent': 40, 'start': None})
    state = synthetic_state()
    state['planning'] = wp.apply_action(state['planning'], 'constructor_cube', {'cube_id': 'native-alias',
        'kind': 'brainalot', 'source_ref': note['id'], 'title': 'Old native title', 'minutes': 30})
    state['planning'] = wp.apply_action(state['planning'], 'constructor_select',
        {'cube_id': 'native-alias', 'date': '2027-01-05'})
    state['planning'] = wp.apply_action(state['planning'], 'cube_progress',
        {'cube_id': 'native-alias', 'percent': 40, 'actual_minutes': 17})
    planner = PeriodPlanner(synthetic_store(tmp_path, state))
    original = deepcopy(planner.snapshot()['state']['planning'])
    row = one(planner.model())
    assert row['id'] == 'native-alias' and row['actual_minutes'] == 17
    deleted = send(planner, 'standalone_delete', {'cube_id': row['id'],
        'expected_note_version': row['library_note_version']}, 'native-delete')
    native = next(cube for cube in deleted['cubes'] if cube['id'] == 'native-alias')
    assert native['status'] == 'cancelled' and native['actual_minutes'] == 17
    assert planner.snapshot()['state']['planning'] == original
    restored = send(planner, 'standalone_undo', {'operation_id': deleted['source_result']['operation_id']}, 'native-undo')
    assert one(restored)['id'] == 'native-alias' and one(restored)['actual_minutes'] == 17
    assert len([cube for cube in restored['cubes'] if cube.get('source_ref') == note['id']]) == 1


def test_http_source_only_commands_invalidate_etag_and_409_foreign_note(tmp_path):
    from fastapi.testclient import TestClient
    from megamozg.api import create_app
    store = Store(tmp_path)
    app = create_app(tmp_path, port=43891)
    client = TestClient(app, base_url='http://127.0.0.1:43891',
                        headers={'Authorization': 'Bearer '+store.token()})
    path = '/api/work/period/command'
    command = {'request_id': 'http-create', 'expected_revision': 0, 'action': 'standalone_create',
               'data': {'title': 'Pasta HTTP', 'date': '2027-01-05', 'minutes': 30, 'activity_kind': 'personal'}}
    created = client.post(path, json=command)
    assert created.status_code == 200
    row = one(created.json())
    previous = client.get('/api/work/period/model')
    note = store.list_notes()[0]
    edited = client.patch('/api/notes/'+note['id'], json={'expected_version': note['version'],
        'patch': {'title': 'Pasta Library', 'due': '2027-01-06', 'status': 'done'}})
    assert edited.status_code == 200
    current = client.get('/api/work/period/model', headers={'If-None-Match': previous.headers['etag']})
    assert current.status_code == 200 and one(current.json())['progress_percent'] == 100
    assert one(current.json())['title'] == 'Pasta Library'
    stale = {'request_id': 'http-stale', 'expected_revision': 0, 'action': 'standalone_update',
             'data': {'cube_id': row['id'], 'expected_note_version': row['library_note_version'], 'patch': {'title': 'Stale'}}}
    assert client.post(path, json=stale).status_code == 409
    assert client.post(path, json=command).status_code == 200
    deleted = client.request('DELETE', '/api/notes/'+note['id'], json={'expected_version': edited.json()['version']})
    assert deleted.status_code == 200
    assert client.get('/api/work/period/model').json()['cubes'] == []


def test_old_notes_do_not_gain_optional_metadata_on_read_and_nested_metadata_is_copied(tmp_path):
    store = Store(tmp_path)
    legacy = store.capture('Legacy', kind='task')
    before = (store.vault/legacy['path']).read_bytes()
    assert 'activity_kind' not in store.list_notes()[0] and 'work_cube' not in store.list_notes()[0]
    assert (store.vault/legacy['path']).read_bytes() == before
    note = store.capture('Metadata', kind='task', work_cube={'minutes': 30, 'progress_percent': 0, 'start': None})
    note['work_cube']['minutes'] = 90
    assert next(row for row in store.list_notes() if row['id'] == note['id'])['work_cube']['minutes'] == 30


def test_standalone_sources_live_in_note_and_support_undo_without_native_plan(tmp_path):
    planner = PeriodPlanner(WorkStore(tmp_path))
    create(planner)
    sources = [{'id': 'recipe', 'url': 'https://example.test/recipe', 'text': 'Recipe'}]
    saved = update(planner, {'sources': sources})
    assert one(saved)['sources'] == sources
    assert Store(tmp_path).list_notes()[0]['work_cube']['sources'] == sources
    assert planner.snapshot()['revision'] == 0
    undo = send(planner, 'standalone_undo', {'operation_id': saved['source_result']['operation_id']}, 'undo-sources')
    assert one(undo)['sources'] == []
    with pytest.raises(ValidationError):
        update(planner, {'sources': [{'id': 'bad', 'url': 'javascript:alert(1)', 'text': ''}]}, 'bad-sources')


def test_simultaneous_window_create_retries_share_one_note_and_receipt(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    first, second = PeriodPlanner(WorkStore(tmp_path)), PeriodPlanner(WorkStore(tmp_path))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, (first, second)))
    assert results[0]['source_result'] == results[1]['source_result']
    assert len(Store(tmp_path).list_notes()) == 1


def test_delete_response_crash_replay_does_not_resurrect_and_undo_restores(tmp_path, monkeypatch):
    planner = PeriodPlanner(WorkStore(tmp_path))
    create(planner)
    row = one(planner.model())
    data = {'cube_id': row['id'], 'expected_note_version': row['library_note_version']}
    def crash(boundary):
        if boundary == 'after_source_write':
            raise OSError('response failed')
    with monkeypatch.context() as patch:
        patch.setattr(work_library_link, '_boundary', crash)
        with pytest.raises(OSError):
            send(planner, 'standalone_delete', data, 'delete-crash')
    result = send(planner, 'standalone_delete', data, 'delete-crash')
    assert Store(tmp_path).list_notes() == [] and result['cubes'] == []
    undone = send(planner, 'standalone_undo', {'operation_id': result['source_result']['operation_id']}, 'undo-crash')
    assert one(undone)['source_ref'] == row['source_ref']


@pytest.mark.parametrize('path', ['undo-capture', 'archive', 'change-kind', 'undo-reopen', 'feedback-archive'])
@pytest.mark.parametrize('timer_source', ['work', 'library'])
def test_public_core_source_lifetime_paths_reserve_paused_timer_before_notes(tmp_path, path, timer_source):
    import json
    from megamozg.time_tracking import TimeStore
    store = Store(tmp_path)
    note = store.capture('Lifetime source', kind='task', due='2027-01-05',
        work_cube={'minutes': 30, 'progress_percent': 0, 'start': None})
    capture_id = next(op['id'] for op in store.history() if op['kind'] == 'capture')
    operation_id = capture_id
    if path == 'undo-reopen':
        note = store.update(note['id'], {'status': 'archived'}, note['version'])
        note = store.update(note['id'], {'status': 'active'}, note['version'])
        operation_id = next(op['id'] for op in store.history() if op['kind'] == 'update')
    TimeStore(tmp_path)
    task = {'source': timer_source, 'id': 'work-cube:brainalot-'+note['id'] if timer_source == 'work' else note['id'],
            'title': note['title']}
    with sqlite3.connect(tmp_path/'state'/'time.sqlite3') as db:
        db.execute("UPDATE timer SET status='paused',task=? WHERE id=1", (json.dumps(task),))
        timer_before = db.execute('SELECT * FROM timer').fetchall()
    history_before = store.history()
    with pytest.raises(ConflictError):
        if path in {'undo-capture', 'undo-reopen'}:
            store.undo(operation_id)
        elif path == 'archive':
            store.update(note['id'], {'status': 'archived'}, note['version'])
        elif path == 'change-kind':
            store.update(note['id'], {'kind': 'idea'}, note['version'])
        else:
            store.feedback(note['id'], 'archive', note['version'])
    assert store.list_notes()[0]['version'] == note['version']
    assert store.history() == history_before
    with sqlite3.connect(tmp_path/'state'/'time.sqlite3') as db:
        assert db.execute('SELECT * FROM timer').fetchall() == timer_before


def test_public_core_lifetime_guard_expands_native_alias_and_does_not_block_unrelated_notes(tmp_path):
    import json
    from megamozg import work_planning as wp
    from megamozg.time_tracking import TimeStore
    store = Store(tmp_path)
    note = store.capture('Legacy linked task', kind='task')
    state = synthetic_state()
    state['planning'] = wp.apply_action(state['planning'], 'constructor_cube', {'cube_id': 'existing-library-alias',
        'kind': 'brainalot', 'source_ref': note['id'], 'title': 'Old alias', 'minutes': 30})
    synthetic_store(tmp_path, state)
    TimeStore(tmp_path)
    with sqlite3.connect(tmp_path/'state'/'time.sqlite3') as db:
        db.execute("UPDATE timer SET status='paused',task=? WHERE id=1",
                   (json.dumps({'source': 'work', 'id': 'work-cube:existing-library-alias', 'title': 'Old alias'}),))
    with pytest.raises(ConflictError):
        store.update(note['id'], {'kind': 'idea'}, note['version'])
    unrelated = store.capture('Unlinked legacy task', kind='task')
    archived = store.feedback(unrelated['id'], 'archive', unrelated['version'])
    assert archived['status'] == 'archived'
    assert store.undo(store.history()[0]['id'])['status'] == 'active'


def test_core_lifetime_preflight_keeps_validation_and_harmless_undo_compatible(tmp_path):
    import json
    from megamozg.time_tracking import TimeStore
    store = Store(tmp_path)
    note = store.capture('Source', kind='task')
    for patch in ({'status': []}, {'kind': {}}, {'status': {'active': True}}):
        with pytest.raises(ValidationError):
            store.update(note['id'], patch, note['version'])
    note = store.update(note['id'], {'title': 'Renamed'}, note['version'])
    operation = store.history()[0]['id']
    TimeStore(tmp_path)
    with sqlite3.connect(tmp_path/'state'/'time.sqlite3') as db:
        db.execute("UPDATE timer SET status='paused',task=? WHERE id=1",
                   (json.dumps({'source': 'library', 'id': note['id'], 'title': note['title']}),))
    assert store.undo(operation)['title'] == 'Source'


@pytest.mark.parametrize('patch', [{'activity_kind': 'invalid'}, {'minutes': True}, {'minutes': 0},
    {'start': '25:00'}, {'date': '2027-02-30'}, {'percent': 101}, {'title': ''}])
def test_invalid_update_has_no_receipt_or_write(tmp_path, patch):
    planner = PeriodPlanner(WorkStore(tmp_path))
    create(planner)
    original = Store(tmp_path).list_notes()
    with pytest.raises(ValidationError):
        update(planner, patch)
    assert Store(tmp_path).list_notes() == original
    with sqlite3.connect(tmp_path/'state'/'journal.sqlite3') as db:
        assert db.execute('SELECT COUNT(*) FROM work_note_requests').fetchone()[0] == 1
