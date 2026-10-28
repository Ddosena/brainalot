"""Manual Work planning: the owner builds the plan by hand, without an agent.

Owner request 2026-10-27: offline and without limits it must be possible to
start from an empty Work, create a project, its tasks, the days a task covers
and the cubes for each day. Agent imports keep these manual tasks.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import re
from uuid import uuid4

from .core import ConflictError, ValidationError
import threading

ACTIONS = frozenset({"manual_project", "manual_project_delete", "manual_task", "manual_task_delete", "manual_cube"})
HORIZON_DAYS = 182
from .calendar_palette import STANDARD
PALETTE = tuple(color for color, _ in STANDARD)


def _fields(data, required, optional=()):
    if not isinstance(data, dict) or not set(required) <= set(data) or set(data) - set(required) - set(optional):
        raise ValidationError("Неверные поля команды")


def _text(value, label, limit):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValidationError(f"{label}: нужен текст до {limit} символов")
    return value.strip()


def _day(value, label):
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except ValueError:
        raise ValidationError(f"{label}: требуется дата ГГГГ-ММ-ДД") from None
    return value


def _minutes(value, label, high):
    if type(value) is not int or not 5 <= value <= high or value % 5:
        raise ValidationError(f"{label}: от 5 минут до {high // 60} ч, шаг 5 минут")
    return value


def empty_planning(today, timezone):
    """A plan without tasks: the next six months, ordinary working hours."""
    start = today - timedelta(days=today.weekday())
    end = start + timedelta(days=HORIZON_DAYS - 1)
    return {"version": 1,
        "settings": {"start": start.isoformat(), "end": end.isoformat(), "timezone": timezone,
            "day_limit_minutes": 300, "work_start": "10:00", "work_end": "20:00",
            "prep_minutes": 25, "break_minutes": 15, "workdays": 6},
        "tasks": [], "calendar": {"events": [], "start": start.isoformat(), "end": end.isoformat(),
            "stale": False, "configured": False},
        "completed_cubes": {}, "day_outcomes": [], "locks": [], "result": {}, "history": []}


def start_state(state, today, timezone):
    """Return the Work state with an empty plan; existing projects and boards stay."""
    from .work_planning import _refresh_constructor_result, calculate, validate_planning
    if state and state.get("planning"):
        raise ConflictError("План Work уже есть; обновите экран")
    state = deepcopy(state) if state else {"version": 1, "projects": [], "items": [],
                                           "boards": [{"id": "general", "items": []}]}
    planning = empty_planning(today, timezone)
    validate_planning(planning, state)
    planning = calculate(planning, reason="manual-start")
    _refresh_constructor_result(planning)
    state["planning"] = planning
    return state


def preserve_manual_tasks(incoming, previous):
    """An agent import replaces its own tasks, never the owner's manual ones."""
    if not isinstance(previous, dict):
        return
    known = {task.get("id") for task in incoming["tasks"] if isinstance(task, dict)}
    incoming["tasks"].extend(deepcopy(task) for task in previous.get("tasks", [])
                             if task.get("origin") == "manual" and task["id"] not in known)


def apply(state, planning, action, data, today):
    """Apply one manual edit; `state` (projects, Week periods) changes in place."""
    planning = deepcopy(planning)
    if action == "manual_project":
        _project(state, data)
    elif action == "manual_project_delete":
        _project_delete(state, planning, data)
    elif action == "manual_task":
        planning = _task(state, planning, data)
    elif action == "manual_task_delete":
        planning = _task_delete(state, planning, data, today)
    elif action == "manual_cube":
        planning = _cube(state, planning, data)
    return planning


def _project(state, data):
    _fields(data, {"name"}, {"project_id", "color"})
    name = _text(data["name"], "Название проекта", 120)
    color = data.get("color")
    if color is None and "project_id" not in data:
        raise ValidationError("Выберите цвет проекта из палитры календаря")
    if color is not None:
        from .work_period.model import read_model
        palette = {row["color"] for row in read_model({"state": state, "revision": 0})["calendar_palette"]}
        if not isinstance(color, str) or color.lower() not in palette:
            raise ValidationError("Цвет проекта должен быть из палитры календаря")
        color = color.lower()
    projects = state.setdefault("projects", [])
    if "project_id" in data:
        project = next((row for row in projects if row["id"] == data["project_id"]), None)
        if project is None:
            raise ValidationError("Проект не найден; обновите экран")
        project["name"] = name
        if "title" in project:
            project["title"] = name
        if color:
            project["color"] = color
        # The ruler editor may hold an older title for this project; the new name wins.
        override = state.get("period_planner", {}).get("ruler_overrides", {}).get(project["id"])
        if override:
            override.pop("title", None)
        return
    used = {row.get("color") for row in projects}
    projects.append({"id": "project-" + uuid4().hex[:12], "name": name,
                     "color": color or next((value for value in PALETTE if value not in used), PALETTE[len(projects) % len(PALETTE)])})


def _project_delete(state, planning, data):
    _fields(data, {"project_id"})
    pid = data["project_id"]
    ext = state.setdefault("period_planner", {})
    if not any(row["id"] == pid for row in state.get("projects", [])) or ext.get("deleted_projects", {}).get(pid):
        raise ValidationError("Проект не найден; обновите экран")
    # A tombstone hides the project and all its goals without altering agent
    # source data, Work items, factual reports or calendar data.
    ext.setdefault("deleted_projects", {})[pid] = True


def _period(data):
    start, end = data.get("start"), data.get("end")
    if (start is None) != (end is None):
        raise ValidationError("Укажите и начало, и конец периода")
    if start is not None and _day(end, "Конец периода") < _day(start, "Начало периода"):
        raise ValidationError("Конец периода раньше начала")
    return start, end


def _task(state, planning, data):
    from .work_planning import reconcile_segment_estimates
    _fields(data, {"project_id", "title", "minutes"}, {"task_id", "start", "end", "deadline", "volume_by_cubes"})
    if not any(row["id"] == data["project_id"] for row in state.get("projects", [])):
        raise ValidationError("Проект не найден; обновите экран")
    title = _text(data["title"], "Название цели", 300)
    minutes = _minutes(data["minutes"], "Объём цели", 60000)
    start, end = _period(data)
    deadline = data.get("deadline")
    if deadline is not None:
        _day(deadline, "Срок")
    previous = deepcopy(planning)
    fields = {"project_id": data["project_id"], "title": title, "due": deadline,
              "due_status": "owner_defined" if deadline else "unknown", "methodist_start": start,
              "estimate_minutes": {"lower": minutes, "central": minutes, "upper": minutes}}
    ext = state.setdefault("period_planner", {})
    if "volume_by_cubes" in data and type(data["volume_by_cubes"]) is not bool:
        raise ValidationError("volume_by_cubes: требуется true/false")
    if "task_id" in data:
        task = next((row for row in planning["tasks"] if row["id"] == data["task_id"]), None)
        if task is None:
            raise ValidationError("Цель не найдена; обновите экран")
        # An explicit owner edit replaces the deadline produced by a prior
        # period shift; otherwise the stale overlay masks the new value.
        ext.get("deadlines", {}).pop(task["id"], None)
        if task.get("origin") != "manual":
            from .work_period.model import apply_command
            ext.setdefault("task_fields", {})[task["id"]] = {
                "project_id": data["project_id"], "minutes": minutes, "deadline": deadline}
            ext.setdefault("task_titles", {})[task["id"]] = title
            ext.get("volume_by_cubes", {}).pop(task["id"], None)
            if start is not None:
                apply_command(state, "period_set", {"task_id": task["id"], "start": start, "end": end})
            else:
                ext.get("periods", {}).pop(task["id"], None)
            return planning
        task.update(fields)
        ext.get("task_fields", {}).get(task["id"], {}).pop("minutes", None)
        ext.get("task_titles", {}).pop(task["id"], None)
    else:
        task = {"id": "manual-" + uuid4().hex[:12], "course": "manual", "kind": "other", "review_minutes": 0,
                "resources": [], "origin": "manual", **fields}
        planning["tasks"].append(task)
    flags = ext.setdefault("volume_by_cubes", {})
    if "volume_by_cubes" in data:
        flags[task["id"]] = data["volume_by_cubes"]
    elif "task_id" in data:
        flags.pop(task["id"], None)
    if start is not None:
        if "task_id" in data:
            from .work_period.model import apply_command
            apply_command(state, "period_set", {"task_id": task["id"], "start": start, "end": end})
        else:
            ext.setdefault("periods", {})[task["id"]] = {"start": start, "end": end}
    else:
        ext.get("periods", {}).pop(task["id"], None)
    reconcile_segment_estimates(planning, previous)
    _sync_result_task(planning, task)
    return planning


def _sync_result_task(planning, task):
    """Day reads remaining work from the plan result; a manual task appears there
    at once, without recalculating the owner's other days."""
    from .work_planning import _remaining, _retired_minutes, _scope_exempt
    rows = planning.setdefault("result", {}).setdefault("tasks", [])
    demand = _remaining(planning).get(task["id"], 0)
    exempt = _scope_exempt(planning, task["id"])
    record = {**deepcopy(task), "remaining_minutes": demand,
              "cancelled_minutes": _retired_minutes(planning, active_only=False)[task["id"]],
              "active_remaining_minutes": 0 if exempt else demand, "scope_exempt": exempt,
              "scheduled_by_due_minutes": 0, "deficit_by_due_minutes": 0,
              "remaining_at_horizon_minutes": 0 if exempt else demand}
    index = next((i for i, row in enumerate(rows) if row.get("id") == task["id"]), None)
    if index is None:
        rows.append(record)
    else:
        rows[index] = {**rows[index], **record}


_warnings = threading.local()


def take_warnings():
    """Warnings raised while one command was applied; reading clears them."""
    value = getattr(_warnings, "items", [])
    _warnings.items = []
    return list(dict.fromkeys(value))


def note_warnings(items):
    _warnings.items = [*getattr(_warnings, "items", []), *items]


class VolumeWarning(str):
    def __new__(cls, message, task_id):
        value = super().__new__(cls, message)
        value.task_id = task_id
        return value


def absorb_overflow(planning):
    """Cubes the owner placed are never refused for lack of a task's volume.

    Owner decision 2026-10-28: no blocking "budget" or "remaining" errors; the
    plan grows and says so. A task's volume (and the stage it comes from)
    grows by exactly the minutes its cubes exceed. Returns warnings.
    """
    from .work_planning import _constructor_allocation
    overflow = _constructor_allocation(planning, check=False)
    if not overflow:
        return []
    tasks = {task["id"]: task for task in planning["tasks"]}
    segments = {row["id"]: row for row in planning.get("task_segments", [])}
    baseline = planning.get("segment_baseline_remaining")
    grown = {}
    for key, minutes in overflow.items():
        kind, ident = key.split(":", 1)
        grow = -(-minutes // 5) * 5
        if kind == "segment" and ident in segments and baseline is not None:
            segment = segments[ident]
            room = max(0, 300 - segment["minutes"])
            step = min(grow, room)
            if not step:
                continue
            segment["minutes"] += step
            baseline[segment["task_id"]] = baseline.get(segment["task_id"], 0) + step
            task = tasks.get(segment["task_id"])
            if task:
                task["estimate_minutes"] = {name: task["estimate_minutes"][name] + step for name in ("lower", "central", "upper")}
                grown[task["id"]] = grown.get(task["id"], 0) + step
    overflow = {key: value for key, value in _constructor_allocation(planning, check=False).items() if key.startswith("task:")}
    for key, minutes in overflow.items():
        task = tasks.get(key[5:])
        if not task:
            continue
        previous = deepcopy(planning)
        grow = -(-minutes // 5) * 5
        task["estimate_minutes"] = {name: task["estimate_minutes"][name] + grow for name in ("lower", "central", "upper")}
        from .work_planning import reconcile_segment_estimates
        reconcile_segment_estimates(planning, previous)
        grown[task["id"]] = grown.get(task["id"], 0) + grow
    for tid in grown:
        _sync_result_task(planning, tasks[tid])
    return [VolumeWarning(f"Объём цели «{tasks[tid]['title']}» вырос на {minutes} мин: кубики заняли больше, чем было запланировано.", tid)
            for tid, minutes in grown.items()]


def _has_facts(planning, tid):
    from .work_planning import _started
    cubes = planning.get("constructor", {}).get("cubes", {})
    segments = {row["id"] for row in planning.get("task_segments", []) if row["task_id"] == tid}
    return (any(entry["task_id"] == tid for entry in planning.get("completed_cubes", {}).values())
            or any(entry["task_id"] == tid for entry in planning.get("day_outcomes", []))
            or tid in planning.get("remaining_epochs", {})
            or any(cubes.get(cid, {}).get("task_id") == tid for cid in planning.get("cube_deletions", {}))
            or any(sid in segments for sid in planning.get("segment_progress", {}))
            or any(lock["task_id"] == tid and _started(planning, lock["cube_id"]) for lock in planning.get("locks", []))
            or any(live.get("task_id") == tid for live in planning.get("live_events", [])))


def _task_delete(state, planning, data, today):
    from .work_planning import apply_scope_exempt, reconcile_segment_estimates
    _fields(data, {"task_id"})
    task = next((row for row in planning["tasks"] if row["id"] == data["task_id"]), None)
    if task is None:
        raise ValidationError("Цель не найдена; обновите экран")
    tid = task["id"]
    reassigned = state.get("period_planner", {}).get("cube_tasks", {})
    native_cubes = planning.get("constructor", {}).get("cubes", {})
    has_reassigned_source = any(cube.get("task_id") == tid and reassigned.get(cid) not in (None, tid)
        for cid, cube in native_cubes.items())
    if not has_reassigned_source:
        # A moved composition is keyed by merged ID, not by its native leaves.
        # Preserve those leaves and their source task when hiding the old goal.
        merged = state.get("period_planner", {}).get("merged_cubes", {})
        has_reassigned_source = any(
            target != tid and target is not None and record is not None and (
                any(native_cubes.get(cid, {}).get("task_id") == tid for cid in record.get("source_ids", []))
                or any(part.get("task_id") == tid for part in record.get("source_task_allocations", [])))
            for key, target in reassigned.items() for record in [merged.get(key)])
    if task.get("origin") == "manual" and not _has_facts(planning, tid) and not has_reassigned_source:
        previous = deepcopy(planning)
        planning["tasks"] = [row for row in planning["tasks"] if row["id"] != tid]
        ctor = planning.get("constructor", {})
        gone = {cid for cid, cube in ctor.get("cubes", {}).items() if cube.get("task_id") == tid}
        for cid in gone:
            ctor["cubes"].pop(cid)
        for day in ctor.get("days", {}).values():
            day["selected_source_ids"] = [cid for cid in day.get("selected_source_ids", []) if cid not in gone]
            day["hidden_cube_ids"] = [cid for cid in day.get("hidden_cube_ids", []) if cid not in gone]
            for cid in gone:
                day.get("source_slots", {}).pop(cid, None)
            if day.get("main_task_id") == tid:
                day["main_task_id"] = None
        planning["locks"] = [lock for lock in planning.get("locks", []) if lock["task_id"] != tid]
        result = planning.get("result", {})
        result["cubes"] = [cube for cube in result.get("cubes", []) if cube.get("task_id") != tid]
        result["tasks"] = [row for row in result.get("tasks", []) if row.get("id") != tid]
        for row in result.get("days", []):
            if row.get("focus_task_id") == tid:
                row["focus_task_id"] = None
        planning.get("scope_exemptions", {}).pop(tid, None)
        monthly = planning.get("monthly_plan")
        if monthly and tid in monthly.get("task_ids", []):
            monthly["task_ids"] = [value for value in monthly["task_ids"] if value != tid]
        from .work_planning import reconcile_segment_estimates
        reconcile_segment_estimates(planning, previous)
        for key in ("periods", "task_titles", "volume_by_cubes"):
            state.get("period_planner", {}).get(key, {}).pop(tid, None)
    else:
        state.setdefault("period_planner", {}).setdefault("deleted_tasks", {})[tid] = True
    return planning



def _cover(state, task, day):
    """A cube is shown on the days of its task: widen the task period to its day."""
    periods = state.setdefault("period_planner", {}).setdefault("periods", {})
    current = periods.get(task["id"])
    first = current["start"] if current else task.get("methodist_start") or day
    last = current["end"] if current else task.get("due") or day
    first, last = min(first, day), max(last, day)
    if first > last:
        first, last = day, day
    periods[task["id"]] = {"start": first, "end": last}


def _cube(state, planning, data):
    from .work_planning import _constructor_allocation, _constructor_cube_update, _scope_exempt, reconcile_segment_estimates
    _fields(data, {"task_id", "title", "minutes"}, {"date"})
    task = next((row for row in planning["tasks"] if row["id"] == data["task_id"]), None)
    if task is None:
        raise ValidationError("Цель не найдена; обновите экран")
    if _scope_exempt(planning, task["id"]):
        raise ValidationError("Цель убрана из работы")
    title = _text(data["title"], "Название кубика", 300)
    minutes = _minutes(data["minutes"], "Длительность кубика", 240)
    cube = {"cube_id": "manual-cube-" + uuid4().hex[:12], "kind": "task", "task_id": task["id"],
            "title": title, "minutes": minutes}
    if data.get("date") is not None:
        cube["planned_date"] = _day(data["date"], "День кубика")
        from .work_period.model import read_model
        projection = read_model({"state": state, "revision": 0})
        effective = next((item for item in projection["tasks"] if item["id"] == task["id"]), None)
        first, last = (effective or {}).get("start"), (effective or {}).get("end")
        if (first and cube["planned_date"] < first) or (last and cube["planned_date"] > last):
            state.setdefault("period_planner", {}).setdefault("independent_cubes", {})[cube["cube_id"]] = True
        elif not first and not last:
            _cover(state, task, cube["planned_date"])
    previous = deepcopy(planning)
    _constructor_cube_update(planning, cube)
    overflow = _constructor_allocation(planning, check=False).get("task:" + task["id"], 0)
    if overflow and task.get("origin") == "manual":
        # A manual task is sized by its cubes: the volume grows instead of refusing.
        grow = -(-overflow // 5) * 5
        estimate = task["estimate_minutes"]
        task["estimate_minutes"] = {key: estimate[key] + grow for key in ("lower", "central", "upper")}
        reconcile_segment_estimates(planning, previous)
        _sync_result_task(planning, task)
        if not state.get("period_planner", {}).get("volume_by_cubes", {}).get(task["id"]):
            note_warnings([f"Объём цели «{task['title']}» вырос на {grow} мин: кубики заняли больше, чем было запланировано."])
    return planning


def sync_volume_by_cubes(state):
    """Persist the displayed cube sum for manual quick goals, without changing agent facts."""
    flags = state.get("period_planner", {}).get("volume_by_cubes", {})
    if not any(flags.values()) or not state.get("planning"):
        return
    from .work_period.model import read_model
    from .work_planning import reconcile_segment_estimates
    view = read_model({"state": state, "revision": 0})
    totals = {task["id"]: task["minutes"] for task in view["tasks"] if flags.get(task["id"])}
    planning = state["planning"]
    before = deepcopy(planning)
    changed = []
    for task in planning["tasks"]:
        total = totals.get(task["id"])
        if task.get("origin") == "manual" and total is not None and task["estimate_minutes"]["central"] != total:
            task["estimate_minutes"] = dict.fromkeys(("lower", "central", "upper"), total)
            changed.append(task)
    if changed:
        reconcile_segment_estimates(planning, before)
        for task in changed:
            _sync_result_task(planning, task)


def filter_volume_warnings(state, warnings):
    flags = state.get("period_planner", {}).get("volume_by_cubes", {})
    return [warning for warning in warnings if not flags.get(getattr(warning, "task_id", None))]
