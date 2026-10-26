"""Validated namespace adoption; never restore an old native Work document."""
from copy import deepcopy
from datetime import datetime
import json
import re

from ..core import ValidationError
from ..shared_state import encoded, identifier
from .. import work_planning as wp
from . import calendar_rules, course_rules, ruler_rules, timeline_rules

SCHEMA = 'work-period-adopt-v1'
DICT_FIELDS = frozenset({'context', 'periods', 'visibility', 'cube_titles', 'task_titles', 'placements',
    'cancellations', 'merged_cubes', 'merges', 'cube_durations', 'calendar_source',
    'calendar_event_placements', 'calendar_offer_estimates', 'calendar_event_titles', 'course_source',
    'course_task_titles', 'rulers', 'ruler_overrides', 'ruler_entry_overrides', 'ruler_task_completion',
    'course_catalog', 'retained_cubes', 'retained_events'})
LIST_FIELDS = frozenset({'ruler_archive', 'calendar_placement_archive'})


def _fail(message):
    raise ValidationError('Перенос периодного плана: ' + message)


def _walk(value, depth=0):
    if depth > 40:
        _fail('слишком глубокая структура')
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or key in {'__proto__', 'prototype', 'constructor'}:
                _fail('недопустимое поле JSON')
            _walk(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _walk(child, depth + 1)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail('повторяющееся поле JSON')
        result[key] = value
    return result


def _mapping(ext, field):
    value = ext.get(field, {})
    if not isinstance(value, dict) or len(value) > 10000:
        _fail('нужен объект ' + field)
    return value


def _minutes(value, *, zero=False):
    if type(value) is not int or not (0 if zero else 1) <= value <= 1440:
        _fail('нужны целые минуты 0–1440' if zero else 'нужны целые минуты 1–1440')
    return value


def _placement(value, *, event=False):
    from .model import day, clock
    fields = {'date', 'start', 'end', 'end_date'} if event else {'date', 'start'}
    if not isinstance(value, dict) or not {'date', 'start'} <= set(value) or set(value) - fields:
        _fail('неверное размещение')
    if value['date'] is None:
        if value['start'] is not None or event:
            _fail('время без даты')
    else:
        day(value['date'])
        if value['start'] is not None:
            clock(value['start'])
        if event and value['start'] is None:
            _fail('для события нужно точное время')
    if value.get('end') is not None:
        clock(value['end'])
    if value.get('end_date') is not None:
        day(value['end_date'])


def _calendar(ext):
    from .model import day, clock, text
    source = ext.get('calendar_source')
    if source is None:
        return
    if not isinstance(source, dict) or not isinstance(source.get('events'), dict):
        _fail('Calendar source требует events')
    coverage = source.get('coverage', {})
    if day(coverage.get('start')) > day(coverage.get('end')):
        _fail('неверный диапазон Calendar')
    captured = datetime.fromisoformat(source.get('captured_at', '').replace('Z', '+00:00'))
    if captured.tzinfo is None:
        _fail('Calendar captured_at требует часовой пояс')
    for key, row in source['events'].items():
        identifier(key)
        if not isinstance(row, dict) or row.get('id') != key:
            _fail('Calendar ID расходится с ключом')
        for field in ('provider_id', 'provider_calendar_id', 'source_title'):
            text(row.get(field))
        if row.get('calendar_role') not in set(calendar_rules.ROLES.values()) | {'unknown'}:
            _fail('неизвестная роль Calendar')
        if type(row.get('all_day')) is not bool:
            _fail('Calendar all_day требует bool')
        day(row.get('source_date'))
        day(row.get('source_end_date'))
        for field in ('source_start', 'source_end'):
            if row.get(field) is not None:
                clock(row[field])
        if row.get('duration_minutes') is not None:
            _minutes(row['duration_minutes'])
        if row.get('native_event_id') is not None:
            identifier(row['native_event_id'])


def _documentary(ext, state):
    from .model import day, text
    projects = {row['id'] for row in state['projects']}
    tasks = {row['id']: row for row in state['planning']['tasks']}
    catalog = ext.get('course_catalog', {})
    if catalog:
        if set(catalog) - {'items', 'owner_scope', 'schema', 'checked_on', 'private', 'version', 'gaps',
                          'source_work_revision', 'summary', 'user_scope_policy'} or not isinstance(catalog.get('items'), list):
            _fail('неверная форма каталога курса')
        seen = set()
        for row in catalog['items']:
            key = identifier(row.get('id'))
            if key in seen or row.get('project_id') not in projects:
                _fail('повторный ID или неизвестный проект каталога')
            seen.add(key)
            text(row.get('title'))
            tid = row.get('work_task_id')
            if tid is not None and (tid not in tasks or tasks[tid]['project_id'] != row['project_id']):
                _fail('каталог ссылается на чужую/неизвестную задачу')
            if 'owned_by_user' in row and type(row['owned_by_user']) is not bool:
                _fail('неверная принадлежность задачи каталога')
            if row.get('due') is not None:
                day(row['due'])
    if ext.get('course_source') is not None:
        course_rules._validated(state, ext['course_source'])
    for key, goal in _mapping(ext, 'rulers').items():
        if not isinstance(goal, dict) or goal.get('id') != key or key in projects:
            _fail('неверная собственная линейка')
        identifier(key)
        text(goal.get('title'))
        for field in ('start', 'end'):
            if goal.get(field) is not None:
                day(goal[field])
        ruler_rules._window(goal.get('start'), goal.get('end'))
        if not isinstance(goal.get('entries'), dict):
            _fail('линейка требует entries')
        for eid, entry in goal['entries'].items():
            _entry(key, eid, entry)
    for pid, entries in _mapping(ext, 'ruler_entry_overrides').items():
        if not isinstance(entries, dict):
            _fail('неверные отметки линейки')
        for eid, entry in entries.items():
            _entry(pid, eid, entry, override=True)


def _entry(pid, eid, entry, *, override=False):
    from .model import day, text
    identifier(pid)
    identifier(eid)
    if not isinstance(entry, dict):
        _fail('неверная отметка')
    if not override and (entry.get('id') != eid or entry.get('project_id') != pid):
        _fail('отметка ссылается на другую линейку')
    if not override or 'title' in entry:
        text(entry.get('title'))
    if entry.get('date') is not None:
        day(entry['date'])
    if (not override or 'event_kind' in entry) and entry.get('event_kind') not in ruler_rules.KINDS:
        _fail('неверный тип отметки')
    if 'full_completion' in entry and entry['full_completion'] is not None and type(entry['full_completion']) is not bool:
        _fail('неверная готовность отметки')
    if 'visible' in entry and type(entry['visible']) is not bool:
        _fail('неверная видимость отметки')


def _validate_namespace(state, ext):
    from . import model
    native = state['planning']
    native_sources = model._source_catalog(native)
    tasks = {row['id']: row for row in native['tasks']}
    projects = {row['id'] for row in state['projects']}
    for key in ext:
        if key not in DICT_FIELDS | LIST_FIELDS:
            _fail('неизвестный раздел ' + key)
    for field in DICT_FIELDS:
        _mapping(ext, field)
    for field in LIST_FIELDS:
        if field in ext and not isinstance(ext[field], list):
            _fail('нужен список ' + field)
    _calendar(ext)
    _documentary(ext, {**state, model.NAMESPACE: ext})
    metadata = ext.get('calendar_source', {}).get('events', {})
    for key, row in _mapping(ext, 'retained_events').items():
        if (not isinstance(row, dict) or row.get('id') != key or row.get('kind') != 'calendar'
                or key not in metadata or metadata[key].get('calendar_role') != 'flexible'):
            _fail('сохранённое событие требует подтверждённый гибкий источник Calendar')
        _placement({'date': row.get('date'), 'start': row.get('start'), 'end': row.get('end'), 'end_date': row.get('end_date')}, event=True)
        _minutes(row.get('duration_minutes'))
        if row.get('conflict') or row.get('alternatives'):
            _fail('историю фактов нельзя придумывать для сохранённого события')
    retained = _mapping(ext, 'retained_cubes')
    for key, row in retained.items():
        if not isinstance(row, dict) or row.get('id') != key or row.get('kind') not in {'task', 'prep', 'weekly_review', 'brainalot'}:
            _fail('неверный сохранённый источник кубика')
        _minutes(row.get('minutes'), zero=row['kind'] in {'prep', 'weekly_review'})
        if row.get('progress_percent') or row.get('actual_minutes') or row.get('historical_facts'):
            _fail('факты сохранённых источников должны происходить из текущего Work')
        if row.get('kind') == 'task' and row.get('task_id') not in tasks:
            _fail('сохранённый этап требует текущую Work-задачу')
        if row.get('calendar_event_id') is not None and row['calendar_event_id'] not in metadata:
            _fail('сохранённая подготовка ссылается на неизвестное событие')
    sources = {**retained, **native_sources}
    # Native constructor rows inherit their project from their task; the
    # effective model has already materialized it on stored merge allocations.
    for source in sources.values():
        if source.get('task_id') in tasks:
            source.setdefault('project_id', tasks[source['task_id']]['project_id'])
    merged = _mapping(ext, 'merged_cubes')
    merges = _mapping(ext, 'merges')
    if set(merged) != set(merges):
        _fail('состав объединений неполон')
    for key, row in merged.items():
        if not isinstance(row, dict) or row.get('id') != key or row.get('kind') != 'task':
            _fail('неверный объединённый кубик')
        leaves = row.get('source_ids')
        if not isinstance(leaves, list) or not leaves or len(leaves) != len(set(leaves)) or any(cid not in sources for cid in leaves):
            _fail('исходные этапы объединения не найдены в текущем Work')
        leaf_rows = [sources[cid] for cid in leaves]
        if any(leaf.get('kind') != 'task' or leaf.get('task_id') not in tasks for leaf in leaf_rows):
            _fail('объединение требует этапы текущих задач Work')
        if (row.get('minutes') != sum(leaf['minutes'] for leaf in leaf_rows)
                or row.get('progress_percent') or row.get('actual_minutes')):
            _fail('объём/факты объединения расходятся с текущими источниками')
        if any(wp.deleted_cube(native, leaf['id'], leaf.get('blueprint_ref')) for leaf in leaf_rows):
            _fail('объединение содержит удалённый исходный этап')
        from .cube_ops import _allocations, _row_allocations
        expected = _allocations(leaf_rows)
        actual = _row_allocations(row)
        def portions(rows):
            return {(part['task_id'], part.get('project_id')): (part['minutes'], set(part['source_ids'])) for part in rows}
        if portions(expected) != portions(actual):
            _fail('родительский объём объединения расходится с текущим Work')
        record = merges[key]
        if not isinstance(record, dict) or not isinstance(record.get('source_ids'), list) or any(cid not in sources and cid not in merged for cid in record['source_ids']):
            _fail('история объединения ссылается на неизвестный этап')
    candidate = {**state, model.NAMESPACE: ext}
    current_model = model.read_model({'state': candidate, 'revision': 0})
    known_cubes = set(sources) | set(merged) | {row['id'] for row in current_model['cubes']}
    known_events = {row['id'] for row in current_model['events']} | set(metadata)
    known_projects = {row['id'] for row in current_model['projects']}
    known_tasks = set(tasks) | {row['id'] for p in current_model['projects'] for row in p['tasks']}
    for key, period in _mapping(ext, 'periods').items():
        if key not in tasks or not isinstance(period, dict) or set(period) != {'start', 'end'}:
            _fail('период ссылается на неизвестную задачу')
        if model.day(period['start']) > model.day(period['end']):
            _fail('неверный период задачи')
    for key, value in _mapping(ext, 'visibility').items():
        if key not in known_projects or type(value) is not bool:
            _fail('неверная видимость линейки')
    for field, allowed in [('cube_titles', known_cubes), ('task_titles', set(tasks)),
                           ('calendar_event_titles', known_events), ('course_task_titles', known_tasks)]:
        for key, value in _mapping(ext, field).items():
            if key not in allowed:
                _fail('имя ссылается на неизвестный источник')
            model.text(value)
    for key, placement in _mapping(ext, 'placements').items():
        if key not in known_cubes:
            _fail('размещение ссылается на неизвестный кубик')
        _placement(placement)
        native_row = native_sources.get(key)
        if native_row and (native_row.get('progress_percent') or native_row.get('actual_minutes')):
            if placement['date'] != native_row.get('selected_date'):
                _fail('перенос не может менять дату уже выполненной работы')
    for key, value in _mapping(ext, 'cube_durations').items():
        if key not in sources or sources[key].get('kind') not in {'prep', 'weekly_review', 'brainalot'}:
            _fail('длительность ссылается на неизменяемый источник')
        _minutes(value, zero=sources[key]['kind'] in {'prep', 'weekly_review'})
        if sources[key].get('progress_percent') or sources[key].get('actual_minutes'):
            _fail('длительность уже выполненной работы защищена')
    for key, value in _mapping(ext, 'calendar_offer_estimates').items():
        offer = next((row for row in current_model['cubes'] if row['id'] == key and row['kind'] == 'calendar_optional'), None)
        if offer is None or offer.get('source_duration_minutes') is not None and value != offer['source_duration_minutes']:
            _fail('оценка не соответствует источнику Calendar')
        _minutes(value)
    for key, value in _mapping(ext, 'calendar_event_placements').items():
        if key not in known_events or metadata.get(key, {}).get('calendar_role') != 'flexible':
            _fail('перенос требует гибкое событие Calendar')
        _placement(value, event=True)
    cancelled_task_leaves = set()
    for key, record in _mapping(ext, 'cancellations').items():
        if key not in known_cubes or not isinstance(record, dict) or not isinstance(record.get('source'), dict):
            _fail('отмена ссылается на неизвестный источник')
        source = sources.get(key, merged.get(key))
        effective = next((row for row in current_model['cubes'] if row['id'] == key), source or {})
        if (source is None or effective.get('progress_percent') or effective.get('actual_minutes')
                or effective.get('historical_facts') or record.get('minutes') != source.get('minutes')):
            _fail('отмена расходится с текущими фактами/объёмом Work')
        leaves = {cid for cid in record.get('source_ids', [key]) if sources.get(cid, {}).get('kind') == 'task'}
        if leaves & cancelled_task_leaves:
            _fail('один исходный объём отменён дважды')
        cancelled_task_leaves.update(leaves)
        model.text(record.get('reason'))
    # Validates current context against executable/documentary IDs and dates.
    model.apply_context(deepcopy(candidate), ext.get('context', {}))
    # Validate new explicit schedules against the full effective plan, including
    # current native day-specific budgets. Absorbed null rows remain history.
    for cube in current_model['cubes']:
        if cube['id'] in ext.get('placements', {}) and cube.get('selected_date') and cube['status'] != 'cancelled':
            validation_model = {**current_model, 'cubes': [row for row in current_model['cubes'] if row['id'] != cube['id']]}
            model._place(deepcopy(candidate), validation_model, cube, cube['selected_date'], cube.get('start'))
    for event in current_model['events']:
        if event['id'] in ext.get('calendar_event_placements', {}):
            calendar_rules.validate_slot(current_model, event['date'], event['duration_minutes'], event['start'], exclude_event=event['id'])
    return candidate


def adopt_namespace(state, raw):
    if not isinstance(raw, bytes) or len(raw) > 8_000_000:
        _fail('пакет должен быть UTF-8 JSON до 8 МБ')
    packet = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs)
    if not isinstance(packet, dict) or set(packet) != {'schema', 'namespace', 'source_revision', 'source_hash'} or packet['schema'] != SCHEMA:
        _fail('ожидается только пакет namespace схемы ' + SCHEMA)
    if type(packet['source_revision']) is not int or packet['source_revision'] < 0 or not isinstance(packet['source_hash'], str) or not re.fullmatch('[a-f0-9]{64}', packet['source_hash']):
        _fail('неверная исходная ревизия или SHA-256')
    ext = packet['namespace']
    if not isinstance(ext, dict):
        _fail('namespace должен быть объектом')
    _walk(ext)
    encoded(ext)
    if state.get('period_planner'):
        _fail('периодный план уже содержит изменения; используйте типизированные команды')
    candidate = _validate_namespace(state, deepcopy(ext))
    candidate['period_planner']['adoption'] = {'schema': SCHEMA, 'source_revision': packet['source_revision'],
        'source_hash': packet['source_hash']}
    return candidate
