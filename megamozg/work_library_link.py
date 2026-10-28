"""Core-only commands for exact Note-backed standalone Work cubes.

Title, date, completion and the small planning metadata live in the Note. No
second Work snapshot is written, so journal recovery has no cross-database gap.
The Work revision protects the read model used for collision checks; the Note
version protects the editable source. Elapsed time remains a separate ledger.
"""
from copy import deepcopy
from collections import Counter
from contextlib import nullcontext
import hashlib
import json
import sqlite3
from uuid import uuid4

from filelock import FileLock

from .core import ConflictError, Store, ValidationError
from .shared_state import encoded
from .work_activity import validate_note_activity


ACTIONS = frozenset({'standalone_create', 'standalone_update', 'standalone_undo', 'standalone_delete'})


def delete_canonical_note(root, note_id, expected_version):
    """Library/CLI canonical deletion uses the same exact journal and timer guard."""
    from .work_store import WorkStore
    from .work_period import PeriodPlanner
    from .work_period.library_rules import LibraryReader
    notes = LibraryReader(root).notes()
    note = next((note for note in notes if note['id'] == note_id), None)
    journal = root / 'state' / 'journal.sqlite3'
    request_id = 'library-standalone-delete:'+hashlib.sha256((note_id+':'+str(expected_version)).encode()).hexdigest()
    previous = None
    if journal.exists():
        with sqlite3.connect(journal) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='work_note_requests'").fetchone():
                while True:
                    previous = db.execute('SELECT command,operation_id FROM work_note_requests WHERE request_id=?', (request_id,)).fetchone()
                    if not previous:
                        break
                    undo = db.execute("SELECT id FROM operations WHERE reverses=? AND status='applied'", (previous[1],)).fetchone()
                    if not undo:
                        break
                    # Repeated delete/undo cycles have identical Note bytes and
                    # versions. Each reversal supplies a new durable identity.
                    request_id = 'library-standalone-delete:'+hashlib.sha256((request_id+':'+undo[0]).encode()).hexdigest()
    if (note is None and previous is None) or (note is not None and not note.get('work_cube')):
        return None
    planner = PeriodPlanner(WorkStore(root))
    linked = next((row for row in planner.model()['cubes'] if row.get('source_ref') == note_id), None)
    command = json.loads(previous[0]) if previous else {'request_id': request_id,
        'expected_revision': planner.snapshot()['revision'], 'action': 'standalone_delete',
        'data': {'cube_id': linked['id'] if linked else 'brainalot-'+note_id, 'expected_note_version': expected_version}}
    result = WorkLibraryLink(planner).command(command)
    return {'id': note_id, 'deleted': True, 'operation_id': result['source_result']['operation_id']}


def _boundary(name):
    """Fault-injection boundary; the Core journal owns recovery after intent."""


def _fields(data, required, optional=()):
    if not isinstance(data, dict) or not set(required) <= set(data) or set(data)-set(required)-set(optional):
        raise ValidationError('Неверные поля команды отдельного кубика')


class WorkLibraryLink:
    def __init__(self, planner):
        self.planner = planner
        self.work = planner.store
        self.store = Store(self.work.root)

    def _receipt(self, db, command):
        row = db.execute('SELECT * FROM work_note_requests WHERE request_id=?',
                         (command['request_id'],)).fetchone()
        if row is not None:
            if row['command'] != encoded(command):
                raise ConflictError('request_id уже использован с другим содержимым')
            operation = db.execute('SELECT status,error FROM operations WHERE id=?',
                                   (row['operation_id'],)).fetchone()
            if operation is None or operation['status'] != 'applied':
                raise ConflictError('Изменение источника ещё не завершено; проверьте журнал')
        return row

    def _slot(self, model, note, key):
        from .work_period.calendar_rules import validate_slot
        cube = note['work_cube']
        if note.get('due') is not None:
            validate_slot(model, note['due'], cube['minutes'], cube['start'],
                          exclude_cube=key, work_window=False)

    def _create(self, command, model):
        from .work_period.model import day
        data = command['data']
        _fields(data, {'title', 'date', 'minutes', 'activity_kind'}, {'start'})
        if data['date'] is not None:
            day(data['date'])
        note = {'kind': 'task', 'due': data['date'], 'activity_kind': data['activity_kind'],
                'work_cube': {'minutes': data['minutes'], 'progress_percent': 0, 'start': data.get('start')}}
        validate_note_activity(note, ValidationError)
        self._slot(model, note, None)
        note = self.store.capture(data['title'], kind='task', title=data['title'], due=data['date'],
            source='work', external_id='work-standalone:'+hashlib.sha256(command['request_id'].encode()).hexdigest(),
            activity_kind=data['activity_kind'], work_cube=note['work_cube'], _work_command=command)
        return note['id']

    def _update(self, command, model):
        from .work_period.model import day
        data = command['data']
        _fields(data, {'cube_id', 'expected_note_version', 'patch'})
        patch = data['patch']
        _fields(patch, set(), {'title', 'date', 'minutes', 'percent', 'start', 'activity_kind', 'sources'})
        if not patch:
            raise ValidationError('Пустое изменение кубика')
        row = next((row for row in model['cubes'] if row['id'] == data['cube_id']), None)
        if not row or row.get('kind') != 'brainalot' or not row.get('source_ref'):
            raise ValidationError('Нужен точный кубик задачи Brainalot')
        with self.store._db() as db:
            self.store._recover(db)
            note = self.store._find(row['source_ref'])
            if note['kind'] != 'task' or note['status'] not in {'active', 'done'}:
                raise ConflictError('Исходная задача Brainalot недоступна')
            if note['version'] != data['expected_note_version']:
                raise ConflictError('Заметка уже изменена. Обновите список и повторите решение')
            cube = deepcopy(note.get('work_cube') or {'minutes': row['minutes'],
                'progress_percent': row.get('progress_percent', 0), 'start': row.get('start')})
            update = {'work_cube': cube}
            for key in ('title', 'activity_kind'):
                if key in patch:
                    update[key] = patch[key]
            if 'date' in patch:
                if patch['date'] is not None:
                    day(patch['date'])
                update['due'] = patch['date']
            elif not note.get('work_cube'):
                update['due'] = row.get('selected_date') or note.get('due')
            for source, target in (('minutes', 'minutes'), ('percent', 'progress_percent'), ('start', 'start')):
                if source in patch:
                    cube[target] = patch[source]
            if 'sources' in patch:
                cube['sources'] = deepcopy(patch['sources'])
            if 'percent' in patch:
                update['status'] = 'done' if patch['percent'] == 100 else 'active'
            if not update.get('due', note.get('due')):
                cube['start'] = None
            proposed = {**note, **update}
            validate_note_activity(proposed, ValidationError)
            self._slot(model, proposed, row['id'])
            operation_id = uuid4().hex
            db.execute('INSERT INTO work_note_requests VALUES(?,?,?,?)',
                       (command['request_id'], encoded(command), operation_id, note['id']))
            self.store._update(db, note, update, data['expected_note_version'], operation_id=operation_id)
            return note['id']

    def command(self, command):
        self.planner._envelope(command, {'request_id', 'expected_revision', 'action', 'data'})
        if command['action'] not in ACTIONS:
            raise ValidationError('Неизвестная команда отдельного кубика')
        # Same lock order as linked deletion: deletion coordinator, Note writer,
        # then Work CAS. A competing delete cannot enter between source preflight
        # and journal intent; independent Work edits cannot invalidate slot checks.
        deleting = command['action'] == 'standalone_delete'
        if deleting:
            _fields(command['data'], {'cube_id', 'expected_note_version'})
            if not isinstance(command['data']['cube_id'], str):
                raise ValidationError('Требуется точный ID кубика')
        from .work_delete import timer_reservation_many
        reserved_ids = [command['data']['cube_id']] if deleting else []
        if command['action'] == 'standalone_undo':
            _fields(command['data'], {'operation_id'})
            removed_note_id = self.store._undo_source_removal_id(command['data']['operation_id'])
            if removed_note_id is not None:
                reserved_ids.append('brainalot-'+removed_note_id)
        reservation = timer_reservation_many(self.work.root, reserved_ids) if reserved_ids else nullcontext()
        with FileLock(str(self.work.root/'state'/'work-delete.lock'), timeout=15), reservation, self.store.lock:
            with self.store._db() as db:
                self.store._recover(db)
                receipt = self._receipt(db, command)
            if receipt is None:
                from .work_delete import ensure_writable
                with self.work.db() as work_db:
                    ensure_writable(work_db)
                    current = self.work._version(work_db)
                    self.work.check_revision(command, current['revision'] if current else 0)
                    notes = self.store._scan(validate_cached=True)[0]
                    counts = Counter(note['id'] for note in notes)
                    from .work_period.library_rules import LibraryNotes, deleted_work_note_ids
                    notes = LibraryNotes(note for note in notes if counts[note['id']] == 1)
                    notes.deleted_work_note_ids = deleted_work_note_ids(self.store.state)
                    model = self.planner._model(self.work._read(work_db, current),
                        notes)
                    _boundary('before_source_write')
                    if command['action'] == 'standalone_create':
                        self._create(command, model)
                    elif command['action'] == 'standalone_update':
                        self._update(command, model)
                    elif deleting:
                        data = command['data']
                        row = next((row for row in model['cubes'] if row['id'] == data['cube_id']), None)
                        if not row or row.get('kind') != 'brainalot' or not row.get('standalone') or not row.get('source_ref'):
                            raise ValidationError('Нужен точный ID отдельного кубика Brainalot')
                        note_id = row['source_ref']
                        note = self.store._find(note_id)
                        if note['kind'] != 'task' or not note.get('work_cube') or note['status'] not in {'active', 'done'}:
                            raise ConflictError('Нужен отдельный кубик активной или выполненной задачи Brainalot')
                        self.store.delete(note_id, data['expected_note_version'], _coordinated=True, _work_command=command)
                    else:
                        _fields(command['data'], {'operation_id'})
                        with self.store._db() as db:
                            known = db.execute('SELECT 1 FROM work_note_requests WHERE operation_id=?',
                                               (command['data']['operation_id'],)).fetchone()
                        if not known:
                            raise ValidationError('Отмена доступна только для связанной операции кубика')
                        self.store.undo(command['data']['operation_id'], _work_command=command, _lifetime_reserved=True)
                    _boundary('after_source_write')
                with self.store._db() as db:
                    receipt = self._receipt(db, command)
            try:
                note = self.store._find(receipt['note_id'])
            except LookupError:
                note = None
            source_result = {'cube_id': 'brainalot-'+receipt['note_id'],
                'note_id': receipt['note_id'], 'note_version': note['version'] if note else None,
                'operation_id': receipt['operation_id']}
        result = self.planner.model()
        cube = next((row for row in result['cubes'] if row.get('source_ref') == source_result['note_id']), None)
        if cube:
            source_result['cube_id'] = cube['id']
        result['source_result'] = source_result
        return result
