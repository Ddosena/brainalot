"""HTTP cache invalidation with real synthetic stores and external Markdown edits."""
import os
from copy import deepcopy

import pytest

from megamozg.api import WorkProjectionCache
from megamozg.core import Store
from megamozg.shared_state import encoded
from megamozg.work_period.model import PeriodPlanner
from megamozg.work_store import WorkStore
from test_period_api_install import client
import hashlib


def replace_state(root, mutate):
    store = WorkStore(root)
    snapshot = store.read()
    state = deepcopy(snapshot['state'])
    mutate(state)
    raw = encoded(state).encode()
    digest = hashlib.sha256(raw).hexdigest()
    with store.db() as db:
        db.execute('INSERT OR IGNORE INTO work_blobs VALUES(?,?)', (digest, raw))
        db.execute('INSERT INTO work_versions VALUES(?,?,?,?)',
                   (snapshot['revision']+1, 1, digest, '[]'))


def test_empty_projection_uses_utc_midnight(tmp_path, monkeypatch):
    api, auth = client(tmp_path, monkeypatch, initialized=False)
    zones = []
    def today(zone):
        zones.append(zone)
        return '2026-10-28' if len(zones) == 1 else '2026-10-29'
    monkeypatch.setattr(WorkProjectionCache, 'today', staticmethod(today))
    first = api.get('/api/work/period/model', headers=auth)
    fresh = api.get('/api/work/period/model', headers={**auth, 'If-None-Match': first.headers['etag']})
    assert fresh.status_code == 200 and zones == ['UTC', 'UTC']


@pytest.mark.parametrize('endpoint', ['model', 'day'])
def test_pending_delete_recovery_and_metadata_invalidate_without_revision(tmp_path, monkeypatch, endpoint):
    from megamozg import work_delete
    api, auth = client(tmp_path, monkeypatch)
    store = WorkStore(tmp_path)
    url = '/api/work/period/' + endpoint
    first = api.get(url, headers=auth)
    with store.db() as db:
        db.execute('INSERT INTO work_cube_deletes VALUES(?,?,?,?,?,?,?,?,?)',
                   ('pending-test', encoded({'data': {'cube_id': 'geometry'}}), 'pending',
                    None, None, None, '0'*64, '[]', 'Pending test'))
    calls = []
    # A still-pending recovery must be exposed, without spinning forever.
    monkeypatch.setattr(work_delete, 'recover', lambda work: calls.append(work))
    fresh = api.get(url, headers={**auth, 'If-None-Match': first.headers['etag']})
    assert fresh.status_code == 200 and len(calls) == 1
    assert fresh.json()['deletion_state']['pending'][0]['request_id'] == 'pending-test'
    with store.db() as db:
        db.execute('DELETE FROM work_cube_deletes WHERE request_id=?', ('pending-test',))
    cleared = api.get(url, headers={**auth, 'If-None-Match': fresh.headers['etag']})
    assert cleared.status_code == 200 and 'deletion_state' not in cleared.json()


@pytest.mark.parametrize('endpoint', ['model', 'day'])
def test_unchanged_poll_never_builds_or_decodes_projection(tmp_path, monkeypatch, endpoint):
    api, auth = client(tmp_path, monkeypatch)
    url = '/api/work/period/' + endpoint
    first = api.get(url, headers=auth)
    assert first.status_code == 200
    if endpoint == 'day':
        assert set(first.json()) == {'revision', 'initialized', 'enabled', 'view_model'}
    def forbidden(*args, **kwargs):
        pytest.fail('unchanged poll rebuilt or decoded Work state')
    monkeypatch.setattr(PeriodPlanner, '_model', forbidden)
    monkeypatch.setattr(WorkStore, '_read', forbidden)
    for tag in (first.headers['etag'], 'W/'+first.headers['etag'], '"other", '+first.headers['etag'], '*'):
        reply = api.get(url, headers={**auth, 'If-None-Match': tag})
        assert reply.status_code == 304 and reply.content == b''
    assert api.get(url, headers=auth).content == first.content


@pytest.mark.parametrize('endpoint', ['model', 'day'])
@pytest.mark.parametrize('cause', ['revision', 'calendar', 'settings', 'timezone', 'midnight', 'note_edit', 'note_add', 'note_delete'])
def test_each_input_invalidates(tmp_path, monkeypatch, endpoint, cause):
    api, auth = client(tmp_path, monkeypatch)
    library = Store(tmp_path)
    note = library.capture('Cache Alpha', kind='task')
    url = '/api/work/period/' + endpoint
    monkeypatch.setattr(WorkProjectionCache, 'today', staticmethod(lambda zone: '2026-10-28'))
    first = api.get(url, headers=auth)
    revision = WorkStore(tmp_path).read()['revision']
    if cause == 'revision':
        replace_state(tmp_path, lambda state: state.update(unknown_cache_test=True))
    elif cause == 'calendar':
        replace_state(tmp_path, lambda state: state['planning']['calendar']['events'][0].update(title='Changed calendar event'))
    elif cause == 'settings':
        replace_state(tmp_path, lambda state: state['planning']['settings'].update(day_limit_minutes=240))
    elif cause == 'timezone':
        replace_state(tmp_path, lambda state: state['planning']['settings'].update(timezone='Pacific/Honolulu'))
    elif cause == 'midnight':
        monkeypatch.setattr(WorkProjectionCache, 'today', staticmethod(lambda zone: '2026-10-29'))
    elif cause == 'note_add':
        library.capture('Another cache task', kind='task')
    else:
        path = next(p for p in library.vault.rglob('*.md') if note['id'] in p.read_text(encoding='utf-8'))
        if cause == 'note_delete':
            path.unlink()
        else:
            stat = path.stat()
            path.write_bytes(path.read_bytes().replace(b'Cache Alpha', b'Cache Bravo'))
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    fresh = api.get(url, headers={**auth, 'If-None-Match': first.headers['etag']})
    assert fresh.status_code == 200, cause
    assert fresh.headers['etag'] != first.headers['etag']
    if cause == 'calendar':
        assert 'Changed calendar event' in fresh.text
    if cause.startswith('note_') or cause == 'midnight':
        assert WorkStore(tmp_path).read()['revision'] == revision
    if cause == 'note_edit':
        assert 'Cache Bravo' in fresh.text and 'Cache Alpha' not in fresh.text
    assert api.get(url, headers={**auth, 'If-None-Match': fresh.headers['etag']}).status_code == 304
