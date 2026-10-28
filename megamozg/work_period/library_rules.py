"""Read-only canonical Library tasks projected as stable Work source cards."""
from collections import Counter
from copy import deepcopy
from pathlib import Path
import sqlite3
from filelock import FileLock

from .. import paths
from ..core import Store, LEGACY_IMPORTANCE_SCORES

DEFAULT_MINUTES = 60


class LibraryNotes(list):
    """Read-only identities of deleted canonical sources accompany live Notes."""
    deleted_work_note_ids = frozenset()


def deleted_work_note_ids(state):
    journal = Path(state) / 'journal.sqlite3'
    if not journal.exists():
        return frozenset()
    with sqlite3.connect(journal) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='work_note_requests'").fetchone():
            return frozenset()
        return frozenset(row[0] for row in db.execute("""SELECT r.note_id FROM work_note_requests r
            JOIN operations o ON o.id=r.operation_id WHERE o.kind='delete' AND o.status='applied'
            AND NOT EXISTS (SELECT 1 FROM operations u WHERE u.reverses=o.id AND u.status='applied')"""))


class LibraryReader(Store):
    """Reuse the Store decoder/index without initialization, recovery or rollover."""
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.vault = paths.vault_path(self.root).resolve()
        self.state = self.root / 'state'
        self.lock = FileLock(str(self.state / 'writer.lock'), timeout=15)
        self._index = {}

    def notes(self):
        if not self.vault.is_dir():
            return []
        with self.lock:
            rows, _ = self._scan(validate_cached=True)
            deleted = deleted_work_note_ids(self.state)
        counts = Counter(row['id'] for row in rows)
        # Ambiguous duplicate IDs never authorize execution or a new placement.
        result = LibraryNotes(row for row in rows if counts[row['id']] == 1)
        result.deleted_work_note_ids = frozenset(deleted)
        return result


def _source(note, key):
    horizon = 'day' if note.get('due') else note.get('planning_horizon') or 'unscheduled'
    score = note.get('importance_score')
    if type(score) is not int or not 0 <= score <= 5:
        score = LEGACY_IMPORTANCE_SCORES.get(note.get('importance'), 2)
    row = {'id': key, 'kind': 'brainalot', 'source_ref': note['id'], 'title': note['title'],
        'source_title': note['title'], 'minutes': DEFAULT_MINUTES, 'planned_date': note.get('due'),
        'source_due': note.get('due'), 'source_horizon': horizon, 'library_source': True,
        'library_note_version': note.get('version'), 'source_active': True,
        'library_importance_score': score, 'library_importance': note.get('importance', 'normal'),
        'progress_percent': 0, 'actual_minutes': 0, 'output': '', 'steps': [],
        'comment': '', 'links': [], 'groups': [], 'candidate': False}
    if note.get('activity_kind') is not None:
        row['activity_kind'] = note['activity_kind']
    if note.get('work_cube') is not None:
        cube = note['work_cube']
        percent = 100 if note['status'] == 'done' else cube['progress_percent']
        row.update(standalone=True, minutes=cube['minutes'], selected_date=note.get('due'),
                   start=cube['start'], progress_percent=percent,
                   report_date=note.get('due') if percent else None,
                   sources=deepcopy(cube.get('sources', [])),
                   activity_kind=note.get('activity_kind') or 'personal')
    return row


def catalog(state, native, notes=None):
    """Current native identities/facts win; reads never persist source snapshots."""
    rows = deepcopy(native)
    cached = state.get('period_planner', {}).get('brainalot_sources', {})
    native_by_ref = {row['source_ref']: key for key, row in rows.items()
        if row.get('kind') == 'brainalot' and row.get('source_ref')}
    current = {note['id']: note for note in notes or []}
    eligible = {key: note for key, note in current.items() if note.get('kind') == 'task' and
                (note.get('status') == 'active' or note.get('work_cube') and note.get('status') == 'done')}
    for key, saved in cached.items():
        ref = saved.get('source_ref')
        if ref and ref not in native_by_ref:
            rows.setdefault(key, deepcopy(saved))
    existing_by_ref = {row['source_ref']: key for key, row in rows.items()
        if row.get('kind') == 'brainalot' and row.get('source_ref')}
    for ref, note in eligible.items():
        key = existing_by_ref.get(ref, 'brainalot-'+ref)
        if key not in rows:
            rows[key] = _source(note, key)
        else:
            metadata = _source(note, key)
            row = rows[key]
            if metadata.get('standalone'):
                # Identity/factual elapsed time remain native; source-owned editable
                # fields have one owner, the Note, even after a native time report.
                for field in ('title', 'minutes', 'planned_date', 'selected_date', 'start',
                              'progress_percent', 'report_date', 'activity_kind', 'standalone', 'sources'):
                    row[field] = deepcopy(metadata[field])
            # A native Work name/duration/report is its own durable owner choice.
            if key not in native:
                row['title'] = note['title']
                row['planned_date'] = note.get('due')
            for field in ('source_title', 'source_due', 'source_horizon', 'library_source', 'library_note_version',
                          'library_importance_score', 'library_importance', 'source_active'):
                row[field] = metadata[field]
            if 'activity_kind' in metadata and not metadata.get('standalone'):
                row['activity_kind'] = metadata['activity_kind']
    if notes is not None:
        for row in rows.values():
            if row.get('kind') == 'brainalot':
                row['library_source'] = True
                row['source_active'] = row.get('source_ref') in eligible
                if not row['source_active']:
                    row.pop('standalone', None)
                    if row.get('source_ref') in getattr(notes, 'deleted_work_note_ids', ()):
                        row['standalone_deleted'] = True
                    row['source_unavailable'] = True
                    row['availability_block_reason'] = 'Исходная задача Brainalot завершена, удалена или неоднозначна'
    return rows


def retain(state, row):
    """Persist only an explicitly edited/chosen real Library source."""
    if row.get('kind') != 'brainalot' or not row.get('library_source') or row.get('standalone'):
        return
    state.setdefault('period_planner', {}).setdefault('brainalot_sources', {})[row['id']] = {
        key: deepcopy(row[key]) for key in ('id', 'kind', 'source_ref', 'title', 'source_title', 'minutes',
            'planned_date', 'source_due', 'source_horizon', 'library_source', 'library_note_version',
            'library_importance_score', 'library_importance',
            'output', 'steps', 'comment', 'links', 'groups') if key in row}


def can_cleanup(row):
    """An inactive source may free its Work slot without touching Library facts."""
    return bool(row.get('kind') == 'brainalot' and row.get('source_unavailable')
        and row.get('status') not in {'cancelled', 'started', 'completed'}
        and not any(row.get(key) for key in ('progress_percent', 'actual_minutes', 'historical_facts', 'factual', 'started')))
