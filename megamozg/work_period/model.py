"""Read model and transactional commands for an Work period planner.

Native Work data is preserved. Period edits live in a separate top-level
namespace, committed in the same WorkStore with revision CAS and durable receipts.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib
import re
import time
from zoneinfo import ZoneInfo

from ..core import ValidationError
from ..shared_state import encoded, identifier
from .. import work_planning as wp
from . import calendar_rules
from . import timeline_rules
from . import course_rules
from . import cube_ops
from . import ruler_rules

NAMESPACE = "period_planner"
ACTIONS = frozenset({"period_set", "period_clear", "project_visibility", "cube_select", "cube_return",
    "cube_move", "cube_time", "cube_merge", "cube_rename", "cube_cancel", "task_rename",
    "calendar_source_import", "calendar_event_move", "calendar_offer_estimate", "cube_resize", "course_source_import", "calendar_event_rename", "assignment_rename", "cube_delete"}) | ruler_rules.ACTIONS
CONTEXT_FIELDS = frozenset({"mode", "date", "week_start", "cube_id", "task_id", "project_id", "window_start", "expanded_week", "calendar_event_id"})


def day(value):
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValidationError("Требуется дата ГГГГ-ММ-ДД") from None
    return value


def clock(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise ValidationError("Требуется время ЧЧ:ММ")
    return int(value[:2]) * 60 + int(value[3:])


def hhmm(value):
    return f"{value // 60:02d}:{value % 60:02d}"


def text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 1000:
        raise ValidationError("Нужен непустой текст до 1000 символов")
    return value.strip()


def extension(state):
    return state.get(NAMESPACE, {})


def default_context(state):
    planning = state["planning"]
    today = datetime.now(ZoneInfo(planning["settings"]["timezone"])).date()
    monday = today - timedelta(days=today.weekday())
    return {"mode": "week", "date": today.isoformat(), "week_start": monday.isoformat(),
        "cube_id": None, "task_id": None, "project_id": None, "window_start": monday.isoformat(), "expanded_week": None,
        "calendar_event_id": None}


def context(state):
    return {**default_context(state), **extension(state).get("context", {})}


def _source_catalog(planning, retained=None):
    # Copy and project only; no read operation mutates the stored source.
    rows = deepcopy(retained or {})
    rows.update(wp._goal_cube_catalog(planning))
    represented = {value for row in rows.values() for value in (row.get("id"), row.get("blueprint_ref")) if value}
    represented.update(row["id"][8:] for row in rows.values() if row.get("id", "").startswith("planned-"))
    absorbed = {ref for row in planning.get("constructor", {}).get("live_merges", {}).values()
                for ref in row.get("absorbed_blueprints", [])}
    absorbed_ids = {ref for row in planning.get("constructor", {}).get("live_merges", {}).values()
                    for ref in row.get("source_ids", [])[1:]}
    for cube_id in absorbed_ids:
        rows.pop(cube_id, None)
    for forecast in planning.get("result", {}).get("cubes", []):
        if forecast["id"] in represented | absorbed or forecast.get("manual") or forecast.get("locked"):
            continue
        rows["planned-" + forecast["id"]] = {**deepcopy(forecast), "id": "planned-" + forecast["id"],
            "blueprint_ref": forecast["id"], "kind": "task", "planned_date": forecast["date"],
            "candidate": True, "progress_percent": 0, "actual_minutes": 0}
    # Prep/review sources are available independently of a selected task.
    represented_sources = {row.get("source_ref") for row in rows.values()}
    for entry in planning.get("result", {}).get("days", []):
        for prep in entry.get("prep_cubes", []):
            if prep["id"] in represented_sources or prep["id"] in rows:
                continue
            report = planning.get("prep_progress", {}).get(prep["id"], {})
            rows[prep["id"]] = {**deepcopy(prep), "id": prep["id"], "kind": "prep", "source_ref": prep["id"],
                "title": prep.get("title") or "Подготовка к уроку", "planned_date": entry["date"],
                "progress_percent": report.get("percent", 0), "actual_minutes": report.get("actual_minutes", 0)}
    for review in planning.get("result", {}).get("weekly_reviews", []):
        review_id = review.get("id") or "review:" + review["date"]
        if review_id in rows or review_id in represented_sources:
            continue
        rows[review_id] = {**deepcopy(review), "id": review_id, "kind": "weekly_review",
            "title": review.get("title") or "Недельный разбор", "planned_date": review.get("date") or review.get("week_end"),
            "minutes": review.get("minutes", planning["settings"].get("weekly_review", {}).get("minutes", 60)),
            "progress_percent": 100 if review.get("date") in planning.get("weekly_review_done", []) else 0,
            "actual_minutes": 0}
    return rows


def cube_rows(state):
    planning, ext = state["planning"], extension(state)
    tasks = {row["id"]: row for row in planning["tasks"]}
    catalog_owned = {row["work_task_id"]: row.get("owned_by_user", True)
        for row in course_rules.catalog_items(state) if row.get("work_task_id")}
    rows = _source_catalog(planning, ext.get("retained_cubes"))
    native_sources = deepcopy(rows)
    rows.update(deepcopy(ext.get("merged_cubes", {})))
    absorbed = {cid for merge in ext.get("merges", {}).values() for cid in merge["source_ids"]}
    for cid in absorbed:
        rows.pop(cid, None)
    result = []
    locks = {row["cube_id"]: row for row in planning.get("locks", [])}
    for cid, source in rows.items():
        if source.get("kind") == "calendar" or source.get("alias_of") or source.get("archived_goal_member"):
            continue
        row = deepcopy(source)
        # A native Day report/delete may arrive after an overlay merge. Keep
        # that source authoritative instead of exposing an editable stale copy.
        if cid in ext.get("merged_cubes", {}):
            facts, changed, weighted, actual = [], [], 0, 0
            for leaf_id in row.get("source_ids", []):
                leaf = native_sources.get(leaf_id)
                if leaf is None:
                    changed.append(leaf_id)
                    continue
                if wp.deleted_cube(planning, leaf_id, leaf.get("blueprint_ref")):
                    changed.append(leaf_id)
                if leaf.get("progress_percent") or leaf.get("actual_minutes"):
                    facts.append({"cube_id": leaf_id, "minutes": leaf["minutes"],
                        "progress_percent": leaf.get("progress_percent", 0),
                        "actual_minutes": leaf.get("actual_minutes", 0)})
                    weighted += leaf["minutes"] * leaf.get("progress_percent", 0)
                    actual += leaf.get("actual_minutes", 0)
                leaf_task = leaf.get("task_id")
                if leaf_task and (wp._scope_exempt(planning, leaf_task) or catalog_owned.get(leaf_task) is False):
                    changed.append(leaf_id)
            if facts:
                row.update(historical_facts=facts, progress_percent=min(100, weighted / row["minutes"]), actual_minutes=actual)
            if changed:
                row.update(allocation_stale=True, changed_source_ids=list(dict.fromkeys(changed)),
                    source_change_reason="Состав исходных этапов изменился в Work; история объединения сохранена, требуется сверка состава.")
        tid = row.get("task_id")
        lock = locks.get(cid, {})
        placement = ext.get("placements", {}).get(cid)
        selected = row.get("selected_date") or (lock.get("date") if lock else None)
        start = lock.get("start") if not lock.get("floating") else None
        if row.get("kind") != "task" and selected:
            start = planning.get("constructor", {}).get("days", {}).get(selected, {}).get("source_slots", {}).get(cid)
        if placement is not None:
            selected, start = placement.get("date"), placement.get("start")
        deleted = bool(wp.deleted_cube(planning, cid, row.get("blueprint_ref")))
        canceled = cid in ext.get("cancellations", {})
        exempt = bool(tid and (wp._scope_exempt(planning, tid) or catalog_owned.get(tid) is False))
        percent = row.get("progress_percent", 0)
        row.update(id=cid, task_id=tid, project_id=tasks.get(tid, {}).get("project_id", row.get("project_id")),
            title=ext.get("cube_titles", {}).get(cid, row.get("title", "Кубик")), source_title=source.get("title", "Кубик"),
            planned_date=row.get("planned_date") or row.get("date"), selected_date=selected, start=start,
            progress_percent=percent, actual_minutes=row.get("actual_minutes", 0),
            kind=row.get("kind", "task"), candidate=bool(row.get("candidate") and cid not in ext.get("placements", {})),
            status="cancelled" if canceled or deleted else "completed" if percent >= 100 else "started" if percent or row.get("actual_minutes") else "selected" if selected else "available",
            available=not (canceled or deleted or exempt or percent >= 100 or row.get("allocation_stale")),
            output=row.get("output", ""), steps=row.get("steps", []),
            source_ids=deepcopy(row.get("source_ids") or [cid]), returned=placement is not None and placement.get("date") is None)
        row["end"] = hhmm(clock(start) + row["minutes"]) if start else None
        if canceled:
            row["cancel_reason"] = ext["cancellations"][cid]["reason"]
        result.append(row)
    return sorted(result, key=lambda row: (row.get("planned_date") or "9999", row.get("project_id") or "", row["id"]))


def _event_rows(planning, calendar_metadata=None):
    zone = ZoneInfo(planning["settings"]["timezone"])
    native_tasks = {row["id"]: row for row in planning["tasks"]}
    live_rows = planning.get("live_events", [])
    # Provider-backed native snapshots and ICS snapshots use different IDs.
    # The reviewed metadata keeps a stable period ID and records its exact
    # native provider ID; no title matching or ID invention is involved.
    aliases = {record["native_event_id"]: record["id"] for record in (calendar_metadata or {}).values()
        if record.get("native_event_id")}
    result, linked_live = [], set()
    seen = set()
    for event in planning["calendar"].get("events", []):
        event_id = aliases.get(event["id"], event["id"])
        if event.get("status") == "cancelled" or event.get("category") == "ignore" and event_id not in (calendar_metadata or {}):
            continue
        identity = (event.get("calendar_id"), event.get("occurrence_id") or event.get("id"))
        if identity in seen:
            continue
        seen.add(identity)
        first = datetime.fromisoformat(event["start"].replace("Z", "+00:00"))
        last = datetime.fromisoformat(event["end"].replace("Z", "+00:00"))
        if first.tzinfo:
            first, last = first.astimezone(zone), last.astimezone(zone)
        record = (calendar_metadata or {}).get(event_id, {})
        refs = {event.get("id"), event_id, event.get("occurrence_id"), event.get("prep_key"),
            record.get("provider_id"), record.get("source_occurrence_id")} - {None}
        matches = [live for live in live_rows if (live.get("calendar_event_id") in refs
            or aliases.get(live.get("calendar_event_id")) == event_id) and live.get("date") == first.date().isoformat()]
        alternatives = [{"kind": "calendar", "date": first.date().isoformat(), "start": None if event.get("all_day") else first.strftime("%H:%M"),
                         "end": None if event.get("all_day") else last.strftime("%H:%M"), "end_date": last.date().isoformat()}]
        for live in matches:
            linked_live.add(live["id"])
            if live.get("start"):
                alternatives.append({"kind": "work", "date": live["date"], "start": live["start"],
                    "end": hhmm((clock(live["start"]) + live["duration_minutes"]) % 1440),
                    "end_date": (datetime.fromisoformat(live["date"] + "T" + live["start"]) + timedelta(minutes=live["duration_minutes"])).date().isoformat()})
        conflict = len({(a["date"], a["start"], a["end"]) for a in alternatives}) > 1
        result.append({"id": event_id, "native_event_id": event["id"], "title": event.get("title") or event.get("summary", "Событие"),
            "date": first.date().isoformat(), "start": alternatives[0]["start"], "end": alternatives[0]["end"],
            "end_date": last.date().isoformat(), "duration_minutes": None if event.get("all_day") else round((last-first).total_seconds()/60),
            "all_day": bool(event.get("all_day")), "kind": "calendar", "conflict": conflict,
            "alternatives": alternatives if conflict else [],
            "project_id": native_tasks.get(matches[0].get("task_id"), {}).get("project_id") if matches else None,
            "source_ids": list(dict.fromkeys([event_id, event["id"], *[row["id"] for row in matches]]))})
    for live in live_rows:
        if live["id"] in linked_live or not live.get("date") or not live.get("start"):
            continue
        result.append({"id": live["id"], "title": live["title"], "date": live["date"], "start": live["start"],
            "end": hhmm((clock(live["start"]) + live["duration_minutes"]) % 1440),
            "end_date": (datetime.fromisoformat(live["date"] + "T" + live["start"]) + timedelta(minutes=live["duration_minutes"])).date().isoformat(),
            "duration_minutes": live["duration_minutes"], "all_day": False, "kind": "work",
            "conflict": False, "alternatives": [], "project_id": native_tasks.get(live.get("task_id"), {}).get("project_id"), "source_ids": [live["id"]]})
    return sorted(result, key=lambda row: (row["date"], row.get("start") or "", row["id"]))


def read_model(snapshot):
    state = snapshot["state"]
    planning, ext = state["planning"], extension(state)
    settings = planning["settings"]
    cubes = cube_rows(state)
    native_events = _event_rows(planning, ext.get("calendar_source", {}).get("events"))
    native_event_ids = {event["id"] for event in native_events}
    native_events.extend(deepcopy(event) for event_id, event in ext.get("retained_events", {}).items()
        if event_id not in native_event_ids)
    events, cubes, calendar_warnings = calendar_rules.enrich(state, native_events, cubes)
    cubes = timeline_rules.enrich(state, cubes)
    by_task = {}
    for cube in cubes:
        by_task.setdefault(cube.get("task_id"), []).append(cube)
    source_items = course_rules.catalog_items(state)
    source_by_task = {row["work_task_id"]: row for row in source_items if row.get("work_task_id")}
    result_tasks = {row["id"]: row for row in planning.get("result", {}).get("tasks", [])}
    remaining = wp._remaining_before_deletions(planning)
    native_cancelled = wp._retired_minutes(planning)
    overlay_cancelled = {}
    native_sources = _source_catalog(planning, ext.get("retained_cubes"))
    for record in ext.get("cancellations", {}).values():
        portions = record["source"].get("source_task_allocations")
        if portions:
            for portion in portions:
                tid = portion["task_id"]
                overlay_cancelled[tid] = overlay_cancelled.get(tid, 0) + portion["minutes"]
        else:
            tid = record["source"].get("task_id")
            if tid:
                overlay_cancelled[tid] = overlay_cancelled.get(tid, 0) + record["minutes"]
        # The same leaf can later be deleted through native Work. Its retirement
        # remains authoritative and must not be counted a second time here.
        for source_id in set(record.get("source_ids", [])):
            source = native_sources.get(source_id, {})
            deletions = planning.get("cube_deletions", {})
            aliases = {source_id, source.get("blueprint_ref")}
            if source_id.startswith("planned-"):
                aliases.add(source_id[len("planned-"):])
            aliases.discard(None)
            deletion = deletions.get(source_id) or next((row for row in deletions.values()
                if row.get("blueprint_ref") in aliases), None)
            tid = source.get("task_id")
            if tid and source.get("progress_percent"):
                # Progress on a previously cancelled leaf is still a native
                # completion fact. Retire only its unfinished scope; elapsed
                # actual time alone never supplies completion credit.
                credit = source.get("minutes", 0) * source["progress_percent"] / 100
                overlay_cancelled[tid] = max(0, overlay_cancelled.get(tid, 0) - credit)
            native_cube = planning.get("constructor", {}).get("cubes", {}).get((deletion or {}).get("cube_id"), {})
            if (deletion and tid and native_cube.get("kind") == "task"
                    and deletion.get("remaining_epoch", 0) == planning.get("remaining_epochs", {}).get(tid, 0)):
                overlap = min(source.get("minutes", 0), deletion.get("retired_minutes", 0))
                overlay_cancelled[tid] = max(0, overlay_cancelled.get(tid, 0) - overlap)
    tasks = []
    for native in planning["tasks"]:
        tid = native["id"]
        explicit = ext.get("periods", {}).get(tid)
        dates = [cube["planned_date"] for cube in by_task.get(tid, []) if cube.get("planned_date")]
        source = source_by_task.get(tid, {})
        total = native["estimate_minutes"]["central"]
        left = remaining.get(tid, result_tasks.get(tid, {}).get("remaining_minutes", total))
        # Completion derives exclusively from Work facts, never from a course table's status.
        percent = max(0, min(100, round(100 * (total-left) / total, 1))) if total else 0
        tasks.append({"id": tid, "project_id": native["project_id"],
            "title": ext.get("task_titles", {}).get(tid, native["title"]),
            "start": explicit["start"] if explicit else min(dates) if dates else native.get("methodist_start"),
            "end": explicit["end"] if explicit else max(dates) if dates else native.get("due"),
            "deadline": native.get("due"), "deadline_kind": native.get("due_status", "unknown"),
            "minutes": total, "source_minutes": total, "remaining_minutes": left,
            "cancelled_minutes": native_cancelled.get(tid, 0) + overlay_cancelled.get(tid, 0),
            "effective_remaining_minutes": max(0, left-native_cancelled.get(tid, 0)-overlay_cancelled.get(tid, 0)),
            "progress_percent": percent,
            "status": "completed" if percent >= 100 else "started" if percent > 0 else "available",
            "period_explicit": bool(explicit), "source_status": source.get("status"),
            "owned": bool(source.get("owned_by_user", True) and not wp._scope_exempt(planning, tid))})
    task_map = {row["id"]: row for row in tasks}
    projects = []
    for native in state["projects"]:
        pid = native["id"]
        project_tasks = []
        seen_tasks = set()
        for source in source_items:
            if source.get("project_id") != pid:
                continue
            linked = task_map.get(source.get("work_task_id"))
            seen_tasks.add(source.get("work_task_id"))
            project_tasks.append({"id": linked["id"] if linked else source["id"],
                "title": linked["title"] if linked else source["title"],
                "date": linked["start"] if linked else source.get("due"),
                "end": linked["end"] if linked else source.get("due"),
                "status": source.get("status", "unknown"), "source_status": source.get("status", "unknown"),
                "status_label": source.get("status_label"), "work_progress_percent": linked["progress_percent"] if linked else None,
                "deadline": linked["deadline"] if linked else source.get("due"),
                "deadline_kind": linked["deadline_kind"] if linked else source.get("due_status"),
                "owned": linked["owned"] if linked else source.get("owned_by_user", True), "order": source.get("order"),
                "auxiliary": source.get("auxiliary", False), "parent_id": source.get("parent_id")})
        for task in tasks:
            if task["project_id"] == pid and task["id"] not in seen_tasks:
                project_tasks.append({"id": task["id"], "title": task["title"], "date": task["start"], "end": task["end"],
                    "status": task["status"], "source_status": None, "deadline": task["deadline"],
                    "deadline_kind": task["deadline_kind"], "owned": task["owned"], "work_progress_percent": task["progress_percent"]})
        span = [d for row in project_tasks if row.get("owned") for d in (row.get("date"), row.get("end"), row.get("deadline")) if d]
        projects.append({"id": pid, "title": native.get("name") or native.get("title", pid),
            "color": native.get("color", "teal"), "visible": ext.get("visibility", {}).get(pid, True),
            "start": min(span) if span else settings["start"], "end": max(span) if span else settings["end"], "tasks": project_tasks})
    projects = course_rules.enrich(state, projects)
    projects = ruler_rules.enrich(state, projects)
    date_overrides = {task.get("source_work_task_id"): task for project in projects for task in project.get("tasks", [])
        if task.get("owner_date_override") and task.get("source_work_task_id") and not task.get("package_id")}
    for task in tasks:
        if task["id"] in date_overrides:
            override = date_overrides[task["id"]]
            task.update(source_deadline=task.get("deadline"), source_deadline_kind=task.get("deadline_kind"),
                deadline=override.get("deadline"), deadline_kind=override.get("deadline_kind", "confirmed"), owner_date_override=True)
    warnings = list(calendar_warnings)
    warnings.extend(dict.fromkeys(cube["source_change_reason"] for cube in cubes if cube.get("source_change_reason")))
    if any(row["conflict"] for row in events):
        warnings.append("Время календаря и Work различается; показаны оба варианта одного события.")
    if planning.get("calendar", {}).get("stale"):
        warnings.append("Календарь сохранён снимком; часть данных может быть устаревшей.")
    personal_tasks = [task for task in tasks if task["owned"]]
    reference_task_ids = {task["id"] for task in tasks if not task["owned"]}
    personal_cubes = [cube for cube in cubes if cube.get("task_id") not in reference_task_ids]
    reference_cubes = [cube for cube in cubes if cube.get("task_id") in reference_task_ids]
    return {"revision": snapshot["revision"], "today": datetime.now(ZoneInfo(settings["timezone"])).date().isoformat(),
        "context": context(state), "projects": projects, "tasks": tasks, "cubes": cubes, "events": events, "warnings": warnings,
        "calendar_coverage": calendar_rules.coverage(state),
        "totals": {"source_minutes": sum(t["minutes"] for t in personal_tasks),
            "source_all_minutes": sum(t["minutes"] for t in tasks),
            "reference_minutes": sum(t["minutes"] for t in tasks if not t["owned"]),
            "factual_remaining_minutes": sum(t["remaining_minutes"] for t in personal_tasks),
            "cancelled_minutes": sum(t["cancelled_minutes"] for t in personal_tasks),
            "effective_remaining_minutes": sum(t["effective_remaining_minutes"] for t in personal_tasks),
            "effective_cube_minutes": sum(c["minutes"] for c in personal_cubes if c["status"] != "cancelled"),
            "cancelled_cube_minutes": sum(c["minutes"] for c in personal_cubes if c["status"] == "cancelled"),
            "reference_cube_minutes": sum(c["minutes"] for c in reference_cubes),
            "source_all_cube_minutes": sum(c["minutes"] for c in cubes)},
        "settings": {"start": settings["start"], "end": settings["end"], "day_start": settings["work_start"],
            "day_end": settings["work_end"], "daily_limit_minutes": settings["day_limit_minutes"],
            "daily_limit_minutes_by_date": {
                **{key: value["minutes"] for key, value in settings.get("day_capacity_overrides", {}).items()},
                **{key: value["budget_minutes"] for key, value in planning.get("manual_days", {}).items()}}}}


def apply_context(state, data):
    if not isinstance(data, dict) or set(data) - CONTEXT_FIELDS:
        raise ValidationError("Неизвестное поле контекста")
    updated = {**context(state), **data}
    if "date" in data and data["date"] != context(state)["date"]:
        if "cube_id" not in data:
            updated["cube_id"] = None
        if "task_id" not in data:
            updated["task_id"] = None
        if "calendar_event_id" not in data:
            updated["calendar_event_id"] = None
    if updated["mode"] not in {"week", "month"}:
        raise ValidationError("Режим: week или month")
    selected = date.fromisoformat(day(updated["date"]))
    monday = (selected - timedelta(days=selected.weekday())).isoformat()
    if "date" in data and "week_start" not in data:
        updated["week_start"] = monday
    if updated["week_start"] != monday:
        raise ValidationError("Неделя должна начинаться в понедельник выбранной даты")
    for field in ("window_start", "expanded_week"):
        if updated[field] is not None:
            if date.fromisoformat(day(updated[field])).weekday():
                raise ValidationError("Начало недели должно быть понедельником")
    model = read_model({"state": state, "revision": 0})
    # Timeline history has source-only materials without an executable Work task.
    # They can be selected for inspection; typed task edits still resolve only
    # model.tasks in apply_command, so this does not authorize historical writes.
    timeline_tasks = model["tasks"] + [task for project in model["projects"] for task in project["tasks"]]
    for field, rows in (("task_id", timeline_tasks), ("cube_id", model["cubes"]), ("project_id", model["projects"]), ("calendar_event_id", model["events"])):
        if updated[field] is not None and updated[field] not in {row["id"] for row in rows}:
            raise ValidationError("Неизвестный " + field)
    state.setdefault(NAMESPACE, {})["context"] = updated
    return state


def _fields(data, required, optional=()):
    if not isinstance(data, dict) or not set(required) <= set(data) or set(data) - set(required) - set(optional):
        raise ValidationError("Неверные поля команды: " + ", ".join(required))


def _editable_cube(row, *, placement=False):
    if row["status"] == "cancelled":
        raise ValidationError("Кубик отменён; его история сохранена")
    if row.get("actual_minutes") or row.get("progress_percent"):
        raise ValidationError("У кубика уже есть факты: менять его состав или размещение нельзя")
    if not row.get("available"):
        raise ValidationError("Кубик недоступен: проверьте остаток и границы ответственности")


def _place(state, model, row, target, start):
    day(target)
    calendar_offer = row["kind"] == "calendar_optional"
    linked_calendar_prep = row["kind"] == "prep" and any(event["id"] == row.get("calendar_event_id") and event.get("calendar_role") == "flexible" and target in {event["date"], row.get("selected_date")} for event in model["events"])
    if not calendar_offer and not linked_calendar_prep and not model["settings"]["start"] <= target <= model["settings"]["end"]:
        raise ValidationError("Дата за пределами горизонта Work")
    if calendar_offer and row.get("duration_unknown"):
        raise ValidationError("В Calendar не указана длительность: сначала задайте минуты предложения явно")
    if calendar_offer and row.get("availability") == "day" and target != row["source_date"]:
        raise ValidationError("Серое предложение доступно только на предложенный источником день")
    task = next((t for t in model["tasks"] if t["id"] == row.get("task_id")), None)
    # A work period controls proposals, not an explicitly chosen placement.
    # Existing cubes and direct owner scheduling must not be lost on narrowing.
    if row["kind"] in {"prep", "weekly_review"} and target not in {row.get("planned_date"), row.get("selected_date")}:
        raise ValidationError("Подготовка и недельный разбор относятся к своей исходной дате")
    already_selected = any(cube["id"] == row["id"] and cube.get("selected_date") for cube in model["cubes"])
    if row["kind"] == "task" and not already_selected:
        def allocations(cube):
            return cube.get("source_task_allocations") or [{"task_id": cube.get("task_id"), "minutes": cube["minutes"]}]
        proposed = {part["task_id"]: part["minutes"] for part in allocations(row)}
        selected = {}
        for cube in model["cubes"]:
            if cube["id"] == row["id"] or cube.get("kind") != "task" or not cube.get("selected_date") or cube["status"] == "cancelled":
                continue
            for part in allocations(cube):
                tid = part["task_id"]
                selected[tid] = selected.get(tid, 0) + part["minutes"] * max(0, 100-cube.get("progress_percent", 0)) / 100
        for task in model["tasks"]:
            if task["id"] in proposed and proposed[task["id"]] + selected.get(task["id"], 0) > task["effective_remaining_minutes"]:
                raise ValidationError("Объём выбранных этапов превышает действующий остаток задачи с учётом выполненного и отменённого")
    calendar_rules.validate_slot(model, target, row["minutes"], start, exclude_cube=row["id"], work_window=not calendar_offer, allow_zero=row["kind"] == "prep")
    state.setdefault(NAMESPACE, {}).setdefault("placements", {})[row["id"]] = {"date": target, "start": start}


def apply_command(state, action, data):
    if action not in ACTIONS:
        raise ValidationError("Неизвестная команда периодного плана")
    model = read_model({"state": state, "revision": 0})
    ext = state.setdefault(NAMESPACE, {})
    if action in ruler_rules.ACTIONS:
        return ruler_rules.apply(state, action, data, model)
    if action == "cube_delete":
        return cube_ops.delete(state, data, model)
    if action == "cube_merge":
        return cube_ops.merge(state, data, model, _place)
    if action == "cube_resize":
        return timeline_rules.resize(state, data, model, _place)
    if action == "course_source_import":
        _fields(data, {"source"})
        return course_rules.apply_source(state, data["source"])
    if action == "calendar_event_rename":
        _fields(data, {"event_id", "title"})
        if not any(event["id"] == data["event_id"] for event in model["events"]):
            raise ValidationError("Календарный блок не найден; перечитайте план")
        ext.setdefault("calendar_event_titles", {})[data["event_id"]] = text(data["title"])
        return state
    if action == "assignment_rename":
        _fields(data, {"task_id", "title"})
        assignment = next((task for project in model["projects"] for task in project["tasks"] if task["id"] == data["task_id"]), None)
        if assignment is None:
            raise ValidationError("Задание не найдено; перечитайте план")
        goal = next((p for p in model["projects"] if p.get("kind") == "goal" and any(t["id"] == data["task_id"] for t in p["tasks"])), None)
        if goal is not None and data["task_id"] == goal["id"] + ":goal":
            return ruler_rules.apply(state, "ruler_update", {"ruler_id": goal["id"], "title": text(data["title"])}, model)
        title = text(data["title"])
        ext.setdefault("course_task_titles", {})[data["task_id"]] = title
        native_id = data["task_id"] if any(task["id"] == data["task_id"] for task in model["tasks"]) else assignment.get("source_work_task_id") if not assignment.get("package_id") else None
        if native_id:
            ext.setdefault("task_titles", {})[native_id] = title
            for project in model["projects"]:
                for task in project["tasks"]:
                    if task.get("source_work_task_id") == native_id and not task.get("package_id"):
                        ext.setdefault("course_task_titles", {})[task["id"]] = title
        return state
    if action == "calendar_source_import":
        _fields(data, {"source"})
        return calendar_rules.apply_import(state, data["source"])
    if action == "calendar_event_move":
        _fields(data, {"event_id", "date", "start"})
        event = next((event for event in model["events"] if event["id"] == data["event_id"]), None)
        if not event or not event.get("movable"):
            raise ValidationError((event or {}).get("move_block_reason") or "Событие не найдено или закреплено источником Calendar")
        clock(data["start"])
        placement = calendar_rules.validate_slot(model, data["date"], event["duration_minutes"], data["start"], exclude_event=event["id"])
        ext.setdefault("calendar_event_placements", {})[event["id"]] = placement
        return state
    if action == "calendar_offer_estimate":
        _fields(data, {"cube_id", "minutes"})
        row = next((cube for cube in model["cubes"] if cube["id"] == data["cube_id"]), None)
        if not row or row["kind"] != "calendar_optional":
            raise ValidationError("Длительность задаётся только необязательному предложению Calendar")
        _editable_cube(row)
        if row.get("selected_date"):
            raise ValidationError("Перед изменением длительности верните предложение на полку")
        if type(data["minutes"]) is not int or not 1 <= data["minutes"] <= 1440:
            raise ValidationError("Укажите целое число минут от1 до1440")
        if row.get("source_duration_minutes") is not None and data["minutes"] != row["source_duration_minutes"]:
            raise ValidationError("Длительность указана в Calendar; её нельзя менять локальной оценкой")
        ext.setdefault("calendar_offer_estimates", {})[row["id"]] = data["minutes"]
        return state
    if action in {"period_set", "period_clear", "task_rename"}:
        _fields(data, {"task_id", "start", "end"} if action == "period_set" else {"task_id", "title"} if action == "task_rename" else {"task_id"})
        task = next((t for t in model["tasks"] if t["id"] == data["task_id"]), None)
        if not task:
            raise ValidationError("Задача не найдена")
        if not task["owned"]:
            raise ValidationError("Справочная задача вне вашей работы: доступно только чтение")
        if action == "period_set":
            first, last = day(data["start"]), day(data["end"])
            if first > last:
                raise ValidationError("Конец периода раньше начала")
            ext.setdefault("periods", {})[task["id"]] = {"start": first, "end": last}
        elif action == "period_clear":
            ext.setdefault("periods", {}).pop(task["id"], None)
        else:
            title = text(data["title"])
            ext.setdefault("task_titles", {})[task["id"]] = title
            for project in model["projects"]:
                for assignment in project["tasks"]:
                    if assignment.get("source_work_task_id") == task["id"] and not assignment.get("package_id"):
                        ext.setdefault("course_task_titles", {})[assignment["id"]] = title
        return state
    if action == "project_visibility":
        _fields(data, {"project_id", "visible"})
        if data["project_id"] not in {p["id"] for p in model["projects"]} or type(data["visible"]) is not bool:
            raise ValidationError("Требуются существующий проект и visible=true/false")
        ext.setdefault("visibility", {})[data["project_id"]] = data["visible"]
        return state
    required = {"cube_id", "date"} if action in {"cube_select", "cube_move"} else {"cube_id", "start"} if action == "cube_time" else {"cube_id", "title"} if action == "cube_rename" else {"cube_id", "reason"} if action == "cube_cancel" else {"cube_id"}
    _fields(data, required, {"start"} if action in {"cube_select", "cube_move"} else ())
    row = next((c for c in model["cubes"] if c["id"] == data["cube_id"]), None)
    if row is None:
        raise ValidationError("Кубик не найден или уже объединён; обновите экран")
    if action == "cube_rename":
        title = text(data["title"])
        ext.setdefault("cube_titles", {})[row["id"]] = title
        if row.get("kind") == "calendar_optional":
            for occurrence in row.get("source_occurrences", []):
                ext.setdefault("calendar_event_titles", {})[occurrence["event_id"]] = title
        return state
    _editable_cube(row, placement=True)
    if action == "cube_return":
        ext.setdefault("placements", {})[row["id"]] = {"date": None, "start": None}
    elif action == "cube_cancel":
        # Scope cancellation is explicit, persisted separately, and never erases a fact.
        if row.get("selected_date"):
            raise ValidationError("Сначала верните кубик на полку; затем явно отмените его остаток")
        task = next((task for task in model["tasks"] if task["id"] == row.get("task_id")), None)
        if task and row["minutes"] > task["effective_remaining_minutes"]:
            raise ValidationError("Объём кубика превышает действующий остаток задачи; отмена небезопасна")
        ext.setdefault("cancellations", {})[row["id"]] = {"reason": text(data["reason"]), "minutes": row["minutes"],
            "source_ids": deepcopy(row["source_ids"]), "source": deepcopy(row)}
    elif action == "cube_time":
        if not row.get("selected_date"):
            raise ValidationError("Сначала выберите кубик на день")
        _place(state, model, row, row["selected_date"], data["start"])
    else:
        if action == "cube_move" and not row.get("selected_date"):
            raise ValidationError("Переносится выбранный кубик; для полки используйте выбор на день")
        _place(state, model, row, data["date"], data.get("start", row.get("start") if action == "cube_move" else None))
    return state


def _empty_projection(snapshot):
    """Render an empty installation without creating personal tasks or state."""
    state = deepcopy(snapshot.get("state") or {"version": 1, "projects": [], "items": [], "boards": []})
    today = datetime.now(ZoneInfo("UTC")).date()
    state["planning"] = {"version": 1,
        "settings": {"timezone": "UTC", "start": today.isoformat(),
            "end": (today + timedelta(days=90)).isoformat(), "work_start": "11:00", "work_end": "20:00",
            "day_limit_minutes": 300, "prep_minutes": 25, "break_minutes": 15, "workdays": 6},
        "tasks": [], "calendar": {"events": []}, "completed_cubes": {}, "day_outcomes": [],
        "locks": [], "history": [], "result": {}}
    result = read_model({**snapshot, "state": state})
    # A missing Calendar source is not an actionable warning on an empty plan.
    result["warnings"] = []
    return result


class PeriodPlanner:
    """Typed namespace edits sharing WorkStore's CAS, receipts and delete recovery.

    No native planning field is rewritten. The effective model combines current
    native sources and facts with the period_planner display/scheduling overlay.
    """

    def __init__(self, store):
        self.store = store

    def _model(self, snapshot):
        ready = isinstance((snapshot.get("state") or {}).get("planning"), dict)
        result = read_model(snapshot) if ready else _empty_projection(snapshot)
        result["initialized"] = snapshot["initialized"]
        result["planning_initialized"] = ready
        result["provenance"] = {"source": "work", "training_copy": False,
            "source_revision": snapshot["revision"]}
        if snapshot.get("deletion_state") is not None:
            result["deletion_state"] = deepcopy(snapshot["deletion_state"])
        return result

    def snapshot(self):
        return self.store.read()

    def model(self):
        return self._model(self.snapshot())

    def day_model(self):
        from .day import day_model
        return day_model(self.snapshot())

    def day_command(self, command):
        from .day import day_command
        return day_command(self, command)

    @staticmethod
    def _envelope(command, fields):
        if not isinstance(command, dict) or set(command) != set(fields):
            raise ValidationError("Нужна типизированная команда с request_id и expected_revision")
        identifier(command["request_id"], "request_id")
        # Validate JSON before opening the transaction or computing its receipt.
        encoded(command)

    def _write(self, command, scope, transform, *, context_only=False):
        from ..work_delete import recover, ensure_writable
        recover(self.store)
        with self.store.db() as db:
            ensure_writable(db)
            old = self.store.receipt(db, scope, command)
            if old is not None:
                return old
            current = self.store._version(db)
            revision = current["revision"] if current else 0
            self.store.check_revision(command, revision)
            snapshot = self.store._read(db, current)
            before = snapshot["state"]
            if not isinstance((before or {}).get("planning"), dict):
                raise ValidationError("План Work ещё не инициализирован; сначала импортируйте или создайте план Work")
            try:
                state = transform(deepcopy(before))
            except ValidationError:
                raise
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                raise ValidationError("Неверные данные периодного плана: " + str(exc)) from None
            # Enforce the boundary here, independently of individual commands.
            if ({key: value for key, value in state.items() if key != NAMESPACE}
                    != {key: value for key, value in before.items() if key != NAMESPACE}):
                raise ValidationError("Команда периодного плана не может менять исходные данные Work")
            self.store._guard_deletions(before["planning"], state["planning"])
            raw = encoded(state).encode("utf-8")
            digest = hashlib.sha256(raw).hexdigest()
            db.execute("INSERT OR IGNORE INTO work_blobs VALUES(?,?)", (digest, raw))
            # Work validates descriptors as exactly id/blob/mime. Preserve every
            # descriptor, including images currently unattached to any board.
            images = [{key: image[key] for key in ("id", "blob", "mime")} for image in snapshot["images"]]
            descriptors = self.store._validate(db, digest, images)
            db.execute("INSERT INTO work_versions VALUES(?,?,?,?)", (revision + 1, int(time.time()*1000), digest, encoded(descriptors)))
            result = {"context": context(state), "revision": revision + 1} if context_only else self._model(self.store._read(db))
            return self.store.remember(db, scope, command, result)

    def command(self, command):
        self._envelope(command, {"request_id", "expected_revision", "action", "data"})
        if not isinstance(command["action"], str) or command["action"] not in ACTIONS or not isinstance(command["data"], dict):
            raise ValidationError("Нужны известная команда периодного плана и объект data")
        return self._write(command, "period-planner", lambda state: apply_command(state, command["action"], command["data"]))

    def set_context(self, command):
        self._envelope(command, {"request_id", "expected_revision", "patch"})
        if not isinstance(command["patch"], dict):
            raise ValidationError("Контекст: нужен объект patch")
        return self._write(command, "period-planner-context", lambda state: apply_context(state, command["patch"]), context_only=True)

    def adopt(self, command):
        """Adopt an explicitly reviewed namespace blob without importing native Work."""
        from .adoption import adopt_namespace
        self._envelope(command, {"request_id", "expected_revision", "source_blob"})
        self.store._hash(command["source_blob"])
        # Blob reading is immutable; the live state is checked in _write's CAS.
        with self.store.db() as db:
            raw = self.store._blob(db, command["source_blob"])
        return self._write(command, "period-planner-adopt", lambda state: adopt_namespace(state, raw))
