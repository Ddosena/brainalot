"""Deterministic, local Work planning. No project data or model calls live here."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
import math
from pathlib import Path
from random import SystemRandom
from urllib.parse import urlsplit
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .core import ValidationError
from .shared_state import encoded, identifier

MAX_DAYS = 366
AWARD_VARIANTS = ("enamel", "crystal", "order", "cup", "relic", "crown", "shield", "compass",
                  "laurel", "sun", "peak", "book", "phoenix", "orbit", "obelisk")
STEPS = {
    "webinar_prep": [("Исследование", "проверенные источники"), ("Практика", "повторяемые шаги"),
                     ("Слайды", "готовый блок слайдов"), ("Сценарий", "текст действий"),
                     ("Проверка", "проверенный материал")],
    "video": [("Исследование", "план и источники"), ("Практика", "проверенная демонстрация"),
              ("Сценарий", "готовый фрагмент сценария"), ("Запись", "записанный блок"),
              ("Проверка", "проверенный фрагмент")],
    "longread": [("Исследование", "проверенные источники"), ("Структура", "план материала"),
                 ("Черновик", "готовый раздел"), ("Примеры", "практические примеры"),
                 ("Проверка", "проверенный раздел")],
    "slides": [("Структура", "план слайдов"), ("Слайды", "готовый блок слайдов"),
               ("Примеры", "проверенные изображения и демонстрации"),
               ("Проверка", "проверенная презентация")],
    "assignment": [("Критерии", "критерии выполнения"), ("Черновик", "готовое задание"),
                   ("Проверка", "проверенное решение")],
}
STEPS["other"] = [("Разработка", "готовый блок"), ("Проверка", "проверенный блок")]
WEEKLY_REVIEW_TITLE = "Спланировать следующую неделю с агентом"
WEEKLY_REVIEW_CHECKLIST = (
    ("outcomes", "Итоги прошедшей недели"),
    ("calendar", "Календарь и дедлайны"),
    ("remaining", "Остатки и неопределённости"),
    ("steps", "Пошаговые задачи на 7 дней"),
    ("recalculate", "Пересчёт и согласование плана"),
)


def _ensure_segments(planning):
    if "task_segments" in planning:
        return
    baseline = _remaining_unsegmented(planning)
    segments = []
    for task in planning["tasks"]:
        total = baseline[task["id"]]
        if total <= 0:
            continue
        count = (total + 299) // 300
        base, extra = divmod(total, count)
        steps = STEPS[task.get("kind", "other")]
        for index in range(count):
            minutes = base + (1 if index < extra else 0)
            label, output = steps[min(len(steps)-1, index * len(steps) // count)]
            segments.append({
                "id": "segment:" + sha256(f"{task['id']}|{index}".encode()).hexdigest()[:24],
                "task_id": task["id"], "parent_id": None,
                "title": f"{label}: {task['title']}", "minutes": minutes,
                "output": output, "steps": [], "origin": "auto",
            })
    planning["task_segments"] = segments
    planning["segment_baseline_remaining"] = baseline
    planning.setdefault("segment_progress", {})


def reconcile_segment_estimates(planning, previous):
    """Keep started/manual segments while absorbing a corrected parent estimate."""
    if "task_segments" not in planning:
        return
    baseline = planning["segment_baseline_remaining"]
    prior_tasks = {task["id"]: task for task in previous.get("tasks", [])}
    current_tasks = {task["id"]: task for task in planning["tasks"]}
    active = set(planning.get("segment_progress", {}))
    active.update(entry["segment_id"] for entry in planning.get("completed_cubes", {}).values()
                  if entry.get("segment_id") and (entry["percent"] or entry.get("actual_minutes")))
    active.update(lock.get("segment_id") for lock in planning.get("locks", []) if lock.get("segment_id"))
    active.update(cube.get("segment_id") for cid, cube in planning.get("constructor", {}).get("cubes", {}).items()
                  if cid in planning.get("cube_deletions", {}) and cube.get("segment_id"))
    for task_id in list(baseline):
        if task_id not in current_tasks:
            if any(segment["task_id"] == task_id and segment["id"] in active
                   for segment in planning["task_segments"]):
                raise ValidationError("Нельзя удалить задачу с начатым или выбранным этапом")
            planning["task_segments"] = [segment for segment in planning["task_segments"]
                                         if segment["task_id"] != task_id]
            del baseline[task_id]
    for task_id, task in current_tasks.items():
        if task_id not in baseline:
            baseline[task_id] = 0
            delta = task["estimate_minutes"]["central"]
        else:
            delta = task["estimate_minutes"]["central"] - prior_tasks.get(
                task_id, task)["estimate_minutes"]["central"]
        if delta < 0:
            amount = -delta
            donors = [segment for segment in reversed(planning["task_segments"])
                      if segment["task_id"] == task_id and segment["origin"] == "auto"
                      and segment["id"] not in active]
            if sum(segment["minutes"] for segment in donors) < amount:
                raise ValidationError("Исправленная оценка меньше начатых или выбранных этапов")
            for donor in donors:
                take = min(amount, donor["minutes"])
                donor["minutes"] -= take
                amount -= take
                if donor["minutes"] == 0:
                    planning["task_segments"].remove(donor)
                if amount == 0:
                    break
        elif delta > 0:
            step, output = STEPS[task.get("kind", "other")][0]
            while delta:
                size = min(delta, 300)
                planning["task_segments"].append({
                    "id": "segment:" + uuid4().hex, "task_id": task_id, "parent_id": None,
                    "title": f"{step}: {task['title']}", "minutes": size,
                    "output": output, "steps": [], "origin": "auto",
                })
                delta -= size
            delta = task["estimate_minutes"]["central"] - prior_tasks.get(
                task_id, task)["estimate_minutes"]["central"] if task_id in prior_tasks else task["estimate_minutes"]["central"]
        baseline[task_id] += delta


def _day(value, label):
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"{label}: требуется YYYY-MM-DD") from exc


def _clock(value, label):
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        raise ValidationError(f"{label}: требуется HH:MM")
    try:
        h, m = int(value[:2]), int(value[3:])
    except ValueError as exc:
        raise ValidationError(f"{label}: требуется HH:MM") from exc
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValidationError(f"{label}: неверное время")
    return h * 60 + m


def _hhmm(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _int(value, label, low=0, high=10**8):
    if type(value) is not int or not low <= value <= high:
        raise ValidationError(f"{label}: целое от {low} до {high}")
    return value


def _dates(settings):
    first, last = _day(settings.get("start"), "settings.start"), _day(settings.get("end"), "settings.end")
    if not 0 <= (last - first).days < MAX_DAYS:
        raise ValidationError("planning horizon: от 1 до 366 дней")
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def _union(intervals):
    result = []
    for left, right in sorted(intervals):
        if right <= left:
            continue
        if result and left <= result[-1][1]:
            result[-1] = (result[-1][0], max(right, result[-1][1]))
        else:
            result.append((left, right))
    return result


def _subtract(windows, block):
    left, right = block
    out = []
    for start, end in windows:
        if end <= left or right <= start:
            out.append((start, end))
        else:
            if start < left:
                out.append((start, left))
            if right < end:
                out.append((right, end))
    return out


def _duration(intervals):
    return sum(end - start for start, end in _union(intervals))


def _has_free_cube_slot(day, cubes, break_minutes):
    free = [(_clock(a, "free window start"), _clock(b, "free window end"))
            for a, b in day.get("free_windows", [])]
    for cube in cubes:
        if cube["date"] == day["date"] and cube.get("start") is not None and cube.get("end") is not None:
            a, b = _clock(cube["start"], "cube.start"), _clock(cube["end"], "cube.end")
            free = _subtract(free, (a-break_minutes, b+break_minutes))
    review = day.get("weekly_review")
    if review and review.get("start") and review.get("end"):
        a, b = _clock(review["start"], "weekly_review.start"), _clock(review["end"], "weekly_review.end")
        free = _subtract(free, (a-break_minutes, b+break_minutes))
    return any(b-a >= 60 for a, b in free)


def _valid_event(event):
    if not isinstance(event, dict):
        raise ValidationError("calendar.events: ожидаются объекты")
    identifier(event.get("id"), "calendar event id")
    for field in ("start", "end"):
        if not isinstance(event.get(field), str):
            raise ValidationError(f"calendar event {field}: требуется ISO дата/время")
        try:
            datetime.fromisoformat(event[field].replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValidationError(f"calendar event {field}: неверная дата") from exc
    if not event.get("all_day") and ("T" in event["start"] or "T" in event["end"]):
        if any(datetime.fromisoformat(event[field].replace("Z", "+00:00")).tzinfo is None
               for field in ("start", "end")):
            raise ValidationError("Календарное время требует UTC offset")
    if event.get("category", "unknown") not in {"lesson", "work", "busy", "homework", "ignore", "unknown"}:
        raise ValidationError("calendar event category: неизвестная категория")


def _validate_cube_detail(detail):
    if not isinstance(detail, dict) or set(detail) != {"title", "output", "steps"}:
        raise ValidationError("cube_details: нужны title/output/steps")
    if not isinstance(detail["title"], str) or not detail["title"].strip() or len(detail["title"]) > 300:
        raise ValidationError("cube_details.title: текст до 300")
    if not isinstance(detail["output"], str) or not detail["output"].strip() or len(detail["output"]) > 1000:
        raise ValidationError("cube_details.output: текст до 1000")
    if (not isinstance(detail["steps"], list) or not 1 <= len(detail["steps"]) <= 20
            or any(not isinstance(step, str) or not step.strip() or len(step) > 500
                   for step in detail["steps"])):
        raise ValidationError("cube_details.steps: 1..20 коротких шагов")


def validate_planning(planning, state=None):
    """Validate optional planning namespace before any canonical Work commit."""
    if not isinstance(planning, dict) or planning.get("version") != 1:
        raise ValidationError("planning.version: требуется 1")
    settings = planning.get("settings")
    if not isinstance(settings, dict):
        raise ValidationError("planning.settings: требуется объект")
    days = _dates(settings)
    _int(settings.get("day_limit_minutes"), "day_limit_minutes", 1, 300)
    _int(settings.get("prep_minutes"), "prep_minutes", 0, 180)
    _int(settings.get("break_minutes"), "break_minutes", 0, 120)
    if settings.get("workdays") != 6:
        raise ValidationError("workdays: требуется 6")
    if _clock(settings.get("work_end"), "work_end") <= _clock(settings.get("work_start"), "work_start"):
        raise ValidationError("Рабочее окно пустое")
    _clock(settings.get("webinar_start_weekday", "19:00"), "webinar_start_weekday")
    _clock(settings.get("webinar_start_weekend", "15:00"), "webinar_start_weekend")
    try:
        ZoneInfo(settings.get("timezone"))
    except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
        raise ValidationError("settings.timezone: нужна IANA-зона") from exc
    if not isinstance(settings.get("course_roots", {}), dict):
        raise ValidationError("settings.course_roots: требуется объект")
    if settings.get("priority_course") is not None:
        identifier(settings["priority_course"], "priority_course")
    if settings.get("priority_until") is not None:
        _day(settings["priority_until"], "priority_until")
    _int(settings.get("secondary_early_prep_minutes_per_week", 90), "secondary_early_prep_minutes_per_week", 0, 90)
    overrides = settings.get("day_capacity_overrides", {})
    if not isinstance(overrides, dict):
        raise ValidationError("day_capacity_overrides: требуется объект")
    for day, override in overrides.items():
        if _day(day, "capacity override date") not in days:
            raise ValidationError("capacity override вне горизонта")
        if not isinstance(override, dict) or override.get("approved") is not True:
            raise ValidationError("capacity override: требуется явное approved=true")
        _int(override.get("minutes"), "capacity override minutes", settings["day_limit_minutes"], 480)
        if not isinstance(override.get("reason"), str) or not override["reason"].strip():
            raise ValidationError("capacity override: нужна причина")
    manual_days = planning.get("manual_days", {})
    if not isinstance(manual_days, dict):
        raise ValidationError("manual_days: требуется объект")
    for day, manual in manual_days.items():
        if _day(day, "manual day") not in days or not isinstance(manual, dict):
            raise ValidationError("manual_days: день вне горизонта или неверная форма")
        minutes = _int(manual.get("budget_minutes"), "manual day budget", 0, 480)
        if minutes % 15:
            raise ValidationError("manual day budget: шаг 15 минут")
    prep_overrides = planning.get("prep_overrides", {})
    if not isinstance(prep_overrides, dict):
        raise ValidationError("prep_overrides: требуется объект")
    for prep_id, minutes in prep_overrides.items():
        identifier(prep_id, "prep_id")
        if _int(minutes, "prep minutes", 0, 120) % 5:
            raise ValidationError("prep minutes: шаг 5 минут")
    prep_progress = planning.get("prep_progress", {})
    if not isinstance(prep_progress, dict):
        raise ValidationError("prep_progress: требуется объект")
    for prep_id, progress in prep_progress.items():
        identifier(prep_id, "prep progress id")
        if not isinstance(progress, dict):
            raise ValidationError("prep_progress: требуется объект результата")
        _int(progress.get("percent"), "prep progress percent", 0, 100)
        _int(progress.get("actual_minutes", 0), "prep actual_minutes")
    weekly = settings.get("weekly_review", {"enabled": False})
    if not isinstance(weekly, dict) or type(weekly.get("enabled")) is not bool:
        raise ValidationError("settings.weekly_review: требуется enabled bool")
    if weekly["enabled"]:
        if weekly.get("minutes") != 60:
            raise ValidationError("weekly_review.minutes: требуется 60")
        _int(weekly.get("weekday"), "weekly_review.weekday", 0, 6)
    tasks = planning.get("tasks")
    if not isinstance(tasks, list) or len(tasks) > 3000:
        raise ValidationError("planning.tasks: требуется список до 3000")
    projects = {x["id"] for x in (state or {}).get("projects", [])}
    items = {x["id"]: x for x in (state or {}).get("items", [])}
    seen = set()
    linked_items = set()
    for task in tasks:
        if not isinstance(task, dict):
            raise ValidationError("planning task: требуется объект")
        tid = identifier(task.get("id"), "task.id")
        if tid in seen:
            raise ValidationError("planning task.id: повтор")
        seen.add(tid)
        course = identifier(task.get("course"), "task.course")
        project = identifier(task.get("project_id"), "task.project_id")
        if state is not None and project not in projects:
            raise ValidationError(f"task.project_id: неизвестный проект {project}")
        if task.get("work_item_id") is not None:
            item_id = identifier(task["work_item_id"], "task.work_item_id")
            if item_id in linked_items:
                raise ValidationError("task.work_item_id: повтор")
            linked_items.add(item_id)
            if state is not None and item_id not in items:
                raise ValidationError("task.work_item_id: неизвестный Work item")
            if state is not None and items[item_id].get("project") not in (None, project):
                raise ValidationError("task.work_item_id: Work item другого проекта")
        if not isinstance(task.get("title"), str) or not task["title"].strip():
            raise ValidationError("task.title: требуется текст")
        if "importance_score" in task:
            _int(task["importance_score"], "task.importance_score", 0, 5)
        if task.get("kind", "other") not in STEPS:
            raise ValidationError("task.kind: неизвестный тип")
        if type(task.get("early_prep", False)) is not bool:
            raise ValidationError("task.early_prep: требуется bool")
        if task.get("due") is not None:
            _day(task["due"], "task.due")
        if task.get("methodist_start") is not None:
            _day(task["methodist_start"], "task.methodist_start")
        estimate = task.get("estimate_minutes")
        if not isinstance(estimate, dict):
            raise ValidationError("task.estimate_minutes: требуется объект")
        lower = _int(estimate.get("lower"), "estimate.lower")
        central = _int(estimate.get("central"), "estimate.central")
        upper = _int(estimate.get("upper"), "estimate.upper")
        if not lower <= central <= upper:
            raise ValidationError("estimate: требуется lower <= central <= upper")
        _int(task.get("review_minutes", 0), "review_minutes", 0, central)
        if not isinstance(task.get("resources", []), list):
            raise ValidationError("task.resources: требуется список")
        for resource in task.get("resources", []):
            _validate_resource(resource, course, settings)
    if "monthly_plan" in planning:
        _validate_monthly_plan(planning["monthly_plan"], seen)
    segment_by_id = {}
    segments = planning.get("task_segments")
    if segments is not None:
        if not isinstance(segments, list) or len(segments) > 10000:
            raise ValidationError("task_segments: требуется список до 10000")
        totals = defaultdict(int)
        for segment in segments:
            if not isinstance(segment, dict):
                raise ValidationError("task_segment: требуется объект")
            segment_id = identifier(segment.get("id"), "segment.id")
            task_id = segment.get("task_id")
            if segment_id in segment_by_id or task_id not in seen:
                raise ValidationError("task_segment: повтор ID или неизвестная задача")
            if segment.get("parent_id") is not None:
                raise ValidationError("task_segment.parent_id: первая версия поддерживает только null")
            _int(segment.get("minutes"), "segment.minutes", 1, 300)
            if segment.get("origin") not in {"auto", "manual"}:
                raise ValidationError("segment.origin: auto или manual")
            if not isinstance(segment.get("title"), str) or not segment["title"].strip() or len(segment["title"]) > 300:
                raise ValidationError("segment.title: текст до 300")
            if not isinstance(segment.get("output", ""), str) or len(segment.get("output", "")) > 1000:
                raise ValidationError("segment.output: текст до 1000")
            if (not isinstance(segment.get("steps", []), list) or len(segment.get("steps", [])) > 20 or
                    any(not isinstance(step, str) or not step.strip() or len(step) > 500
                        for step in segment.get("steps", []))):
                raise ValidationError("segment.steps: до 20 коротких шагов")
            segment_by_id[segment_id] = segment
            totals[task_id] += segment["minutes"]
        baseline = planning.get("segment_baseline_remaining")
        if not isinstance(baseline, dict) or set(baseline) != seen:
            raise ValidationError("segment_baseline_remaining: требуется остаток каждой задачи")
        for task in tasks:
            value = _int(baseline[task["id"]], "segment baseline", 0, task["estimate_minutes"]["central"])
            if totals[task["id"]] != value:
                raise ValidationError("Сумма этапов должна равняться базовому остатку задачи")
    segment_progress = planning.get("segment_progress", {})
    if not isinstance(segment_progress, dict):
        raise ValidationError("segment_progress: требуется объект")
    for segment_id, report in segment_progress.items():
        if segment_id not in segment_by_id or not isinstance(report, dict):
            raise ValidationError("segment_progress: неизвестный этап")
        _int(report.get("percent"), "segment.percent", 0, 100)
        _int(report.get("cube_credit_at_report"), "segment.cube_credit_at_report",
             0, segment_by_id[segment_id]["minutes"])
        if _day(report.get("date"), "segment.date") not in days:
            raise ValidationError("segment_progress.date вне горизонта")
    calendar = planning.get("calendar")
    if not isinstance(calendar, dict) or not isinstance(calendar.get("events"), list):
        raise ValidationError("planning.calendar.events: требуется список")
    if calendar.get("stale") is not None and type(calendar["stale"]) is not bool:
        raise ValidationError("calendar.stale: требуется bool")
    for event in calendar["events"]:
        _valid_event(event)
    live_events = planning.get("live_events", [])
    if not isinstance(live_events, list) or len(live_events) > 1000:
        raise ValidationError("planning.live_events: требуется список до 1000")
    live_ids = set()
    for live in live_events:
        if not isinstance(live, dict):
            raise ValidationError("live_event: требуется объект")
        live_id = identifier(live.get("id"), "live_event.id")
        if live_id in live_ids:
            raise ValidationError("live_event.id: повтор")
        live_ids.add(live_id)
        if live.get("task_id") is not None and live["task_id"] not in seen:
            raise ValidationError("live_event.task_id: неизвестная задача")
        if not isinstance(live.get("title"), str) or not live["title"].strip():
            raise ValidationError("live_event.title: требуется текст")
        if live.get("duration_minutes") != 180:
            raise ValidationError("live_event.duration_minutes: эфир 180 минут")
        if live.get("date") is not None:
            _day(live["date"], "live_event.date")
        if live.get("start") is not None:
            _clock(live["start"], "live_event.start")
            if live.get("date") is None:
                raise ValidationError("live_event.start: без даты время неизвестно")
        if live.get("calendar_event_id") is not None:
            identifier(live["calendar_event_id"], "live_event.calendar_event_id")
    for field in ("completed_cubes", "day_outcomes", "locks", "history"):
        expected = dict if field == "completed_cubes" else list
        if not isinstance(planning.get(field, expected()), expected):
            raise ValidationError(f"planning.{field}: неверная форма")
    review_done = planning.get("weekly_review_done", {})
    if not isinstance(review_done, dict) or any(type(value) is not bool for value in review_done.values()):
        raise ValidationError("planning.weekly_review_done: требуется map id -> bool")
    valid_reviews = {f"weekly-review:{day.isoformat()}" for day in days
                     if weekly["enabled"] and day.weekday() == weekly["weekday"]}
    if set(review_done) - valid_reviews:
        raise ValidationError("weekly_review_done: неизвестный ID недели")
    details = planning.get("cube_details", {})
    if not isinstance(details, dict) or len(details) > 5000:
        raise ValidationError("planning.cube_details: требуется map до 5000")
    for cube_id, detail in details.items():
        identifier(cube_id, "cube_details id")
        _validate_cube_detail(detail)
    for cube_id, entry in planning.get("completed_cubes", {}).items():
        identifier(cube_id, "completed cube id")
        if not isinstance(entry, dict) or entry.get("task_id") not in seen:
            raise ValidationError("completed cube: неизвестная задача")
        if _day(entry.get("date"), "completed cube date") > days[-1]:
            raise ValidationError("completed cube date вне горизонта")
        _int(entry.get("planned_minutes"), "completed cube planned_minutes")
        _int(entry.get("percent"), "completed cube percent", 0, 100)
        _int(entry.get("actual_minutes", 0), "completed cube actual_minutes")
        if entry.get("segment_id") is not None:
            segment = segment_by_id.get(entry["segment_id"])
            if segment is None or segment["task_id"] != entry["task_id"]:
                raise ValidationError("completed cube.segment_id: чужой этап")
    pairs = set()
    for entry in planning.get("day_outcomes", []):
        if not isinstance(entry, dict) or entry.get("task_id") not in seen:
            raise ValidationError("day_outcome: неизвестная задача")
        reported_day = _day(entry.get("date"), "day_outcome.date")
        if reported_day not in days:
            raise ValidationError("day_outcome.date вне горизонта")
        key = (entry["task_id"], reported_day)
        if key in pairs:
            raise ValidationError("day_outcome: повтор задачи/дня")
        pairs.add(key)
        if entry.get("remaining_minutes") is not None:
            _int(entry["remaining_minutes"], "remaining_minutes")
        if type(entry.get("done", False)) is not bool:
            raise ValidationError("day_outcome.done: требуется bool")
    for lock in planning.get("locks", []):
        if not isinstance(lock, dict) or lock.get("task_id") not in seen or lock.get("locked") is not True:
            raise ValidationError("lock: неизвестная задача или locked != true")
        identifier(lock.get("cube_id"), "lock.cube_id")
        _day(lock.get("date"), "lock.date")
        minutes = _int(lock.get("minutes"), "lock.minutes", 1, 240 if lock.get("constructor") else 90)
        if lock.get("segment_id") is not None:
            segment = segment_by_id.get(lock["segment_id"])
            if segment is None or segment["task_id"] != lock["task_id"]:
                raise ValidationError("lock.segment_id: чужой этап")
        if lock.get("manual"):
            if lock.get("constructor"):
                if minutes < 5 or minutes % 5:
                    raise ValidationError("constructor lock: 5..240 с шагом 5")
            elif minutes not in (15, 30, 45, 60, 90) and not (lock.get("is_remainder") and 1 <= minutes < 60):
                raise ValidationError("manual lock: размер 15/30/45/60/90")
            if lock.get("floating"):
                if lock.get("start") is not None or lock.get("end") is not None:
                    raise ValidationError("floating lock: время должно быть null")
                _int(lock.get("order"), "floating lock order", 0, 10000)
            else:
                _clock(lock.get("start"), "lock.start")
                _clock(lock.get("end"), "lock.end")
        elif minutes < 60 and lock.get("is_remainder") is not True:
            raise ValidationError("lock: короткий кубик требует is_remainder=true")
    if len(planning.get("history", [])) > 40:
        raise ValidationError("planning.history: максимум 40")
    if not isinstance(planning.get("result", {}), dict):
        raise ValidationError("planning.result: требуется объект")
    _validate_cube_deletions(planning)
    _validate_constructor(planning)
    exemptions = planning.get("scope_exemptions", {})
    if not isinstance(exemptions, dict):
        raise ValidationError("scope_exemptions: требуется map")
    for task_id, entry in exemptions.items():
        if task_id not in seen or not isinstance(entry, dict) or type(entry.get("exempt")) is not bool:
            raise ValidationError("scope_exemptions: неизвестная задача или неверная запись")
        _day(entry.get("from_date"), "scope from_date")
        if not isinstance(entry.get("reason"), str) or not entry["reason"].strip() or len(entry["reason"]) > 1000:
            raise ValidationError("scope reason: требуется текст до1000")
    if "constructor" in planning:
        _constructor_validate_slots(planning)
    return days


def _validate_resource(resource, course, settings):
    if not isinstance(resource, dict) or resource.get("type") not in {"url", "local_file"}:
        raise ValidationError("resource.type: url или local_file")
    if not isinstance(resource.get("label"), str) or not resource["label"].strip():
        raise ValidationError("resource.label: требуется текст")
    if resource["type"] == "url":
        from urllib.parse import urlsplit
        url = resource.get("url")
        if not isinstance(url, str) or urlsplit(url).scheme not in {"http", "https"} or not urlsplit(url).netloc:
            raise ValidationError("resource.url: требуется http(s) URL")
    else:
        root = settings.get("course_roots", {}).get(course)
        path = resource.get("path")
        if not isinstance(root, str) or not isinstance(path, str):
            raise ValidationError("local_file: требуются course_root и path")
        base = Path(root).resolve()
        target = Path(path).resolve() if Path(path).is_absolute() else (base / path).resolve()
        if not target.is_relative_to(base):
            raise ValidationError("local_file: путь вне course_root")


def _event_category(event, rules):
    category = event.get("category")
    if category in {"lesson", "work", "busy", "homework", "ignore"}:
        return category
    title = str(event.get("title") or event.get("summary") or "").strip().casefold()
    for key, result in (("ignore_titles", "ignore"), ("homework_titles", "homework"),
                        ("lesson_titles", "lesson"), ("work_titles", "work")):
        if title and title in {str(x).strip().casefold() for x in rules.get(key, [])}:
            return result
    return "unknown"


def _live_details(planning):
    settings = planning["settings"]
    zone = ZoneInfo(settings["timezone"])
    calendar_by_id = {str(event.get(key)): event for event in planning["calendar"]["events"]
                      for key in ("id", "occurrence_id") if event.get(key)}
    details, synthetic, matched_ids = [], [], set()
    for live in planning.get("live_events", []):
        record = deepcopy(live)
        if _scope_exempt(planning, live.get("task_id"), live.get("date")):
            matched = calendar_by_id.get(live.get("calendar_event_id"))
            if matched:
                matched_ids.add(str(matched["id"]))
            record.update(status="scope_exempt", scope_exempt=True, effective_start=None, covered_by_calendar=bool(matched))
            details.append(record)
            continue
        if live.get("date") is None:
            record.update(status="unscheduled", effective_start=None, covered_by_calendar=False)
        else:
            selected = _day(live["date"], "live_event.date")
            matched = calendar_by_id.get(live.get("calendar_event_id"))
            if matched is None and live.get("calendar_event_id"):
                candidates = []
                for event in planning["calendar"]["events"]:
                    try:
                        begins = datetime.fromisoformat(event["start"].replace("Z", "+00:00"))
                        if begins.tzinfo is not None:
                            begins = begins.astimezone(zone)
                        title = str(event.get("title") or event.get("summary") or "").strip().casefold()
                        if begins.date() == selected and title == live["title"].strip().casefold():
                            candidates.append(event)
                    except (KeyError, ValueError):
                        continue
                if len(candidates) == 1:
                    matched = candidates[0]
                    record["calendar_match"] = "title_date_fallback"
            calendar_start = None
            if matched:
                calendar_start = datetime.fromisoformat(matched["start"].replace("Z", "+00:00"))
                if calendar_start.tzinfo is not None:
                    calendar_start = calendar_start.astimezone(zone)
                if calendar_start.date() != selected:
                    raise ValidationError("live_event.calendar_event_id: дата не совпадает")
                matched_ids.add(str(matched["id"]))
                calendar_end = datetime.fromisoformat(matched["end"].replace("Z", "+00:00"))
                record["calendar_duration_minutes"] = round((calendar_end-calendar_start).total_seconds()/60)
            proposed = live.get("start") is None and calendar_start is None
            start = (live.get("start") or
                     (calendar_start.strftime("%H:%M") if calendar_start is not None else None) or
                     settings.get("webinar_start_weekend" if selected.weekday() >= 5 else "webinar_start_weekday",
                                  "15:00" if selected.weekday() >= 5 else "19:00"))
            minute = _clock(start, "live_event.start")
            begin = datetime.combine(selected, time(minute//60, minute%60), zone)
            finish = begin + timedelta(minutes=180)
            synthetic.append({"id": "live:" + live["id"], "title": live["title"],
                              "start": begin.isoformat(), "end": finish.isoformat(),
                              "category": "lesson", "live_event_id": live["id"]})
            record.update(status=("calendar_covered" if matched else
                                  "scheduled_provisional_start" if proposed else "scheduled_explicit"),
                          effective_start=start, covered_by_calendar=bool(matched))
        details.append(record)
    return details, synthetic, matched_ids


def _ledger(planning, days):
    settings = planning["settings"]
    zone = ZoneInfo(settings["timezone"])
    start, end = days[0], days[-1]
    window = (_clock(settings["work_start"], "work_start"), _clock(settings["work_end"], "work_end"))
    entries = {day.isoformat(): {"timed": [], "work": [], "prep": [], "homework": 0,
                                "events": [], "lessons": [], "prep_cubes": [], "unknown": []} for day in days}
    rules = planning["calendar"].get("rules", {})
    _, synthetic, matched_ids = _live_details(planning)
    for event in [*(e for e in planning["calendar"]["events"] if str(e["id"]) not in matched_ids), *synthetic]:
        if event.get("status") == "cancelled" or event.get("transparency") == "transparent" or event.get("my_response_status") == "declined":
            continue
        first = datetime.fromisoformat(event["start"].replace("Z", "+00:00"))
        last = datetime.fromisoformat(event["end"].replace("Z", "+00:00"))
        if first.tzinfo is None or event.get("all_day"):
            day = first.date()
            if start <= day <= end:
                category = _event_category(event, rules)
                if category == "homework":
                    entries[day.isoformat()]["homework"] += 1
                elif category != "ignore":
                    entries[day.isoformat()]["unknown"].append(event["id"])
            continue
        first, last = first.astimezone(zone), last.astimezone(zone)
        if last <= first:
            continue
        category = _event_category(event, rules)
        cursor = max(start, first.date())
        while cursor <= min(end, last.date()):
            midnight = datetime.combine(cursor, time.min, zone)
            a = max(first, midnight)
            b = min(last, midnight + timedelta(days=1))
            if b > a:
                left = int((a-midnight).total_seconds() // 60)
                right = int((b-midnight).total_seconds() // 60)
                entry = entries[cursor.isoformat()]
                constructor_day = planning.get("constructor", {}).get("days", {}).get(cursor.isoformat(), {})
                required = _constructor_event_required(planning, event)
                selected = False  # Optional Calendar selections use their own movable Work slot.
                if constructor_day.get("initialized") or _event_identity(event) in planning.get("constructor", {}).get("event_required", {}):
                    if not required and not selected:
                        cursor += timedelta(days=1)
                        continue
                entry["timed"].append((left, right))
                entry["events"].append(event["id"])
                if category in {"work", "lesson", "unknown"} or selected or required and constructor_day.get("initialized"):
                    entry["work"].append((left, right))
                if category == "unknown":
                    entry["unknown"].append(event["id"])
            cursor += timedelta(days=1)
        if category == "lesson" and start <= first.date() <= end:
            entries[first.date().isoformat()]["lessons"].append(
                (int(first.hour*60+first.minute), event["id"],
                 event.get("prep_key") or event.get("occurrence_id") or event["id"]))
    for entry in entries.values():
        entry["timed"] = _union(entry["timed"])
        entry["work"] = _union(entry["work"])
    # Prepare before each lesson, moving earlier around existing events/preparation.
    for key, entry in entries.items():
        for lesson_start, event_id, stable_key in sorted(entry["lessons"]):
            candidate = lesson_start
            prep_id = _prep_id(stable_key, key)
            duration = planning.get("prep_overrides", {}).get(prep_id, settings["prep_minutes"])
            entry["prep_cubes"].append({
                "id": prep_id, "calendar_event_id": event_id, "date": key,
                "lesson_start": _hhmm(lesson_start), "minutes": duration,
                "default_minutes": settings["prep_minutes"], "floating": True,
                "start": None, "end": None,
            })
            ctor_day = planning.get("constructor", {}).get("days", {}).get(key, {})
            prep_selected = _constructor_source_selected(planning, "prep", prep_id, key)
            if duration == 0 or ctor_day.get("initialized") and not prep_selected:
                continue
            if ctor_day.get("initialized"):
                entry["constructor_prep_minutes"] = entry.get("constructor_prep_minutes", 0)+duration
                continue
            while candidate - duration >= 0:
                clashes = [(a, b) for a, b in _union(entry["timed"] + entry["prep"])
                           if a < candidate and candidate-duration < b]
                if not clashes:
                    entry["prep"].append((candidate-duration, candidate))
                    break
                candidate = min(a for a, _ in clashes)
            else:
                entry["prep"].append((-duration, 0))
                entry["unknown"].append(f"prep_outside_day:{event_id}")
    for key, entry in entries.items():
        entry["prep"] = _union(entry["prep"])
        entry["timed_minutes"] = _duration(entry["work"])
        entry["busy_minutes"] = _duration(entry["timed"])
        entry["prep_minutes"] = _duration(entry["prep"]) + entry.get("constructor_prep_minutes", 0)
        entry["homework_minutes"] = (min(30, _int(settings.get("homework_minutes", 30),
                                                   "homework_minutes", 0, 30)) if entry["homework"] else 0)
        entry["fixed_minutes"] = entry["timed_minutes"] + entry["prep_minutes"] + entry["homework_minutes"]
        manual = planning.get("manual_days", {}).get(key)
        override = settings.get("day_capacity_overrides", {}).get(key)
        entry["day_limit_minutes"] = (manual["budget_minutes"] if manual is not None else
                                      override["minutes"] if override else settings["day_limit_minutes"])
        entry["raised_load"] = entry["day_limit_minutes"] > settings["day_limit_minutes"]
        entry["emergency_approved"] = entry["raised_load"]
        entry["available_minutes"] = max(0, entry["day_limit_minutes"] - entry["fixed_minutes"])
        entry["over_cap_minutes"] = max(0, entry["fixed_minutes"] - entry["day_limit_minutes"])
        free = [window]
        for block in entry["timed"] + entry["prep"]:
            free = _subtract(free, block)
        entry["free_windows"] = free
    return entries


def _rest_days(days, ledger, tasks, locked_dates):
    """One floating rest day per complete week; avoid locked days."""
    due = [date.fromisoformat(t["due"]) for t in tasks if t.get("due")]
    weeks = []
    monday = days[0] - timedelta(days=days[0].weekday())
    while monday <= days[-1]:
        week = [monday + timedelta(days=i) for i in range(7)]
        if week[0] >= days[0] and week[-1] <= days[-1]:
            weeks.append(week)
        monday += timedelta(days=7)
    if not weeks:
        return set()
    states = {None: (0, [])}
    for index, week in enumerate(weeks):
        nxt = {}
        for candidate in week:
            key = candidate.isoformat()
            if key in locked_dates:
                continue
            if index == 0 and (candidate-days[0]).days > 6:
                continue
            if index == len(weeks)-1 and (days[-1]-candidate).days > 6:
                continue
            led = ledger[key]
            pressure = sum(1 for d in due if 0 <= (d-candidate).days <= 1)
            penalty = led["fixed_minutes"]*100 + pressure*3 + abs(candidate.weekday()-1)
            for previous, (cost, path) in states.items():
                if previous is not None and (candidate-previous).days > 7:
                    continue
                value = (cost+penalty, path+[candidate])
                if candidate not in nxt or value[0] < nxt[candidate][0]:
                    nxt[candidate] = value
        if not nxt:
            raise ValidationError("Невозможно выбрать плавающий выходной с текущими locks")
        states = nxt
    return {d.isoformat() for d in min(states.values(), key=lambda value: value[0])[1]}


def _weekly_reviews(planning, days, ledger, rest, locks):
    settings = planning["settings"]
    weekly = settings.get("weekly_review", {"enabled": False})
    if not weekly["enabled"]:
        return [], {}
    by_date = {}
    reviews = []
    done_map = planning.get("weekly_review_done", {})
    break_minutes = settings["break_minutes"]
    for week_end in (d for d in days if d.weekday() == weekly["weekday"]):
        identifier_value = f"weekly-review:{week_end.isoformat()}"
        week_start = max(days[0], week_end-timedelta(days=6))
        review = {"id": identifier_value, "week_start": week_start.isoformat(),
                  "week_end": week_end.isoformat(), "date": None, "start": None, "end": None,
                  "minutes": weekly["minutes"], "status": "unplaced",
                  "title": WEEKLY_REVIEW_TITLE,
                  "checklist": [{"id": key, "label": label} for key, label in WEEKLY_REVIEW_CHECKLIST]}
        if any(cube.get("kind") == "weekly_review" and cube.get("source_ref") == identifier_value
               for cube in planning.get("constructor", {}).get("cubes", {}).values()):
            review["status"] = "done" if done_map.get(identifier_value) else "constructor_proposal"
            reviews.append(review)
            continue
        for candidate in reversed([d for d in days if week_start <= d <= week_end]):
            key = candidate.isoformat()
            if key in rest or key in by_date:
                continue
            led = ledger[key]
            day_locks = [x for x in locks if x["date"] == key]
            if led["available_minutes"]-sum(_lock_reservation(planning, x) for x in day_locks) < weekly["minutes"]:
                continue
            free = list(led["free_windows"])
            for lock in day_locks:
                if lock.get("floating"):
                    continue
                left, right = _clock(lock["start"], "lock.start"), _clock(lock["end"], "lock.end")
                free = _subtract(free, (left-break_minutes, right+break_minutes))
            slot = next(((right-60, right) for left, right in reversed(free) if right-left >= 60), None)
            if slot:
                left, right = slot
                review.update(date=key, start=_hhmm(left), end=_hhmm(right),
                              status="done" if done_map.get(identifier_value) else "planned")
                by_date[key] = review
                break
        reviews.append(review)
    return reviews, by_date


def _remaining_unsegmented(planning):
    remaining = {task["id"]: task["estimate_minutes"]["central"] for task in planning["tasks"]}
    timeline = [(entry["date"], 0, entry["task_id"], entry)
                for entry in planning.get("completed_cubes", {}).values()]
    timeline += [(entry["date"], 1, entry["task_id"], entry)
                 for entry in planning.get("day_outcomes", [])]
    for _, kind, tid, entry in sorted(timeline, key=lambda x: (x[0], x[1], x[2])):
        if kind == 0:
            remaining[tid] = max(0, remaining[tid]-round(entry["planned_minutes"]*entry["percent"]/100))
        elif entry.get("done"):
            remaining[tid] = 0
        elif entry.get("remaining_minutes") is not None:
            remaining[tid] = entry["remaining_minutes"]
    return remaining


def _segment_effective_credits(planning):
    by_id = {segment["id"]: segment for segment in planning.get("task_segments", [])}
    cube_credit = defaultdict(int)
    for entry in planning.get("completed_cubes", {}).values():
        segment_id = entry.get("segment_id")
        if segment_id in by_id:
            cube_credit[segment_id] += round(entry["planned_minutes"] * entry["percent"] / 100)
    credits = {}
    for segment_id, segment in by_id.items():
        report = planning.get("segment_progress", {}).get(segment_id)
        if report:
            explicit = round(segment["minutes"] * report["percent"] / 100)
            effective = explicit + max(0, cube_credit[segment_id] - report["cube_credit_at_report"])
        else:
            effective = cube_credit[segment_id]
        credits[segment_id] = min(segment["minutes"], max(0, effective))
    return credits


def _remaining_before_deletions(planning):
    segments = {segment["id"]: segment for segment in planning.get("task_segments", [])}
    if not segments:
        return _remaining_unsegmented(planning)
    remaining = {task["id"]: task["estimate_minutes"]["central"] for task in planning["tasks"]}
    state = {segment_id: {"cube": 0, "explicit": None, "anchor": 0, "effective": 0}
             for segment_id in segments}
    timeline = [(entry["date"], 0, entry["task_id"], entry)
                for entry in planning.get("completed_cubes", {}).values()]
    timeline += [(entry["date"], 1, segments[segment_id]["task_id"], (segment_id, entry))
                 for segment_id, entry in planning.get("segment_progress", {}).items()
                 if segment_id in segments]
    timeline += [(entry["date"], 2, entry["task_id"], entry)
                 for entry in planning.get("day_outcomes", [])]
    for _, kind, task_id, entry in sorted(timeline, key=lambda x: (x[0], x[1], x[2])):
        if kind == 2:
            if entry.get("done"):
                remaining[task_id] = 0
            elif entry.get("remaining_minutes") is not None:
                remaining[task_id] = entry["remaining_minutes"]
            continue
        if kind == 0:
            credit = round(entry["planned_minutes"] * entry["percent"] / 100)
            segment_id = entry.get("segment_id")
            if segment_id not in segments:
                remaining[task_id] = max(0, remaining[task_id]-credit)
                continue
            state[segment_id]["cube"] += credit
        else:
            segment_id, report = entry
            state[segment_id]["explicit"] = round(segments[segment_id]["minutes"] * report["percent"] / 100)
            state[segment_id]["anchor"] = report["cube_credit_at_report"]
        part = state[segment_id]
        effective = (part["cube"] if part["explicit"] is None else
                     part["explicit"] + max(0, part["cube"]-part["anchor"]))
        effective = min(segments[segment_id]["minutes"], max(0, effective))
        remaining[task_id] = max(0, remaining[task_id] - (effective-part["effective"]))
        part["effective"] = effective
    return remaining


def latest_remaining_baseline(planning, task_id):
    entries = [entry for entry in planning.get("day_outcomes", []) if entry["task_id"] == task_id
               and (entry.get("remaining_minutes") is not None or entry.get("done"))]
    if not entries:
        return None
    latest = max(entries, key=lambda entry: entry["date"])
    return latest["date"], latest.get("remaining_minutes"), latest.get("done", False)


def _retired_minutes(planning, *, by_segment=False, active_only=True):
    retired = defaultdict(int)
    for record in planning.get("cube_deletions", {}).values():
        cube = planning.get("constructor", {}).get("cubes", {}).get(record["cube_id"], {})
        key = cube.get("segment_id") if by_segment else cube.get("task_id")
        epoch = planning.get("remaining_epochs", {}).get(cube.get("task_id"), 0)
        if cube.get("kind") == "task" and key and (not active_only or record.get("remaining_epoch", 0) == epoch):
            retired[key] += record["retired_minutes"]
    return retired


def _remaining(planning):
    # Cancellation is not completion credit: reports and percentages stay untouched.
    retired = _retired_minutes(planning)
    return {tid: max(0, amount-retired[tid]) for tid, amount in _remaining_before_deletions(planning).items()}


def deleted_cube(planning, cube_id, blueprint_ref=None):
    records = planning.get("cube_deletions", {})
    aliases = {value for value in (cube_id, blueprint_ref) if isinstance(value, str)}
    if isinstance(cube_id, str) and cube_id.startswith("planned-"):
        aliases.add(cube_id[len("planned-"):])
    aliases.discard(None)
    return cube_id in records or any(record.get("blueprint_ref") in aliases for record in records.values())


def _validate_cube_deletions(planning):
    records = planning.get("cube_deletions", {})
    if not isinstance(records, dict) or len(records) > 10000:
        raise ValidationError("cube_deletions: требуется map до10000")
    cubes = planning.get("constructor", {}).get("cubes", {})
    epochs = planning.get("remaining_epochs", {})
    task_ids = {task["id"] for task in planning.get("tasks", [])}
    if not isinstance(epochs, dict) or any(tid not in task_ids for tid in epochs):
        raise ValidationError("remaining_epochs: требуется map известных задач")
    for epoch in epochs.values():
        _int(epoch, "remaining epoch", 0)
    fields = {"cube_id", "request_id", "deleted_at", "blueprint_ref", "retired_minutes", "selected_date", "remaining_epoch"}
    for cube_id, record in records.items():
        if not isinstance(record, dict) or set(record) != fields or record["cube_id"] != cube_id:
            raise ValidationError("cube_deletions: неверные поля")
        cube = cubes.get(cube_id)
        if not isinstance(cube, dict) or cube.get("kind") not in {"task", "brainalot"}:
            raise ValidationError("cube_deletions: отсутствует исторический кубик")
        _int(cube.get("minutes"), "deleted cube minutes", 1, 1440)
        identifier(record["request_id"], "delete request_id")
        _int(record["retired_minutes"], "retired_minutes", 0, cube["minutes"])
        _int(record["remaining_epoch"], "deleted remaining_epoch", 0, epochs.get(cube.get("task_id"), 0))
        if record["blueprint_ref"] is not None:
            identifier(record["blueprint_ref"], "deleted blueprint_ref")
        if record["selected_date"] is not None:
            _day(record["selected_date"], "deleted selected_date")
        try:
            stamp = datetime.fromisoformat(record["deleted_at"])
            if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
                raise ValueError()
        except (ValueError, TypeError) as exc:
            raise ValidationError("deleted_at: требуется время UTC") from exc


def _priority(task, remaining, day, settings):
    due = date.fromisoformat(task["due"]) if task.get("due") else date.max
    gap = (due-day).days
    confirmed = task.get("due_status") != "inferred_provisional"
    tier = (0 if confirmed else 1) if -3 <= gap <= 14 else ((2 if confirmed else 3) if gap < -3 else (4 if confirmed else 5))
    if settings.get("priority_course") and settings.get("priority_until") and day.isoformat() < settings["priority_until"]:
        if task["course"] == settings["priority_course"]:
            tier = -2
        elif task.get("early_prep"):
            tier = 3
        else:
            tier = 9
    return (tier, due, -remaining[task["id"]], task["id"])


def _stable_cube_id(task_id, day, start, phase):
    return sha256(f"{task_id}|{day}|{start}|{phase}".encode()).hexdigest()[:24]


def _prep_id(event_id, day):
    return "prep:" + sha256(f"{event_id}|{day}".encode()).hexdigest()[:24]


def _final_remainder(minutes):
    while minutes >= 60:
        minutes -= 90 if minutes >= 90 and minutes - 90 != 30 else 60
    return minutes


def _lock_reservation(planning, lock):
    if lock.get("manual"):
        progress = planning.get("completed_cubes", {}).get(lock["cube_id"])
        if progress and (progress.get("percent", 0) > 0 or progress.get("actual_minutes", 0) > 0):
            return 0
    return lock["minutes"]


def _validate_locks(planning, ledger, remaining, rest, from_date):
    tasks = {task["id"]: task for task in planning["tasks"]}
    occupied = defaultdict(list)
    by_task = defaultdict(int)
    by_phase = defaultdict(int)
    short_locks = defaultdict(list)
    early_by_week = defaultdict(int)
    for lock in planning.get("locks", []):
        if lock["date"] < from_date:
            continue
        day = lock["date"]
        if day not in ledger or day in rest:
            raise ValidationError(f"lock {day}: вне горизонта или выходной")
        task = tasks[lock["task_id"]]
        minutes = lock["minutes"]
        manual = bool(lock.get("manual"))
        if manual and lock.get("floating"):
            if (not lock.get("constructor") and minutes not in (15, 30, 45, 60, 90)
                    and not lock.get("is_remainder")):
                raise ValidationError("floating lock: размер 15/30/45/60/90")
            a = b = None
        else:
            a, b = _clock(lock["start"], "lock.start"), _clock(lock["end"], "lock.end")
            if b-a != minutes or not (manual or minutes in (60, 90) or
                                      1 <= minutes < 60 and lock.get("is_remainder") is True):
                raise ValidationError("lock: неверная длительность")
        if lock.get("phase", "preparation") == "review" and task.get("methodist_start") and day < task["methodist_start"]:
            raise ValidationError("lock: review раньше окна методиста")
        if a is not None and not any(left <= a and b <= right for left, right in ledger[day]["free_windows"]):
            raise ValidationError("lock: занятый слот или вне рабочего окна")
        if a is not None:
            occupied[day].append((a, b, lock["task_id"]))
        if not lock.get("constructor"):
            by_task[lock["task_id"]] += _lock_reservation(planning, lock)
        phase = lock.get("phase", "preparation")
        if not manual:
            by_phase[(lock["task_id"], phase)] += minutes
        if minutes < 60 and not manual:
            short_locks[(lock["task_id"], phase)].append(lock)
        if (not manual and planning["settings"].get("priority_course") and planning["settings"].get("priority_until")
                and day < planning["settings"]["priority_until"] and task.get("early_prep")
                and task["course"] != planning["settings"]["priority_course"]):
            d = date.fromisoformat(day)
            early_by_week[(d-timedelta(days=d.weekday())).isoformat()] += lock["minutes"]
    for day, intervals in occupied.items():
        ids = {item[2] for item in intervals}
        if len(ids) > 1 and day not in planning.get("manual_days", {}):
            primary = [tid for tid in ids if tasks[tid]["course"] == planning["settings"].get("priority_course")]
            secondary = [tid for tid in ids if tasks[tid].get("early_prep")
                         and tasks[tid]["course"] != planning["settings"].get("priority_course")]
            secondary_minutes = sum(b-a for a, b, tid in intervals if tid in secondary)
            if not (day < planning["settings"].get("priority_until", "") and len(ids) == 2
                    and len(primary) == len(secondary) == 1 and secondary_minutes == 60):
                raise ValidationError("lock: больше одной фокусной задачи в день")
        if (day not in planning.get("manual_days", {}) and
                ledger[day]["fixed_minutes"] + sum(b-a for a, b, _ in intervals) > ledger[day]["day_limit_minutes"]):
            raise ValidationError("lock: дневной лимит превышен")
        for first, second in zip(sorted(intervals), sorted(intervals)[1:]):
            minimum_break = (0 if planning.get("constructor", {}).get("days", {}).get(day, {}).get("initialized")
                             else planning["settings"]["break_minutes"])
            if second[0]-first[1] < minimum_break:
                raise ValidationError("lock: пересечение или недостаточный перерыв")
    for tid, minutes in by_task.items():
        if minutes > remaining[tid]:
            raise ValidationError("lock: резерв больше остатка задачи")
    for (tid, phase), entries in short_locks.items():
        task = tasks[tid]
        phase_total = (max(0, remaining[tid]-task.get("review_minutes", 0))
                       if phase == "preparation" else min(remaining[tid], task.get("review_minutes", 0)))
        tail = _final_remainder(phase_total)
        if len(entries) != 1 or entries[0]["minutes"] != tail or by_phase[(tid, phase)] > phase_total:
            raise ValidationError("lock: короткий кубик не равен финальному остатку этапа")
    if any(minutes > 60 for minutes in early_by_week.values()):
        raise ValidationError("lock: ранняя подготовка второго курса чаще одного часа в неделю")


def _manual_day_view(planning, key, led, locks, weekly_review, rest):
    ordered = sorted(locks, key=lambda item: (item.get("order", 0), item["cube_id"]))
    reports = {cube_id: entry for cube_id, entry in planning.get("completed_cubes", {}).items()
               if entry["date"] == key}
    measured = sum(entry.get("actual_minutes", 0) for entry in reports.values())
    estimated = sum(round(entry["planned_minutes"] * entry["percent"] / 100)
                    for entry in reports.values() if not entry.get("actual_minutes"))
    reported_work = measured + estimated
    unstarted = sum(_lock_reservation(planning, lock) for lock in ordered)
    constructor_day = planning.get("constructor", {}).get("days", {}).get(key, {}).get("initialized", False)
    if constructor_day:
        selected_ids = {lock["cube_id"] for lock in ordered}
        task_charge = sum(_constructor_cube_charge(lock["minutes"], reports.get(lock["cube_id"], {}), selected=True)
                          for lock in ordered)
        task_charge += sum(_constructor_cube_charge(report["planned_minutes"], report, selected=False)
                           for cube_id, report in reports.items() if cube_id not in selected_ids)
        unstarted = max(0, task_charge-reported_work)
    weekly_minutes = weekly_review["minutes"] if weekly_review else 0
    completed_preps = planning.get("prep_progress", {})
    prep_cubes = []
    prep_actual_minutes = 0
    prep_actual_excess = 0
    for prep in led["prep_cubes"]:
        report = completed_preps.get(prep["id"], {})
        actual = report.get("actual_minutes", 0)
        prep_actual_minutes += actual
        prep_actual_excess += max(0, actual-prep["minutes"])
        prep_cubes.append({**prep, "progress_percent": report.get("percent", 0),
                           "actual_minutes": actual})
    fixed_minutes = led["fixed_minutes"]
    if constructor_day:
        # In the constructor prep is a movable selected source, charged below
        # with the same policy as task cubes. Only Calendar remains fixed.
        fixed_minutes -= led["prep_minutes"]
        prep_actual_excess = 0
    source_load = _constructor_source_load(planning, key)
    total = fixed_minutes + prep_actual_excess + weekly_minutes + reported_work + unstarted + source_load
    first = ordered[0] if ordered else None
    segment_by_id = {segment["id"]: segment for segment in planning.get("task_segments", [])}
    first_segment = segment_by_id.get(first.get("segment_id")) if first else None
    goal = (first_segment.get("output") or first_segment["title"]) if first_segment else (first.get("output") if first else None)
    reported = bool(reports) or any(entry["date"] == key for entry in planning.get("day_outcomes", []))
    return {"date": key, "rest_provisional": key in rest,
            "manual": True, "focus_task_id": first["task_id"] if first else None,
            "focus_segment_id": first.get("segment_id") if first else None, "goal": goal,
            "secondary_early_prep_exception": False,
            "calendar_minutes": led["timed_minutes"], "lesson_count": len(led["lessons"]),
            "lesson_prep_minutes": led["prep_minutes"], "prep_cubes": prep_cubes,
            "prep_actual_minutes": prep_actual_minutes,
            "prep_actual_excess_minutes": prep_actual_excess,
            "all_day_assumed_minutes": led["homework_minutes"], "fixed_minutes": fixed_minutes,
            "development_minutes": reported_work + unstarted + source_load, "manual_cube_minutes": unstarted,
            "builder_selected_minutes": reported_work + unstarted + source_load,
            "builder_available_minutes": max(0, led["day_limit_minutes"] -
                                             fixed_minutes - prep_actual_excess -
                                             weekly_minutes - reported_work - unstarted - source_load),
            "weekly_review_minutes": weekly_minutes,
            "weekly_review": ({k: weekly_review[k] for k in ("id", "start", "end", "minutes", "status")}
                              if weekly_review else None),
            "total_work_minutes": total, "budget_minutes": led["day_limit_minutes"],
            "builder_total_work_minutes": total,
            "builder_over_cap_minutes": max(0, total-led["day_limit_minutes"]),
            "day_limit_minutes": led["day_limit_minutes"], "energy_units": led["day_limit_minutes"]/60,
            "raised_load": led["raised_load"], "emergency_approved": led["raised_load"],
            "unused_capacity_minutes": max(0, led["day_limit_minutes"]-total),
            "over_cap_minutes": max(0, total-led["day_limit_minutes"]),
            "free_windows": [[_hhmm(a), _hhmm(b)] for a, b in led["free_windows"]],
            "event_ids": led["events"], "calendar_needs_review": bool(led["unknown"]) or
            bool(planning["calendar"].get("stale")) or not planning["calendar"].get("configured", True),
            "needs_review": bool(led["unknown"]) or bool(estimated) or
            bool(planning["calendar"].get("stale")) or not planning["calendar"].get("configured", True),
            "over_cap_fixed_minutes": led["over_cap_minutes"],
            "reported": reported, "report_basis": "actual" if measured else "estimated_from_percent" if estimated else None,
            "reported_actual_minutes": measured if measured else None,
            "reported_effective_minutes": reported_work if reported else None}


def _task_pool(planning, cubes, days):
    pool_today = max(days[0], min(days[-1], datetime.now(
        ZoneInfo(planning["settings"]["timezone"])).date()))
    manual_reserved = defaultdict(int)
    manual_segment_reserved = defaultdict(int)
    for lock in planning.get("locks", []):
        if lock.get("manual") and lock["date"] >= pool_today.isoformat():
            amount = _lock_reservation(planning, lock)
            manual_reserved[lock["task_id"]] += amount
            if lock.get("segment_id"):
                manual_segment_reserved[lock["segment_id"]] += amount
    auto_future = defaultdict(int)
    for cube in cubes:
        if not cube.get("manual") and cube["date"] >= pool_today.isoformat():
            auto_future[cube["task_id"]] += cube["minutes"]
    actual_remaining = _remaining(planning)
    credits = _segment_effective_credits(planning)
    retired = _retired_minutes(planning, by_segment=True)
    segments_by_task = defaultdict(list)
    for segment in planning.get("task_segments", []):
        remaining_segment = min(actual_remaining[segment["task_id"]], max(
            0, segment["minutes"]-credits.get(segment["id"], 0)-retired[segment["id"]]))
        if remaining_segment <= 0:
            continue
        segments_by_task[segment["task_id"]].append({
            "id": segment["id"], "task_id": segment["task_id"], "title": segment["title"],
            "minutes": segment["minutes"], "remaining_minutes": remaining_segment,
            "progress_percent": round(100*credits.get(segment["id"], 0)/segment["minutes"]),
            "steps": segment.get("steps", []), "output": segment.get("output", ""),
            "origin": segment["origin"],
            "available_for_manual_minutes": max(
                0, remaining_segment-manual_segment_reserved[segment["id"]]),
        })
    return [{
        "task_id": task["id"], "course": task["course"], "title": task["title"],
        "due": task.get("due"), "due_status": task.get("due_status"),
        "remaining_minutes": actual_remaining[task["id"]],
        "manual_reserved_minutes": manual_reserved[task["id"]],
        "auto_future_minutes": auto_future[task["id"]],
        "available_for_manual_minutes": max(
            0, actual_remaining[task["id"]]-manual_reserved[task["id"]]),
        "segments": segments_by_task[task["id"]],
    } for task in sorted(planning["tasks"], key=lambda task: _priority(
        task, actual_remaining, pool_today, planning["settings"]))
       if actual_remaining[task["id"]] > 0 and not _scope_exempt(planning, task["id"])]


def _refresh_result_remaining(planning):
    actual = _remaining(planning)
    cancelled = _retired_minutes(planning, active_only=False)
    for task in planning.get("result", {}).get("tasks", []):
        task["remaining_minutes"] = actual.get(task["id"], 0)
        task["active_remaining_minutes"] = 0 if _scope_exempt(planning, task["id"]) else task["remaining_minutes"]
        task["scope_exempt"] = _scope_exempt(planning, task["id"])
        task["cancelled_minutes"] = cancelled[task["id"]]


def calculate(planning, *, from_date=None, reason="recalculate"):
    _ensure_segments(planning)
    days = validate_planning(planning)
    requested = _day(from_date, "from_date") if from_date else days[0]
    if not days[0] <= requested <= days[-1]:
        raise ValidationError("from_date вне горизонта")
    report_days = [date.fromisoformat(entry["date"]) for entry in planning.get("completed_cubes", {}).values()]
    report_days += [date.fromisoformat(entry["date"]) for entry in planning.get("day_outcomes", [])]
    last_report = max(report_days, default=None)
    start = max(requested, last_report + timedelta(days=1)) if last_report else requested
    report_warning = (f"Отчётные дни по {last_report.isoformat()} сохранены; пересчёт начат с {start.isoformat()}."
                      if start > requested else None)
    ledger = _ledger(planning, days)
    old = planning.get("result", {})
    old_cubes = [deepcopy(c) for c in old.get("cubes", []) if c.get("date", "") < start.isoformat()
                 and not deleted_cube(planning, c["id"], c["id"])]
    old_days = [deepcopy(d) for d in old.get("days", []) if d.get("date", "") < start.isoformat()]
    reported_cube_by_day = defaultdict(list)
    for entry in planning.get("completed_cubes", {}).values():
        reported_cube_by_day[entry["date"]].append(entry)
    outcome_dates = {entry["date"] for entry in planning.get("day_outcomes", [])}
    local_today = datetime.now(ZoneInfo(planning["settings"]["timezone"])).date().isoformat()
    for day in old_days:
        key = day["date"]
        if key in planning.get("manual_days", {}):
            continue
        day.setdefault("calendar_needs_review", day.get("needs_review", False))
        reports = reported_cube_by_day.get(key, [])
        if reports:
            prior_planned = day.get("planned_development_minutes", day.get("development_minutes", 0))
            measured = sum(entry.get("actual_minutes", 0) for entry in reports)
            estimated = sum(round(entry["planned_minutes"] * entry["percent"] / 100)
                            for entry in reports if entry.get("actual_minutes", 0) == 0)
            effective_work = measured + estimated
            credited = sum(round(entry["planned_minutes"] * entry["percent"] / 100)
                           for entry in reports)
            day.update(reported=True, report_basis=("mixed" if measured and estimated else
                                                    "actual" if measured else "estimated_from_percent"),
                       planned_development_minutes=prior_planned,
                       reported_actual_minutes=measured if measured else None,
                       reported_effective_minutes=effective_work,
                       unreported_planned_minutes=max(0, prior_planned - credited),
                       development_minutes=effective_work,
                       total_work_minutes=day["fixed_minutes"] + day.get("weekly_review_minutes", 0) + effective_work,
                       builder_total_work_minutes=day["fixed_minutes"] +
                       day.get("weekly_review_minutes", 0) + effective_work,
                       unused_capacity_minutes=0,
                       needs_review=day.get("needs_review", False) or bool(estimated))
            day["builder_available_minutes"] = max(
                0, day["day_limit_minutes"]-day["builder_total_work_minutes"])
            day["builder_over_cap_minutes"] = max(
                0, day["builder_total_work_minutes"]-day["day_limit_minutes"])
        elif key in outcome_dates:
            day.update(reported=True, report_basis="actual_unknown",
                       planned_development_minutes=day.get("development_minutes", 0),
                       reported_actual_minutes=None, unused_capacity_minutes=0,
                       needs_review=True)
        elif key < local_today:
            day.update(historical_unreported=True, unused_capacity_minutes=0,
                       needs_review=True)
    remaining = _remaining(planning)
    task_by_id = {task["id"]: task for task in planning["tasks"]}
    future_locks = [deepcopy(x) for x in planning.get("locks", []) if x["date"] >= start.isoformat()]
    occupied_dates = {x["date"] for x in future_locks}
    occupied_dates.update(day for day, manual in planning.get("manual_days", {}).items()
                          if manual.get("budget_minutes", 0) > 0)
    rest = _rest_days(days, ledger, planning["tasks"], occupied_dates)
    _validate_locks(planning, ledger, remaining, rest, start.isoformat())
    weekly_reviews, reviews_by_day = _weekly_reviews(planning, days, ledger, rest, future_locks)
    prior_reviews = {x["id"]: x for x in old.get("weekly_reviews", [])}
    for index, review in enumerate(weekly_reviews):
        prior = prior_reviews.get(review["id"])
        if prior and prior.get("date") and prior["date"] < start.isoformat():
            if review.get("date"):
                reviews_by_day.pop(review["date"], None)
            weekly_reviews[index] = deepcopy(prior)
            reviews_by_day[prior["date"]] = weekly_reviews[index]
    reserved = defaultdict(int)
    for lock in future_locks:
        reserved[lock["task_id"]] += _lock_reservation(planning, lock)
    result_days, cubes = old_days, old_cubes
    early_weekly = defaultdict(int)
    for cube in old_cubes:
        task = task_by_id.get(cube.get("task_id"))
        if task and task.get("early_prep") and task["course"] != planning["settings"].get("priority_course"):
            old_day = date.fromisoformat(cube["date"])
            early_weekly[(old_day-timedelta(days=old_day.weekday())).isoformat()] += cube["minutes"]
    for day in days:
        key = day.isoformat()
        if key < start.isoformat():
            continue
        led = ledger[key]
        week = (day - timedelta(days=day.weekday())).isoformat()
        priority_active = bool(planning["settings"].get("priority_course") and planning["settings"].get("priority_until")
                               and key < planning["settings"]["priority_until"])
        locks = [x for x in future_locks if x["date"] == key]
        weekly_review = reviews_by_day.get(key)
        weekly_minutes = weekly_review["minutes"] if weekly_review else 0
        if key in planning.get("manual_days", {}):
            for lock in sorted(locks, key=lambda item: (item.get("order", 0), item["cube_id"])):
                amount = _lock_reservation(planning, lock)
                remaining[lock["task_id"]] -= amount
                reserved[lock["task_id"]] -= amount
                progress = planning.get("completed_cubes", {}).get(lock["cube_id"], {})
                cubes.append({**lock, "id": lock["cube_id"], "course": task_by_id[lock["task_id"]]["course"],
                              "progress_percent": progress.get("percent", 0),
                              "actual_minutes": progress.get("actual_minutes", 0)})
            result_days.append(_manual_day_view(planning, key, led, locks, weekly_review, rest))
            continue
        for lock in locks:
            remaining[lock["task_id"]] -= lock["minutes"]
            reserved[lock["task_id"]] -= lock["minutes"]
            locked_task = task_by_id[lock["task_id"]]
            if priority_active and locked_task.get("early_prep") and locked_task["course"] != planning["settings"].get("priority_course"):
                early_weekly[week] += lock["minutes"]
            cubes.append({**lock, "id": lock["cube_id"], "course": task_by_id[lock["task_id"]]["course"],
                          "progress_percent": planning.get("completed_cubes", {}).get(lock["cube_id"], {}).get("percent", 0),
                          "actual_minutes": planning.get("completed_cubes", {}).get(lock["cube_id"], {}).get("actual_minutes", 0)})
        budget = max(0, led["available_minutes"] - sum(x["minutes"] for x in locks) - weekly_minutes)
        free = list(led["free_windows"])
        for lock in locks:
            a, b = _clock(lock["start"], "lock.start"), _clock(lock["end"], "lock.end")
            free = _subtract(free, (a-planning["settings"]["break_minutes"], b+planning["settings"]["break_minutes"]))
        if weekly_review:
            a, b = _clock(weekly_review["start"], "weekly_review.start"), _clock(weekly_review["end"], "weekly_review.end")
            free = _subtract(free, (a-planning["settings"]["break_minutes"],
                                    b+planning["settings"]["break_minutes"]))
        primary_lock = next((x for x in locks if task_by_id[x["task_id"]]["course"] ==
                             planning["settings"].get("priority_course")), None)
        non_early_lock = next((x for x in locks if not task_by_id[x["task_id"]].get("early_prep")), None)
        focus = (primary_lock or non_early_lock or {}).get("task_id")
        primary_left = any(task["course"] == planning["settings"].get("priority_course") and
                           not _scope_exempt(planning, task["id"], key) and
                           remaining[task["id"]] - reserved[task["id"]] >= 60 for task in planning["tasks"])
        def schedulable(task):
            if _scope_exempt(planning, task["id"], key):
                return False
            amount = remaining[task["id"]] - reserved[task["id"]]
            prep_left = max(0, amount-task.get("review_minutes", 0))
            review_open = not task.get("methodist_start") or key >= task["methodist_start"]
            return prep_left > 0 or (review_open and amount > 0)
        candidates = [task for task in planning["tasks"] if schedulable(task)
                      and (not priority_active or not primary_left or
                           task["course"] == planning["settings"]["priority_course"] or task.get("early_prep"))]
        early_limit = min(60, planning["settings"].get("secondary_early_prep_minutes_per_week", 90))
        secondary = [task for task in candidates if task["course"] != planning["settings"].get("priority_course")
                     and task.get("early_prep") and early_weekly[week] == 0
                     and remaining[task["id"]] - reserved[task["id"]] - task.get("review_minutes", 0) >= 60]
        if focus is None and key not in rest and candidates:
            focus = min(candidates, key=lambda task: _priority(task, remaining, day, planning["settings"]))["id"]
        if focus and _scope_exempt(planning, focus, key):
            focus = None
        early_cube = None
        if (focus and priority_active and task_by_id[focus]["course"] == planning["settings"]["priority_course"]
                and day.weekday() >= 3 and secondary and budget >= 120 and key not in rest):
            candidate = min(secondary, key=lambda task: _priority(task, remaining, day, planning["settings"]))
            slot = next(((right-60, right) for left, right in reversed(free) if right-left >= 60), None)
            if slot:
                left, right = slot
                phase = "preparation"
                step, output = STEPS[candidate.get("kind", "other")][0]
                early_cube = {"id": _stable_cube_id(candidate["id"], key, left, phase),
                              "task_id": candidate["id"], "course": candidate["course"],
                              "date": key, "start": _hhmm(left), "end": _hhmm(right),
                              "minutes": 60, "phase": phase,
                              "title": f"{step}: {candidate['title']}", "output": output,
                              "locked": False, "progress_percent": 0, "actual_minutes": 0}
                free = _subtract(free, (left-planning["settings"]["break_minutes"],
                                        right+planning["settings"]["break_minutes"]))
                budget -= 60
        placed = []
        if focus and key not in rest:
            task = task_by_id[focus]
            available = max(0, remaining[focus] - reserved[focus])
            is_early_secondary = priority_active and task["course"] != planning["settings"].get("priority_course")
            if is_early_secondary:
                available = min(available, max(0, early_limit-early_weekly[week]))
                budget = min(budget, available)
            prep_left = max(0, available-task.get("review_minutes", 0))
            phase = "preparation" if prep_left > 0 else "review"
            if phase == "preparation":
                available = prep_left
            if phase != "review" or not task.get("methodist_start") or key >= task["methodist_start"]:
                last_end = None
                for left, right in free:
                    cursor = max(left, last_end+planning["settings"]["break_minutes"]) if last_end is not None else left
                    while budget >= 60 and available >= 60:
                        possible = min(right-cursor, budget, available)
                        size = 90 if possible >= 90 and available-90 != 30 else 60 if possible >= 60 else 0
                        if not size:
                            break
                        cube_id = _stable_cube_id(focus, key, cursor, phase)
                        step_list = STEPS[task.get("kind", "other")]
                        index = min(len(step_list)-1, max(0, (task["estimate_minutes"]["central"] - remaining[focus]) * len(step_list) // max(1, task["estimate_minutes"]["central"])))
                        step, output = step_list[index]
                        placed.append({"id": cube_id, "task_id": focus, "course": task["course"],
                                       "date": key, "start": _hhmm(cursor), "end": _hhmm(cursor+size),
                                       "minutes": size, "phase": phase, "title": f"{step}: {task['title']}",
                                       "output": output, "locked": False, "is_remainder": False,
                                       "progress_percent": 0, "actual_minutes": 0})
                        last_end = cursor+size
                        cursor = last_end+planning["settings"]["break_minutes"]
                        budget -= size
                        available -= size
                        remaining[focus] -= size
                        if is_early_secondary:
                            early_weekly[week] += size
                    if 0 < available < 60 and budget >= available and right-cursor >= available:
                        cube_id = _stable_cube_id(focus, key, cursor, phase)
                        step_list = STEPS[task.get("kind", "other")]
                        index = min(len(step_list)-1, max(0, (task["estimate_minutes"]["central"] - remaining[focus]) * len(step_list) // max(1, task["estimate_minutes"]["central"])))
                        step, output = step_list[index]
                        placed.append({"id": cube_id, "task_id": focus, "course": task["course"],
                                       "date": key, "start": _hhmm(cursor), "end": _hhmm(cursor+available),
                                       "minutes": available, "phase": phase,
                                       "title": f"{step}: {task['title']}", "output": output,
                                       "locked": False, "is_remainder": True,
                                       "progress_percent": 0, "actual_minutes": 0})
                        budget -= available
                        remaining[focus] -= available
                        if is_early_secondary:
                            early_weekly[week] += available
                        available = 0
        if early_cube and deleted_cube(planning, early_cube["id"], early_cube["id"]):
            early_cube = None
        if early_cube:
            placed.append(early_cube)
            remaining[early_cube["task_id"]] -= 60
            early_weekly[week] += 60
        for blocked in [cube for cube in placed if deleted_cube(planning, cube["id"], cube["id"])]:
            remaining[blocked["task_id"]] += blocked["minutes"]
        placed = [cube for cube in placed if not deleted_cube(planning, cube["id"], cube["id"])]
        cubes.extend(placed)
        planned = sum(x["minutes"] for x in locks) + sum(x["minutes"] for x in placed)
        secondary_exception = priority_active and focus and task_by_id[focus]["course"] == planning["settings"].get("priority_course") and any(
            task_by_id[x["task_id"]]["course"] != planning["settings"].get("priority_course")
            for x in locks + placed)
        reported_course = sum(
            (entry.get("actual_minutes") or round(entry["planned_minutes"]*entry["percent"]/100))
            for entry in planning.get("completed_cubes", {}).values() if entry["date"] == key)
        prep_excess = sum(max(0, planning.get("prep_progress", {}).get(prep["id"], {}).get("actual_minutes", 0) -
                              prep["minutes"]) for prep in led["prep_cubes"])
        builder_selected = sum(_lock_reservation(planning, lock) for lock in locks
                               if lock.get("manual") or lock.get("locked")) + reported_course
        builder_total = led["fixed_minutes"] + prep_excess + weekly_minutes + builder_selected
        result_days.append({"date": key, "rest_provisional": key in rest,
                            "focus_task_id": focus, "calendar_minutes": led["timed_minutes"],
                            "secondary_early_prep_exception": bool(secondary_exception),
                            "lesson_count": len(led["lessons"]), "lesson_prep_minutes": led["prep_minutes"],
                            "all_day_assumed_minutes": led["homework_minutes"],
                            "fixed_minutes": led["fixed_minutes"], "development_minutes": planned,
                            "builder_selected_minutes": builder_selected,
                            "builder_total_work_minutes": builder_total,
                            "builder_available_minutes": max(0, led["day_limit_minutes"]-builder_total),
                            "builder_over_cap_minutes": max(0, builder_total-led["day_limit_minutes"]),
                            "weekly_review_minutes": weekly_minutes,
                            "weekly_review": ({k: weekly_review[k] for k in ("id", "start", "end", "minutes", "status")}
                                              if weekly_review else None),
                            "total_work_minutes": led["fixed_minutes"] + weekly_minutes + planned,
                            "day_limit_minutes": led["day_limit_minutes"],
                            "emergency_approved": led["emergency_approved"],
                            "unused_capacity_minutes": max(0, led["available_minutes"]-weekly_minutes-planned),
                            "free_windows": [[_hhmm(a), _hhmm(b)] for a, b in led["free_windows"]],
                            "event_ids": led["events"],
            "prep_cubes": [{**prep, "progress_percent": planning.get("prep_progress", {}).get(prep["id"], {}).get("percent", 0),
                            "actual_minutes": planning.get("prep_progress", {}).get(prep["id"], {}).get("actual_minutes", 0)}
                           for prep in led["prep_cubes"]],
            "budget_minutes": led["day_limit_minutes"], "energy_units": led["day_limit_minutes"]/60,
            "manual": False, "raised_load": led["raised_load"], "over_cap_minutes": max(0, led["fixed_minutes"]+weekly_minutes+planned-led["day_limit_minutes"]),
                            "calendar_needs_review": bool(led["unknown"]) or bool(planning["calendar"].get("stale")) or not planning["calendar"].get("configured", True),
                            "needs_review": bool(led["unknown"]) or bool(planning["calendar"].get("stale")) or not planning["calendar"].get("configured", True),
                            "over_cap_fixed_minutes": led["over_cap_minutes"]})
    result_tasks = []
    risks = []
    active_ids = {cube["id"] for cube in cubes}
    orphaned_details = sorted(set(planning.get("cube_details", {})) - active_ids)
    for cube in cubes:
        detail = planning.get("cube_details", {}).get(cube["id"])
        if detail:
            cube.update(deepcopy(detail))
    if orphaned_details:
        risks.append({"code": "cube_detail_orphaned", "count": len(orphaned_details),
                      "cube_ids": orphaned_details[:20]})
    for task in planning["tasks"]:
        tid = task["id"]
        due = task.get("due")
        assigned = sum(c["minutes"] for c in cubes if c["task_id"] == tid and c["date"] >= start.isoformat()
                       and (not due or c["date"] <= due))
        demand = _remaining(planning)[tid]
        exempt = _scope_exempt(planning, tid)
        active_demand = 0 if exempt else demand
        deficit = max(0, active_demand-assigned)
        record = {**task, "remaining_minutes": demand,
                  "cancelled_minutes": _retired_minutes(planning, active_only=False)[tid],
                  "active_remaining_minutes": active_demand, "scope_exempt": exempt,
                  "scheduled_by_due_minutes": assigned, "deficit_by_due_minutes": deficit,
                  "remaining_at_horizon_minutes": 0 if exempt else remaining[tid]}
        result_tasks.append(record)
        if exempt and any(lock["task_id"] == tid and lock["date"] >= planning["scope_exemptions"][tid]["from_date"]
                          and _started(planning, lock["cube_id"]) for lock in planning.get("locks", [])):
            risks.append({"code": "scope_exempt_started_work_retained", "task_id": tid})
        if due and deficit:
            risks.append({"code": "deadline_deficit", "task_id": tid, "minutes": deficit})
    task_pool = _task_pool(planning, cubes, days)
    for day in result_days:
        if day["over_cap_fixed_minutes"]:
            risks.append({"code": "fixed_work_exceeds_day_limit", "date": day["date"], "minutes": day["over_cap_fixed_minutes"]})
        if day.get("manual") and day.get("over_cap_minutes", 0):
            risks.append({"code": "manual_day_over_budget", "date": day["date"],
                          "minutes": day["over_cap_minutes"]})
        if day.get("reported") and day["total_work_minutes"] > day["day_limit_minutes"]:
            risks.append({"code": "reported_work_exceeds_day_limit", "date": day["date"],
                          "minutes": day["total_work_minutes"]-day["day_limit_minutes"]})
        if day.get("historical_unreported"):
            risks.append({"code": "past_day_unreported", "date": day["date"]})
        if day.get("reported") and day.get("report_basis") in {"mixed", "estimated_from_percent", "actual_unknown"}:
            risks.append({"code": "reported_actual_unknown", "date": day["date"]})
        if day.get("calendar_needs_review", day["needs_review"]):
            risks.append({"code": "calendar_needs_review", "date": day["date"]})
    for review in weekly_reviews:
        if review["status"] == "unplaced":
            risks.append({"code": "weekly_review_unplaced", "week_end": review["week_end"],
                          "minutes": review["minutes"]})
    live_details, _, _ = _live_details(planning)
    unscheduled_live = [item for item in live_details if item["status"] == "unscheduled"]
    for live in live_details:
        if live["status"] == "unscheduled":
            risks.append({"code": "live_event_date_unknown", "live_event_id": live["id"],
                          "minutes": live["duration_minutes"]})
        elif live["status"] == "scheduled_provisional_start":
            risks.append({"code": "live_event_start_provisional", "live_event_id": live["id"],
                          "date": live["date"], "suggested_start": live["effective_start"]})
        if live.get("calendar_duration_minutes") is not None and live["calendar_duration_minutes"] != 180:
            risks.append({"code": "calendar_live_duration_mismatch", "live_event_id": live["id"],
                          "calendar_minutes": live["calendar_duration_minutes"], "planned_minutes": 180})
        if live.get("calendar_match") == "title_date_fallback":
            risks.append({"code": "live_calendar_link_inferred", "live_event_id": live["id"]})
        if live.get("calendar_event_id") and live.get("date") and not live["covered_by_calendar"]:
            risks.append({"code": "live_calendar_link_missing", "live_event_id": live["id"]})
        if live.get("date") and not live.get("calendar_event_id"):
            risks.append({"code": "live_calendar_link_unverified", "live_event_id": live["id"]})
    totals = {"days": len(result_days), "tasks": len(result_tasks), "cubes": len(cubes),
              "fixed_hours": round(sum(x["fixed_minutes"] for x in result_days)/60, 2),
              "development_hours": round(sum(x["development_minutes"] for x in result_days
                                              if x["date"] >= start.isoformat() or x.get("reported"))/60, 2),
              "future_development_hours": round(sum(x["development_minutes"] for x in result_days
                                                     if x["date"] >= start.isoformat())/60, 2),
              "reported_actual_hours": round(sum(x.get("reported_actual_minutes") or 0 for x in result_days)/60, 2),
              "historical_unreported_planned_hours": round(sum(x.get("planned_development_minutes",
                                                                   x.get("development_minutes", 0))
                                                               for x in result_days if x.get("historical_unreported"))/60, 2),
              "remaining_horizon_hours": round(sum(n for tid, n in remaining.items() if not _scope_exempt(planning, tid))/60, 2),
              "tasks_missing_due": sum(bool(x["deficit_by_due_minutes"] and x.get("due")) for x in result_tasks),
              "max_total_work_minutes": max((x["total_work_minutes"] for x in result_days), default=0)}
    totals["weekly_review_hours"] = round(sum(x.get("weekly_review_minutes", 0) for x in result_days)/60, 2)
    totals["weekly_reviews_unplaced"] = sum(x["status"] == "unplaced" for x in weekly_reviews)
    totals["unscheduled_live_minutes"] = sum(x["duration_minutes"] for x in unscheduled_live)
    totals["by_course"] = {course: {"scheduled_hours": round(sum(c["minutes"] for c in cubes
                                                                 if c["course"] == course and c["date"] >= start.isoformat())/60, 2),
                                   "remaining_hours": round(sum(remaining[t["id"]] for t in planning["tasks"] if t["course"] == course and not _scope_exempt(planning, t["id"]))/60, 2)}
                           for course in sorted({x["course"] for x in planning["tasks"]})}
    unresolved = [x for x in result_tasks if x.get("remaining_at_horizon_minutes", 0) > 0 or
                  (x.get("due") and x["deficit_by_due_minutes"] > 0)]
    emergency_proposals = []
    if unresolved:
        for day in result_days:
            if day["date"] < start.isoformat() or day["rest_provisional"] or day["lesson_count"] or day["emergency_approved"]:
                continue
            extra = max(0, min(180, 480-day["total_work_minutes"]))
            if extra >= 60 and _has_free_cube_slot(day, cubes, planning["settings"]["break_minutes"]):
                emergency_proposals.append({"date": day["date"], "maximum_extra_minutes": extra,
                                            "status": "proposal_only_requires_user_approval",
                                            "reason": "Остаток/риск срока; сверх стандартных 5 часов только по решению пользователя"})
            if len(emergency_proposals) >= 12:
                break
    result = {"days": result_days, "cubes": cubes, "tasks": result_tasks, "totals": totals,
              "task_pool": task_pool,
              "needs_detail_review": bool(orphaned_details),
              "orphaned_detail_count": len(orphaned_details),
              "weekly_reviews": weekly_reviews,
              "live_events": live_details,
              "unscheduled_live_minutes": totals["unscheduled_live_minutes"],
              "unscheduled_live_count": len(unscheduled_live),
              "risks": risks, "emergency_proposals": emergency_proposals,
              "provenance": {"calendar_fetched_at": planning["calendar"].get("fetched_at"),
                                          "calendar_stale": planning["calendar"].get("stale"),
                                          "calendar_coverage_status": planning["calendar"].get("coverage_status"),
                                          "from_date": start.isoformat(), "requested_from": requested.isoformat(),
                                          "effective_from": start.isoformat(), "report_warning": report_warning,
                                          "reason": reason}}
    planning["result"] = result
    _refresh_constructor_result(planning)
    planning["needs_recalculation"] = False
    planning["last_update"] = {"action": "recalculate", "reason": reason,
                               "at": datetime.now(timezone.utc).isoformat()}
    history = planning.setdefault("history", [])
    history.append({"version": history[-1]["version"]+1 if history else 1,
                    "at": datetime.now(timezone.utc).isoformat(),
                    "reason": reason, "counts": {"days": len(result_days), "cubes": len(cubes), "risks": len(risks)}})
    planning["history"] = history[-40:]
    planning["plan_version"] = sha256(encoded({"settings": planning["settings"], "tasks": planning["tasks"],
                                                "calendar": planning["calendar"], "completed_cubes": planning.get("completed_cubes", {}),
                                                "day_outcomes": planning.get("day_outcomes", []), "locks": planning.get("locks", []),
                                                "live_events": planning.get("live_events", []),
                                                "weekly_review_done": planning.get("weekly_review_done", {}),
                                                "cube_details": planning.get("cube_details", {}),
                                                "manual_days": planning.get("manual_days", {}),
                                                "prep_overrides": planning.get("prep_overrides", {}),
                                                "prep_progress": planning.get("prep_progress", {}),
                                                "task_segments": planning.get("task_segments", []),
                                                "segment_baseline_remaining": planning.get("segment_baseline_remaining", {}),
                                                "segment_progress": planning.get("segment_progress", {}),
                                                "constructor": planning.get("constructor", {}),
                                                "scope_exemptions": planning.get("scope_exemptions", {}),
                                                "from": start.isoformat()}).encode()).hexdigest()[:24]
    return planning


def merge_calendar_refresh(planning, response):
    """Retain user classifications by stable occurrence identity; no writes."""
    old_events = planning["calendar"].get("events", [])
    old = {str(e.get("occurrence_id") or e.get("id")): e for e in old_events}
    def fingerprint(event):
        return (str(event.get("title") or event.get("summary") or "").strip().casefold(),
                event.get("start"), event.get("end"))
    old_by_fingerprint = defaultdict(list)
    for event in old_events:
        old_by_fingerprint[fingerprint(event)].append(event)
    events = []
    for event in response.get("events", []):
        copied = deepcopy(event)
        prior = old.get(str(event.get("occurrence_id") or event.get("id"))) or old.get(str(event.get("id")))
        if prior is None and len(old_by_fingerprint[fingerprint(event)]) == 1:
            prior = old_by_fingerprint[fingerprint(event)][0]
        if prior and prior.get("category"):
            copied["category"] = prior["category"]
        if prior:
            copied["prep_key"] = prior.get("prep_key") or prior.get("occurrence_id") or prior.get("id")
        events.append(copied)
    incomplete = bool(response.get("stale") or response.get("error") or not response.get("configured"))
    if incomplete:
        known = {(event.get("calendar_id"), event.get("occurrence_id") or event.get("id"))
                 for event in events}
        known_fingerprints = {fingerprint(event) for event in events}
        for event in old_events:
            identity = (event.get("calendar_id"), event.get("occurrence_id") or event.get("id"))
            if identity not in known and fingerprint(event) not in known_fingerprints:
                events.append(deepcopy(event))
                known.add(identity)
                known_fingerprints.add(fingerprint(event))
    planning["calendar"].update({"events": events, "fetched_at": datetime.now(timezone.utc).isoformat(),
                                 "start": planning["settings"]["start"], "end": planning["settings"]["end"],
                                 "stale": incomplete,
                                 "configured": bool(response.get("configured")),
                                 "coverage_status": ("unconfigured_preserved" if not response.get("configured") else
                                                     "stale_merged" if incomplete else "current"),
                                 "source": "Brainalot Calendar read-only",
                                 "calendar_count": response.get("calendar_count", 0)})
    return planning


def _builder_date(planning, value):
    selected = _day(value, "day builder date")
    if selected not in _dates(planning["settings"]):
        raise ValidationError("Дата конструктора вне горизонта")
    today = datetime.now(ZoneInfo(planning["settings"]["timezone"])).date()
    if selected < today:
        raise ValidationError("Прошедший день доступен только для чтения")
    return selected.isoformat()


def _started(planning, cube_id):
    report = planning.get("completed_cubes", {}).get(cube_id)
    return bool(report and (report.get("percent", 0) > 0 or report.get("actual_minutes", 0) > 0))


def _manual_locks(planning, key):
    return sorted((lock for lock in planning.get("locks", []) if lock["date"] == key and lock.get("manual")),
                  key=lambda lock: (lock.get("order", 0), lock["cube_id"]))


def _normalize_manual_order(planning, key):
    for index, lock in enumerate(_manual_locks(planning, key)):
        lock["order"] = index


def _materialize_manual_day(planning, key):
    existing_day = next((day for day in planning.get("result", {}).get("days", []) if day["date"] == key), None)
    if existing_day is None:
        raise ValidationError("План дня отсутствует; сначала выполните recalculate")
    planning.setdefault("manual_days", {}).setdefault(
        key, {"budget_minutes": existing_day.get("day_limit_minutes", planning["settings"]["day_limit_minutes"])})
    locks = planning.setdefault("locks", [])
    by_id = {lock["cube_id"]: lock for lock in locks}
    selected = [cube for cube in planning["result"].get("cubes", [])
                if cube["date"] == key and (cube.get("manual") or cube.get("locked") or
                                            _started(planning, cube["id"]))]
    for index, cube in enumerate(selected):
        lock = by_id.get(cube["id"])
        if lock is None:
            lock = {"cube_id": cube["id"], "task_id": cube["task_id"], "date": key,
                    "minutes": cube["minutes"], "phase": cube.get("phase", "preparation"),
                    "title": cube.get("title", ""), "output": cube.get("output", ""),
                    "locked": True, "manual": True, "floating": True, "start": None, "end": None,
                    "is_remainder": bool(cube.get("is_remainder")), "order": index}
            if cube.get("segment_id"):
                lock["segment_id"] = cube["segment_id"]
            if cube.get("steps"):
                lock["steps"] = deepcopy(cube["steps"])
            locks.append(lock)
            by_id[lock["cube_id"]] = lock
        else:
            lock.update(manual=True)
            if not lock.get("constructor"):
                lock.update(floating=True, start=None, end=None)
            lock.setdefault("order", index)
    _normalize_manual_order(planning, key)


def _refresh_manual_day(planning, key):
    ledger = _ledger(planning, _dates(planning["settings"]))
    led = ledger[key]
    reviews = planning.get("result", {}).get("weekly_reviews", [])
    review = next((review for review in reviews if review.get("date") == key), None)
    locks = _manual_locks(planning, key)
    row = _manual_day_view(planning, key, led, locks, review, set())
    old_days = planning["result"].get("days", [])
    for index, day in enumerate(old_days):
        if day["date"] == key:
            old_days[index] = row
            break
    old_cubes = planning["result"].get("cubes", [])
    old_cubes[:] = [cube for cube in old_cubes if cube["date"] != key]
    task_by_id = {task["id"]: task for task in planning["tasks"]}
    for lock in locks:
        report = planning.get("completed_cubes", {}).get(lock["cube_id"], {})
        cube = {**lock, "id": lock["cube_id"], "course": task_by_id[lock["task_id"]]["course"],
                "progress_percent": report.get("percent", 0), "actual_minutes": report.get("actual_minutes", 0)}
        if detail := planning.get("cube_details", {}).get(lock["cube_id"]):
            cube.update(deepcopy(detail))
        old_cubes.append(cube)
    ctor_day = planning.get("constructor", {}).get("days", {}).get(key, {})
    if not ctor_day.get("initialized"):
        ctor_day = {}
    ctor_cubes = planning.get("constructor", {}).get("cubes", {})
    unstarted_source = any(not _constructor_started(planning, ctor_cubes[cid])
                           for cid in ctor_day.get("selected_source_ids", []))
    if not ctor_day and row["over_cap_minutes"] and (any(_lock_reservation(planning, lock) for lock in locks) or unstarted_source):
        raise ValidationError("Бюджета недостаточно для не начатых кубиков: сначала верните или переставьте кубик")
    available_by_task = _remaining(planning)
    reserved_by_task = defaultdict(int)
    for lock in planning.get("locks", []):
        if lock.get("manual"):
            reserved_by_task[lock["task_id"]] += _lock_reservation(planning, lock)
    if not ctor_day and any(reserved > available_by_task[tid] for tid, reserved in reserved_by_task.items()):
        raise ValidationError("Выбранные ручные кубики превышают остаток задачи")
    segment_credits = _segment_effective_credits(planning)
    by_segment = {segment["id"]: segment for segment in planning.get("task_segments", [])}
    segment_reserved = defaultdict(int)
    for lock in planning.get("locks", []):
        if lock.get("manual") and lock.get("segment_id"):
            segment_id = lock["segment_id"]
            if segment_id not in by_segment or by_segment[segment_id]["task_id"] != lock["task_id"]:
                raise ValidationError("Ручной кубик ссылается на чужой этап")
            segment_reserved[segment_id] += _lock_reservation(planning, lock)
    if not ctor_day and any(reserved > by_segment[segment_id]["minutes"]-segment_credits.get(segment_id, 0)
           for segment_id, reserved in segment_reserved.items()):
        raise ValidationError("Выбранные кубики превышают остаток этапа")


def apply_day_builder(planning, action, data):
    planning = deepcopy(planning)
    if not isinstance(data, dict):
        raise ValidationError("day builder data: требуется объект")
    affected = set()
    if action == "day_budget":
        if set(data) != {"date", "minutes"}:
            raise ValidationError("day_budget: нужны date,minutes")
        key = _builder_date(planning, data["date"])
        minutes = _int(data["minutes"], "day_budget.minutes", 0, 480)
        if minutes % 15:
            raise ValidationError("day_budget: шаг 15 минут")
        _materialize_manual_day(planning, key)
        planning["manual_days"][key]["budget_minutes"] = minutes
        affected.add(key)
    elif action in {"prep_set", "prep_progress"}:
        allowed = {"prep_id", "minutes"} if action == "prep_set" else {"prep_id", "percent", "actual_minutes"}
        required = {"prep_id", "minutes"} if action == "prep_set" else {"prep_id", "percent"}
        if set(data) - allowed or not required <= set(data):
            raise ValidationError(f"{action}: неверные поля")
        prep_id = identifier(data["prep_id"], "prep_id")
        prep = next((prep for day in planning.get("result", {}).get("days", [])
                     for prep in day.get("prep_cubes", []) if prep["id"] == prep_id), None)
        if prep is None:
            raise ValidationError(f"{action}: подготовка не найдена")
        key = _builder_date(planning, prep["date"])
        _materialize_manual_day(planning, key)
        if action == "prep_set":
            minutes = _int(data["minutes"], "prep_set.minutes", 0, 120)
            if minutes % 5:
                raise ValidationError("prep_set: шаг 5 минут")
            progress = planning.get("prep_progress", {}).get(prep_id)
            if progress and (progress.get("percent", 0) > 0 or progress.get("actual_minutes", 0) > 0):
                credited = round(prep["default_minutes"] * progress.get("percent", 0) / 100)
                if minutes < max(progress.get("actual_minutes", 0), credited):
                    raise ValidationError("Подтверждённую подготовку нельзя уменьшить или убрать")
            planning.setdefault("prep_overrides", {})[prep_id] = minutes
            for cube in planning.get("constructor", {}).get("cubes", {}).values():
                if cube.get("kind") == "prep" and cube.get("source_ref") == prep_id:
                    cube["minutes"] = minutes
        else:
            percent = _int(data["percent"], "prep_progress.percent", 0, 100)
            prior = planning.setdefault("prep_progress", {}).get(prep_id, {})
            actual = _int(data.get("actual_minutes", prior.get("actual_minutes", 0)),
                          "prep_progress.actual_minutes")
            planning["prep_progress"][prep_id] = {"percent": percent, "actual_minutes": actual, "date": key}
        affected.add(key)
    elif action in {"day_cube_remove", "day_cube_move", "day_cube_resize"}:
        allowed = ({"cube_id"} if action == "day_cube_remove" else
                   {"cube_id", "date", "index"} if action == "day_cube_move" else
                   {"cube_id", "minutes"})
        required = {"cube_id"} if action == "day_cube_remove" else (
            {"cube_id", "date"} if action == "day_cube_move" else {"cube_id", "minutes"})
        if set(data) - allowed or not required <= set(data):
            raise ValidationError(f"{action}: неверные поля")
        cube_id = identifier(data["cube_id"], "cube_id")
        cube = next((cube for cube in planning.get("result", {}).get("cubes", [])
                     if cube["id"] == cube_id), None)
        if cube is None:
            raise ValidationError("Кубик не найден")
        old_day = _builder_date(planning, cube["date"])
        _materialize_manual_day(planning, old_day)
        lock = next((lock for lock in planning["locks"] if lock["cube_id"] == cube_id), None)
        if lock is None:
            raise ValidationError("AUTO-прогноз не выбран в конструктор и уже доступен в пуле")
        if _started(planning, cube_id):
            if action != "day_cube_move" or data["date"] != old_day:
                raise ValidationError("Начатый кубик нельзя удалить, изменить или перенести")
        if action == "day_cube_remove":
            planning["locks"].remove(lock)
            affected.add(old_day)
        elif action == "day_cube_resize":
            minutes = _int(data["minutes"], "day_cube_resize.minutes", 15, 90)
            if minutes not in (15, 30, 45, 60, 90):
                raise ValidationError("day_cube_resize: размер 15/30/45/60/90")
            lock.update(minutes=minutes, is_remainder=False)
            affected.add(old_day)
        else:
            target = _builder_date(planning, data["date"])
            _materialize_manual_day(planning, target)
            lock["date"] = target
            siblings = [item for item in _manual_locks(planning, target) if item["cube_id"] != cube_id]
            index = _int(data.get("index", len(siblings)), "day_cube_move.index", 0, len(siblings))
            siblings.insert(index, lock)
            for order, item in enumerate(siblings):
                item["order"] = order
            affected.update((old_day, target))
        _normalize_manual_order(planning, old_day)
    elif action == "day_cube_add":
        if set(data) - {"date", "task_id", "minutes", "segment_id", "index"} or not {"date", "task_id", "minutes"} <= set(data):
            raise ValidationError("day_cube_add: нужны date,task_id,minutes")
        key = _builder_date(planning, data["date"])
        task = next((task for task in planning["tasks"] if task["id"] == data["task_id"]), None)
        if task is None:
            raise ValidationError("day_cube_add: задача не найдена")
        minutes = _int(data["minutes"], "day_cube_add.minutes", 15, 90)
        if minutes not in (15, 30, 45, 60, 90):
            raise ValidationError("day_cube_add: размер 15/30/45/60/90")
        _materialize_manual_day(planning, key)
        siblings = _manual_locks(planning, key)
        index = _int(data.get("index", len(siblings)), "day_cube_add.index", 0, len(siblings))
        cube_id = uuid4().hex
        phase = ("review" if task.get("review_minutes", 0) and
                 _remaining(planning)[task["id"]] <= task["review_minutes"] else "preparation")
        lock = {"cube_id": cube_id, "task_id": task["id"], "date": key,
                "minutes": minutes, "phase": phase, "title": task["title"],
                "output": "Часть материала готова", "locked": True, "manual": True,
                "floating": True, "start": None, "end": None, "order": index,
                "is_remainder": False}
        if data.get("segment_id") is not None:
            lock["segment_id"] = identifier(data["segment_id"], "segment_id")
        planning["locks"].append(lock)
        siblings.insert(index, lock)
        for order, item in enumerate(siblings):
            item["order"] = order
        affected.add(key)
    else:
        raise ValidationError("Неизвестное действие конструктора дня")
    for key in sorted(affected):
        _refresh_manual_day(planning, key)
    return calculate(planning, reason=action)


def apply_segment_action(planning, action, data):
    planning = deepcopy(planning)
    _ensure_segments(planning)
    if not isinstance(data, dict):
        raise ValidationError("segment data: требуется объект")
    by_id = {segment["id"]: segment for segment in planning["task_segments"]}
    if action == "segment_create":
        if set(data) - {"task_id", "parent_id", "title", "minutes", "output", "steps"} or not {
                "task_id", "title", "minutes"} <= set(data):
            raise ValidationError("segment_create: неверные поля")
        task_id = identifier(data["task_id"], "task_id")
        if task_id not in {task["id"] for task in planning["tasks"]}:
            raise ValidationError("segment_create: задача не найдена")
        if data.get("parent_id") is not None:
            raise ValidationError("segment_create.parent_id: только null")
        minutes = _int(data["minutes"], "segment_create.minutes", 15, 300)
        if minutes % 15:
            raise ValidationError("segment_create: шаг 15 минут")
        reserved_task = sum(_lock_reservation(planning, lock) for lock in planning.get("locks", [])
                            if lock.get("manual") and lock["task_id"] == task_id)
        if minutes > max(0, _remaining(planning)[task_id]-reserved_task):
            raise ValidationError("Новый этап больше доступного остатка задачи")
        title = data["title"]
        output = data.get("output", "")
        steps = data.get("steps", [])
        candidate = {"id": "segment:" + uuid4().hex, "task_id": task_id,
                     "parent_id": None, "title": title, "minutes": minutes,
                     "output": output, "steps": steps, "origin": "manual"}
        _validate_cube_detail({"title": title, "output": output or "Результат этапа",
                               "steps": steps or ["Выполнить этап"]})
        started = set(planning.get("segment_progress", {}))
        started.update(cube.get("segment_id") for cid, cube in planning.get("constructor", {}).get("cubes", {}).items()
                       if cid in planning.get("cube_deletions", {}) and cube.get("segment_id"))
        started.update(entry["segment_id"] for entry in planning.get("completed_cubes", {}).values()
                       if entry.get("segment_id") and (entry["percent"] or entry.get("actual_minutes")))
        selected = {lock.get("segment_id") for lock in planning.get("locks", [])
                    if lock.get("manual") and _lock_reservation(planning, lock)}
        donors = [segment for segment in reversed(planning["task_segments"])
                  if segment["task_id"] == task_id and segment["origin"] == "auto"
                  and segment["id"] not in started and segment["id"] not in selected]
        if sum(segment["minutes"] for segment in donors) < minutes:
            raise ValidationError("Недостаточно не начатого объёма задачи для нового этапа")
        left = minutes
        for donor in donors:
            take = min(left, donor["minutes"])
            donor["minutes"] -= take
            left -= take
            if not donor["minutes"]:
                planning["task_segments"].remove(donor)
            if not left:
                break
        planning["task_segments"].append(candidate)
        return calculate(planning, reason="segment_create")
    if action == "segment_progress":
        if set(data) != {"segment_id", "percent"}:
            raise ValidationError("segment_progress: нужны segment_id,percent")
        segment_id = identifier(data["segment_id"], "segment_id")
        segment = by_id.get(segment_id)
        if segment is None:
            raise ValidationError("segment_progress: этап не найден")
        percent = _int(data["percent"], "segment_progress.percent", 0, 100)
        cube_credit = sum(round(entry["planned_minutes"]*entry["percent"]/100)
                          for entry in planning.get("completed_cubes", {}).values()
                          if entry.get("segment_id") == segment_id)
        today = datetime.now(ZoneInfo(planning["settings"]["timezone"])).date()
        first, last = _dates(planning["settings"])[0], _dates(planning["settings"])[-1]
        report_day = min(last, max(first, today)).isoformat()
        planning.setdefault("segment_progress", {})[segment_id] = {
            "percent": percent, "cube_credit_at_report": min(segment["minutes"], cube_credit),
            "date": report_day}
        credit = _segment_effective_credits(planning)[segment_id]
        eligible = max(0, segment["minutes"]-credit)
        locks = [lock for lock in planning.get("locks", []) if lock.get("manual")
                 and lock.get("segment_id") == segment_id and _lock_reservation(planning, lock)]
        reserved = sum(lock["minutes"] for lock in locks)
        released = []
        for lock in sorted(locks, key=lambda row: (row["minutes"], row["date"], row.get("order", 0)),
                           reverse=True):
            if reserved <= eligible:
                break
            planning["locks"].remove(lock)
            reserved -= lock["minutes"]
            released.append({"cube_id": lock["cube_id"], "segment_id": segment_id,
                             "date": lock["date"], "minutes": lock["minutes"],
                             "reason": "segment_progress"})
        for key in sorted({item["date"] for item in released}):
            _normalize_manual_order(planning, key)
            _refresh_manual_day(planning, key)
        result = calculate(planning, reason="segment_progress")
        result["result"]["released_reservations"] = released
        return result
    if action == "day_segment_add":
        if set(data) - {"date", "segment_id", "minutes"} or not {"date", "segment_id"} <= set(data):
            raise ValidationError("day_segment_add: неверные поля")
        key = _builder_date(planning, data["date"])
        segment_id = identifier(data["segment_id"], "segment_id")
        segment = by_id.get(segment_id)
        if segment is None:
            raise ValidationError("day_segment_add: этап не найден")
        credit = _segment_effective_credits(planning).get(segment_id, 0)
        reserved = sum(_lock_reservation(planning, lock) for lock in planning.get("locks", [])
                       if lock.get("manual") and lock.get("segment_id") == segment_id)
        available = max(0, segment["minutes"]-credit-reserved)
        minutes = data.get("minutes", min(300, (available // 15) * 15))
        if type(minutes) is not int or not 15 <= minutes <= 300 or minutes % 15 or minutes > available:
            raise ValidationError("day_segment_add: длительность 15..300 с шагом 15 в пределах остатка этапа")
        _materialize_manual_day(planning, key)
        sizes = []
        left = minutes
        for size in (90, 60, 45, 30, 15):
            while left >= size:
                sizes.append(size)
                left -= size
        if left:
            raise ValidationError("day_segment_add: длительность не раскладывается")
        order = len(_manual_locks(planning, key))
        for size in sizes:
            planning["locks"].append({
                "cube_id": uuid4().hex, "task_id": segment["task_id"],
                "segment_id": segment_id, "date": key, "minutes": size,
                "phase": "preparation", "title": segment["title"],
                "output": segment.get("output", ""), "locked": True,
                "manual": True, "floating": True, "start": None, "end": None,
                "order": order, "is_remainder": False,
            })
            order += 1
        _refresh_manual_day(planning, key)
        return calculate(planning, reason="day_segment_add")
    raise ValidationError("Неизвестное действие этапа")


CONSTRUCTOR_ACTIONS = frozenset({"constructor_day", "constructor_cube", "constructor_split",
    "constructor_select", "constructor_return", "constructor_hide", "constructor_timeline", "calendar_required", "constructor_goal", "constructor_goal_status"})
CONSTRUCTOR_KINDS = frozenset({"task", "prep", "calendar", "weekly_review", "brainalot"})


def _event_identity(event):
    return str(event.get("prep_key") or event.get("occurrence_id") or event["id"])


def _constructor_event_required(planning, event):
    overrides = planning.get("constructor", {}).get("event_required", {})
    return overrides.get(_event_identity(event),
                         _event_category(event, planning["calendar"].get("rules", {})) in {"lesson", "work", "unknown"})


def _constructor_source_selected(planning, kind, source_ref, key):
    ctor = planning.get("constructor", {})
    selected = ctor.get("days", {}).get(key, {}).get("selected_source_ids", [])
    return any(cube_id in selected and cube.get("kind") == kind and cube.get("source_ref") == source_ref
               for cube_id, cube in ctor.get("cubes", {}).items())


def _constructor_selected_date(planning, cube_id):
    lock = next((lock for lock in planning.get("locks", []) if lock["cube_id"] == cube_id), None)
    if lock is not None:
        return lock["date"]
    return next((key for key, day in planning.get("constructor", {}).get("days", {}).items()
                 if cube_id in day.get("selected_source_ids", [])), None)


def _constructor_report(planning, cube):
    if cube["kind"] == "task":
        return planning.get("completed_cubes", {}).get(cube["id"], {})
    if cube["kind"] == "prep":
        return planning.get("prep_progress", {}).get(cube["source_ref"], {})
    return planning.get("constructor", {}).get("source_reports", {}).get(cube["id"], {})


def _constructor_started(planning, cube):
    report = _constructor_report(planning, cube)
    return bool(report.get("percent", 0) or report.get("actual_minutes", 0))


def _constructor_source_load(planning, key):
    """Charge each non-task source once, including returned facts on their date."""
    ctor = planning.get("constructor", {})
    day = ctor.get("days", {}).get(key, {})
    if not day.get("initialized"):
        return 0
    selected = set(day.get("selected_source_ids", []))
    events = {_event_identity(event): event for event in planning["calendar"]["events"]}
    total = 0
    for cube_id, cube in ctor.get("cubes", {}).items():
        if cube["kind"] == "task":
            continue
        report = _constructor_report(planning, cube)
        same_date_report = report if report.get("date") == key else {}
        charge = _constructor_cube_charge(cube["minutes"], same_date_report, selected=cube_id in selected)
        if cube["kind"] == "calendar" and cube["source_ref"] in events and _constructor_event_required(planning, events[cube["source_ref"]]):
            event = events[cube["source_ref"]]
            first = datetime.fromisoformat(event["start"].replace("Z", "+00:00"))
            if first.tzinfo:
                first = first.astimezone(ZoneInfo(planning["settings"]["timezone"]))
            if first.date().isoformat() != key:
                total += charge
            elif cube_id in selected or report.get("date") == key:
                total += max(0, charge-cube["minutes"])
            continue
        total += charge
    return total


def _constructor_cube_charge(minutes, report, *, selected):
    """Energy, not task credit: selected unfinished work keeps its full budget."""
    actual = report.get("actual_minutes", 0)
    percent = report.get("percent", 0)
    if selected:
        return (actual or minutes) if percent == 100 else max(minutes, actual)
    return actual or round(minutes*percent/100)


def _constructor_position(value):
    if not isinstance(value, dict) or set(value) != {"x", "y", "angle"}:
        raise ValidationError("position: нужны x,y,angle")
    for field, limit in (("x", 10000), ("y", 10000), ("angle", 360)):
        n = value[field]
        if type(n) not in (int, float) or not math.isfinite(n) or abs(n) > limit:
            raise ValidationError("position: требуется конечная координата в пределах")


def _constructor_resources(value, *, drafts=False):
    if not isinstance(value, dict):
        raise ValidationError("resources: требуется объект")
    def links(items):
        if not isinstance(items, list) or len(items) > 100:
            raise ValidationError("links: до 100 ссылок")
        seen = set()
        for item in items:
            if not isinstance(item, dict):
                raise ValidationError("link: требуется объект")
            item_id = identifier(item.get("id"), "link.id")
            if item_id in seen:
                raise ValidationError("link.id: повтор")
            seen.add(item_id)
            if not isinstance(item.get("title", ""), str) or len(item.get("title", "")) > 300:
                raise ValidationError("link.title: до 300 символов")
            raw = item.get("url")
            if not isinstance(raw, str) or len(raw) > 4000:
                raise ValidationError("link.url: текст до 4000")
            if drafts:
                continue
            try:
                parsed = urlsplit(raw)
                if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or any(c.isspace() for c in raw):
                    raise ValueError()
                parsed.port
            except ValueError as exc:
                raise ValidationError("link.url: требуется http(s) и hostname без credentials") from exc
    links(value.get("links", []))
    groups = value.get("groups", [])
    if not isinstance(groups, list) or len(groups) > 50:
        raise ValidationError("groups: до 50 групп")
    seen = set()
    for group in groups:
        if not isinstance(group, dict):
            raise ValidationError("group: требуется объект")
        group_id = identifier(group.get("id"), "group.id")
        if group_id in seen:
            raise ValidationError("group.id: повтор")
        seen.add(group_id)
        if not isinstance(group.get("title"), str) or len(group["title"]) > 300 or not drafts and not group["title"].strip():
            raise ValidationError("group.title: текст до 300")
        links(group.get("links", []))


def _validate_constructor(planning):
    ctor = planning.get("constructor")
    if ctor is None:
        return
    if not isinstance(ctor, dict) or ctor.get("version") != 1:
        raise ValidationError("constructor.version: требуется 1")
    cubes, days = ctor.get("cubes", {}), ctor.get("days", {})
    if not isinstance(cubes, dict) or len(cubes) > 10000 or not isinstance(days, dict):
        raise ValidationError("constructor: неверные cubes/days")
    tasks = {task["id"]: task for task in planning["tasks"]}
    segments = {segment["id"]: segment for segment in planning.get("task_segments", [])}
    source_ids = set()
    for cube_id, cube in cubes.items():
        identifier(cube_id, "constructor cube_id")
        if not isinstance(cube, dict) or cube.get("id") != cube_id or cube.get("kind") not in CONSTRUCTOR_KINDS:
            raise ValidationError("constructor cube: неверные id/kind")
        kind = cube["kind"]
        if kind == "task":
            if cube.get("task_id") not in tasks:
                raise ValidationError("constructor task_id: задача не найдена")
            if cube.get("segment_id") is not None and (cube["segment_id"] not in segments or segments[cube["segment_id"]]["task_id"] != cube["task_id"]):
                raise ValidationError("constructor segment_id: чужой этап")
        else:
            ref = identifier(cube.get("source_ref"), "source_ref")
            if (kind, ref) in source_ids:
                raise ValidationError("constructor source_ref: источник уже представлен")
            source_ids.add((kind, ref))
        minutes = _int(cube.get("minutes"), "constructor minutes", 0 if kind == "prep" else 1, 1440 if kind == "calendar" else 240)
        if kind in {"task", "brainalot"} and (minutes < 5 or minutes % 5) and not deleted_cube(planning, cube_id):
            raise ValidationError("constructor minutes: 5..240 шаг5")
        if kind == "prep" and (minutes > 120 or minutes % 5) or kind == "weekly_review" and minutes != 60:
            raise ValidationError("constructor source minutes: неверная длительность")
        for field, maximum, required in (("title", 300, True), ("output", 1000, False), ("comment", 4000, False)):
            value = cube.get(field, "")
            if not isinstance(value, str) or len(value) > maximum or required and not value.strip():
                raise ValidationError(f"constructor {field}: неверный текст")
        steps = cube.get("steps", [])
        if not isinstance(steps, list) or len(steps) > 20 or any(not isinstance(s, str) or not s.strip() or len(s) > 500 for s in steps):
            raise ValidationError("constructor steps: до20 шагов")
        if cube.get("planned_date") is not None and _day(cube["planned_date"], "planned_date") not in _dates(planning["settings"]):
            raise ValidationError("planned_date: вне горизонта")
        _constructor_resources(cube)
        if "resource_drafts" in cube:
            _constructor_resources(cube["resource_drafts"], drafts=True)
        if "week_position" in cube:
            _constructor_position(cube["week_position"])
        if "blueprint_ref" in cube:
            identifier(cube["blueprint_ref"], "blueprint_ref")
    selected_ids = set()
    for key, day in days.items():
        selected_day = _day(key, "constructor day")
        if not isinstance(day, dict) or type(day.get("initialized")) is not bool:
            raise ValidationError("constructor day: неверная дата/initialized")
        if day["initialized"] and selected_day not in _dates(planning["settings"]):
            raise ValidationError("constructor day: рабочий день вне горизонта")
        _int(day.get("goal_percent", 0), "goal_percent", 0, 100)
        if not day["initialized"] and any(day.get(field) for field in ("selected_source_ids", "source_slots", "hidden_cube_ids", "positions", "main_task_id")):
            raise ValidationError("constructor goal-only day: недопустимо рабочее состояние")
        if day.get("main_task_id") is not None and day["main_task_id"] not in tasks:
            raise ValidationError("constructor main_task_id: неизвестная задача")
        if not isinstance(day.get("goal", ""), str) or len(day.get("goal", "")) > 1000:
            raise ValidationError("constructor goal: до1000 символов")
        for field in ("hidden_cube_ids", "selected_source_ids"):
            values = day.get(field, [])
            if not isinstance(values, list) or len(values) != len(set(values)) or any(v not in cubes for v in values):
                raise ValidationError(f"constructor {field}: неизвестные/повторные кубики")
        for cube_id in day.get("selected_source_ids", []):
            if cubes[cube_id]["kind"] == "task" or cube_id in selected_ids:
                raise ValidationError("constructor selection: повтор/неверный источник")
            selected_ids.add(cube_id)
        slots = day.get("source_slots", {})
        if not isinstance(slots, dict) or any(cid not in day.get("selected_source_ids", []) for cid in slots):
            raise ValidationError("constructor source_slots: неверные выбранные ID")
        for start in slots.values():
            _clock(start, "constructor source slot")
        for tray, positions in day.get("positions", {}).items():
            if tray not in {"selected", "possible"} or not isinstance(positions, dict) or len(positions) > 10000:
                raise ValidationError("constructor positions: неверная россыпь")
            for cube_id, position in positions.items():
                identifier(cube_id, "position cube_id")
                _constructor_position(position)
    weeks = ctor.get("weeks", {})
    if not isinstance(weeks, dict) or len(weeks) > 10000:
        raise ValidationError("constructor weeks: требуется map до10000")
    for key, week in weeks.items():
        if _day(key, "goal week").weekday() != 0 or not isinstance(week, dict):
            raise ValidationError("constructor week: требуется понедельник и объект")
        if not isinstance(week.get("goal", ""), str) or len(week.get("goal", "")) > 1000:
            raise ValidationError("constructor week goal: до1000 символов")
        _int(week.get("goal_percent", 0), "weekly goal_percent", 0, 100)
    for record in [*days.values(), *weeks.values()]:
        if record.get("goal_status", "active") not in {"active", "completed", "cancelled"}:
            raise ValidationError("goal_status: active|completed|cancelled")
        member_ids = record.get("closed_cube_ids", [])
        if not isinstance(member_ids, list) or len(member_ids) > 20000 or any(not isinstance(cid, str) for cid in member_ids) or len(member_ids) != len(set(member_ids)):
            raise ValidationError("closed_cube_ids: требуется список уникальных ID")
        for cube_id in member_ids:
            identifier(cube_id, "closed cube_id")
        snapshots = record.get("closed_cube_snapshots", {})
        if not isinstance(snapshots, dict) or len(snapshots) > 20000:
            raise ValidationError("closed_cube_snapshots: требуется map")
        for cube_id, snapshot in snapshots.items():
            identifier(cube_id, "closed snapshot id")
            if not isinstance(snapshot, dict) or snapshot.get("id") != cube_id:
                raise ValidationError("closed cube snapshot: неверный id/объект")
            _int(snapshot.get("minutes"), "closed snapshot minutes", 0, 1440)
            if not isinstance(snapshot.get("title"), str) or len(snapshot["title"]) > 300:
                raise ValidationError("closed snapshot title: до300 символов")
            _constructor_resources(snapshot)
    awards = ctor.get("awards")
    if awards is not None:
        if not isinstance(awards, dict) or awards.get("version") != 1:
            raise ValidationError("awards.version: требуется 1")
        if not isinstance(awards.get("decks"), dict):
            raise ValidationError("awards.decks: требуется объект")
        for kind in ("goals", "cubes"):
            assignments = awards.get(kind)
            if not isinstance(assignments, dict) or len(assignments) > 100000:
                raise ValidationError("awards assignments: требуется map до100000")
            for entity_id, variant in assignments.items():
                identifier(entity_id, "award entity id")
                if variant not in AWARD_VARIANTS:
                    raise ValidationError("award variant: неизвестный вариант")
            deck = awards["decks"].get(kind)
            if not isinstance(deck, dict) or not isinstance(deck.get("remaining"), list):
                raise ValidationError("award deck: требуется remaining[]")
            remaining = deck["remaining"]
            if len(remaining) > len(AWARD_VARIANTS) or any(variant not in AWARD_VARIANTS for variant in remaining) or len(remaining) != len(set(remaining)):
                raise ValidationError("award deck: повтор/неизвестный вариант")
            if "last" not in deck or deck.get("last") is not None and deck["last"] not in AWARD_VARIANTS:
                raise ValidationError("award deck.last: неизвестный вариант")
            _int(deck.get("cycle"), "award deck.cycle", 0, 10**8)
    for field in ("event_required", "source_reports"):
        if not isinstance(ctor.get(field, {}), dict):
            raise ValidationError(f"constructor {field}: требуется map")
    for stable_id, required in ctor.get("event_required", {}).items():
        identifier(stable_id, "event occurrence id")
        if type(required) is not bool:
            raise ValidationError("event_required: требуется bool")
    for cube_id, report in ctor.get("source_reports", {}).items():
        if cube_id not in cubes or cubes[cube_id]["kind"] == "task" or not isinstance(report, dict):
            raise ValidationError("source_reports: неизвестный источник")
        if _day(report.get("date"), "source report date") not in _dates(planning["settings"]):
            raise ValidationError("source report date: вне горизонта")
        _int(report.get("percent"), "source percent", 0, 100)
        _int(report.get("actual_minutes", 0), "source actual_minutes")


def _constructor_init(planning, key):
    ctor = planning.setdefault("constructor", {"version": 1, "cubes": {}, "days": {}, "event_required": {}, "source_reports": {}})
    if ctor["days"].get(key, {}).get("initialized"):
        return ctor["days"][key]
    prior_goal = deepcopy(ctor["days"].get(key, {}))
    old_row = next(row for row in planning["result"]["days"] if row["date"] == key)
    prior_slots = {lock["cube_id"]: {field: deepcopy(lock.get(field)) for field in ("floating", "start", "end")}
                   for lock in planning.get("locks", []) if lock["date"] == key and not lock.get("floating")}
    _materialize_manual_day(planning, key)
    for lock in _manual_locks(planning, key):
        if lock["cube_id"] in prior_slots:
            lock.update(prior_slots[lock["cube_id"]])
    day = {"initialized": True, "main_task_id": old_row.get("focus_task_id"), "goal": old_row.get("goal") or "",
           "hidden_cube_ids": [], "selected_source_ids": [], "positions": {"selected": {}, "possible": {}}, "source_slots": {}}
    day.update(prior_goal)
    day["initialized"] = True
    day.setdefault("goal_percent", 0)
    ctor["days"][key] = day
    for lock in _manual_locks(planning, key):
        cube_id = lock["cube_id"]
        # Preserve historical short tails without changing their duration contract.
        if cube_id not in ctor["cubes"] and lock["minutes"] >= 5 and lock["minutes"] % 5 == 0:
            ctor["cubes"][cube_id] = {"id": cube_id, "kind": "task", "task_id": lock["task_id"],
                **({"segment_id": lock["segment_id"]} if lock.get("segment_id") else {}), "minutes": lock["minutes"],
                "title": lock.get("title") or next(t["title"] for t in planning["tasks"] if t["id"] == lock["task_id"]),
                "output": lock.get("output", ""), "steps": deepcopy(lock.get("steps", [])), "comment": "", "links": [], "groups": []}
            if detail := planning.get("cube_details", {}).get(cube_id):
                ctor["cubes"][cube_id].update(deepcopy(detail))
            lock["constructor"] = True
    for prep in old_row.get("prep_cubes", []):
        cube_id = "constructor:"+prep["id"]
        ctor["cubes"].setdefault(cube_id, {"id": cube_id, "kind": "prep", "source_ref": prep["id"], "minutes": prep["minutes"],
            "title": "Подготовка к уроку "+prep.get("lesson_start", ""), "output": "", "steps": [], "comment": "", "planned_date": key, "links": [], "groups": []})
        if _constructor_started(planning, ctor["cubes"][cube_id]):
            day["selected_source_ids"].append(cube_id)
    zone = ZoneInfo(planning["settings"]["timezone"])
    for event in planning["calendar"]["events"]:
        if event.get("all_day") or event.get("status") == "cancelled":
            continue
        first = datetime.fromisoformat(event["start"].replace("Z", "+00:00")).astimezone(zone)
        if first.date().isoformat() != key:
            continue
        stable_id = _event_identity(event)
        cube_id = "constructor:event:"+sha256(stable_id.encode()).hexdigest()[:24]
        if not any(c.get("kind") == "calendar" and c.get("source_ref") == stable_id for c in ctor["cubes"].values()):
            duration = int((datetime.fromisoformat(event["end"].replace("Z", "+00:00"))-datetime.fromisoformat(event["start"].replace("Z", "+00:00"))).total_seconds()/60)
            if duration > 0:
                ctor["cubes"][cube_id] = {"id": cube_id, "kind": "calendar", "source_ref": stable_id,
                    "minutes": duration, "title": event.get("title") or event.get("summary") or "Calendar",
                    "output": "", "steps": [], "comment": "", "planned_date": key, "links": [], "groups": []}
    review = old_row.get("weekly_review")
    if review:
        cube_id = "constructor:"+review["id"]
        ctor["cubes"].setdefault(cube_id, {"id": cube_id, "kind": "weekly_review", "source_ref": review["id"], "minutes": 60,
            "title": WEEKLY_REVIEW_TITLE, "output": "", "steps": [], "comment": "", "planned_date": key, "links": [], "groups": []})
        if planning.get("weekly_review_done", {}).get(review["id"]):
            ctor["source_reports"][cube_id] = {"date": key, "percent": 100, "actual_minutes": 0}
    return day


def _constructor_cube_update(planning, data):
    allowed = {"cube_id", "kind", "task_id", "segment_id", "source_ref", "minutes", "title", "output", "steps", "comment", "planned_date", "links", "groups", "week_position", "resource_drafts", "blueprint_ref"}
    if set(data)-allowed or "cube_id" not in data:
        raise ValidationError("constructor_cube: неверные поля")
    cube_id = identifier(data["cube_id"], "cube_id")
    if deleted_cube(planning, cube_id, data.get("blueprint_ref")):
        raise ValidationError("Кубик удалён; повторное создание запрещено")
    if data.get("blueprint_ref") and any(row["id"] == data["blueprint_ref"] and row.get("allocation_stale")
                                         for row in planning.get("result", {}).get("cubes", [])):
        raise ValidationError("Прогноз этапа устарел после удаления; явно пересчитайте план или нарежьте остаток")
    ctor = planning.setdefault("constructor", {"version": 1, "cubes": {}, "days": {}, "event_required": {}, "source_reports": {}})
    previous = ctor["cubes"].get(cube_id)
    cube = deepcopy(previous) if previous else {"id": cube_id, "minutes": 60, "output": "", "steps": [], "comment": "", "links": [], "groups": []}
    for field, value in data.items():
        if field != "cube_id":
            cube[field] = deepcopy(value)
    if "planned_date" in data:
        cube["planned_date_configured"] = data["planned_date"] is not None
    existing_lock = next((lock for lock in planning.get("locks", []) if lock["cube_id"] == cube_id), None)
    existing_report = planning.get("completed_cubes", {}).get(cube_id)
    if not previous and (existing_lock or existing_report):
        raise ValidationError("cube_id: уже используется; сначала инициализируйте исходный день")
    if previous:
        if any(cube.get(field) != previous.get(field) for field in ("kind", "task_id", "segment_id", "source_ref")):
            raise ValidationError("constructor cube identity: нельзя менять источник")
        if _constructor_started(planning, previous) and cube["minutes"] != previous["minutes"]:
            raise ValidationError("Начатую длительность нельзя менять")
        report = _constructor_report(planning, previous)
        if _constructor_started(planning, previous) and cube.get("planned_date") not in (None, report.get("date")):
            raise ValidationError("Факт нельзя перенести на другую дату")
    elif cube.get("kind") == "task" and _scope_exempt(planning, cube.get("task_id"), cube.get("planned_date")):
        raise ValidationError("Задача освобождена от будущей работы")
    if cube.get("kind") in {"calendar", "prep", "weekly_review"}:
        if previous and cube["minutes"] != previous["minutes"]:
            if cube["kind"] != "prep":
                raise ValidationError("Длительность события/разбора определяется источником")
        ref = cube.get("source_ref")
        if cube["kind"] == "prep":
            prep = next((p for day in planning["result"]["days"] for p in day.get("prep_cubes", []) if p["id"] == ref), None)
            if prep is None:
                raise ValidationError("Подготовка не найдена")
            planning.setdefault("prep_overrides", {})[ref] = cube["minutes"]
            cube["planned_date"] = prep["date"]
        elif cube["kind"] == "calendar":
            event = next((e for e in planning["calendar"]["events"] if ref in {_event_identity(e), e["id"]}), None)
            if event is None or event.get("all_day"):
                raise ValidationError("Calendar: требуется существующее событие со временем")
            cube["source_ref"] = _event_identity(event)
            cube["minutes"] = int((datetime.fromisoformat(event["end"].replace("Z", "+00:00"))-datetime.fromisoformat(event["start"].replace("Z", "+00:00"))).total_seconds()/60)
        elif not any(review["id"] == ref for review in planning["result"].get("weekly_reviews", [])):
            raise ValidationError("Недельный разбор не найден")
    ctor["cubes"][cube_id] = cube
    _validate_constructor(planning)
    lock = next((lock for lock in planning.get("locks", []) if lock["cube_id"] == cube_id), None)
    if lock:
        lock.update({field: deepcopy(cube[field]) for field in ("minutes", "title", "output", "steps")})
        if not lock.get("floating"):
            lock["end"] = _hhmm(_clock(lock["start"], "start")+cube["minutes"])
    if cube_id in planning.get("cube_details", {}):
        detail = planning["cube_details"][cube_id]
        for field in ("title", "output", "steps"):
            if field in data:
                detail[field] = deepcopy(cube[field])
        _validate_cube_detail(detail)
    return cube


def _constructor_allocation(planning, previous=None, *, check=True):
    """Proposals reserve an estimate once; selecting the same ID adds no estimate."""
    ctor = planning.get("constructor", {})
    task_alloc, segment_alloc = defaultdict(int), defaultdict(int)
    for cube in ctor.get("cubes", {}).values():
        if cube["kind"] != "task":
            continue
        if deleted_cube(planning, cube["id"]):
            continue
        if _scope_exempt(planning, cube["task_id"]):
            continue
        report = _constructor_report(planning, cube)
        amount = max(0, cube["minutes"]-round(cube["minutes"]*report.get("percent", 0)/100))
        task_alloc[cube["task_id"]] += amount
        if cube.get("segment_id"):
            segment_alloc[cube["segment_id"]] += amount
    for lock in planning.get("locks", []):
        if lock.get("manual") and lock["cube_id"] not in ctor.get("cubes", {}):
            task_alloc[lock["task_id"]] += _lock_reservation(planning, lock)
            if lock.get("segment_id"):
                segment_alloc[lock["segment_id"]] += _lock_reservation(planning, lock)
    remaining = _remaining(planning)
    credits = _segment_effective_credits(planning)
    retired = _retired_minutes(planning, by_segment=True)
    segments = {s["id"]: s for s in planning.get("task_segments", [])}
    overflow = {"task:"+tid: max(0, n-remaining.get(tid, 0)) for tid, n in task_alloc.items()}
    overflow.update({"segment:"+sid: max(0, n-segments[sid]["minutes"]+credits.get(sid, 0)+retired[sid]) for sid, n in segment_alloc.items()})
    overflow = {key: value for key, value in overflow.items() if value}
    baseline = _constructor_allocation(previous, check=False) if previous is not None else {}
    if check and any(value > baseline.get(key, 0) for key, value in overflow.items()):
        raise ValidationError("Предложения превышают остаток задачи или этапа")
    return overflow


def _constructor_budget_guard(planning, previous=None):
    before_days = {day["date"]: day for day in (previous or {}).get("result", {}).get("days", [])}
    for day in planning.get("result", {}).get("days", []):
        key = day["date"]
        meta = planning.get("constructor", {}).get("days", {}).get(key)
        if not meta or not meta.get("initialized"):
            continue
        prior = before_days.get(key, {})
        if day.get("over_cap_minutes", 0) <= prior.get("over_cap_minutes", 0):
            continue
        cubes = planning["constructor"]["cubes"]
        unstarted = any(_lock_reservation(planning, lock) for lock in _manual_locks(planning, key))
        unstarted |= any(not _constructor_started(planning, cubes[cid]) for cid in meta.get("selected_source_ids", []))
        if unstarted:
            raise ValidationError("Бюджета недостаточно: сначала верните или переставьте не начатый кубик")


def _constructor_return(planning, cube):
    key = _constructor_selected_date(planning, cube["id"])
    if key is None:
        return
    _builder_date(planning, key)
    planning["locks"] = [lock for lock in planning.get("locks", []) if lock["cube_id"] != cube["id"]]
    day = planning["constructor"]["days"][key]
    day["selected_source_ids"] = [cid for cid in day["selected_source_ids"] if cid != cube["id"]]
    day.setdefault("source_slots", {}).pop(cube["id"], None)
    _normalize_manual_order(planning, key)


def _constructor_snap(planning, cube, key, desired):
    if desired is None:
        return None
    wanted = _clock(desired, "constructor start")
    ledger = _ledger(planning, _dates(planning["settings"]))
    free = list(ledger[key]["free_windows"])
    for lock in planning.get("locks", []):
        if lock["date"] == key and lock["cube_id"] != cube["id"] and not lock.get("floating"):
            free = _subtract(free, (_clock(lock["start"], "start"), _clock(lock["end"], "end")))
    ctor = planning["constructor"]
    for cid, start in ctor["days"][key].get("source_slots", {}).items():
        if cid != cube["id"] and cid in ctor["days"][key]["selected_source_ids"]:
            left = _clock(start, "source slot")
            free = _subtract(free, (left, left+ctor["cubes"][cid]["minutes"]))
    candidates = [minute for a, b in free for minute in range(((a+4)//5)*5, b-cube["minutes"]+1, 5)]
    if not candidates:
        raise ValidationError("Нет свободного интервала для кубика")
    return _hhmm(min(candidates, key=lambda minute: (abs(minute-wanted), minute)))


def _constructor_validate_slots(planning):
    ledger = _ledger(planning, _dates(planning["settings"]))
    ctor = planning.get("constructor", {})
    for key, day in ctor.get("days", {}).items():
        if not day.get("initialized"):
            continue
        occupied = []
        for lock in _manual_locks(planning, key):
            if not lock.get("floating"):
                occupied.append((_clock(lock["start"], "start"), _clock(lock["end"], "end")))
        for cube_id, start in day.get("source_slots", {}).items():
            if cube_id not in day.get("selected_source_ids", []):
                raise ValidationError("source slot: кубик не выбран")
            left = _clock(start, "source slot")
            occupied.append((left, left+ctor["cubes"][cube_id]["minutes"]))
        for left, right in occupied:
            if not any(a <= left < right <= b for a, b in ledger[key]["free_windows"]):
                raise ValidationError("constructor slot: пересечение события или вне рабочего окна")
        if any(second[0] < first[1] for first, second in zip(sorted(occupied), sorted(occupied)[1:])):
            raise ValidationError("constructor slot: пересечение кубиков")


def _constructor_set_slot(planning, cube, key, desired):
    if cube["kind"] == "calendar":
        event = next(e for e in planning["calendar"]["events"] if _event_identity(e) == cube["source_ref"])
        if _constructor_event_required(planning, event):
            raise ValidationError("Обязательное Calendar событие неподвижно")
    start = _constructor_snap(planning, cube, key, desired)
    if cube["kind"] == "task":
        lock = next(lock for lock in planning["locks"] if lock["cube_id"] == cube["id"])
        lock.update(floating=start is None, start=start, end=None if start is None else _hhmm(_clock(start, "start")+cube["minutes"]))
    else:
        slots = planning["constructor"]["days"][key].setdefault("source_slots", {})
        if start is None:
            slots.pop(cube["id"], None)
        else:
            slots[cube["id"]] = start


def _constructor_select(planning, cube, key, start=None):
    overflow = _constructor_allocation(planning, check=False)
    if cube["kind"] == "task" and not _constructor_started(planning, cube) and (
            "task:"+cube["task_id"] in overflow or cube.get("segment_id") and "segment:"+cube["segment_id"] in overflow):
        raise ValidationError("Предложение устарело после отчёта: превышен остаток задачи/этапа")
    if cube["kind"] == "task" and _scope_exempt(planning, cube["task_id"], key) and not _constructor_started(planning, cube):
        raise ValidationError("Задача освобождена от будущей работы")
    if cube["kind"] == "calendar":
        event = next(e for e in planning["calendar"]["events"] if _event_identity(e) == cube["source_ref"])
        begins = datetime.fromisoformat(event["start"].replace("Z", "+00:00")).astimezone(ZoneInfo(planning["settings"]["timezone"]))
        if begins.date().isoformat() != key:
            raise ValidationError("Calendar occurrence относится к исходной дате события")
    day = _constructor_init(planning, key)
    report = _constructor_report(planning, cube)
    if _constructor_started(planning, cube) and report.get("date") != key:
        raise ValidationError("Факт нельзя перенести на другую дату")
    current = _constructor_selected_date(planning, cube["id"])
    if current is not None and current != key:
        _constructor_return(planning, cube)
    if cube["kind"] == "task":
        if not any(lock["cube_id"] == cube["id"] for lock in planning["locks"]):
            task = next(task for task in planning["tasks"] if task["id"] == cube["task_id"])
            planning["locks"].append({"cube_id": cube["id"], "task_id": cube["task_id"],
                **({"segment_id": cube["segment_id"]} if cube.get("segment_id") else {}), "date": key,
                "minutes": cube["minutes"], "title": cube["title"], "output": cube.get("output", ""), "steps": deepcopy(cube.get("steps", [])),
                "phase": "review" if _remaining(planning)[task["id"]] <= task.get("review_minutes", 0) else "preparation",
                "manual": True, "constructor": True, "locked": True, "floating": True, "start": None, "end": None,
                "order": len(_manual_locks(planning, key))})
    else:
        if cube["minutes"] == 0:
            raise ValidationError("Кубик нулевой длительности нельзя выбрать")
        if cube["kind"] == "prep" and cube.get("planned_date") != key:
            raise ValidationError("Подготовка относится к дате урока")
        if cube["id"] not in day["selected_source_ids"]:
            day["selected_source_ids"].append(cube["id"])
    day["hidden_cube_ids"] = [cid for cid in day["hidden_cube_ids"] if cid != cube["id"]]
    if start is not None:
        _constructor_set_slot(planning, cube, key, start)


def _goal_cube_catalog(planning):
    """Expose stable report metadata, including cubes no longer in a day view."""
    ctor = planning.get("constructor", {})
    tasks = {task["id"]: task for task in planning["tasks"]}
    existing = {cube["id"]: cube for cube in planning.get("result", {}).get("cubes", [])}
    catalog = {}
    snapshots = {}
    goal_records = [record for records in (ctor.get("days", {}), ctor.get("weeks", {})) for record in records.values()]
    for record in sorted(goal_records, key=lambda value: value.get("closed_at", "")):
        snapshots.update(deepcopy(record.get("closed_cube_snapshots", {})))
    fixed_calendar_refs = {_event_identity(event) for event in planning["calendar"]["events"]
                           if _constructor_event_required(planning, event)}
    for cube_id, cube in ctor.get("cubes", {}).items():
        report = _constructor_report(planning, cube)
        deleted = planning.get("cube_deletions", {}).get(cube_id)
        catalog[cube_id] = {**deepcopy(cube), "selected_date": deleted.get("selected_date") if deleted else _constructor_selected_date(planning, cube_id),
            "progress_percent": report.get("percent", 0), "actual_minutes": report.get("actual_minutes", 0),
            "report_date": report.get("date"), "fixed_calendar": cube["kind"] == "calendar" and cube["source_ref"] in fixed_calendar_refs}
        if cube.get("task_id") in tasks:
            catalog[cube_id].setdefault("resources", deepcopy(tasks[cube["task_id"]].get("resources", [])))
    for cube_id, report in planning.get("completed_cubes", {}).items():
        if cube_id in catalog:
            continue
        known = existing.get(cube_id, {})
        detail = planning.get("cube_details", {}).get(cube_id, {})
        catalog[cube_id] = {**deepcopy(known), **deepcopy(detail), "id": cube_id, "kind": "task",
            "task_id": report["task_id"], "title": detail.get("title") or known.get("title") or tasks[report["task_id"]]["title"],
            "minutes": report["planned_minutes"], "progress_percent": report["percent"],
            "actual_minutes": report.get("actual_minutes", 0), "report_date": report["date"],
            "selected_date": _constructor_selected_date(planning, cube_id), "fixed_calendar": False}
        catalog[cube_id].setdefault("resources", deepcopy(tasks[report["task_id"]].get("resources", [])))
    # Legacy manually selected cubes can have no report yet. AUTO forecasts are
    # never guessed to be goal members.
    for lock in planning.get("locks", []):
        cube_id = lock["cube_id"]
        if cube_id in catalog or not (lock.get("manual") or lock.get("locked")):
            continue
        known = existing.get(cube_id, {})
        catalog[cube_id] = {**deepcopy(lock), **deepcopy(known), "id": cube_id, "kind": "task",
            "title": known.get("title") or lock.get("title") or tasks[lock["task_id"]]["title"],
            "progress_percent": 0, "actual_minutes": 0, "report_date": None,
            "selected_date": lock["date"], "fixed_calendar": False}
        catalog[cube_id].setdefault("resources", deepcopy(tasks[lock["task_id"]].get("resources", [])))
    represented_preps = {cube.get("source_ref") for cube in catalog.values() if cube.get("kind") == "prep"}
    known_preps = {prep["id"]: prep for day in planning.get("result", {}).get("days", []) for prep in day.get("prep_cubes", [])}
    for prep_id, report in planning.get("prep_progress", {}).items():
        if prep_id in represented_preps or not report.get("date"):
            continue
        prep = known_preps.get(prep_id, {})
        catalog[prep_id] = {"id": prep_id, "kind": "prep", "source_ref": prep_id, "title": "Подготовка к уроку",
            "minutes": prep.get("minutes", planning.get("prep_overrides", {}).get(prep_id, planning["settings"]["prep_minutes"])),
            "progress_percent": report["percent"], "actual_minutes": report.get("actual_minutes", 0),
            "report_date": report["date"], "selected_date": None, "fixed_calendar": False}
    for cube_id, snapshot in snapshots.items():
        if cube_id in catalog:
            # Durable descriptive fields are a fallback; all live/report values
            # and current source metadata take precedence over closure history.
            catalog[cube_id] = {**snapshot, **catalog[cube_id], "archived_goal_member": False}
        else:
            catalog[cube_id] = {**snapshot, "selected_date": None, "report_date": None,
                "progress_percent": 0, "actual_minutes": 0, "archived_goal_member": True}
    # Preserve a legacy prep ID already captured by a closed goal, while new
    # active membership/trophy projection uses the canonical source only once.
    closed_ids = {cid for records in (ctor.get("days", {}), ctor.get("weeks", {}))
                  for record in records.values() for cid in record.get("closed_cube_ids", [])}
    for cube_id, cube in list(catalog.items()):
        cube["canonical_id"] = cube_id
        if cube.get("kind") == "prep" and cube.get("source_ref") in closed_ids and cube["source_ref"] != cube_id:
            catalog[cube["source_ref"]] = {**deepcopy(cube), "id": cube["source_ref"], "alias_of": cube_id}
    return catalog


def _goal_member_ids(scope, key, record, catalog):
    if record.get("goal_status", "active") in {"completed", "cancelled"}:
        return list(record.get("closed_cube_ids", []))
    last = key if scope == "day" else (date.fromisoformat(key)+timedelta(days=6)).isoformat()
    def belongs(value):
        return isinstance(value, str) and key <= value <= last
    members = []
    for cube_id, cube in catalog.items():
        if cube.get("fixed_calendar") or cube.get("alias_of") or cube.get("archived_goal_member"):
            continue
        selected_or_reported = belongs(cube.get("selected_date")) or belongs(cube.get("report_date"))
        configured_week = (scope == "week" and belongs(cube.get("planned_date")) and
                           (cube.get("kind") in {"task", "brainalot"} or cube.get("planned_date_configured")))
        if selected_or_reported or configured_week:
            members.append(cube_id)
    return sorted(members)


def _goal_read_row(scope, key, record, catalog):
    member_ids = _goal_member_ids(scope, key, record, catalog)
    members = [catalog.get(cid, {"minutes": 0, "progress_percent": 0}) for cid in member_ids]
    total = sum(cube["minutes"] for cube in members)
    weighted = sum(cube["minutes"]*cube.get("progress_percent", 0) for cube in members)
    status = record.get("goal_status", "active")
    complete_ids = [cid for cid in member_ids if catalog.get(cid, {}).get("progress_percent", 0) == 100]
    achieved = bool(member_ids) and len(complete_ids) == len(member_ids)
    percent = (100 if achieved else min(99.9, round(weighted/total))) if total else 0
    return {"id": scope+":"+key, "scope": scope, "date": key, "title": record.get("goal", ""),
        "percent": percent, "status": status, "achieved": achieved,
        "display_status": "achieved" if status == "active" and achieved else status,
        "cube_ids": member_ids, "completed_cube_ids": complete_ids,
        "unfinished_cube_ids": [cid for cid in member_ids if cid not in complete_ids],
        "total_minutes": total, "completed_minutes": round(weighted/100, 2)}


def _refresh_constructor_result(planning):
    ctor = planning.get("constructor", {})
    days, cubes = {}, []
    overflow = _constructor_allocation(planning, check=False)
    for cube_id, cube in ctor.get("cubes", {}).items():
        if deleted_cube(planning, cube_id):
            continue
        key = _constructor_selected_date(planning, cube_id)
        report = _constructor_report(planning, cube)
        lock = next((lock for lock in planning.get("locks", []) if lock["cube_id"] == cube_id), {})
        start = lock.get("start") if cube["kind"] == "task" else ctor.get("days", {}).get(key, {}).get("source_slots", {}).get(cube_id)
        stale = cube["kind"] == "task" and ("task:"+cube["task_id"] in overflow or bool(cube.get("segment_id") and "segment:"+cube["segment_id"] in overflow))
        cubes.append({**deepcopy(cube), "allocation_stale": stale, "scope_exempt": cube["kind"] == "task" and _scope_exempt(planning, cube["task_id"]), "selected_date": key, "start": start,
                      "end": _hhmm(_clock(start, "start")+cube["minutes"]) if start else None,
                      "progress_percent": report.get("percent", 0), "actual_minutes": report.get("actual_minutes", 0),
                      "report_date": report.get("date")})
    zone = ZoneInfo(planning["settings"]["timezone"])
    for row in planning.get("result", {}).get("days", []):
        key = row["date"]
        meta = ctor.get("days", {}).get(key)
        events = []
        for event in planning["calendar"]["events"]:
            first = datetime.fromisoformat(event["start"].replace("Z", "+00:00"))
            last = datetime.fromisoformat(event["end"].replace("Z", "+00:00"))
            if first.tzinfo:
                first, last = first.astimezone(zone), last.astimezone(zone)
            if first.date().isoformat() <= key < last.date().isoformat() or first.date().isoformat() == key:
                events.append({**deepcopy(event), "stable_id": _event_identity(event), "required": _constructor_event_required(planning, event)})
        selected = [cube["id"] for cube in cubes if cube["selected_date"] == key]
        hidden = (meta or {}).get("hidden_cube_ids", [])
        required_refs = {event["stable_id"] for event in events if event["required"]}
        possible = [cube["id"] for cube in cubes if not cube["selected_date"] and cube["id"] not in hidden
                    and not cube.get("scope_exempt")
                    and not (cube["kind"] == "calendar" and cube["source_ref"] in required_refs)
                    and cube.get("planned_date") in (None, key) and (not cube.get("report_date") or cube["report_date"] == key)]
        reports = [(cube, _constructor_report(planning, cube)) for cube in ctor.get("cubes", {}).values()]
        actual = sum(report.get("actual_minutes", 0) for cube, report in reports if report.get("date") == key)
        credit = sum(round(cube["minutes"]*report.get("percent", 0)/100) for cube, report in reports if report.get("date") == key)
        days[key] = {**deepcopy(meta or {}), "date": key, "initialized": bool(meta and meta.get("initialized")),
            "goal": (meta or {}).get("goal", row.get("goal") or ""), "goal_percent": (meta or {}).get("goal_percent", 0), "selected_cube_ids": selected,
            "possible_cube_ids": possible, "hidden_cube_ids": deepcopy(hidden), "actual_minutes": actual,
            "credited_minutes": credit, "selected_minutes": sum(cube["minutes"] for cube in cubes if cube["selected_date"] == key),
            "fixed_minutes": row.get("fixed_minutes", 0), "total_work_minutes": row.get("builder_total_work_minutes", row["total_work_minutes"]),
            "budget_minutes": row["day_limit_minutes"], "available_minutes": row.get("builder_available_minutes", row.get("unused_capacity_minutes", 0)),
            "over_cap_minutes": row.get("builder_over_cap_minutes", row.get("over_cap_minutes", 0)), "events": events}
    goals = []
    goal_cubes = _goal_cube_catalog(planning)
    goal_records = {"day": dict(ctor.get("days", {})), "week": dict(ctor.get("weeks", {}))}
    for goal_cube in goal_cubes.values():
        if goal_cube.get("fixed_calendar") or goal_cube.get("archived_goal_member"):
            continue
        known_dates = [goal_cube.get("selected_date"), goal_cube.get("report_date")]
        for known_date in known_dates:
            if known_date:
                goal_records["day"].setdefault(known_date, {})
                known = date.fromisoformat(known_date)
                goal_records["week"].setdefault((known-timedelta(days=known.weekday())).isoformat(), {})
        planned = goal_cube.get("planned_date")
        if planned and (goal_cube.get("kind") in {"task", "brainalot"} or goal_cube.get("planned_date_configured")):
            known = date.fromisoformat(planned)
            goal_records["week"].setdefault((known-timedelta(days=known.weekday())).isoformat(), {})
    for scope, records in goal_records.items():
        for key, record in sorted(records.items()):
            goals.append(_goal_read_row(scope, key, record, goal_cubes))
    for goal in goals:
        if goal["scope"] == "day" and goal["date"] in days:
            days[goal["date"]]["goal_percent"] = goal["percent"]
    weeks = deepcopy(goal_records["week"])
    for goal in goals:
        if goal["scope"] == "week":
            weeks[goal["date"]].setdefault("goal", goal["title"])
            weeks[goal["date"]]["goal_percent"] = goal["percent"]
    assignments = ctor.get("awards", {})
    for goal in goals:
        goal["award_variant"] = assignments.get("goals", {}).get(goal["id"])
    for cube_id, cube in goal_cubes.items():
        cube["award_variant"] = assignments.get("cubes", {}).get(cube.get("canonical_id", cube_id))
    for cube in cubes:
        cube["award_variant"] = assignments.get("cubes", {}).get(cube["id"])
    planning["result"]["constructor"] = {"version": 1, "days": days, "cubes": cubes, "allocation_overflow": overflow,
        "goals": goals, "weeks": weeks, "goal_cubes": goal_cubes}


def _eligible_award_ids(planning):
    if planning is None:
        return {"goals": set(), "cubes": set()}
    projected = deepcopy(planning)
    _refresh_constructor_result(projected)
    model = projected["result"]["constructor"]
    goals = {goal["id"] for goal in model["goals"] if goal["status"] != "cancelled" and
             (goal["status"] == "completed" or goal["achieved"])}
    cubes = {cube.get("canonical_id", cube_id) for cube_id, cube in model["goal_cubes"].items()
             if cube.get("progress_percent") == 100 and not cube.get("alias_of") and not cube.get("archived_goal_member")
             and not deleted_cube(planning, cube_id)}
    return {"goals": goals, "cubes": cubes}


def _draw_award(deck):
    if not deck["remaining"]:
        order = list(AWARD_VARIANTS)
        SystemRandom().shuffle(order)
        if order[0] == deck["last"]:
            order[0], order[1] = order[1], order[0]
        deck["remaining"] = order
        deck["cycle"] += 1
    variant = deck["remaining"].pop(0)
    deck["last"] = variant
    return variant


def update_constructor_awards(planning, previous, *, backfill=False, allow_new=True):
    """Assign only on typed writes; receipts persist the exact shuffled result."""
    eligible = _eligible_award_ids(planning)
    prior_eligible = {"goals": set(), "cubes": set()} if backfill else _eligible_award_ids(previous)
    pending = {kind: eligible[kind]-prior_eligible[kind] if allow_new or backfill else set() for kind in eligible}
    ctor = planning.get("constructor")
    awards = (ctor or {}).get("awards")
    if awards is None and not any(pending.values()):
        return
    if ctor is None:
        ctor = planning.setdefault("constructor", {"version": 1, "cubes": {}, "days": {}, "event_required": {}, "source_reports": {}})
    if awards is None:
        awards = ctor.setdefault("awards", {"version": 1, "goals": {}, "cubes": {},
            "decks": {kind: {"remaining": [], "last": None, "cycle": 0} for kind in ("goals", "cubes")}})
    # A prep can acquire its constructor wrapper after a legacy completion.
    # Move that alias key, preserving its award and consuming no second draw.
    for cube_id, cube in _goal_cube_catalog(planning).items():
        if cube.get("kind") == "prep" and not cube.get("alias_of"):
            legacy_id = cube.get("source_ref")
            if legacy_id != cube_id and legacy_id in awards["cubes"]:
                awards["cubes"].setdefault(cube_id, awards["cubes"].pop(legacy_id))
    for kind in ("goals", "cubes"):
        for entity_id in sorted(pending[kind]):
            if entity_id not in awards[kind]:
                awards[kind][entity_id] = _draw_award(awards["decks"][kind])
    _validate_constructor(planning)


def _constructor_progress(planning, data):
    if set(data)-{"cube_id", "percent", "actual_minutes"} or not {"cube_id", "percent"} <= set(data):
        raise ValidationError("cube_progress: неверные поля")
    planning = deepcopy(planning)
    cube = planning["constructor"]["cubes"][data["cube_id"]]
    prior = _constructor_report(planning, cube)
    key = _constructor_selected_date(planning, cube["id"]) or prior.get("date") or cube.get("planned_date")
    if key is None:
        raise ValidationError("Для факта требуется выбранная/назначенная дата")
    percent = _int(data["percent"], "percent", 0, 100)
    actual = _int(data.get("actual_minutes", prior.get("actual_minutes", 0)), "actual_minutes")
    report = {**deepcopy(prior), "date": key, "percent": percent, "actual_minutes": actual}
    if cube["kind"] == "task":
        report.update(task_id=cube["task_id"], planned_minutes=cube["minutes"])
        if cube.get("segment_id"):
            report["segment_id"] = cube["segment_id"]
        planning.setdefault("completed_cubes", {})[cube["id"]] = report
    elif cube["kind"] == "prep":
        planning.setdefault("prep_progress", {})[cube["source_ref"]] = report
    else:
        planning["constructor"].setdefault("source_reports", {})[cube["id"]] = report
    for day_key in planning["constructor"]["days"]:
        if planning["constructor"]["days"][day_key].get("initialized"):
            _refresh_manual_day(planning, day_key)
    _refresh_result_remaining(planning)
    planning["result"]["task_pool"] = _task_pool(planning, planning["result"]["cubes"], _dates(planning["settings"]))
    _refresh_constructor_result(planning)
    planning["needs_recalculation"] = True
    planning["last_update"] = {"action": "cube_progress", "at": datetime.now(timezone.utc).isoformat()}
    return planning


def apply_constructor(planning, action, data):
    original = planning
    planning = deepcopy(planning)
    ctor = planning.setdefault("constructor", {"version": 1, "cubes": {}, "days": {}, "event_required": {}, "source_reports": {}})
    if action == "constructor_day":
        if set(data)-{"date", "main_task_id", "goal", "replace_unstarted", "positions"} or "date" not in data:
            raise ValidationError("constructor_day: неверные поля")
        key = _builder_date(planning, data["date"])
        day = _constructor_init(planning, key)
        old_main = day.get("main_task_id")
        if "main_task_id" in data:
            if data["main_task_id"] is not None and data["main_task_id"] not in {t["id"] for t in planning["tasks"]}:
                raise ValidationError("main_task_id: неизвестная задача")
            day["main_task_id"] = data["main_task_id"]
        if "goal" in data:
            day["goal"] = deepcopy(data["goal"])
        if type(data.get("replace_unstarted", False)) is not bool:
            raise ValidationError("replace_unstarted: требуется bool")
        if data.get("replace_unstarted") and old_main != day.get("main_task_id") and day.get("main_task_id"):
            replace = [lock for lock in _manual_locks(planning, key) if lock["task_id"] == old_main
                       and lock.get("floating") and lock["cube_id"] in ctor["cubes"] and not _constructor_started(planning, ctor["cubes"][lock["cube_id"]])]
            for lock in replace:
                old_cube = ctor["cubes"][lock["cube_id"]]
                _constructor_return(planning, old_cube)
                new_id = "constructor:"+uuid4().hex
                new_cube = _constructor_cube_update(planning, {"cube_id": new_id, "kind": "task", "task_id": day["main_task_id"],
                    "minutes": old_cube["minutes"], "title": next(t["title"] for t in planning["tasks"] if t["id"] == day["main_task_id"]), "planned_date": key})
                _constructor_select(planning, new_cube, key)
        if "positions" in data:
            if not isinstance(data["positions"], dict):
                raise ValidationError("positions: требуется объект")
            for tray, positions in data["positions"].items():
                if tray not in {"selected", "possible"} or not isinstance(positions, dict):
                    raise ValidationError("positions: неверная россыпь")
                day["positions"].setdefault(tray, {}).update(deepcopy(positions))
    elif action == "constructor_cube":
        _constructor_cube_update(planning, data)
    elif action == "constructor_split":
        if set(data)-{"cube_id", "minutes", "new_cube_id"} or not {"cube_id", "minutes"} <= set(data):
            raise ValidationError("constructor_split: неверные поля")
        cube = ctor["cubes"].get(data["cube_id"])
        if cube is None or cube["kind"] != "task" or _constructor_started(planning, cube) or _constructor_selected_date(planning, cube["id"]):
            raise ValidationError("Разделить можно неначатое невыбранное предложение задачи")
        minutes = _int(data["minutes"], "split minutes", 5, cube["minutes"]-5)
        if minutes % 5:
            raise ValidationError("split: шаг5")
        new_id = identifier(data.get("new_cube_id", "constructor:"+uuid4().hex), "new_cube_id")
        if new_id in ctor["cubes"]:
            raise ValidationError("new_cube_id: уже существует")
        second = deepcopy(cube)
        second.update(id=new_id, minutes=cube["minutes"]-minutes)
        second.pop("week_position", None)
        cube["minutes"] = minutes
        ctor["cubes"][new_id] = second
    elif action == "constructor_select":
        if set(data)-{"cube_id", "date", "cube", "start"} or not {"cube_id", "date"} <= set(data):
            raise ValidationError("constructor_select: неверные поля")
        if "cube" in data:
            if not isinstance(data["cube"], dict) or "cube_id" in data["cube"]:
                raise ValidationError("constructor_select.cube: неверный объект")
            _constructor_cube_update(planning, {**data["cube"], "cube_id": data["cube_id"]})
        cube = ctor["cubes"].get(data["cube_id"])
        if cube is None:
            raise ValidationError("constructor cube: не найден")
        _constructor_select(planning, cube, _builder_date(planning, data["date"]), data.get("start"))
    elif action in {"constructor_return", "constructor_hide", "constructor_timeline"}:
        allowed = {"cube_id"} if action == "constructor_return" else {"cube_id", "date", "hidden"} if action == "constructor_hide" else {"cube_id", "date", "start"}
        required = allowed if action != "constructor_timeline" else {"cube_id", "start"}
        if set(data)-allowed or not required <= set(data):
            raise ValidationError(f"{action}: неверные поля")
        cube = ctor["cubes"].get(data["cube_id"])
        if cube is None:
            raise ValidationError("constructor cube: не найден")
        if action == "constructor_return":
            _constructor_return(planning, cube)
        elif action == "constructor_hide":
            key = _builder_date(planning, data["date"])
            if type(data["hidden"]) is not bool:
                raise ValidationError("hidden: требуется bool")
            day = _constructor_init(planning, key)
            if data["hidden"]:
                if _constructor_selected_date(planning, cube["id"]) == key:
                    _constructor_return(planning, cube)
                if cube["id"] not in day["hidden_cube_ids"]:
                    day["hidden_cube_ids"].append(cube["id"])
            else:
                day["hidden_cube_ids"] = [cid for cid in day["hidden_cube_ids"] if cid != cube["id"]]
        else:
            key = _constructor_selected_date(planning, cube["id"])
            if key is None or data.get("date", key) != key:
                raise ValidationError("timeline: кубик не выбран на этой дате")
            _builder_date(planning, key)
            _constructor_set_slot(planning, cube, key, data["start"])
    elif action == "calendar_required":
        if set(data) != {"event_id", "required"} or type(data["required"]) is not bool:
            raise ValidationError("calendar_required: нужны event_id,required bool")
        event = next((e for e in planning["calendar"]["events"] if data["event_id"] in {e["id"], _event_identity(e)}), None)
        if event is None:
            raise ValidationError("Calendar event: не найден")
        ctor["event_required"][_event_identity(event)] = data["required"]
        if data["required"]:
            for cube in ctor["cubes"].values():
                if cube["kind"] == "calendar" and cube["source_ref"] == _event_identity(event):
                    key = _constructor_selected_date(planning, cube["id"])
                    if key:
                        ctor["days"][key].setdefault("source_slots", {}).pop(cube["id"], None)
    else:
        raise ValidationError("Неизвестное действие constructor")
    _validate_constructor(planning)
    _constructor_allocation(planning, original)
    _constructor_validate_slots(planning)
    for key in ctor["days"]:
        if ctor["days"][key].get("initialized"):
            _refresh_manual_day(planning, key)
    result = calculate(planning, reason=action)
    # calculate freezes reported days; refresh the explicitly edited manual view
    # so return/hide after progress removes only selection, never the report.
    for key in ctor["days"]:
        if ctor["days"][key].get("initialized"):
            _refresh_manual_day(result, key)
    _refresh_constructor_result(result)
    return result


def _scope_exempt(planning, task_id, key=None):
    entry = planning.get("scope_exemptions", {}).get(task_id, {})
    return entry.get("exempt", False) and (key is None or key >= entry["from_date"])


def apply_scope_exempt(planning, data):
    allowed = {"task_ids", "exempt", "reason", "from_date", "release_unstarted"}
    if set(data)-allowed or not {"task_ids", "exempt", "reason", "from_date"} <= set(data):
        raise ValidationError("scope_exempt: неверные поля")
    ids = data["task_ids"]
    if not isinstance(ids, list) or not ids or len(ids) > 3000 or len(ids) != len(set(ids)) or any(tid not in {t["id"] for t in planning["tasks"]} for tid in ids):
        raise ValidationError("scope_exempt.task_ids: нужны существующие уникальные ID")
    if type(data["exempt"]) is not bool or type(data.get("release_unstarted", False)) is not bool:
        raise ValidationError("scope_exempt: требуется bool")
    if not isinstance(data["reason"], str) or not data["reason"].strip() or len(data["reason"]) > 1000:
        raise ValidationError("scope_exempt.reason: требуется текст до1000")
    planning = deepcopy(planning)
    key = _builder_date(planning, data["from_date"])
    released = []
    if data["exempt"]:
        to_release = [lock for lock in planning.get("locks", []) if lock["task_id"] in ids and lock["date"] >= key and not _started(planning, lock["cube_id"])]
        if to_release and not data.get("release_unstarted"):
            raise ValidationError("Сначала явно разрешите release_unstarted для будущих резервов")
        for lock in to_release:
            planning["locks"].remove(lock)
            released.append({"cube_id": lock["cube_id"], "date": lock["date"], "minutes": lock["minutes"]})
            _normalize_manual_order(planning, lock["date"])
    for tid in ids:
        planning.setdefault("scope_exemptions", {})[tid] = {**planning.get("scope_exemptions", {}).get(tid, {}),
            "exempt": data["exempt"], "reason": data["reason"], "from_date": key, "at": datetime.now(timezone.utc).isoformat()}
    # Keep reported/frozen history, remove only unstarted forecasts from the future
    # part before calculate may retain old days behind a later report boundary.
    if data["exempt"]:
        planning["result"]["cubes"] = [cube for cube in planning["result"]["cubes"]
            if cube["task_id"] not in ids or cube["date"] < key or _started(planning, cube["id"])]
    for day_key in planning.get("constructor", {}).get("days", {}):
        if planning["constructor"]["days"][day_key].get("initialized"):
            _refresh_manual_day(planning, day_key)
    result = calculate(planning, from_date=key, reason="scope_exempt")
    for day_key in result.get("constructor", {}).get("days", {}):
        if result["constructor"]["days"][day_key].get("initialized"):
            _refresh_manual_day(result, day_key)
    _refresh_constructor_result(result)
    result["result"]["scope_released_reservations"] = released
    result["last_update"] = {"action": "scope_exempt", "at": datetime.now(timezone.utc).isoformat()}
    return result


def apply_constructor_goal(planning, data):
    """Store explicit goal progress without converting a day or crediting work."""
    if (set(data)-{"scope", "date", "title", "percent"} or not {"scope", "date"} <= set(data)
            or not ({"title", "percent"} & set(data))):
        raise ValidationError("constructor_goal: нужны scope,date и title или percent")
    if data["scope"] not in {"day", "week"}:
        raise ValidationError("constructor_goal.scope: day или week")
    selected = _day(data["date"], "constructor_goal.date")
    key = (selected-timedelta(days=selected.weekday())).isoformat() if data["scope"] == "week" else selected.isoformat()
    if "title" in data and (not isinstance(data["title"], str) or len(data["title"]) > 1000):
        raise ValidationError("constructor_goal.title: текст до1000")
    if "percent" in data:
        _int(data["percent"], "constructor_goal.percent", 0, 100)
    planning = deepcopy(planning)
    ctor = planning.setdefault("constructor", {"version": 1, "cubes": {}, "days": {}, "event_required": {}, "source_reports": {}})
    if data["scope"] == "day":
        record = ctor["days"].setdefault(key, {"initialized": False, "goal": "", "goal_percent": 0})
    else:
        record = ctor.setdefault("weeks", {}).setdefault(key, {"goal": "", "goal_percent": 0})
    if "title" in data:
        record["goal"] = data["title"]
    if "percent" in data:
        record["goal_percent"] = data["percent"]
    _validate_constructor(planning)
    _refresh_constructor_result(planning)
    planning["last_update"] = {"action": "constructor_goal", "at": datetime.now(timezone.utc).isoformat()}
    return planning


def apply_constructor_goal_status(planning, data):
    if set(data) != {"scope", "date", "status"} or data["scope"] not in {"day", "week"}:
        raise ValidationError("constructor_goal_status: нужны scope,date,status")
    if data["status"] not in {"active", "completed", "cancelled"}:
        raise ValidationError("constructor_goal_status.status: active|completed|cancelled")
    selected = _day(data["date"], "constructor_goal_status.date")
    key = (selected-timedelta(days=selected.weekday())).isoformat() if data["scope"] == "week" else selected.isoformat()
    planning = deepcopy(planning)
    ctor = planning.setdefault("constructor", {"version": 1, "cubes": {}, "days": {}, "event_required": {}, "source_reports": {}})
    records = ctor["days"] if data["scope"] == "day" else ctor.setdefault("weeks", {})
    record = records.setdefault(key, {"goal": "", "goal_percent": 0, **({"initialized": False} if data["scope"] == "day" else {})})
    old_status = record.get("goal_status", "active")
    if data["status"] != "active" and old_status == "active":
        catalog = _goal_cube_catalog(planning)
        record["closed_cube_ids"] = _goal_member_ids(data["scope"], key, record, catalog)
        preserved = record.setdefault("closed_cube_snapshots", {})
        for cube_id in record["closed_cube_ids"]:
            if cube_id in catalog:
                preserved[cube_id] = deepcopy(catalog[cube_id])
        record["closed_at"] = datetime.now(timezone.utc).isoformat()
    record["goal_status"] = data["status"]
    _validate_constructor(planning)
    _refresh_constructor_result(planning)
    planning["last_update"] = {"action": "constructor_goal_status", "at": datetime.now(timezone.utc).isoformat()}
    return planning


def _month(value):
    if not isinstance(value, str) or len(value) != 7:
        raise ValidationError("month: требуется YYYY-MM")
    try:
        parsed = date.fromisoformat(value + "-01")
    except ValueError as exc:
        raise ValidationError("month: требуется существующий месяц YYYY-MM") from exc
    if parsed.isoformat()[:7] != value:
        raise ValidationError("month: требуется YYYY-MM")
    return value


def _monthly_task_ids(values, known_ids):
    if (not isinstance(values, list) or len(values) > 3000
            or any(not isinstance(tid, str) or tid not in known_ids for tid in values)
            or len(values) != len(set(values))):
        raise ValidationError("monthly_plan.task_ids: уникальные ID известных Work задач")
    return values


def _validate_monthly_plan(record, known_ids):
    fields = {"id", "month", "status", "approved_at", "task_ids"}
    if not isinstance(record, dict) or set(record) != fields:
        raise ValidationError("monthly_plan: нужны только id,month,status,approved_at,task_ids")
    try:
        UUID(record["id"])
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError("monthly_plan.id: требуется UUID") from exc
    _month(record["month"])
    _monthly_task_ids(record["task_ids"], known_ids)
    status = record["status"]
    if not isinstance(status, str) or status not in {"approved", "draft"}:
        raise ValidationError("monthly_plan.status: approved или draft")
    if status == "draft":
        if record["approved_at"] is not None:
            raise ValidationError("monthly_plan draft: approved_at должен быть null")
    else:
        try:
            stamp = datetime.fromisoformat(record["approved_at"])
            if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
                raise ValueError("UTC required")
        except (ValueError, TypeError) as exc:
            raise ValidationError("monthly_plan.approved_at: требуется время UTC") from exc


def project_task_importance(planning):
    """Add read defaults and refresh task rows without touching cube identity/facts."""
    scores = {task["id"]: task.get("importance_score", 0) for task in planning.get("tasks", [])}
    for task in planning.get("tasks", []):
        task["importance_score"] = scores[task["id"]]
    for collection in ("tasks", "task_pool"):
        for row in planning.get("result", {}).get(collection, []):
            task_id = row.get("task_id") if collection == "task_pool" else row.get("id")
            if task_id in scores:
                row["importance_score"] = scores[task_id]
    return planning


def apply_action(planning, action, data):
    if not isinstance(data, dict):
        raise ValidationError("planning data: требуется объект")
    if isinstance(data.get("cube_id"), str) and deleted_cube(planning, data["cube_id"]):
        raise ValidationError("Кубик удалён; изменение исторических фактов запрещено")
    if action == "monthly_plan_approve":
        if set(data) != {"month", "task_ids"}:
            raise ValidationError("monthly_plan_approve: нужны только month,task_ids")
        month = _month(data["month"])
        task_ids = _monthly_task_ids(data["task_ids"], {task["id"] for task in planning["tasks"]})
        planning = deepcopy(planning)
        previous = planning.get("monthly_plan", {})
        planning["monthly_plan"] = {
            "id": previous["id"] if previous.get("month") == month else str(uuid4()),
            "month": month, "status": "approved",
            "approved_at": datetime.now(timezone.utc).isoformat(), "task_ids": list(task_ids),
        }
        _validate_monthly_plan(planning["monthly_plan"], {task["id"] for task in planning["tasks"]})
        return planning
    if action == "task_importance":
        if set(data) != {"task_id", "importance_score"}:
            raise ValidationError("task_importance: нужны task_id, importance_score")
        score = _int(data["importance_score"], "importance_score", 0, 5)
        task_id = identifier(data["task_id"], "task_id")
        planning = deepcopy(planning)
        task = next((task for task in planning["tasks"] if task["id"] == task_id), None)
        if task is None:
            raise ValidationError("task_importance: задача не найдена")
        task["importance_score"] = score
        return project_task_importance(planning)
    if action == "scope_exempt":
        return apply_scope_exempt(planning, data)
    if action == "constructor_goal":
        return apply_constructor_goal(planning, data)
    if action == "constructor_goal_status":
        return apply_constructor_goal_status(planning, data)
    if action.startswith("constructor_") or action == "calendar_required":
        return apply_constructor(planning, action, data)
    if action == "cube_progress" and data.get("cube_id") in planning.get("constructor", {}).get("cubes", {}):
        return _constructor_progress(planning, data)
    if action in {"segment_create", "segment_progress", "day_segment_add"}:
        return apply_segment_action(planning, action, data)
    if action in {"day_budget", "prep_set", "prep_progress", "day_cube_add", "day_cube_remove",
                  "day_cube_move", "day_cube_resize"}:
        return apply_day_builder(planning, action, data)
    planning = deepcopy(planning)
    allowed = {
        "cube_progress": {"cube_id", "percent", "actual_minutes"},
        "day_outcome": {"task_id", "date", "remaining_minutes", "done", "note"},
        "resource": {"task_id", "resource"},
        "place_cube": {"cube_id", "date", "start", "locked"},
        "capacity_override": {"date", "minutes", "reason", "approved"},
        "weekly_review_done": {"id", "done"},
        "describe_cubes": {"entries"},
    }
    if action in allowed and set(data) - allowed[action]:
        raise ValidationError(f"{action}: неизвестное поле")
    if action == "cube_progress":
        cube = next((x for x in planning.get("result", {}).get("cubes", []) if x.get("id") == data.get("cube_id")), None)
        if cube is None:
            raise ValidationError("cube_id: кубик не найден")
        percent = _int(data.get("percent"), "percent", 0, 100)
        existing = planning.setdefault("completed_cubes", {}).get(cube["id"], {})
        actual = _int(data.get("actual_minutes", existing.get("actual_minutes", 0)), "actual_minutes")
        planning["completed_cubes"][cube["id"]] = {
            "task_id": cube["task_id"], "date": cube["date"],
            "planned_minutes": cube["minutes"], "percent": percent,
            "actual_minutes": actual,
            **({"segment_id": cube["segment_id"]} if cube.get("segment_id") else {}),
        }
        cube["progress_percent"], cube["actual_minutes"] = percent, actual
        _refresh_result_remaining(planning)
        if cube["date"] in planning.get("manual_days", {}):
            _refresh_manual_day(planning, cube["date"])
            planning["result"]["task_pool"] = _task_pool(
                planning, planning["result"]["cubes"], _dates(planning["settings"]))
    elif action == "day_outcome":
        task_id = data.get("task_id")
        if task_id not in {task["id"] for task in planning["tasks"]}:
            raise ValidationError("day_outcome: задача не найдена")
        day = _day(data.get("date"), "day_outcome.date").isoformat()
        remaining = data.get("remaining_minutes")
        if remaining is not None:
            _int(remaining, "remaining_minutes")
        done = data.get("done", remaining == 0)
        if type(done) is not bool:
            raise ValidationError("done: требуется bool")
        if done and remaining not in (None, 0):
            raise ValidationError("done=true несовместимо с ненулевым остатком")
        if remaining == 0 and done is False:
            raise ValidationError("Нулевой остаток означает done=true")
        note = data.get("note", "")
        if not isinstance(note, str) or len(note) > 2000:
            raise ValidationError("note: текст до 2000 символов")
        entries = planning.setdefault("day_outcomes", [])
        entries[:] = [x for x in entries if (x["task_id"], x["date"]) != (task_id, day)]
        entries.append({"task_id": task_id, "date": day, "remaining_minutes": remaining,
                        "done": done, "note": note})
        latest = latest_remaining_baseline(planning, task_id)
        if latest and latest[0] == day and (remaining is not None or done):
            epochs = planning.setdefault("remaining_epochs", {})
            epochs[task_id] = epochs.get(task_id, 0)+1
        _refresh_result_remaining(planning)
        if day in planning.get("manual_days", {}):
            _refresh_manual_day(planning, day)
            planning["result"]["task_pool"] = _task_pool(
                planning, planning["result"]["cubes"], _dates(planning["settings"]))
        result = planning.get("result", {})
        possible = [x for x in result.get("days", []) if x["date"] > day and not x.get("rest_provisional")
                    and x.get("unused_capacity_minutes", 0) >= 60
                    and x.get("focus_task_id") in (None, task_id)
                    and _has_free_cube_slot(x, result.get("cubes", []),
                                            planning["settings"]["break_minutes"])]
        result["recovery_proposals"] = {
            "task_id": task_id, "after_date": day,
            "ordinary_free_days": [{"date": x["date"], "free_minutes": x["unused_capacity_minutes"]}
                                   for x in possible[:7]],
            "emergency": result.get("emergency_proposals", [])[:7],
            "status": "proposal_only_replan_explicit"}
    elif action == "resource":
        task = next((x for x in planning["tasks"] if x["id"] == data.get("task_id")), None)
        if task is None:
            raise ValidationError("resource: задача не найдена")
        resource = data.get("resource")
        _validate_resource(resource, task["course"], planning["settings"])
        resources = task.setdefault("resources", [])
        if resource not in resources:
            resources.append(resource)
        for shown in planning.get("result", {}).get("tasks", []):
            if shown.get("id") == task["id"]:
                shown["resources"] = deepcopy(resources)
    elif action == "place_cube":
        cube = next((x for x in planning.get("result", {}).get("cubes", []) if x.get("id") == data.get("cube_id")), None)
        if cube is None:
            raise ValidationError("cube_id: кубик не найден")
        day = _day(data.get("date"), "place_cube.date").isoformat()
        start = _clock(data.get("start"), "place_cube.start")
        locked = data.get("locked")
        if type(locked) is not bool:
            raise ValidationError("place_cube.locked: требуется bool")
        old_day = cube["date"]
        moved = day != old_day or data["start"] != cube["start"]
        saved = planning.get("completed_cubes", {}).get(cube["id"])
        if moved and saved and (saved.get("percent", 0) > 0 or saved.get("actual_minutes", 0) > 0):
            raise ValidationError("Начатый кубик нельзя переносить; сохраните остаток и пересчитайте будущие дни")
        reports = [x["date"] for x in planning.get("completed_cubes", {}).values()]
        reports += [x["date"] for x in planning.get("day_outcomes", [])]
        if moved and reports and old_day <= max(reports):
            raise ValidationError("Отчётный день заморожен; переносите задачу новым будущим кубиком")
        if not locked and (day != old_day or data["start"] != cube["start"]):
            raise ValidationError("Снятие lock не переносит кубик; для переноса укажите locked=true")
        entry = {"cube_id": cube["id"], "task_id": cube["task_id"], "date": day,
                 "start": _hhmm(start), "end": _hhmm(start+cube["minutes"]), "minutes": cube["minutes"],
                 "phase": cube["phase"], "locked": True, "is_remainder": bool(cube.get("is_remainder")),
                 "title": cube["title"], "output": cube["output"]}
        locks = planning.setdefault("locks", [])
        locks[:] = [x for x in locks if x["cube_id"] != cube["id"]]
        if locked:
            locks.append(entry)
        cube.update({"date": day, "start": entry["start"], "end": entry["end"], "locked": locked})
        return calculate(planning, from_date=min(old_day, day), reason="place_cube")
    elif action == "capacity_override":
        if set(data) != {"date", "minutes", "reason", "approved"} or data.get("approved") is not True:
            raise ValidationError("capacity_override: нужны date,minutes,reason,approved=true")
        day = _day(data["date"], "capacity_override.date").isoformat()
        minutes = _int(data["minutes"], "capacity_override.minutes",
                       planning["settings"]["day_limit_minutes"], 480)
        reason = data["reason"]
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
            raise ValidationError("capacity_override.reason: нужен текст до 500 символов")
        planning["settings"].setdefault("day_capacity_overrides", {})[day] = {
            "minutes": minutes, "reason": reason.strip(), "approved": True}
        return calculate(planning, from_date=day, reason="approved_capacity_override")
    elif action == "weekly_review_done":
        review = next((x for x in planning.get("result", {}).get("weekly_reviews", [])
                       if x["id"] == data.get("id")), None)
        if review is None or review["date"] is None:
            raise ValidationError("weekly_review_done: назначенная задача недели не найдена")
        if type(data.get("done")) is not bool:
            raise ValidationError("weekly_review_done.done: требуется bool")
        planning.setdefault("weekly_review_done", {})[review["id"]] = data["done"]
        review["status"] = "done" if data["done"] else "planned"
        for day in planning["result"].get("days", []):
            if day["date"] == review["date"] and day.get("weekly_review"):
                day["weekly_review"]["status"] = review["status"]
    elif action == "describe_cubes":
        entries = data.get("entries")
        if not isinstance(entries, list) or not 1 <= len(entries) <= 100:
            raise ValidationError("describe_cubes.entries: требуется 1..100")
        cubes = {cube["id"]: cube for cube in planning.get("result", {}).get("cubes", [])}
        if len({entry.get("cube_id") for entry in entries if isinstance(entry, dict)}) != len(entries):
            raise ValidationError("describe_cubes: повтор cube_id")
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"cube_id", "title", "output", "steps"}:
                raise ValidationError("describe_cubes: нужны cube_id,title,output,steps")
            cube_id = identifier(entry["cube_id"], "describe_cubes.cube_id")
            if cube_id not in cubes:
                raise ValidationError("describe_cubes: неизвестный cube_id")
            detail = {key: entry[key] for key in ("title", "output", "steps")}
            _validate_cube_detail(detail)
            planning.setdefault("cube_details", {})[cube_id] = deepcopy(detail)
            cubes[cube_id].update(deepcopy(detail))
        orphaned = set(planning["cube_details"]) - set(cubes)
        planning["result"]["needs_detail_review"] = bool(orphaned)
        planning["result"]["orphaned_detail_count"] = len(orphaned)
    else:
        raise ValidationError("Неизвестное действие planning")
    planning["last_update"] = {"action": action, "at": datetime.now(timezone.utc).isoformat()}
    if action in {"cube_progress", "day_outcome"}:
        planning["needs_recalculation"] = True
    return planning


def compact_status(snapshot):
    planning = (snapshot.get("state") or {}).get("planning")
    if not planning:
        return {"available": False, "revision": snapshot["revision"]}
    result = planning.get("result", {})
    today = datetime.now(ZoneInfo(planning["settings"]["timezone"])).date().isoformat()
    coming = [day for day in result.get("days", []) if day.get("date", "") >= today]
    if not coming:
        coming = result.get("days", [])[-7:]
    next_dates = {day["date"] for day in coming[:7]}
    actual_remaining = _remaining(planning)
    progress = planning.get("completed_cubes", {})
    history = planning.get("history", [])
    return {"available": True, "revision": snapshot["revision"], "plan_version": planning.get("plan_version"),
            "last_change": history[-1] if history else None,
            "last_update": planning.get("last_update"),
            "needs_recalculation": bool(planning.get("needs_recalculation")),
            "calendar": {key: planning.get("calendar", {}).get(key) for key in ("fetched_at", "stale", "configured", "start", "end")},
            "totals": result.get("totals", {}), "unscheduled_live_minutes": result.get("unscheduled_live_minutes", 0),
            "unscheduled_live_count": result.get("unscheduled_live_count", 0),
            "progress": {"reported_cubes": len(progress),
                         "actual_minutes": sum(x.get("actual_minutes", 0) for x in progress.values()),
                         "day_outcomes": len(planning.get("day_outcomes", []))},
            "risk_count": len(result.get("risks", [])),
            "risks": result.get("risks", [])[:8], "next_days": coming[:7],
            "next_cubes": [{"id": c.get("id"), "task_id": c.get("task_id"), "date": c.get("date"),
                            "start": c.get("start"), "end": c.get("end"), "title": c.get("title"),
                            "output": c.get("output"), "steps": c.get("steps", []),
                            "floating": bool(c.get("floating")), "order": c.get("order"),
                            "minutes": c.get("minutes"), "manual": bool(c.get("manual")),
                            "segment_id": c.get("segment_id"),
                            "progress_percent": c.get("progress_percent")}
                           for c in result.get("cubes", []) if c.get("date") in next_dates][:16],
            "task_pool": [{
                "task_id": item["task_id"], "title": item["title"],
                "remaining_minutes": item["remaining_minutes"],
                "available_for_manual_minutes": item["available_for_manual_minutes"],
                "segments": [{"id": segment["id"], "title": segment["title"],
                              "minutes": segment["minutes"],
                              "remaining_minutes": segment["remaining_minutes"],
                              "progress_percent": segment["progress_percent"]}
                             for segment in item.get("segments", [])[:3]],
            } for item in result.get("task_pool", [])[:5]],
            "recovery_proposals": result.get("recovery_proposals"),
            "emergency_proposals": result.get("emergency_proposals", [])[:7],
            "weekly_reviews": [x for x in result.get("weekly_reviews", [])
                               if x.get("date") is None or x["date"] >= today][:3],
            "needs_detail_review": bool(result.get("needs_detail_review")),
            "orphaned_detail_count": result.get("orphaned_detail_count", 0),
            "tasks": [{"id": t.get("id"), "course": t.get("course"), "title": t.get("title"),
                       "due": t.get("due"), "due_status": t.get("due_status"),
                       "remaining_minutes": actual_remaining.get(t.get("id")),
                       "active_remaining_minutes": t.get("active_remaining_minutes", actual_remaining.get(t.get("id"))),
                       "scope_exempt": t.get("scope_exempt", False),
                       "remaining_at_horizon_minutes": t.get("remaining_at_horizon_minutes"),
                       "deficit_by_due_minutes": t.get("deficit_by_due_minutes")}
                      for t in result.get("tasks", [])]}
