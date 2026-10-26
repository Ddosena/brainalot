"""Small, typed commands for editing the visible Work with a conversational agent."""
from hashlib import sha256

from .core import ValidationError
from .work_period import PeriodPlanner
from .work_store import WorkStore


def add_parser(commands, parser_class):
    group = commands.add_parser('edit', help='Короткие команды для совместного редактирования Work')
    actions = group.add_subparsers(dest='edit_command', required=True, parser_class=parser_class)
    context = actions.add_parser('context', help='Компактный выбранный контекст без всего плана')
    context.add_argument('--date')
    context.add_argument('--limit', type=int, default=6)
    for name in ('rename', 'place', 'duration', 'period', 'return', 'delete', 'goal'):
        command = actions.add_parser(name)
        command.add_argument('--id', help='Стабильный ID; иначе используется выбранный элемент')
        command.add_argument('--revision', type=int, required=True, help='revision из свежего context')
        command.add_argument('--request-id', required=True, help='Один ID на логическую правку; при повторе не менять')
        if name == 'rename':
            command.add_argument('title')
            command.add_argument('--kind', choices=('cube', 'task', 'event', 'assignment', 'goal'), default='cube')
        elif name == 'place':
            command.add_argument('date')
            command.add_argument('time', nargs='?', default=None)
        elif name == 'duration':
            command.add_argument('minutes', type=int)
        elif name == 'period':
            command.add_argument('start')
            command.add_argument('end')
        elif name == 'goal':
            command.add_argument('title')
            command.add_argument('--start')
            command.add_argument('--end')


def compact_context(model, *, date=None, limit=6):
    if date is not None:
        from .work_period.model import day
        day(date)
    selected = model.get('context', {})
    cubes = model.get('cubes', [])
    chosen_date = date or selected.get('date') or model.get('today')
    fields = ('id', 'title', 'kind', 'task_id', 'project_id', 'selected_date', 'planned_date',
              'start', 'end', 'minutes', 'progress_percent', 'actual_minutes', 'source_horizon',
              'source_ref', 'source_task_allocations', 'library_note_version', 'library_importance_score',
              'duration_provisional', 'can_resize', 'can_delete')
    brief = lambda row: {key: row[key] for key in fields if key in row and row[key] is not None}
    current = next((row for row in cubes if row['id'] == selected.get('cube_id')), None)
    task_id = current.get('task_id') if current is not None else selected.get('task_id')
    if current and not task_id:
        parents = {part['task_id'] for part in current.get('source_task_allocations', [])}
        task_id = next(iter(parents)) if len(parents) == 1 else None
    task = next((row for row in model.get('tasks', []) + [task for project in model.get('projects', [])
                for task in project.get('tasks', [])] if row['id'] == task_id), None)
    event = next((row for row in model.get('events', []) if row['id'] == selected.get('calendar_event_id')), None)
    day_cubes = [row for row in cubes if row.get('selected_date') == chosen_date]
    projects = {project['id']: project for project in model.get('projects', [])}
    tasks = {task['id']: task for task in model.get('tasks', [])}
    def offered(row):
        if not row.get('available') or row.get('selected_date') == chosen_date or row.get('progress_percent', 0) >= 100:
            return False
        if row.get('status') in {'cancelled', 'canceled', 'done', 'completed'}:
            return False
        task = tasks.get(row.get('task_id'))
        if task and task.get('owned') is False or projects.get(row.get('project_id'), {}).get('visible') is False:
            return False
        if row['kind'] == 'calendar_optional' and row.get('availability') != 'any_day' and row.get('calendar_role') != 'evergreen' and (row.get('source_date') or row.get('planned_date')) != chosen_date:
            return False
        if row['kind'] in {'prep', 'weekly_review'} and row.get('planned_date') not in (None, chosen_date):
            return False
        parents = row.get('source_task_allocations') or ([{'task_id': row['task_id']}] if row.get('task_id') else [])
        if row['kind'] == 'task' and parents and not row.get('returned'):
            return all(tasks.get(part['task_id'], {}).get('start') and tasks.get(part['task_id'], {}).get('end')
                and tasks[part['task_id']]['start'] <= chosen_date <= tasks[part['task_id']]['end'] for part in parents)
        return True
    offers = [row for row in cubes if offered(row)]
    limit = max(0, min(30, limit))
    return {'revision': model['revision'], 'source_fingerprint': model.get('source_fingerprint'),
        'date': chosen_date, 'selection': {key: selected.get(key) for key in ('cube_id', 'task_id', 'project_id', 'calendar_event_id')},
        'cube': brief(current) if current else None,
        'task': {key: task.get(key) for key in ('id', 'title', 'start', 'end', 'deadline', 'window_start', 'window_end', 'read_only')} if task else None,
        'event': brief(event) if event else None,
        'day': [brief(row) for row in day_cubes[:limit]], 'day_count': len(day_cubes),
        'offers': [{key: (row[key][:120] if key == 'title' else row[key]) for key in ('id', 'title', 'kind', 'minutes', 'source_horizon') if key in row}
                   for row in offers[:limit]], 'offer_count': len(offers)}


def _target(args, model, field):
    value = args.id
    if not value and field == 'task_id' and model.get('context', {}).get('cube_id'):
        cube = next((row for row in model['cubes'] if row['id'] == model['context']['cube_id']), {})
        parents = {part['task_id'] for part in cube.get('source_task_allocations', [])}
        value = cube.get('task_id') or (next(iter(parents)) if len(parents) == 1 else None)
    elif not value:
        value = model.get('context', {}).get(field)
    if not value:
        raise ValidationError('Выберите элемент в Work или передайте его --id из context')
    return value


def run(root, args):
    planner = PeriodPlanner(WorkStore(root))
    model = planner.model()
    if args.edit_command == 'context':
        return compact_context(model, date=args.date, limit=args.limit)
    command = args.edit_command
    if command == 'goal':
        identifier = args.id or 'goal-' + sha256(args.request_id.encode()).hexdigest()[:16]
        action, data = 'ruler_create', {'ruler_id': identifier, 'title': args.title,
                                      'start': args.start, 'end': args.end, 'description': ''}
    elif command == 'period':
        identifier = _target(args, model, 'task_id')
        action, data = 'period_set', {'task_id': identifier, 'start': args.start, 'end': args.end}
    elif command == 'rename':
        field = {'cube': 'cube_id', 'task': 'task_id', 'event': 'calendar_event_id',
                 'assignment': 'task_id', 'goal': 'project_id'}[args.kind]
        identifier = _target(args, model, field)
        action, key = {'cube': ('cube_rename', 'cube_id'), 'task': ('task_rename', 'task_id'),
            'event': ('calendar_event_rename', 'event_id'), 'assignment': ('assignment_rename', 'task_id'),
            'goal': ('ruler_update', 'ruler_id')}[args.kind]
        data = {key: identifier, 'title': args.title}
    else:
        identifier = _target(args, model, 'cube_id')
        if command == 'place':
            action, data = 'cube_drop', {'cube_id': identifier, 'date': args.date, 'start': args.time}
        elif command == 'duration':
            cube = next((row for row in model['cubes'] if row['id'] == identifier), None)
            if cube is None:
                raise ValidationError('Кубик не найден; перечитайте context')
            if cube['kind'] == 'calendar_optional' and not cube.get('selected_date'):
                action, data = 'calendar_offer_estimate', {'cube_id': identifier, 'minutes': args.minutes}
            else:
                action, data = 'cube_resize', {'cube_id': identifier, 'date': cube.get('selected_date'),
                    'start': cube.get('start'), 'minutes': args.minutes}
        else:
            action, data = ('cube_return' if command == 'return' else 'cube_delete'), {'cube_id': identifier}
    envelope = {'request_id': args.request_id, 'expected_revision': args.revision, 'action': action, 'data': data}
    result = planner.command(envelope)
    changed = next((row for row in result.get('cubes', []) + result.get('tasks', []) + result.get('events', [])
                    + result.get('projects', []) + [task for project in result.get('projects', [])
                        for task in project.get('tasks', [])] if row['id'] == identifier), None)
    return {'ok': True, 'revision': result['revision'], 'request_id': args.request_id,
            'action': action, 'changed': {key: changed[key] for key in
                ('id', 'title', 'minutes', 'selected_date', 'start', 'end', 'status') if key in changed} if changed else {'id': identifier}}
