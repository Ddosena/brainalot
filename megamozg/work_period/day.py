"""Effective legacy Day projection and typed coordination over one raw WorkStore."""
from copy import deepcopy
from contextlib import nullcontext
from datetime import datetime, timedelta
import hashlib
import time

from ..core import ValidationError
from ..shared_state import encoded
from .. import work_planning as wp
from . import calendar_rules
from . import timeline_rules
from . import cube_ops


FACT_GUARD = 'У этого блока нет исполняемого исходного кубика Work; факты и таймер изменяйте после явной подготовки исходного этапа'


def _native_ids(planning, cube):
    cubes = planning.get('constructor', {}).get('cubes', {})
    if cube['id'] in cubes:
        return cube['id']
    if cube.get('kind') == 'calendar_optional':
        refs = set(cube.get('source_ids', []))
        return next((key for key, row in cubes.items() if row.get('kind') == 'calendar' and row.get('source_ref') in refs), None)
    if _proven_task_source(planning, cube['id']) is not None:
        return cube['id']
    if (cube.get('kind') == 'brainalot' and cube.get('library_source') and cube.get('source_active')
            and type(cube.get('minutes')) is int and 5 <= cube['minutes'] <= 240 and cube['minutes'] % 5 == 0):
        return cube['id']
    return None


def _proven_task_source(planning, key, retained=None):
    """Resolve an existing native task or an exact current AUTO blueprint only."""
    native = planning.get('constructor', {}).get('cubes', {}).get(key)
    if native is not None:
        return deepcopy(native) if native.get('kind') == 'task' else None
    if not isinstance(key, str) or not key.startswith('planned-'):
        return None
    source = next((row for row in planning.get('result', {}).get('cubes', []) if row['id'] == key[8:]
        and not row.get('manual') and not row.get('locked') and not row.get('allocation_stale')), None)
    if source is None and retained:
        source = retained.get(key)
    if source is None or wp.deleted_cube(planning, key, key[8:]) or wp._scope_exempt(planning, source.get('task_id')):
        return None
    minutes = source.get('minutes')
    if type(minutes) is not int or not 5 <= minutes <= 240 or minutes % 5:
        return None
    blueprint = source.get('blueprint_ref') or source['id']
    planned = source.get('planned_date') or source.get('date')
    if planned not in wp._dates(planning['settings']):
        planned = None
    return {**deepcopy(source), 'id': key, 'kind': 'task', 'blueprint_ref': blueprint, 'planned_date': planned}


def _rows(snapshot):
    from .model import read_model, _retained_merge_sources
    planning = snapshot['state']['planning']
    effective = read_model(snapshot)
    rows = []
    for source in effective['cubes']:
        row = deepcopy(source)
        if row['id'] in snapshot['state'].get('period_planner', {}).get('merged_cubes', {}):
            # Legacy deletion matching treats blueprint/canonical IDs as aliases.
            # A surviving composition owns its own identity; absorbed leaf IDs
            # remain provenance only and must not mark the composition deleted.
            row.pop('blueprint_ref', None)
            row['canonical_id'] = row['id']
        native_id = _native_ids(planning, row)
        row['period_native_cube_id'] = native_id
        leaves = row.get('source_ids', []) if row['id'] in snapshot['state'].get('period_planner', {}).get('merged_cubes', {}) else []
        native_cubes = planning.get('constructor', {}).get('cubes', {})
        retained = _retained_merge_sources(snapshot['state'], row['id']) if leaves else {}
        leaf_sources = [_proven_task_source(planning, key, retained) for key in leaves]
        exact_leaves = bool(leaves) and len(leaves) == len(set(leaves)) and all(source is not None for source in leaf_sources)
        reports = [wp._constructor_report(planning, native_cubes[key]) if key in native_cubes else {} for key in leaves]
        uniform = exact_leaves and len({report.get('percent', 0) for report in reports}) <= 1
        dates = {report.get('date') for report in reports if report.get('percent') or report.get('actual_minutes')}
        proportional = exact_leaves and [report.get('actual_minutes', 0) for report in reports] == _prorata(
            sum(report.get('actual_minutes', 0) for report in reports), [source['minutes'] for source in leaf_sources])
        row['period_native_cube_ids'] = list(leaves) if exact_leaves and uniform and proportional and len(dates) <= 1 else ([native_id] if native_id else [])
        if len(dates) == 1:
            row['report_date'] = next(iter(dates))
        if native_id in native_cubes and not row.get('standalone'):
            row['report_date'] = wp._constructor_report(planning, native_cubes[native_id]).get('date')
        row['period_can_record_progress'] = bool(row['period_native_cube_ids'] and row['status'] != 'cancelled' and not row.get('allocation_stale') and not row.get('source_unavailable') and (row.get('available') or row.get('progress_percent') or row.get('actual_minutes')))
        row['period_read_only'] = not row['period_can_record_progress']
        if row['kind'] == 'calendar_optional':
            row.update(kind='calendar', source_ref=native_id and planning['constructor']['cubes'][native_id].get('source_ref') or row['id'])
        row['fixed_calendar'] = False
        rows.append(row)
    return effective, rows


def _prorata(total, weights):
    if not weights:
        return []
    base = [total * weight // sum(weights) for weight in weights]
    order = sorted(range(len(weights)), key=lambda index: (-(total*weights[index] % sum(weights)), index))
    for index in order[:total-sum(base)]:
        base[index] += 1
    return base


def _iso_event(event):
    row = deepcopy(event)
    row.update(stable_id=row['id'], required=True, color=row.get('source_color'))
    row['start'] = row['date'] + 'T' + row['start'] if row.get('start') else row['date']
    row['end'] = (row.get('end_date') or row['date']) + 'T' + row['end'] if row.get('end') else row.get('end_date') or row['date']
    return row


def day_model(snapshot):
    """Project the accepted Day UI without mutating or returning persistent state."""
    state = snapshot.get('state') or {}
    planning = deepcopy(state.get('planning'))
    eligible_library = any(note.get('kind') == 'task' and note.get('status') == 'active' for note in snapshot.get('library_notes') or [])
    result = {'revision': snapshot['revision'], 'initialized': snapshot['initialized'],
        'enabled': bool(planning and (state.get('period_planner') or eligible_library)), 'planning': planning}
    if snapshot.get('deletion_state') is not None:
        result['deletion_state'] = deepcopy(snapshot['deletion_state'])
    if not planning or not result['enabled']:
        from .model import read_model, _empty_projection
        effective = read_model(snapshot) if planning else _empty_projection(snapshot)
        effective['initialized'] = snapshot['initialized']
        effective['planning_initialized'] = bool(planning)
        result['view_model'] = effective
        result['fingerprint'] = hashlib.sha256(encoded(planning).encode('utf-8')).hexdigest()
        return result
    effective, rows = _rows(snapshot)
    effective['initialized'] = snapshot['initialized']
    effective['planning_initialized'] = True
    result['view_model'] = effective
    catalog = {row['id']: row for row in rows if row['status'] != 'cancelled'}
    visible = set(catalog)
    native_catalog = wp._goal_cube_catalog(planning)
    hidden = set(native_catalog) - visible
    ext = state.get('period_planner', {})
    hidden_tasks = {task['id'] for task in planning['tasks']
        if ext.get('deleted_tasks', {}).get(task['id']) or ext.get('deleted_projects', {}).get(
            ext.get('task_fields', {}).get(task['id'], {}).get('project_id', task.get('project_id')))}
    if hidden_tasks:
        planning['tasks'] = [task for task in planning['tasks'] if task['id'] not in hidden_tasks]
        planning['locks'] = [lock for lock in planning.get('locks', []) if lock.get('task_id') not in hidden_tasks]
        for key in ('tasks', 'task_pool'):
            if isinstance(planning.get('result', {}).get(key), list):
                planning['result'][key] = [task for task in planning['result'][key] if task.get('id') not in hidden_tasks and task.get('task_id') not in hidden_tasks]
        ctor_cubes = planning.get('constructor', {}).get('cubes', {})
        for cid in [cid for cid, cube in ctor_cubes.items() if cube.get('task_id') in hidden_tasks]:
            del ctor_cubes[cid]
    for task in planning['tasks']:
        source = next((row for row in effective['tasks'] if row['id'] == task['id']), None)
        if source:
            task['title'] = source['title']
    for key, cube in planning.get('constructor', {}).get('cubes', {}).items():
        effective_row = catalog.get(key)
        if effective_row:
            cube.update(title=effective_row['title'], minutes=effective_row['minutes'])
    # A display record prevents the legacy editor from trying to create a fake
    # native composite. Commands still resolve its exact leaf identities below.
    for row in rows:
        if row['id'] in state.get('period_planner', {}).get('merged_cubes', {}):
            planning.setdefault('constructor', {}).setdefault('cubes', {})[row['id']] = deepcopy(row)
    # Suppress absorbed/deleted forecasts in every legacy fallback pool too.
    blueprint_hidden = hidden | {key[8:] for key in hidden if key.startswith('planned-')}
    planning.setdefault('result', {})['cubes'] = [row for row in planning.get('result', {}).get('cubes', [])
        if row['id'] not in blueprint_hidden and 'planned-' + row['id'] not in hidden]
    deletion_rows = planning.setdefault('cube_deletions', {})
    for key in hidden:
        deletion_rows.setdefault(key, {'cube_id': key, 'blueprint_ref': native_catalog.get(key, {}).get('blueprint_ref'),
            'retired_minutes': 0, 'period_projection': True})
    for row in rows:
        if row['status'] == 'cancelled':
            deletion_rows.setdefault(row['id'], {'cube_id': row['id'], 'blueprint_ref': row.get('blueprint_ref'),
                'retired_minutes': 0, 'period_projection': True})
    prior = planning.get('result', {}).get('constructor', {})
    ctor = planning.get('constructor', {})
    dates = {row['date'] for row in planning.get('result', {}).get('days', [])}
    dates.update(prior.get('days', {}))
    dates.update(row['selected_date'] for row in rows if row.get('selected_date'))
    dates.update(row.get('report_date') for row in rows if row.get('report_date'))
    dates.discard(None)
    days = {}
    for key in sorted(dates):
        meta = deepcopy(prior.get('days', {}).get(key, ctor.get('days', {}).get(key, {})))
        selected = [row for row in catalog.values() if row.get('selected_date') == key]
        events = [_iso_event(event) for event in effective['events'] if event['date'] == key]
        intervals = sorted(piece for event in effective['events'] for piece in calendar_rules.event_intervals(event, key))
        union = []
        for start, end in intervals:
            if union and start <= union[-1][1]:
                union[-1] = (union[-1][0], max(end, union[-1][1]))
            else:
                union.append((start, end))
        fixed = sum(end-start for start, end in union)
        chosen = sum(row['minutes'] for row in selected)
        actual = sum(row.get('actual_minutes', 0) for row in rows if row.get('report_date') == key)
        limit = effective['settings'].get('daily_limit_minutes_by_date', {}).get(key, effective['settings']['daily_limit_minutes'])
        hide_ids = state.get('period_planner', {}).get('day_hidden', {}).get(key, [])
        meta.update(date=key, initialized=True, selected_cube_ids=[row['id'] for row in selected],
            possible_cube_ids=[row['id'] for row in catalog.values() if not row.get('selected_date')
                and row.get('available') and row['id'] not in hide_ids
                and (row.get('planned_date') in (None, key) or row.get('returned'))],
            hidden_cube_ids=list(hide_ids), events=events, selected_minutes=chosen, fixed_minutes=fixed,
            total_work_minutes=chosen+fixed, actual_minutes=actual,
            credited_minutes=sum(row['minutes']*row.get('progress_percent', 0)/100 for row in rows if row.get('report_date') == key),
            budget_minutes=limit, available_minutes=max(0, limit-chosen-fixed), over_cap_minutes=max(0, chosen+fixed-limit))
        days[key] = meta
    goals = []
    records = {'day': deepcopy(ctor.get('days', {})), 'week': deepcopy(ctor.get('weeks', {}))}
    for key in days:
        records['day'].setdefault(key, {})
    for row in catalog.values():
        for key in (row.get('selected_date'), row.get('report_date'), row.get('planned_date')):
            if key:
                known = datetime.fromisoformat(key).date()
                records['week'].setdefault((known-timedelta(days=known.weekday())).isoformat(), {})
    for scope, values in records.items():
        for key, record in sorted(values.items()):
            goal = wp._goal_read_row(scope, key, record, catalog)
            goal['award_variant'] = ctor.get('awards', {}).get('goals', {}).get(goal['id'])
            goals.append(goal)
            if scope == 'day' and key in days:
                days[key]['goal_percent'] = goal['percent']
    for row in rows:
        row['award_variant'] = ctor.get('awards', {}).get('cubes', {}).get(row.get('period_native_cube_id') or row['id'])
    planning['result']['constructor'] = {**deepcopy(prior), 'version': 1, 'cubes': list(catalog.values()),
        'days': days, 'goals': goals, 'goal_cubes': catalog, 'weeks': records['week']}
    result['period_guards'] = {row['id']: {'can_record_progress': row['period_can_record_progress'],
        'native_cube_id': row['period_native_cube_id'], 'native_cube_ids': row['period_native_cube_ids']} for row in rows}
    result['fingerprint'] = hashlib.sha256(encoded(planning).encode('utf-8')).hexdigest()
    return result


def _temporary_native(planning, rows):
    """Only a transient Core input: factual date lookup sees effective selections."""
    transient = deepcopy(planning)
    ctor = transient.setdefault('constructor', {'version': 1, 'cubes': {}, 'days': {}, 'event_required': {}, 'source_reports': {}})
    by_native = {row['period_native_cube_id']: row for row in rows if row.get('period_native_cube_id')}
    for row in rows:
        if row.get('period_native_cube_id') or not row.get('period_can_record_progress'):
            continue
        offset = 0
        for key in row.get('period_native_cube_ids', []):
            if key not in ctor['cubes']:
                continue
            native = ctor['cubes'][key]
            start = row.get('start')
            if start:
                beginning = datetime.fromisoformat(row['selected_date']+'T'+start)+timedelta(minutes=offset)
                start = beginning.strftime('%H:%M')
            by_native[key] = {**row, 'id': key, 'minutes': native['minutes'], 'title': native['title'],
                'start': start, 'end': (datetime.fromisoformat(row['selected_date']+'T'+start)+timedelta(minutes=native['minutes'])).strftime('%H:%M') if start else None}
            offset += native['minutes']
    known = set(ctor['cubes'])
    transient['locks'] = [lock for lock in transient.get('locks', []) if lock['cube_id'] not in known]
    for day in ctor['days'].values():
        day['selected_source_ids'] = [cid for cid in day.get('selected_source_ids', []) if cid not in known]
        day['source_slots'] = {cid: start for cid, start in day.get('source_slots', {}).items() if cid not in known}
    for key, cube in ctor['cubes'].items():
        row = by_native.get(key)
        if row is None or row['status'] == 'cancelled':
            cube['archived_goal_member'] = True
            continue
        cube['title'] = row['title']
        if cube.get('kind') != 'task':
            cube['minutes'] = row['minutes']
        if not row.get('selected_date'):
            continue
        when = row['selected_date']
        target = ctor['days'].setdefault(when, {'initialized': True, 'goal': '', 'goal_percent': 0,
            'selected_source_ids': [], 'source_slots': {}, 'hidden_cube_ids': [], 'positions': {'selected': {}, 'possible': {}}})
        target['initialized'] = True
        target.setdefault('selected_source_ids', [])
        target.setdefault('source_slots', {})
        if cube['kind'] == 'task':
            transient['locks'].append({'cube_id': key, 'task_id': cube['task_id'], 'date': when,
                'minutes': cube['minutes'], 'title': cube['title'], 'manual': True, 'constructor': True,
                'locked': True, 'floating': row.get('start') is None, 'start': row.get('start'), 'end': row.get('end'),
                **({'segment_id': cube['segment_id']} if cube.get('segment_id') else {})})
        else:
            target['selected_source_ids'].append(key)
            if row.get('start'):
                target['source_slots'][key] = row['start']
    return transient


def _facts(state, command, rows):
    native = state['planning']
    action, data = command['action'], command['data']
    if action == 'cube_progress':
        row = next((row for row in rows if row['id'] == data.get('cube_id') or row.get('period_native_cube_id') == data.get('cube_id')), None)
        if row is None or not row.get('period_can_record_progress'):
            raise ValidationError(FACT_GUARD)
        shelf_fact = row.get('selected_date') is None and not row.get('report_date')
        if shelf_fact and row.get('kind') != 'task':
            raise ValidationError('Сначала выберите этот кубик на день; факт не получает дату из старого расписания')
        # Validate every blueprint against the same original snapshot, then
        # materialize the batch once. A first recalculation cannot invalidate a
        # later sibling's already proven ID halfway through this transaction.
        missing = [key for key in row['period_native_cube_ids'] if key not in native.get('constructor', {}).get('cubes', {})]
        specs = []
        active_library = {note['id'] for note in command.get('_library_notes', [])
            if note.get('kind') == 'task' and note.get('status') == 'active'}
        from .model import _retained_merge_sources
        retained = _retained_merge_sources(state, row['id']) if row.get('source_ids') else {}
        for key in missing:
            spec = _proven_task_source(native, key, retained)
            library_row = next((item for item in rows if item['id'] == key and item.get('kind') == 'brainalot'
                and item.get('source_ref') in active_library), None)
            if spec is None and library_row is not None:
                spec = deepcopy(library_row)
            specs.append(spec)
        if any(spec is None for spec in specs):
            raise ValidationError(FACT_GUARD)
        if specs:
            before_materialize = deepcopy(native)
            allowed = {'kind', 'task_id', 'segment_id', 'source_ref', 'minutes', 'title', 'output', 'steps',
                'comment', 'planned_date', 'links', 'groups', 'blueprint_ref'}
            for key, spec in zip(missing, specs):
                wp._constructor_cube_update(native, {'cube_id': key, **{field: deepcopy(spec[field]) for field in allowed if field in spec}})
            wp._constructor_allocation(native, before_materialize)
            state['planning'] = native = wp.calculate(native, reason='period_day_materialize')
            _, rows = _rows({'state': state, 'revision': 0, 'initialized': True, 'library_notes': command.get('_library_notes')})
            row = next(item for item in rows if item['id'] == row['id'])
        transient = _temporary_native(native, rows)
        native_ids = row['period_native_cube_ids']
        if shelf_fact:
            from zoneinfo import ZoneInfo
            report_day = datetime.now(ZoneInfo(native['settings']['timezone'])).date().isoformat()
            # This is a transient date solely for the native fact writer. The
            # persisted cube and its effective placement remain untouched.
            for native_id in native_ids:
                transient['constructor']['cubes'][native_id]['planned_date'] = report_day
        baseline = deepcopy(transient)
        if 'actual_minutes' in data and (type(data['actual_minutes']) is not int or data['actual_minutes'] < 0):
            raise ValidationError('actual_minutes: целое число >=0')
        actuals = _prorata(data.get('actual_minutes', row.get('actual_minutes', 0)), [native['constructor']['cubes'][key]['minutes'] for key in native_ids])
        changed = transient
        for index, native_id in enumerate(native_ids):
            changed = wp.apply_action(changed, action, {**data, 'cube_id': native_id, 'actual_minutes': actuals[index]})
            cube = native['constructor']['cubes'][native_id]
            if cube['kind'] == 'task':
                native.setdefault('completed_cubes', {})[native_id] = deepcopy(changed['completed_cubes'][native_id])
            elif cube['kind'] == 'prep':
                native.setdefault('prep_progress', {})[cube['source_ref']] = deepcopy(changed['prep_progress'][cube['source_ref']])
            else:
                native['constructor'].setdefault('source_reports', {})[native_id] = deepcopy(changed['constructor']['source_reports'][native_id])
    else:
        transient = _temporary_native(native, rows)
        baseline = deepcopy(transient)
        projection = day_model({'state': state, 'revision': 0, 'initialized': True, 'library_notes': command.get('_library_notes')})['planning']
        scope = data.get('scope')
        key = data.get('date')
        goal = next((goal for goal in projection['result']['constructor']['goals'] if goal['scope'] == scope and goal['date'] == key), None)
        if goal and any(not projection['result']['constructor']['goal_cubes'][cid].get('period_native_cube_id')
            or not projection['result']['constructor']['goal_cubes'][cid].get('period_can_record_progress') for cid in goal['cube_ids']):
            raise ValidationError('В цели есть составные или сохранённые блоки без исполняемого Work ID; её закрытие требует сверки состава')
        changed = wp.apply_action(transient, action, data)
        scope_key = 'days' if scope == 'day' else 'weeks'
        target = native['constructor'].setdefault(scope_key, {}).setdefault(key, {})
        record = changed['constructor'][scope_key][key]
        for field in ('goal_status', 'closed_cube_ids', 'closed_cube_snapshots', 'closed_at'):
            if field in record:
                target[field] = deepcopy(record[field])
    wp.update_constructor_awards(changed, baseline)
    if 'awards' in changed.get('constructor', {}):
        native['constructor']['awards'] = deepcopy(changed['constructor']['awards'])
    native['last_update'] = deepcopy(changed.get('last_update'))
    native['needs_recalculation'] = True
    wp._refresh_result_remaining(native)
    native['result']['task_pool'] = wp._task_pool(native, native['result'].get('cubes', []), wp._dates(native['settings']))
    for key, day in native.get('constructor', {}).get('days', {}).items():
        if day.get('initialized'):
            wp._refresh_manual_day(native, key)
    wp._refresh_constructor_result(native)
    return state


def _transform(state, command):
    from .model import apply_command as period_apply_command, apply_context, read_model
    action, data = command['action'], command['data']
    library_notes = command.get('_library_notes')
    def apply_command(document, name, payload):
        return period_apply_command(document, name, payload, library_notes)
    if action == 'period_context':
        return apply_context(state, data, library_notes)
    fields = {'constructor_select': {'cube_id', 'date', 'cube', 'start'}, 'constructor_return': {'cube_id'},
        'constructor_timeline': {'cube_id', 'date', 'start'}, 'constructor_hide': {'cube_id', 'date', 'hidden'},
        'cube_delete': {'cube_id', 'expected_note_version'}, 'cube_progress': {'cube_id', 'percent', 'actual_minutes'},
        'constructor_goal_status': {'scope', 'date', 'status'}}
    if action in fields and set(data) - fields[action]:
        raise ValidationError('Неизвестные поля команды дня')
    snapshot = {'state': state, 'revision': 0, 'initialized': True, 'library_notes': library_notes}
    effective, rows = _rows(snapshot)
    cube_id = data.get('cube_id')
    row = next((row for row in rows if row['id'] == cube_id or row.get('period_native_cube_id') == cube_id), None)
    if action == 'cube_progress' and data.get('percent') == 100 and row and not row.get('selected_date'):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo(state['planning']['settings']['timezone'])).date().isoformat()
        state.setdefault('period_planner', {}).setdefault('placements', {})[row['id']] = {'date': today, 'start': None}
        task = next((task for task in effective['tasks'] if task['id'] == row.get('task_id')), None)
        if task:
            first, last = task.get('start'), task.get('end')
            flags = state['period_planner'].setdefault('independent_cubes', {})
            if (first and today < first) or (last and today > last):
                flags[row['id']] = True
            else:
                flags.pop(row['id'], None)
            if not first and not last:
                state['period_planner'].setdefault('periods', {})[task['id']] = {'start': today, 'end': today}
        _, rows = _rows({'state': state, 'revision': 0, 'initialized': True, 'library_notes': library_notes})
    if action in {'cube_progress', 'constructor_goal_status'}:
        return _facts(state, command, rows)
    if action == 'constructor_cube' and row is not None:
        if row is None:
            raise ValidationError('Исходный этап уже объединён или отменён; обновите экран')
        native_data = deepcopy(data)
        allowed = {'cube_id', 'kind', 'task_id', 'segment_id', 'source_ref', 'minutes', 'title', 'output', 'steps',
            'comment', 'planned_date', 'links', 'groups', 'week_position', 'resource_drafts', 'blueprint_ref', 'sources'}
        if set(native_data) - allowed:
            raise ValidationError('constructor_cube: неизвестные поля')
        if 'title' in native_data:
            state = apply_command(state, 'cube_rename', {'cube_id': row['id'], 'title': native_data.pop('title')})
        if 'minutes' in native_data:
            minutes = native_data.pop('minutes')
            current = next(cube for cube in effective['cubes'] if cube['id'] == row['id'])
            if minutes != current['minutes']:
                allowed_size, reason = timeline_rules.capabilities(current)
                if not allowed_size:
                    raise ValidationError(reason)
                minimum = 0 if current['kind'] in {'prep', 'weekly_review'} else 1
                if type(minutes) is not int or not minimum <= minutes <= 1440:
                    raise ValidationError('Недопустимая длительность кубика')
                if current['kind'] == 'task':
                    state = apply_command(state, 'cube_resize', {'cube_id': row['id'], 'date': current.get('selected_date'),
                        'start': current.get('start'), 'minutes': minutes})
                else:
                    field = 'calendar_offer_estimates' if current['kind'] == 'calendar_optional' else 'cube_durations'
                    state['period_planner'].setdefault(field, {})[row['id']] = minutes
                    if current.get('selected_date'):
                        from .model import _place
                        _place(state, effective, {**current, 'minutes': minutes}, current['selected_date'], current.get('start'))
        native_cube = state['planning'].get('constructor', {}).get('cubes', {}).get(row.get('period_native_cube_id'), {})
        defaults = {'output': '', 'steps': [], 'comment': '', 'links': [], 'groups': []}
        if (not native_cube and row.get('library_source') and not row.get('planned_date')
                and {'kind', 'source_ref'} <= set(native_data)
                and native_data.get('planned_date') in (row.get('selected_date'), state.get('period_planner', {}).get('context', {}).get('date'))):
            # The legacy editor fills a proposal date for every unsaved source.
            # A Library horizon is not permission to schedule it by inspection.
            native_data.pop('planned_date', None)
        for field in list(native_data):
            if field != 'cube_id' and native_data[field] == native_cube.get(field, row.get(field, defaults.get(field))):
                native_data.pop(field)
        prior_details = state.get('period_planner', {}).get('cube_details', {}).get(row['id'], {})
        details = {field: value for field, value in native_data.items() if field in cube_ops.DETAIL_FIELDS
            and (field == 'sources' or not native_cube or field in prior_details)}
        if details:
            checked = cube_ops.validate_details(row, details, state['planning'])
            state.setdefault('period_planner', {}).setdefault('cube_details', {}).setdefault(row['id'], {}).update(checked)
            for field in details:
                native_data.pop(field)
        if set(native_data) != {'cube_id'}:
            if not native_cube:
                raise ValidationError(FACT_GUARD)
            native_data['cube_id'] = row['period_native_cube_id']
            state['planning'] = wp.apply_action(state['planning'], 'constructor_cube', native_data)
        return state
    if action == 'constructor_select' and (row is None or data.get('cube')):
        cube = data.get('cube')
        if cube and (row is None or row.get('candidate')):
            if not isinstance(cube, dict):
                raise ValidationError('Нужен объект исходного кубика')
            if cube.get('kind') == 'brainalot':
                note = next((note for note in library_notes or [] if note['id'] == cube.get('source_ref')), None)
                if note is None or note.get('kind') != 'task' or note.get('status') != 'active':
                    raise ValidationError('Brainalot: исходная активная задача не найдена')
            state['planning'] = wp.apply_action(state['planning'], 'constructor_cube', {**cube, 'cube_id': cube_id})
            effective = read_model({'state': state, 'revision': 0, 'library_notes': library_notes})
            row = next((row for row in effective['cubes'] if row['id'] == cube_id), None)
        if row is None:
            raise ValidationError(FACT_GUARD)
    if action in {'constructor_select', 'constructor_return', 'constructor_timeline', 'cube_delete', 'constructor_hide'}:
        if row is None:
            raise ValidationError('Кубик не найден в действующем плане; обновите экран')
        target_id = row['id']
        if action == 'constructor_select':
            return apply_command(state, 'cube_drop',
                {'cube_id': target_id, 'date': data['date'], 'start': data.get('start')})
        if action == 'constructor_return':
            if row.get('source_unavailable'):
                return apply_command(state, 'cube_return', {'cube_id': target_id})
            if row.get('progress_percent') or row.get('actual_minutes') or row.get('historical_facts'):
                state['period_planner'].setdefault('placements', {})[target_id] = {'date': None, 'start': None}
                return state
            return apply_command(state, 'cube_return', {'cube_id': target_id})
        if action == 'constructor_timeline':
            if data.get('date') not in (None, row.get('selected_date')):
                raise ValidationError('timeline: кубик уже находится на другой дате')
            return apply_command(state, 'cube_drop', {'cube_id': target_id, 'date': row.get('selected_date'), 'start': data['start']})
        if action == 'cube_delete':
            return apply_command(state, 'cube_delete', {'cube_id': target_id})
        if type(data.get('hidden')) is not bool:
            raise ValidationError('hidden: требуется bool')
        if data['hidden'] and row.get('selected_date'):
            state = apply_command(state, 'cube_return', {'cube_id': target_id})
        hidden = state['period_planner'].setdefault('day_hidden', {}).setdefault(data['date'], [])
        if data['hidden'] and target_id not in hidden:
            hidden.append(target_id)
        if not data['hidden']:
            hidden[:] = [key for key in hidden if key != target_id]
        return state
    raise ValidationError('Действие не поддержано координатором дня')


def day_command(planner, command):
    """Return the raw Work snapshot expected by the existing legacy store client."""
    from ..work_delete import recover, ensure_writable
    planner._envelope(command, {'request_id', 'expected_revision', 'action', 'data'})
    if not isinstance(command['action'], str) or not isinstance(command['data'], dict):
        raise ValidationError('Нужны действие и объект data')
    store = planner.store
    library_notes = planner.library.notes()
    recover(store)
    with store.db() as db:
        ensure_writable(db)
        for scope in ('period-day', 'work'):
            old = store.receipt(db, scope, command)
            if old is not None:
                return old
        current = store._version(db)
        store.check_revision(command, current['revision'] if current else 0)
        snapshot = store._read(db, current)
        snapshot['library_notes'] = library_notes
    eligible_library = any(note.get('kind') == 'task' and note.get('status') == 'active' for note in library_notes)
    # Until a plan exists (its first import) there is nothing to coordinate.
    if not isinstance((snapshot.get('state') or {}).get('planning'), dict):
        return store.planning_operation(command)
    if not (snapshot.get('state') or {}).get('period_planner') and command['action'] not in {'period_context', 'cube_progress', 'constructor_goal_status'} and not eligible_library:
        return store.planning_operation(command)
    action, data = command['action'], command['data']
    effective, rows = _rows(snapshot)
    row = next((row for row in rows if row['id'] == data.get('cube_id') or row.get('period_native_cube_id') == data.get('cube_id')), None)
    coordinated = {'constructor_select', 'constructor_return', 'constructor_timeline', 'constructor_hide',
        'cube_delete', 'cube_progress', 'constructor_goal_status', 'period_context'}
    if action == 'constructor_cube' and (row is not None or data.get('cube_id') in snapshot['state']['planning'].get('constructor', {}).get('cubes', {})):
        coordinated.add(action)
    if (action == 'cube_delete' and row and row.get('kind') == 'brainalot'
            and not row.get('source_unavailable')
            and row.get('period_native_cube_id') in snapshot['state']['planning'].get('constructor', {}).get('cubes', {})):
        return store.planning_operation(command)
    if action not in coordinated:
        if data.get('cube_id') and row is None and data['cube_id'] in snapshot['state']['planning'].get('constructor', {}).get('cubes', {}):
            raise ValidationError('Исходный кубик уже объединён или отменён; изменяйте действующий блок плана')
        if data.get('cube_id') and row and not row.get('period_native_cube_id') and action != 'constructor_cube':
            raise ValidationError(FACT_GUARD)
        if action == 'constructor_day' and data.get('replace_unstarted'):
            raise ValidationError('Замена этапов по смене задачи недоступна при общем периодном плане; выберите этапы явно')
        return store.planning_operation(command)
    retain_return = bool(action == 'constructor_return' and row and row.get('kind') in {'prep', 'repeatable'})
    if (action == 'cube_delete' or retain_return) and isinstance(data.get('cube_id'), str):
        from ..work_delete import timer_reservation_many
        ids = [data['cube_id']]
        if retain_return and row.get('kind') == 'prep' and row.get('source_ref'):
            ids.append(row['source_ref'])
        reservation = timer_reservation_many(store.root, ids)
    else:
        reservation = nullcontext()
    with reservation, store.db() as db:
        ensure_writable(db)
        old = store.receipt(db, 'period-day', command)
        if old is not None:
            return old
        current = store._version(db)
        store.check_revision(command, current['revision'] if current else 0)
        snapshot = store._read(db, current)
        before = deepcopy(snapshot['state'])
        try:
            candidate = deepcopy(before)
            if retain_return:
                from ..work_delete import tracked_work_cube_ids
                aliases = {row['id'], row.get('source_ref')}
                if tracked_work_cube_ids(store.root, aliases):
                    candidate.setdefault('period_planner', {}).setdefault('time_retained', {})[row['id']] = True
            state = _transform(candidate, {**command, '_root': store.root, '_library_notes': library_notes})
        except ValidationError:
            raise
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            raise ValidationError('Неверные данные дня: '+str(exc)) from None
        from ..work_manual import sync_volume_by_cubes
        sync_volume_by_cubes(state)
        from .model import record_goal_transitions
        record_goal_transitions(before, state, library_notes)
        if action not in {'cube_progress', 'constructor_goal_status'}:
            wp._constructor_allocation(state['planning'], before['planning'])
        store._guard_deletions(before['planning'], state['planning'])
        wp.validate_planning(state['planning'], state)
        raw = encoded(state).encode('utf-8')
        digest = hashlib.sha256(raw).hexdigest()
        db.execute('INSERT OR IGNORE INTO work_blobs VALUES(?,?)', (digest, raw))
        images = [{key: image[key] for key in ('id', 'blob', 'mime')} for image in snapshot['images']]
        descriptors = store._validate(db, digest, images)
        db.execute('INSERT INTO work_versions VALUES(?,?,?,?)', (current['revision']+1, int(time.time()*1000), digest, encoded(descriptors)))
        result = store._read(db)
        from ..work_manual import take_warnings, filter_volume_warnings
        warnings = filter_volume_warnings(state, take_warnings())
        if warnings:
            result['warnings'] = warnings
        if action == 'cube_delete':
            result['delete_result'] = {'status': 'complete', 'request_id': command['request_id'], 'cube_id': data['cube_id'], 'note_id': None}
        return store.remember(db, 'period-day', command, result)
