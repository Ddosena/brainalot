"""Deterministic, local Work planning. No project data or model calls live here."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .core import ValidationError
from .shared_state import encoded, identifier

MAX_DAYS = 366
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
        if cube["date"] == day["date"]:
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
        if _day(entry.get("date"), "completed cube date") not in days:
            raise ValidationError("completed cube date вне горизонта")
        _int(entry.get("planned_minutes"), "completed cube planned_minutes")
        _int(entry.get("percent"), "completed cube percent", 0, 100)
        _int(entry.get("actual_minutes", 0), "completed cube actual_minutes")
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
        _clock(lock.get("start"), "lock.start")
        _clock(lock.get("end"), "lock.end")
        minutes = _int(lock.get("minutes"), "lock.minutes", 1, 90)
        if minutes < 60 and lock.get("is_remainder") is not True:
            raise ValidationError("lock: короткий кубик требует is_remainder=true")
    if len(planning.get("history", [])) > 40:
        raise ValidationError("planning.history: максимум 40")
    if not isinstance(planning.get("result", {}), dict):
        raise ValidationError("planning.result: требуется объект")
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
                                "events": [], "lessons": [], "unknown": []} for day in days}
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
                entry["timed"].append((left, right))
                entry["events"].append(event["id"])
                if category in {"work", "lesson", "unknown"}:
                    entry["work"].append((left, right))
                if category == "unknown":
                    entry["unknown"].append(event["id"])
            cursor += timedelta(days=1)
        if category == "lesson" and start <= first.date() <= end:
            entries[first.date().isoformat()]["lessons"].append((int(first.hour*60+first.minute), event["id"]))
    for entry in entries.values():
        entry["timed"] = _union(entry["timed"])
        entry["work"] = _union(entry["work"])
    # Prepare before each lesson, moving earlier around existing events/preparation.
    for key, entry in entries.items():
        for lesson_start, event_id in sorted(entry["lessons"]):
            candidate = lesson_start
            duration = settings["prep_minutes"]
            if duration == 0:
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
        entry["prep_minutes"] = _duration(entry["prep"])
        entry["homework_minutes"] = (min(30, _int(settings.get("homework_minutes", 30),
                                                   "homework_minutes", 0, 30)) if entry["homework"] else 0)
        entry["fixed_minutes"] = entry["timed_minutes"] + entry["prep_minutes"] + entry["homework_minutes"]
        override = settings.get("day_capacity_overrides", {}).get(key)
        if override and entry["lessons"]:
            raise ValidationError("capacity override: день с уроком не допускается")
        entry["day_limit_minutes"] = override["minutes"] if override else settings["day_limit_minutes"]
        entry["emergency_approved"] = bool(override)
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
        for candidate in reversed([d for d in days if week_start <= d <= week_end]):
            key = candidate.isoformat()
            if key in rest or key in by_date:
                continue
            led = ledger[key]
            day_locks = [x for x in locks if x["date"] == key]
            if led["available_minutes"]-sum(x["minutes"] for x in day_locks) < weekly["minutes"]:
                continue
            free = list(led["free_windows"])
            for lock in day_locks:
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


def _remaining(planning):
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


def _final_remainder(minutes):
    while minutes >= 60:
        minutes -= 90 if minutes >= 90 and minutes - 90 != 30 else 60
    return minutes


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
        a, b = _clock(lock["start"], "lock.start"), _clock(lock["end"], "lock.end")
        minutes = lock["minutes"]
        if b-a != minutes or not (minutes in (60, 90) or 1 <= minutes < 60 and lock.get("is_remainder") is True):
            raise ValidationError("lock: 60–90 минут либо подтверждённый финальный остаток")
        if lock.get("phase", "preparation") == "review" and task.get("methodist_start") and day < task["methodist_start"]:
            raise ValidationError("lock: review раньше окна методиста")
        if not any(left <= a and b <= right for left, right in ledger[day]["free_windows"]):
            raise ValidationError("lock: занятый слот или вне рабочего окна")
        occupied[day].append((a, b, lock["task_id"]))
        by_task[lock["task_id"]] += minutes
        phase = lock.get("phase", "preparation")
        by_phase[(lock["task_id"], phase)] += minutes
        if minutes < 60:
            short_locks[(lock["task_id"], phase)].append(lock)
        if (planning["settings"].get("priority_course") and planning["settings"].get("priority_until")
                and day < planning["settings"]["priority_until"] and task.get("early_prep")
                and task["course"] != planning["settings"]["priority_course"]):
            d = date.fromisoformat(day)
            early_by_week[(d-timedelta(days=d.weekday())).isoformat()] += lock["minutes"]
    for day, intervals in occupied.items():
        ids = {item[2] for item in intervals}
        if len(ids) > 1:
            primary = [tid for tid in ids if tasks[tid]["course"] == planning["settings"].get("priority_course")]
            secondary = [tid for tid in ids if tasks[tid].get("early_prep")
                         and tasks[tid]["course"] != planning["settings"].get("priority_course")]
            secondary_minutes = sum(b-a for a, b, tid in intervals if tid in secondary)
            if not (day < planning["settings"].get("priority_until", "") and len(ids) == 2
                    and len(primary) == len(secondary) == 1 and secondary_minutes == 60):
                raise ValidationError("lock: больше одной фокусной задачи в день")
        if ledger[day]["fixed_minutes"] + sum(b-a for a, b, _ in intervals) > ledger[day]["day_limit_minutes"]:
            raise ValidationError("lock: дневной лимит превышен")
        for first, second in zip(sorted(intervals), sorted(intervals)[1:]):
            if second[0]-first[1] < planning["settings"]["break_minutes"]:
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


def calculate(planning, *, from_date=None, reason="recalculate"):
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
    old_cubes = [deepcopy(c) for c in old.get("cubes", []) if c.get("date", "") < start.isoformat()]
    old_days = [deepcopy(d) for d in old.get("days", []) if d.get("date", "") < start.isoformat()]
    reported_cube_by_day = defaultdict(list)
    for entry in planning.get("completed_cubes", {}).values():
        reported_cube_by_day[entry["date"]].append(entry)
    outcome_dates = {entry["date"] for entry in planning.get("day_outcomes", [])}
    for day in old_days:
        key = day["date"]
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
                       unused_capacity_minutes=0,
                       needs_review=day.get("needs_review", False) or bool(estimated))
        elif key in outcome_dates:
            day.update(reported=True, report_basis="actual_unknown",
                       planned_development_minutes=day.get("development_minutes", 0),
                       reported_actual_minutes=None, unused_capacity_minutes=0,
                       needs_review=True)
        else:
            day.update(historical_unreported=True, unused_capacity_minutes=0,
                       needs_review=True)
    remaining = _remaining(planning)
    task_by_id = {task["id"]: task for task in planning["tasks"]}
    future_locks = [deepcopy(x) for x in planning.get("locks", []) if x["date"] >= start.isoformat()]
    rest = _rest_days(days, ledger, planning["tasks"], {x["date"] for x in future_locks})
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
        reserved[lock["task_id"]] += lock["minutes"]
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
                           remaining[task["id"]] - reserved[task["id"]] >= 60 for task in planning["tasks"])
        def schedulable(task):
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
        if early_cube:
            placed.append(early_cube)
            remaining[early_cube["task_id"]] -= 60
            early_weekly[week] += 60
        cubes.extend(placed)
        planned = sum(x["minutes"] for x in locks) + sum(x["minutes"] for x in placed)
        secondary_exception = priority_active and focus and task_by_id[focus]["course"] == planning["settings"].get("priority_course") and any(
            task_by_id[x["task_id"]]["course"] != planning["settings"].get("priority_course")
            for x in locks + placed)
        result_days.append({"date": key, "rest_provisional": key in rest,
                            "focus_task_id": focus, "calendar_minutes": led["timed_minutes"],
                            "secondary_early_prep_exception": bool(secondary_exception),
                            "lesson_count": len(led["lessons"]), "lesson_prep_minutes": led["prep_minutes"],
                            "all_day_assumed_minutes": led["homework_minutes"],
                            "fixed_minutes": led["fixed_minutes"], "development_minutes": planned,
                            "weekly_review_minutes": weekly_minutes,
                            "weekly_review": ({k: weekly_review[k] for k in ("id", "start", "end", "minutes", "status")}
                                              if weekly_review else None),
                            "total_work_minutes": led["fixed_minutes"] + weekly_minutes + planned,
                            "day_limit_minutes": led["day_limit_minutes"],
                            "emergency_approved": led["emergency_approved"],
                            "unused_capacity_minutes": max(0, led["available_minutes"]-weekly_minutes-planned),
                            "free_windows": [[_hhmm(a), _hhmm(b)] for a, b in led["free_windows"]],
                            "event_ids": led["events"],
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
        deficit = max(0, demand-assigned)
        record = {**task, "scheduled_by_due_minutes": assigned, "deficit_by_due_minutes": deficit,
                  "remaining_at_horizon_minutes": remaining[tid]}
        result_tasks.append(record)
        if due and deficit:
            risks.append({"code": "deadline_deficit", "task_id": tid, "minutes": deficit})
    for day in result_days:
        if day["over_cap_fixed_minutes"]:
            risks.append({"code": "fixed_work_exceeds_day_limit", "date": day["date"], "minutes": day["over_cap_fixed_minutes"]})
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
              "remaining_horizon_hours": round(sum(remaining.values())/60, 2),
              "tasks_missing_due": sum(bool(x["deficit_by_due_minutes"] and x.get("due")) for x in result_tasks),
              "max_total_work_minutes": max((x["total_work_minutes"] for x in result_days), default=0)}
    totals["weekly_review_hours"] = round(sum(x.get("weekly_review_minutes", 0) for x in result_days)/60, 2)
    totals["weekly_reviews_unplaced"] = sum(x["status"] == "unplaced" for x in weekly_reviews)
    totals["unscheduled_live_minutes"] = sum(x["duration_minutes"] for x in unscheduled_live)
    totals["by_course"] = {course: {"scheduled_hours": round(sum(c["minutes"] for c in cubes
                                                                 if c["course"] == course and c["date"] >= start.isoformat())/60, 2),
                                   "remaining_hours": round(sum(remaining[t["id"]] for t in planning["tasks"] if t["course"] == course)/60, 2)}
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


def apply_action(planning, action, data):
    if not isinstance(data, dict):
        raise ValidationError("planning data: требуется объект")
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
        planning["completed_cubes"][cube["id"]] = {"task_id": cube["task_id"], "date": cube["date"],
                                                      "planned_minutes": cube["minutes"], "percent": percent,
                                                      "actual_minutes": actual}
        cube["progress_percent"], cube["actual_minutes"] = percent, actual
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
                            "progress_percent": c.get("progress_percent")}
                           for c in result.get("cubes", []) if c.get("date") in next_dates][:16],
            "recovery_proposals": result.get("recovery_proposals"),
            "emergency_proposals": result.get("emergency_proposals", [])[:7],
            "weekly_reviews": [x for x in result.get("weekly_reviews", [])
                               if x.get("date") is None or x["date"] >= today][:3],
            "needs_detail_review": bool(result.get("needs_detail_review")),
            "orphaned_detail_count": result.get("orphaned_detail_count", 0),
            "tasks": [{"id": t.get("id"), "course": t.get("course"), "title": t.get("title"),
                       "due": t.get("due"), "due_status": t.get("due_status"),
                       "remaining_minutes": actual_remaining.get(t.get("id")),
                       "remaining_at_horizon_minutes": t.get("remaining_at_horizon_minutes"),
                       "deficit_by_due_minutes": t.get("deficit_by_due_minutes")}
                      for t in result.get("tasks", [])]}
