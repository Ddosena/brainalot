"""Owner-controlled ruler metadata, isolated from native Work and source facts.

Call enrich after course_rules.enrich. All writes are under period_planner:
rulers stores custom goals, ruler_overrides stores source-project display aliases,
ruler_entry_overrides stores source-entry display changes and added milestones,
ruler_task_completion stores explicit assignment completion, ruler_archive keeps
previous records. visibility is the existing authoritative project preference.
No function allocates effort, infers completion, or changes a source/native row.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date

from ..core import ValidationError
from ..shared_state import identifier

ACTIONS = frozenset({"ruler_create", "ruler_update", "ruler_delete",
                     "ruler_entry_upsert", "ruler_entry_delete"})
KINDS = frozenset({"start", "deadline", "milestone"})


def _text(value, field, *, empty=False):
    if not isinstance(value, str) or len(value) > 2000 or not empty and not value.strip():
        raise ValidationError(f"Линейка: неверный текст {field}")
    return value.strip()


def _day(value, field):
    if value is None:
        return None
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except (TypeError, ValueError):
        raise ValidationError(f"Линейка: требуется ISO-дата {field}") from None
    return value


def _bool(value, field, *, nullable=False):
    if type(value) is not bool and not (nullable and value is None):
        raise ValidationError(f"Линейка: {field} требует true/false" + ("/null" if nullable else ""))
    return value


def _window(first, last):
    if first is not None and last is not None and first > last:
        raise ValidationError("Линейка: начало позже дедлайна")


def _archive(ext, action, pid, previous, entry_id=None):
    ext.setdefault("ruler_archive", []).append({"action": action, "ruler_id": pid,
        "entry_id": entry_id, "previous": deepcopy(previous)})


def _clear_hidden_note_context(ext, pid, project):
    """A documentary note parent exists only while it has a visible entry."""
    overrides = ext.get("ruler_entry_overrides", {}).get(pid, {})
    entries = {entry["id"]: entry for entry in project.get("timeline_entries", [])}
    entries.update({eid: edit for eid, edit in overrides.items() if edit.get("owner_created")})
    visible_notes = any(entry.get("owner_created") and entry.get("task_id") == pid + ":ruler-notes"
        and overrides.get(eid, {}).get("visible", entry.get("visible", True)) is not False
        for eid, entry in entries.items())
    context = ext.get("context", {})
    if not visible_notes and context.get("task_id") == pid + ":ruler-notes":
        context["task_id"] = None


def _entry(pid, eid, task_id, title, when, kind):
    return {"id": eid, "project_id": pid, "task_id": task_id, "title": title,
        "label": title, "date": when, "event_kind": kind, "full_completion": None,
        "full_checkmark": False, "source_refs": [], "owner_created": True,
        "deadline_kind": "confirmed" if kind == "deadline" and when is not None else "unknown",
        "read_only": True, "can_edit": True, "visible": True, "owned": True}


def _documentary_task(pid, tid, title, description="", first=None, last=None):
    return {"id": tid, "project_id": pid, "title": title, "short_name": title,
        "full_name": title, "description": description, "kind": "goal",
        "date": first, "end": last, "window_start": first, "window_end": last,
        "deadline": last, "timeline_visible": True, "read_only": True,
        "deadline_kind": "confirmed" if last is not None else "unknown",
        "owned": True, "owner_created": True, "documentary_only": True,
        "source_refs": [], "full_completion": None, "full_checkmark": False,
        "status": "available", "work_progress_percent": None}


def _custom_project(goal):
    pid = goal["id"]
    task = _documentary_task(pid, pid + ":goal", goal["title"], goal.get("description", ""),
                             goal.get("start"), goal.get("end"))
    return {"id": pid, "title": goal["title"], "description": goal.get("description", ""),
        "kind": "goal", "owner_created": True, "can_edit": True, "visible": True,
        "color": "teal", "start": goal.get("start"), "end": goal.get("end"),
        "tasks": [task], "timeline_entries": list(deepcopy(goal.get("entries", {})).values())}


def _source_fields(row, fields):
    for field in fields:
        row.setdefault("source_" + field, deepcopy(row.get(field)))


def _complete(row, value):
    _source_fields(row, ("full_completion", "status_label"))
    row.update(full_completion=value, full_checkmark=value is True, owner_completion_override=True)
    percent = row.get("work_progress_percent")
    row["status"] = "completed" if value is True else "started" if type(percent) in (int, float) and percent > 0 else "available"
    row["status_label"] = "Задание готово" if value is True else "Задание ещё не закрыто" if value is False else "Готовность не подтверждена"


def _sync_windows(project, edits):
    """Only an explicitly edited edge changes its assignment's display window."""
    tasks = {row["id"]: row for row in project.get("tasks", [])}
    changed = {}
    for event in project.get("timeline_entries", []):
        edit = edits.get(event["id"], {})
        if "date" in edit and not event.get("owner_created"):
            _source_fields(event, ("deadline_kind",))
            event.update(deadline_kind="confirmed", owner_date_override=True)
            changed.setdefault(event["task_id"], {})[event["event_kind"]] = event.get("date")
    for tid, values in changed.items():
        task = tasks.get(tid)
        if task is None:
            continue
        _source_fields(task, ("deadline_kind",))
        task.update(deadline_kind="confirmed", owner_date_override=True)
        if task.get("kind") == "package" or task.get("window_start") is not None or task.get("window_end") is not None:
            _source_fields(task, ("window_start", "window_end", "deadline", "date", "end"))
            if "start" in values:
                task.update(window_start=values["start"], date=values["start"])
            if "deadline" in values:
                task.update(window_end=values["deadline"], deadline=values["deadline"], end=values["deadline"])
            for child in project.get("tasks", []):
                if child.get("package_id", child.get("parent_id")) == tid:
                    _source_fields(child, ("window_start", "window_end", "deadline", "date", "end"))
                    child.update(window_start=task.get("window_start"), window_end=task.get("window_end"),
                                 date=task.get("window_start"), end=task.get("window_end"), owner_date_override=True)
            for event in project.get("timeline_entries", []):
                if event.get("task_id") == tid:
                    _source_fields(event, ("window_start", "window_end", "deadline_kind"))
                    event.update(window_start=task.get("window_start"), window_end=task.get("window_end"),
                                 deadline_kind="confirmed", owner_date_override=True)
        elif "deadline" in values:
            _source_fields(task, ("date", "end", "deadline", "individual_due"))
            task.update(date=values["deadline"], end=values["deadline"],
                        deadline=values["deadline"], individual_due=values["deadline"])
    for task in project.get("tasks", []):
        if "components" in task:
            task["components"] = [deepcopy(tasks.get(child["id"], child)) for child in task["components"]]


def enrich(state, projects):
    """Return display projects; retained hidden entries can be restored in an editor."""
    ext = state.get("period_planner", {})
    result = deepcopy(projects)
    for goal in ext.get("rulers", {}).values():
        result.append(_custom_project(goal))
    for project in result:
        pid = project["id"]
        project["can_edit"] = True
        override = ext.get("ruler_overrides", {}).get(pid, {})
        if override:
            _source_fields(project, ("title", "description", "start", "end"))
            project.update(deepcopy(override), owner_override=True)
        project["visible"] = ext.get("visibility", {}).get(pid, project.get("visible", True))
        edits = ext.get("ruler_entry_overrides", {}).get(pid, {})
        entries = project.setdefault("timeline_entries", [])
        ids = {row["id"] for row in entries}
        for eid, edit in edits.items():
            if edit.get("owner_created") and eid not in ids:
                entries.append(deepcopy(edit))
        for event in entries:
            event.setdefault("visible", True)
            event["can_edit"] = True
            edit = edits.get(event["id"], {})
            if edit and not event.get("owner_created"):
                _source_fields(event, ("title", "label", "date", "full_completion"))
                event.update(deepcopy(edit), owner_override=True)
                if "title" in edit:
                    event["label"] = edit["title"]
            if "full_completion" in edit:
                _complete(event, edit["full_completion"])
            if event.get("owner_created"):
                event["deadline_kind"] = "confirmed" if event.get("event_kind") == "deadline" and event.get("date") is not None else "unknown"
        notes = [e for e in entries if e.get("owner_created") and e["task_id"] == pid + ":ruler-notes"
                 and e.get("visible") is not False]
        if notes:
            project.setdefault("tasks", []).append(_documentary_task(pid, pid + ":ruler-notes", project["title"]))
        _sync_windows(project, edits)
        completion = ext.get("ruler_task_completion", {}).get(pid, {})
        for task in project.get("tasks", []):
            if task["id"] in completion:
                _complete(task, completion[task["id"]])
            for child in task.get("components", []):
                if child["id"] in completion:
                    _complete(child, completion[child["id"]])
        for event in entries:
            if event["task_id"] in completion:
                _complete(event, completion[event["task_id"]])
        active_dates = [e["date"] for e in entries if e.get("visible", True) and e.get("date")]
        project["timeline_start"] = min(active_dates) if active_dates else None
        project["timeline_end"] = max(active_dates) if active_dates else None
        if not project.get("owner_created") and any("date" in edit for edit in edits.values()):
            _source_fields(project, ("start", "end"))
            project.update(start=project["timeline_start"], end=project["timeline_end"])
        project["undated_task_ids"] = [t["id"] for t in project.get("tasks", [])
            if t.get("timeline_visible") and not t.get("date")]
        entries.sort(key=lambda e: (e.get("date") is None, e.get("date") or "", e["event_kind"] != "start", e["id"]))
    return result


def apply(state, action, data, model):
    """Validate one typed ruler action and return a copy of state.

    model must be the current enriched model. Existing native entry kinds and
    parent links are fixed; their labels/dates are local overrides. Native
    ruler_update start/end is intentionally rejected: choose a specific edge.
    """
    if action not in ACTIONS or not isinstance(data, dict):
        raise ValidationError("Линейка: неизвестная команда")
    fields = {
        "ruler_create": {"ruler_id", "title", "start", "end", "description"},
        "ruler_update": {"ruler_id", "title", "description", "start", "end", "visible"},
        "ruler_delete": {"ruler_id"},
        "ruler_entry_upsert": {"ruler_id", "entry_id", "title", "date", "event_kind", "full_completion", "visible"},
        "ruler_entry_delete": {"ruler_id", "entry_id"},
    }[action]
    if set(data) - fields:
        raise ValidationError("Линейка: неизвестные поля команды")
    pid = identifier(data.get("ruler_id"), "ruler_id")
    for field in ("title", "description"):
        if field in data:
            _text(data[field], field, empty=field == "description")
    for field in ("start", "end", "date"):
        if field in data:
            _day(data[field], field)
    for field in ("visible", "full_completion"):
        if field in data:
            _bool(data[field], field, nullable=field == "full_completion")
    if "event_kind" in data and (not isinstance(data["event_kind"], str) or data["event_kind"] not in KINDS):
        raise ValidationError("Линейка: неизвестный тип отметки")
    result = deepcopy(state)
    ext = result.setdefault("period_planner", {})
    goals = ext.setdefault("rulers", {})
    projects = {p["id"]: p for p in model.get("projects", [])}
    if action == "ruler_create":
        reserved = set(projects) | set(goals) | {p["id"] for p in state.get("projects", [])}
        reserved.update(row["ruler_id"] for row in ext.get("ruler_archive", []) if row["action"] == "ruler_delete")
        task_ids = {t["id"] for p in projects.values() for t in p.get("tasks", [])}
        task_ids.update(t["id"] for t in model.get("tasks", []))
        task_ids.update(t["id"] for t in state.get("planning", {}).get("tasks", []))
        entry_ids = {e["id"] for p in projects.values() for e in p.get("timeline_entries", [])}
        proposed_ids = {pid, pid + ":goal", pid + ":start", pid + ":deadline"}
        if proposed_ids & (reserved | task_ids | entry_ids):
            raise ValidationError("Линейка: ID уже использован")
        for generated in proposed_ids:
            identifier(generated)
        title = _text(data.get("title"), "title")
        first, last = data.get("start"), data.get("end")
        _window(first, last)
        goal = {"id": pid, "title": title, "description": data.get("description", ""),
                "start": first, "end": last, "entries": {}}
        for kind, when in (("start", first), ("deadline", last)):
            if when is not None:
                eid = pid + ":" + kind
                goal["entries"][eid] = _entry(pid, eid, pid + ":goal", title, when, kind)
        goals[pid] = goal
        return result
    project = projects.get(pid)
    if project is None:
        raise ValidationError("Линейка не найдена")
    custom = pid in goals
    if action == "ruler_delete":
        _archive(ext, action, pid, project)
        if custom:
            del goals[pid]
            ext.get("visibility", {}).pop(pid, None)
            for namespace in ("ruler_overrides", "ruler_entry_overrides", "ruler_task_completion"):
                ext.get(namespace, {}).pop(pid, None)
            context = ext.get("context", {})
            if context.get("project_id") == pid:
                context["project_id"] = None
            if context.get("task_id") in {t["id"] for t in project.get("tasks", [])}:
                context["task_id"] = None
        else:
            ext.setdefault("visibility", {})[pid] = False
        return result
    if action == "ruler_update":
        if not custom and {"start", "end"} & set(data):
            raise ValidationError("Измените дату конкретной отметки курса")
        _archive(ext, action, pid, project)
        if "visible" in data:
            ext.setdefault("visibility", {})[pid] = data["visible"]
        target = goals[pid] if custom else ext.setdefault("ruler_overrides", {}).setdefault(pid, {})
        target.update({key: data[key] for key in ("title", "description", "start", "end") if key in data})
        if custom:
            _window(target.get("start"), target.get("end"))
            for field, kind in (("start", "start"), ("end", "deadline")):
                eid = pid + ":" + kind
                if field in data:
                    if eid not in target["entries"]:
                        target["entries"][eid] = _entry(pid, eid, pid + ":goal", target["title"], data[field], kind)
                    else:
                        target["entries"][eid]["date"] = data[field]
                if "title" in data and eid in target["entries"] and target["entries"][eid]["title"] == project["title"]:
                    target["entries"][eid].update(title=data["title"], label=data["title"])
        return result
    eid = identifier(data.get("entry_id"), "entry_id")
    entries = {e["id"]: e for e in project.get("timeline_entries", [])}
    old = entries.get(eid)
    if action == "ruler_entry_delete":
        if old is None:
            raise ValidationError("Отметка не найдена")
        _archive(ext, action, pid, old, eid)
        if custom:
            goals[pid]["entries"].pop(eid, None)
            if eid in (pid + ":start", pid + ":deadline"):
                goals[pid]["start" if eid.endswith(":start") else "end"] = None
        else:
            ext.setdefault("ruler_entry_overrides", {}).setdefault(pid, {}).setdefault(eid, {})["visible"] = False
            _clear_hidden_note_context(ext, pid, project)
        return result
    if old is None:
        if "title" not in data or "date" not in data:
            raise ValidationError("Новая отметка требует title и date (или null)")
        if any(e["id"] == eid for p in projects.values() for e in p.get("timeline_entries", [])):
            raise ValidationError("Отметка: ID уже использован")
        old = _entry(pid, eid, pid + (":goal" if custom else ":ruler-notes"), data["title"], data["date"], data.get("event_kind", "milestone"))
    elif "event_kind" in data and data["event_kind"] != old["event_kind"] and not old.get("owner_created"):
        raise ValidationError("Тип исходной отметки нельзя менять")
    _archive(ext, action, pid, old, eid)
    edits = {key: data[key] for key in ("title", "date", "event_kind", "full_completion", "visible") if key in data}
    if "title" in edits:
        edits["label"] = edits["title"]
    if custom:
        updated = {**deepcopy(old), **edits}
        updated["full_checkmark"] = updated.get("full_completion") is True
        goals[pid]["entries"][eid] = updated
        if eid in (pid + ":start", pid + ":deadline"):
            expected = "start" if eid.endswith(":start") else "deadline"
            if updated["event_kind"] != expected:
                raise ValidationError("Тип границы цели нельзя менять")
            goals[pid]["start" if expected == "start" else "end"] = updated["date"]
            _window(goals[pid].get("start"), goals[pid].get("end"))
    else:
        target = ext.setdefault("ruler_entry_overrides", {}).setdefault(pid, {}).setdefault(eid, {})
        if old.get("owner_created"):
            target.update(deepcopy(old))
        target.update(edits)
        _clear_hidden_note_context(ext, pid, project)
        if "full_completion" in edits and not old.get("owner_created"):
            ext.setdefault("ruler_task_completion", {}).setdefault(pid, {})[old["task_id"]] = edits["full_completion"]
        if "date" in edits and not old.get("owner_created"):
            dates = {entry["event_kind"]: edits["date"] if entry["id"] == eid else entry.get("date")
                     for entry in entries.values() if entry.get("task_id") == old["task_id"]}
            _window(dates.get("start"), dates.get("deadline"))
    return result
