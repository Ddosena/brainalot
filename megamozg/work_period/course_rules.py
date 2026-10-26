"""Reviewed private course deliverables, separate from native Work planning.

``apply_source(state, source)`` persists a validated source map in the period_planner
namespace. ``enrich(state, projects)`` projects its packages and components into
display metadata; only ``timeline_entries`` belongs on the delivery arrow.
Neither function reads files, allocates effort, or changes a native Work task.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import json

from ..core import ValidationError

SCHEMA = "private-proposed-course-deliverables-v1"


def catalog_items(state):
    """Use adopted documentary metadata while current native records take priority."""
    native = state.get("planning", {}).get("course_catalog", {}).get("items", [])
    native_ids = {row["id"] for row in native}
    native_tasks = {row.get("work_task_id") for row in native if row.get("work_task_id")}
    adopted = state.get("period_planner", {}).get("course_catalog", {}).get("items", [])
    return [row for row in adopted if row["id"] not in native_ids
        and row.get("work_task_id") not in native_tasks] + list(native)


def _text(value, field):
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise ValidationError(f"Course source: требуется {field}")
    return value


def _day(value, field):
    if value is None:
        return None
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except (TypeError, ValueError):
        raise ValidationError(f"Course source: неверная дата {field}") from None
    return value


def _completion(record):
    value = record.get("full_completion")
    value = value.get("value") if isinstance(value, dict) else value
    if value is not None and type(value) is not bool:
        raise ValidationError("Course source: полная готовность требует true, false или null")
    return value


def _references(record):
    refs = record.get("source_refs", record.get("evidence", []))
    refs = deepcopy(refs)
    if record.get("window_source"):
        refs.append({"source": record["window_source"],
                     "locator": record.get("window_source_locator"),
                     "role": "official_package_window"})
    return refs


def _validated(state, source):
    if (not isinstance(source, dict) or source.get("schema") != SCHEMA
            or source.get("private") is not True):
        raise ValidationError("Course source: требуется приватная карта известной схемы")
    try:
        size = len(json.dumps(source, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError, OverflowError):
        raise ValidationError("Course source: карта должна содержать обычный JSON") from None
    if size > 4_000_000:
        raise ValidationError("Course source: карта слишком велика")
    packages, rows = source.get("packages"), source.get("rows")
    if (not isinstance(packages, list) or not isinstance(rows, list)
            or len(packages) > 200 or len(rows) > 1000 or not packages and not rows):
        raise ValidationError("Course source: требуются packages и rows")
    projects = {p["id"] for p in state.get("projects", [])}
    native = {t["id"]: t for t in state.get("planning", {}).get("tasks", [])}
    ids = set()
    for record in packages + rows:
        if not isinstance(record, dict):
            raise ValidationError("Course source: строка должна быть объектом")
        rid = _text(record.get("id"), "id")
        if rid in ids:
            raise ValidationError("Course source: повторяющийся id")
        ids.add(rid)
        if not isinstance(record.get("project_id"), str) or record["project_id"] not in projects:
            raise ValidationError("Course source: проект не найден")
        _text(record.get("full_name"), "full_name")
        _text(record.get("short_name"), "short_name")
        _completion(record)
        for field in ("owned", "owned_by_user", "scope_exempt"):
            if field in record and type(record[field]) is not bool:
                raise ValidationError("Course source: scope требует true/false")
        for field in ("due", "individual_due", "window_start", "window_end", "deadline"):
            if field in record:
                _day(record[field], field)
        for field in ("source_refs", "evidence"):
            if field in record and (not isinstance(record[field], list)
                    or any(not isinstance(ref, (dict, str)) for ref in record[field])):
                raise ValidationError("Course source: неверные ссылки на источники")
        task_id = record.get("source_work_task_id")
        if task_id is not None:
            if not isinstance(task_id, str) or task_id not in native:
                raise ValidationError("Course source: связанная Work-задача не найдена")
            if native[task_id].get("project_id") != record["project_id"]:
                raise ValidationError("Course source: Work-задача относится к другому проекту")
    indexed = {record["id"]: record for record in packages}
    for package in packages:
        first, last = package.get("window_start"), package.get("window_end")
        if (first is None) != (last is None) or first is not None and first > last:
            raise ValidationError("Course source: требуется полное корректное окно пакета")
        if package.get("deadline") is not None and package["deadline"] != last:
            raise ValidationError("Course source: дедлайн пакета должен совпадать с концом окна")
    for row in rows:
        package_id = row.get("package_id")
        if package_id is not None:
            parent = indexed.get(package_id) if isinstance(package_id, str) else None
            if not parent or parent["project_id"] != row["project_id"]:
                raise ValidationError("Course source: родительский пакет не найден")
            if row.get("due") is not None or row.get("individual_due") is not None:
                raise ValidationError("Course source: компонент использует окно пакета, не отдельный дедлайн")
            for field in ("window_start", "window_end"):
                if field in row and row[field] != parent.get(field):
                    raise ValidationError("Course source: окно компонента расходится с пакетом")
        elif row.get("individual_due") is not None and row["individual_due"] != row.get("due"):
            raise ValidationError("Course source: индивидуальные сроки расходятся")
    return deepcopy(source)


def apply_source(state, source):
    """Return a new state; the typed command passes ``data['source']`` here."""
    source = _validated(state, source)
    result = deepcopy(state)
    ext = result.setdefault("period_planner", {})
    ext["course_source"] = source
    valid = {r["id"] for r in source["packages"] + source["rows"]}
    valid.update(r["id"] for r in state.get("planning", {}).get("tasks", []))
    valid.update(r["id"] for r in state.get("items", []))
    valid.update(r["id"] for r in catalog_items(state)
                 if r.get("owned_by_user") is False)
    valid.update(ruler_id + ":goal" for ruler_id in ext.get("rulers", {}))
    valid.update(project_id + ":ruler-notes" for project_id, entries in ext.get("ruler_entry_overrides", {}).items()
                 if any(entry.get("owner_created") and entry.get("visible", True) for entry in entries.values()))
    context = ext.get("context", {})
    if context.get("task_id") is not None and context["task_id"] not in valid:
        context["task_id"] = None
    return result


def _owned(record, native_display):
    return (record.get("owned", True) is not False
            and record.get("owned_by_user", True) is not False
            and record.get("scope_exempt", False) is not True
            and native_display.get("owned", True) is not False)


def _display(record, native_display, package=None):
    complete = _completion(record)
    task_id = record.get("source_work_task_id")
    native = native_display.get(task_id, {})
    percent = native.get("work_progress_percent", native.get("progress_percent"))
    details = record.get("full_completion")
    details = deepcopy(details) if isinstance(details, dict) else {"value": complete}
    owned = _owned(record, native)
    if package is not None:
        owned = owned and package["owned"]
    first = package["window_start"] if package is not None else record.get("window_start")
    last = package["window_end"] if package is not None else record.get("window_end")
    started = type(percent) in (int, float) and percent > 0
    status = "completed" if complete is True else "started" if started else "available"
    return {"id": record["id"], "project_id": record["project_id"],
            "title": record["full_name"], "full_name": record["full_name"],
            "short_name": record["short_name"], "kind": record.get("kind", "package"),
            "package_id": record.get("package_id"), "curriculum_order": record.get("curriculum_order"),
            "parent_id": record.get("package_id"), "catalog_id": record.get("catalog_id"),
            "source_work_task_id": task_id, "source_work_item_id": record.get("source_work_item_id"),
            "source_refs": _references(record), "read_only": True, "owned": owned,
            "auxiliary": False, "timeline_visible": package is None and owned,
            "window_start": first, "window_end": last,
            "date": first if first is not None else record.get("due"),
            "end": last if last is not None else record.get("due"),
            "individual_due": None if package is not None else record.get("individual_due", record.get("due")),
            "deadline": last if package is None and last else record.get("due"),
            "deadline_kind": record.get("deadline_kind", record.get("due_status",
                "source_package_window" if record.get("window_source") else "unknown")),
            "full_completion": complete, "full_checkmark": complete is True,
            "completion_details": details, "completion_basis": record.get("completion_basis"),
            "status": status, "source_status": record.get("status"),
            "status_label": record.get("status_label") or ("Задание готово" if complete is True else "Задание ещё не закрыто" if complete is False else "Готовность не подтверждена"),
            "status_note": record.get("status_note") or details.get("reason"),
            "work_progress_percent": percent,
            "factual_work_progress": {"task_id": task_id, "percent": percent,
                "remaining_minutes": native.get("remaining_minutes"),
                "effective_remaining_minutes": native.get("effective_remaining_minutes")},
            "historical_window": deepcopy(record.get("historical_window")),
            "historical_due": record.get("historical_due"),
            "source_details": deepcopy(record)}


def _entry(task, event_kind, when):
    return {"id": task["id"] + ":" + event_kind, "task_id": task["id"],
            "project_id": task["project_id"], "event_kind": event_kind, "date": when,
            "label": task["short_name"], "title": task["full_name"],
            "event_label": "Начало разработки" if event_kind == "start" else "Дедлайн",
            "source_refs": deepcopy(task["source_refs"]), "deadline_kind": task["deadline_kind"],
            "window_start": task["window_start"], "window_end": task["window_end"],
            "source_work_task_id": task["source_work_task_id"], "read_only": True,
            "owned": True, "status": task["status"], "full_completion": task["full_completion"],
            "full_checkmark": task["full_checkmark"]}


def enrich(state, projects):
    """Copy projects and attach canonical tasks and authoritative arrow entries.

    Native ``model.tasks`` remains the executable week plan. Package components
    appear in ``project.tasks`` and ``package.components`` only for detail views.
    Reference/auxiliary rows remain readable but never produce delivery events.
    """
    result = deepcopy(projects)
    source = state.get("period_planner", {}).get("course_source")
    if not source:
        return result
    source_projects = {r["project_id"] for r in source["packages"] + source["rows"]}
    aliases = state.get("period_planner", {}).get("course_task_titles", {})
    native_aliases = state.get("period_planner", {}).get("task_titles", {})
    auxiliary_ids = {r["id"] for r in source.get("auxiliary", [])}
    for project in result:
        pid = project["id"]
        if pid not in source_projects:
            continue
        original = project.get("tasks", [])
        native_display = {r["id"]: r for r in original}
        packages, tasks, events = {}, [], []
        records = [r for r in source["packages"] + source["rows"] if r["project_id"] == pid]
        represented = {value for r in records for value in
                       (r.get("id"), r.get("source_work_task_id"), r.get("catalog_id")) if value}
        for record in source["packages"]:
            if record["project_id"] != pid:
                continue
            task = _display(record, native_display)
            task["kind"] = "package"
            task["component_ids"], task["components"] = [], []
            packages[task["id"]] = task
            tasks.append(task)
            if task["owned"] and task["window_start"] and task["window_end"]:
                events.extend((_entry(task, "start", task["window_start"]),
                               _entry(task, "deadline", task["window_end"])))
        for record in source["rows"]:
            if record["project_id"] != pid:
                continue
            parent = packages.get(record.get("package_id"))
            task = _display(record, native_display, parent)
            tasks.append(task)
            if parent is not None:
                parent["component_ids"].append(task["id"])
                parent["components"].append(deepcopy(task))
            elif task["owned"] and record.get("due"):
                events.append(_entry(task, "deadline", record["due"]))
        for old in original:
            if old["id"] not in represented:
                old.update(read_only=True, timeline_visible=False)
                if old["id"] in auxiliary_ids or old.get("auxiliary"):
                    old["auxiliary"] = True
                tasks.append(old)
        dates = [entry["date"] for entry in events]
        project.update(tasks=tasks, timeline_entries=sorted(events, key=lambda e: (e["date"], e["event_kind"] != "start", e["id"])),
                       start=min(dates) if dates else None, end=max(dates) if dates else None,
                       timeline_start=min(dates) if dates else None, timeline_end=max(dates) if dates else None,
                       undated_task_ids=[t["id"] for t in tasks if t.get("timeline_visible") and not t.get("date")],
                       course_source={"schema": source["schema"], "checked_on": source.get("checked_on"), "private": True})
        def apply_alias(task):
            task.setdefault("source_title", task.get("full_name", task.get("title")))
            task["can_rename"] = True
            alias = aliases.get(task["id"])
            if alias is None and not task.get("package_id"):
                alias = native_aliases.get(task.get("source_work_task_id"))
            if alias is not None:
                task["title"] = alias
                task["short_name"] = alias
            for child in task.get("components", []):
                apply_alias(child)
        for task in tasks:
            apply_alias(task)
        names = {task["id"]: task for task in tasks}
        for event in project["timeline_entries"]:
            task = names[event["task_id"]]
            event["source_title"] = task["source_title"]
            event["title"], event["label"] = task["title"], task.get("short_name", task["title"])
    return result
