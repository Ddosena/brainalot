"""Read model and transactional commands for an Work period planner.

Native Work data is preserved. Period edits live in a separate top-level
namespace, committed in the same WorkStore with revision CAS and durable receipts.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import nullcontext
from datetime import date, datetime, timedelta
import hashlib
import math
import re
import time
from zoneinfo import ZoneInfo
from uuid import uuid4

from ..core import ValidationError, ConflictError
from ..shared_state import encoded, identifier
from .. import work_planning as wp
from . import calendar_rules
from . import timeline_rules
from . import course_rules
from . import cube_ops
from . import ruler_rules
from . import library_rules
from . import color_rules

NAMESPACE = "period_planner"
ACTIONS = frozenset({"cube_activity_set", "cube_pin", "repeatable_save", "repeatable_delete", "repeatable_drop", "prep_repeatable_cleanup", "undo", "ui_batch", "cube_reassign", "period_set", "period_shift", "period_clear", "project_visibility", "goal_hide", "cube_select", "cube_return",
    "cube_move", "cube_time", "cube_merge", "cube_rename", "cube_cancel", "task_rename",
    "calendar_source_import", "calendar_event_move", "calendar_offer_estimate", "cube_resize", "cube_drop", "course_source_import", "calendar_event_rename", "assignment_rename", "cube_delete"}) | ruler_rules.ACTIONS | color_rules.ACTIONS
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
    manual_tasks = {task["id"] for task in planning.get("tasks", []) if task.get("origin") == "manual"}
    for forecast in planning.get("result", {}).get("cubes", []):
        if forecast["id"] in represented | absorbed or forecast.get("manual") or forecast.get("locked"):
            continue
        # A manual goal holds only the cubes its owner made; no automatic forecast.
        if forecast.get("task_id") in manual_tasks:
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



def _retained_merge_sources(state, merge_id):
    """Prove absent AUTO leaves from the exact immutable merge DAG; never write on read."""
    planning, ext = state["planning"], extension(state)
    merges, merged = ext.get("merges", {}), ext.get("merged_cubes", {})
    tasks = {task["id"]: task for task in planning.get("tasks", [])}
    owners = {row.get("work_task_id"): row.get("owned_by_user", True) for row in course_rules.catalog_items(state)}
    segments = {segment["id"]: segment for segment in planning.get("task_segments", [])}
    native = planning.get("constructor", {}).get("cubes", {})
    reports = planning.get("completed_cubes", {})
    native_aliases = {cube.get("blueprint_ref"): cid for cid, cube in native.items() if cube.get("blueprint_ref")}
    current_forecasts = {row.get("id"): row for row in planning.get("result", {}).get("cubes", [])}
    retained = {}
    visiting = set()

    def walk(mid):
        if mid in visiting or mid not in merges or mid not in merged:
            raise ValueError("invalid merge graph")
        visiting.add(mid)
        record, combined = merges[mid], merged[mid]
        ids, sources = record.get("source_ids"), record.get("sources")
        if (not isinstance(ids, list) or not isinstance(sources, list) or not 2 <= len(ids) <= 10
                or len(ids) != len(set(ids)) or len(ids) != len(sources)
                or any(not isinstance(source, dict) or source.get("id") != key for key, source in zip(ids, sources))):
            raise ValueError("invalid source records")
        leaves, size = [], 0
        for source in sources:
            sid = source["id"]
            minutes = source.get("minutes")
            if type(minutes) is not int or minutes <= 0:
                raise ValueError("invalid source duration")
            size += minutes
            if sid in merges:
                children = walk(sid)
                if source.get("source_ids") != children:
                    raise ValueError("nested leaves do not match")
                leaves.extend(children)
                continue
            if (not isinstance(sid, str) or not sid.startswith("planned-") or source.get("kind") != "task"
                    or source.get("blueprint_ref") != sid[len("planned-"):]
                    or source.get("source_ids", [sid]) != [sid]
                    or minutes < 5 or minutes > 240 or minutes % 5
                    or source.get("progress_percent") or source.get("actual_minutes")
                    or source.get("historical_facts") or source.get("factual")):
                # Existing native leaves remain authoritative, including manual
                # IDs. Only absent AUTO leaves may be recovered from snapshots.
                if sid not in native:
                    raise ValueError("unproven missing leaf")
            tid, segment = source.get("task_id"), source.get("segment_id")
            if (tid not in tasks or owners.get(tid) is False or wp._scope_exempt(planning, tid)
                    or segment and (segment not in segments or segments[segment].get("task_id") != tid)):
                raise ValueError("source scope changed")
            if wp.deleted_cube(planning, sid, source.get("blueprint_ref")):
                raise ValueError("source deleted")
            if sid in native:
                current = native[sid]
                if (current.get("kind") != "task" or current.get("task_id") != tid
                        or current.get("segment_id") != segment or current.get("minutes") != minutes
                        or source.get("blueprint_ref") and current.get("blueprint_ref") != source.get("blueprint_ref")):
                    raise ValueError("native source changed")
            else:
                ref = source.get("blueprint_ref")
                current = current_forecasts.get(ref)
                if current and (current.get("task_id") != tid or current.get("segment_id") != segment
                        or current.get("minutes") != minutes or current.get("manual") or current.get("locked")):
                    raise ValueError("forecast source changed")
                if (sid in reports or ref in reports or ref in native_aliases
                        or current_forecasts.get(ref, {}).get("allocation_stale")
                        or any(lock.get("cube_id") in {sid, ref} for lock in planning.get("locks", []))):
                    raise ValueError("source fact or alias conflict")
                retained[sid] = deepcopy(source)
            leaves.append(sid)
        visiting.remove(mid)
        if (len(leaves) != len(set(leaves)) or record.get("original_source_ids") != leaves
                or combined.get("source_ids") != leaves or combined.get("minutes") != size):
            raise ValueError("merge composition mismatch")
        return leaves

    try:
        walk(merge_id)
    except (ValueError, TypeError, KeyError):
        return {}
    return retained

def cube_rows(state, library_notes=None):
    planning, ext = state["planning"], extension(state)
    tasks = {row["id"]: row for row in planning["tasks"]}
    catalog_owned = {row["work_task_id"]: row.get("owned_by_user", True)
        for row in course_rules.catalog_items(state) if row.get("work_task_id")}
    rows = _source_catalog(planning, ext.get("retained_cubes"))
    rows = library_rules.catalog(state, rows, library_notes)
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
            retained_leaves = _retained_merge_sources(state, cid)
            for leaf_id in row.get("source_ids", []):
                leaf = native_sources.get(leaf_id) or retained_leaves.get(leaf_id)
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
        source_tid = row.get("task_id")
        tid = ext.get("cube_tasks", {}).get(cid, source_tid)
        if ext.get("deleted_tasks", {}).get(tid):
            continue
        if cid in ext.get("cube_tasks", {}):
            row["source_task_id"] = source_tid
            row["owner_task_override"] = True
        pid = ext.get("task_fields", {}).get(tid, {}).get("project_id", tasks.get(tid, {}).get("project_id", row.get("project_id")))
        if ext.get("deleted_projects", {}).get(pid):
            continue
        standalone = bool(row.get("standalone"))
        lock = {} if standalone else locks.get(cid, {})
        placement = None if standalone else ext.get("placements", {}).get(cid)
        selected = row.get("selected_date") or (lock.get("date") if lock else None)
        start = row.get("start") if standalone else lock.get("start") if not lock.get("floating") else None
        if not standalone and row.get("kind") != "task" and selected:
            start = planning.get("constructor", {}).get("days", {}).get(selected, {}).get("source_slots", {}).get(cid)
        if placement is not None:
            selected, start = placement.get("date"), placement.get("start")
        deleted = bool(row.get("standalone_deleted")) or (False if standalone else bool(wp.deleted_cube(planning, cid, row.get("blueprint_ref"))))
        canceled = False if standalone else cid in ext.get("cancellations", {})
        exempt = bool((tid and (wp._scope_exempt(planning, tid) or catalog_owned.get(tid) is False))
            or (source_tid and (wp._scope_exempt(planning, source_tid) or catalog_owned.get(source_tid) is False)))
        percent = row.get("progress_percent", 0)
        row.update(id=cid, task_id=tid, project_id=ext.get("task_fields", {}).get(tid, {}).get("project_id", tasks.get(tid, {}).get("project_id", row.get("project_id"))),
            title=row.get("title", "Кубик") if standalone else ext.get("cube_titles", {}).get(cid, row.get("title", "Кубик")), source_title=source.get("title", "Кубик"),
            planned_date=row.get("planned_date") or row.get("date"), selected_date=selected, start=start,
            progress_percent=percent, actual_minutes=row.get("actual_minutes", 0),
            kind=row.get("kind", "task"), candidate=bool(row.get("candidate") and cid not in ext.get("placements", {})),
            status="cancelled" if canceled or deleted else "completed" if percent >= 100 else "started" if percent or row.get("actual_minutes") else "selected" if selected else "available",
            available=not (canceled or deleted or exempt or percent >= 100 or row.get("allocation_stale") or row.get("source_unavailable")),
            output=row.get("output", ""), steps=row.get("steps", []),
            source_ids=deepcopy(row.get("source_ids") or [cid]), returned=placement is not None and placement.get("date") is None,
            independent=bool(ext.get("independent_cubes", {}).get(cid)))
        row["end"] = hhmm(clock(start) + row["minutes"]) if start else None
        if canceled:
            row["cancel_reason"] = ext["cancellations"][cid]["reason"]
        if row["kind"] == "repeatable":
            row["repeatable_instance"] = True
            row["repeatable_id"] = ext.get("repeatable_instances", {}).get(cid)
            # A recorded timer interval is not readiness, but its instance must
            # remain accessible after return even when progress is still zero.
            row["time_retained"] = bool(ext.get("time_retained", {}).get(cid))
            if not selected and not percent and not row.get("actual_minutes") and not canceled and not row["time_retained"]:
                continue
            if not selected and (percent or row.get("actual_minutes") or row["time_retained"]):
                row["available"] = False
                row["reselectable"] = percent < 100
        result.append(row)
    return sorted(result, key=lambda row: (row.get("planned_date") or "9999", row.get("project_id") or "", row["id"]))



_BUILTIN_PREP = "prep-universal"


def _repeatable_templates(state):
    ext = extension(state)
    minutes = state["planning"]["settings"].get("prep_minutes", 25)
    minutes = max(5, min(240, minutes))
    rows = {_BUILTIN_PREP: {"id": _BUILTIN_PREP, "title": "П. к уроку", "minutes": minutes,
                              "sources": [], "origin": "system"}}
    rows.update(deepcopy(ext.get("repeatables", {})))
    for key in ext.get("repeatable_hidden", {}):
        rows.pop(key, None)
    for row in rows.values():
        row["pinned"] = bool(ext.get("cube_pins", {}).get("repeatable:" + row["id"]))
        row.setdefault("activity_kind", "work" if row.get("origin") == "system" else "personal")
    return list(rows.values())


def _safe_prep_ids(state):
    planning, ext = state["planning"], extension(state)
    ctor = planning.get("constructor", {})
    protected = set()
    for group in (ctor.get("days", {}), ctor.get("weeks", {})):
        for record in group.values():
            protected.update(record.get("closed_cube_ids", []))
            protected.update(record.get("closed_cube_snapshots", {}))
    protected.update(ctor.get("awards", {}).get("cubes", {}))
    result = []
    for cid, cube in ctor.get("cubes", {}).items():
        if cube.get("kind") != "prep" or cid in protected:
            continue
        # Descriptions and explicit overrides are owner data, not disposable
        # generated suggestions, even when no progress report exists.
        if (any(cube.get(field) for field in ("output", "steps", "comment", "links", "groups", "resource_drafts"))
                or any(cid in ext.get(field, {}) for field in ("cube_details", "cube_titles", "cube_durations", "retained_cubes", "cube_activity", "cube_pins", "repeatable_origins", "time_retained"))
                or ext.get("context", {}).get("cube_id") == cid):
            continue
        ref = cube.get("source_ref")
        if ref in ext.get("time_retained", {}):
            continue
        prep = next((prep for entry in planning.get("result", {}).get("days", []) for prep in entry.get("prep_cubes", []) if prep.get("id") == ref), {})
        lesson_start = prep.get("lesson_start") or ""
        if not isinstance(lesson_start, str):
            continue
        generated_titles = {"Подготовка к уроку", "Подготовка к уроку " + lesson_start}
        if cube.get("title") not in generated_titles:
            continue
        # Preserve even zero-valued reports: their existence is historical data.
        if ref in planning.get("prep_progress", {}) or cid in ctor.get("source_reports", {}):
            continue
        if wp._constructor_selected_date(planning, cid) or ext.get("placements", {}).get(cid, {}).get("date"):
            continue
        result.append(cid)
    return result



def _disposable_prep(state, row, safe_native):
    if (row.get("kind") != "prep" or row.get("selected_date") or row.get("progress_percent")
            or row.get("actual_minutes") or row.get("report_date") or row.get("status") == "cancelled"):
        return False
    ext, planning = extension(state), state["planning"]
    cid, ref = row["id"], row.get("source_ref") or row["id"]
    native = planning.get("constructor", {}).get("cubes", {})
    native_ids = {key for key, cube in native.items() if cube.get("kind") == "prep" and cube.get("source_ref") == ref}
    if native_ids and not native_ids.issubset(safe_native):
        return False
    if ref in planning.get("prep_progress", {}):
        return False
    keys = {cid, ref, *native_ids}
    if (any(key in ext.get(field, {}) for key in keys for field in ("cube_details", "cube_titles", "cube_durations", "retained_cubes", "cube_activity", "cube_pins", "repeatable_origins", "time_retained"))
            or ext.get("context", {}).get("cube_id") in keys
            or any(row.get(field) for field in ("output", "steps", "comment", "links", "groups", "resource_drafts"))):
        return False
    ctor = planning.get("constructor", {})
    protected = set(ctor.get("awards", {}).get("cubes", {}))
    for group in (ctor.get("days", {}), ctor.get("weeks", {})):
        for record in group.values():
            protected.update(record.get("closed_cube_ids", []))
            protected.update(record.get("closed_cube_snapshots", {}))
    return not bool(keys & protected)

def _prep_cleanup_preview(state, prep_rows):
    native = state["planning"].get("constructor", {}).get("cubes", {})
    ids = _safe_prep_ids(state)
    counts = {}
    for cid in ids:
        minutes = native[cid]["minutes"]
        counts[minutes] = counts.get(minutes, 0) + 1
    return {"hidden_suggestions": len(prep_rows), "native_removable": len(ids),
            "native_minutes_counts": [{"minutes": minutes, "count": count} for minutes, count in sorted(counts.items())]}


def _repeatable_command(state, action, data, model):
    ext, planning = state.setdefault(NAMESPACE, {}), state["planning"]
    if action == "prep_repeatable_cleanup":
        _fields(data, set())
        ctor = planning.get("constructor", {})
        ids = _safe_prep_ids(state)
        for cid in ids:
            ctor.get("cubes", {}).pop(cid, None)
            ext.get("placements", {}).pop(cid, None)
            ext.get("cube_titles", {}).pop(cid, None)
            ext.get("cube_durations", {}).pop(cid, None)
            ext.get("cube_details", {}).pop(cid, None)
        for record in ctor.get("days", {}).values():
            for field in ("selected_source_ids", "hidden_cube_ids"):
                if field in record:
                    record[field] = [cid for cid in record[field] if cid not in ids]
            for cid in ids:
                record.get("source_slots", {}).pop(cid, None)
                for positions in record.get("positions", {}).values():
                    if isinstance(positions, dict):
                        positions.pop(cid, None)
        # Cleanup must not reverse the owner's earlier template deletion.
        minutes = max(5, min(240, planning["settings"].get("prep_minutes", 25)))
        ext.setdefault("repeatables", {}).setdefault(_BUILTIN_PREP, {
            "id": _BUILTIN_PREP, "title": "П. к уроку", "minutes": minutes,
            "sources": [], "origin": "system", "activity_kind": "work"})
        return state
    if action == "repeatable_save":
        _fields(data, set(), {"repeatable_id", "title", "minutes", "sources", "cube_id", "activity_kind"})
        key = data.get("repeatable_id")
        if key is None and data.get("cube_id"):
            key = ext.get("repeatable_origins", {}).get(data["cube_id"])
        if key is not None:
            identifier(key, "repeatable_id")
        existing = next((row for row in _repeatable_templates(state) if row["id"] == key), None)
        if key and existing is None:
            raise ValidationError("Шаблон не найден; обновите экран")
        source = next((row for row in model["cubes"] if row["id"] == data.get("cube_id")), None) if data.get("cube_id") else None
        if data.get("cube_id") and source is None:
            raise ValidationError("Исходный кубик не найден; обновите экран")
        fallback = source or existing or {}
        title = text(data.get("title", fallback.get("title")))
        if len(title) > 300:
            raise ValidationError("Название шаблона: максимум 300 символов")
        minutes = data.get("minutes", fallback.get("minutes"))
        if type(minutes) is not int or not 5 <= minutes <= 240 or minutes % 5:
            raise ValidationError("Длительность шаблона: 5–240 минут, шаг 5 минут")
        activity_kind = data.get("activity_kind", fallback.get("activity_kind", "personal"))
        if activity_kind not in ("work", "personal"):
            raise ValidationError("activity_kind: требуется work или personal")
        sources = deepcopy(data.get("sources", fallback.get("sources")))
        if sources is None:
            sources = [{"id": link.get("id") or "source-" + uuid4().hex, "url": link.get("url", ""),
                        "text": link.get("title", "")} for link in fallback.get("links", [])]
        cube_ops.validate_details({"kind": "repeatable", "links": [], "groups": []}, {"sources": sources}, planning)
        key = key or "repeatable-" + uuid4().hex
        ext.setdefault("repeatables", {})[key] = {"id": key, "title": title, "minutes": minutes,
            "sources": sources, "origin": existing.get("origin", "user") if existing else "user", "activity_kind": activity_kind}
        ext.get("repeatable_hidden", {}).pop(key, None)
        if data.get("cube_id"):
            ext.setdefault("repeatable_origins", {})[data["cube_id"]] = key
        return state
    if action == "repeatable_delete":
        _fields(data, {"repeatable_id"})
        key = identifier(data["repeatable_id"], "repeatable_id")
        if not any(row["id"] == key for row in _repeatable_templates(state)):
            raise ValidationError("Шаблон не найден; обновите экран")
        ext.get("repeatables", {}).pop(key, None)
        origins = ext.get("repeatable_origins", {})
        for cid in [cid for cid, value in origins.items() if value == key]:
            origins.pop(cid, None)
        if key == _BUILTIN_PREP:
            ext.setdefault("repeatable_hidden", {})[key] = True
        return state
    _fields(data, {"repeatable_id", "date", "start"})
    template = next((row for row in _repeatable_templates(state) if row["id"] == data["repeatable_id"]), None)
    if template is None:
        raise ValidationError("Шаблон не найден; обновите экран")
    cid = "repeat-instance-" + uuid4().hex
    row = {"id": cid, "kind": "repeatable", "task_id": None, "title": template["title"],
           "minutes": template["minutes"], "status": "available", "available": True}
    _place(state, model, row, data["date"], data["start"])
    wp._constructor_cube_update(planning, {"cube_id": cid, "kind": "repeatable", "source_ref": cid,
        "title": template["title"], "minutes": template["minutes"]})
    ext.setdefault("repeatable_instances", {})[cid] = template["id"]
    ext.setdefault("cube_activity", {})[cid] = template["activity_kind"]
    if template["sources"]:
        ext.setdefault("cube_details", {})[cid] = {"sources": deepcopy(template["sources"])}
    return state

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
            "color": event.get("color"), "color_name": event.get("color_name"),
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



def _real_task_cube(state, cube):
    """An explicit placement turns a forecast into an owner stage, even on return."""
    if cube.get("kind") != "task" or cube.get("status") == "cancelled" or cube.get("candidate"):
        return False
    cid = cube.get("id", "")
    return (not cid.startswith("planned-") or cid in state["planning"].get("constructor", {}).get("cubes", {})
            or cid in state.get(NAMESPACE, {}).get("placements", {}))

def read_model(snapshot):
    state = snapshot["state"]
    planning, ext = state["planning"], extension(state)
    settings = planning["settings"]
    cubes = cube_rows(state, snapshot.get("library_notes"))
    safe_native = set(_safe_prep_ids(state))
    prep_rows = [cube for cube in cubes if _disposable_prep(state, cube, safe_native)]
    # System prep suggestions are replaced by the reusable owner-facing template.
    hidden_prep = {cube["id"] for cube in prep_rows}
    if not snapshot.get("_include_hidden_prep"):
        cubes = [cube for cube in cubes if cube["id"] not in hidden_prep]
    native_events = _event_rows(planning, ext.get("calendar_source", {}).get("events"))
    native_event_ids = {event["id"] for event in native_events}
    native_events.extend(deepcopy(event) for event_id, event in ext.get("retained_events", {}).items()
        if event_id not in native_event_ids)
    events, cubes, calendar_warnings = calendar_rules.enrich(state, native_events, cubes)
    if not snapshot.get("_include_hidden_prep"):
        converted = ext.get("repeatable_origins", {})
        active_templates = {template["id"] for template in _repeatable_templates(state)}
        cubes = [cube for cube in cubes if not (converted.get(cube["id"]) in active_templates
            and not cube.get("selected_date") and not cube.get("progress_percent") and not cube.get("actual_minutes")
            and not cube.get("report_date") and cube.get("status") != "cancelled")]
    cubes = cube_ops.enrich_details(state, cubes)
    for cube in cubes:
        cube["pinned"] = bool(ext.get("cube_pins", {}).get(cube["id"]))
        cube["time_retained"] = bool(ext.get("time_retained", {}).get(cube["id"]))
        timed_calendar = cube.get("kind") in {"calendar", "calendar_optional"} and not cube.get("all_day")
        default = "work" if cube.get("kind") in {"task", "prep", "weekly_review"} or timed_calendar else "personal"
        explicit = cube.get("activity_kind")
        canonical = explicit if isinstance(explicit, str) and explicit in {"work", "personal"} else default
        cube["activity_kind"] = canonical if cube.get("standalone") else ext.get("cube_activity", {}).get(cube["id"], canonical)
        if (cube.get("kind") == "prep" and not cube.get("selected_date") and not cube.get("progress_percent")
                and not cube.get("actual_minutes") and not cube.get("report_date") and cube.get("id") not in hidden_prep):
            cube.update(retained_preparation=True, repeatable_instance=True, repeatable_id=_BUILTIN_PREP)
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
        if ext.get("deleted_tasks", {}).get(tid):
            continue
        fields = ext.get("task_fields", {}).get(tid, {})
        if ext.get("deleted_projects", {}).get(fields.get("project_id", native.get("project_id"))):
            continue
        explicit = ext.get("periods", {}).get(tid)
        dates = [cube["planned_date"] for cube in by_task.get(tid, []) if cube.get("planned_date") and not cube.get("independent")]
        source = source_by_task.get(tid, {})
        source_total = native["estimate_minutes"]["central"]
        total = fields.get("minutes", source_total)
        left = remaining.get(tid, result_tasks.get(tid, {}).get("remaining_minutes", source_total))
        # Owner estimate edits never manufacture progress from source facts.
        percent = max(0, min(100, 100 * (source_total-left) / source_total)) if source_total else 0
        baseline = wp.latest_remaining_baseline(planning, tid)
        real = [cube for cube in by_task.get(tid, []) if _real_task_cube(state, cube)]
        if ext.get("volume_by_cubes", {}).get(tid):
            total = max(5, sum(cube["minutes"] for cube in real))
        if real:
            percent = math.floor(sum(cube.get("progress_percent", 0) for cube in real) / len(real) + 0.5)
        complete = all(cube.get("progress_percent", 0) >= 100 for cube in real) if real else percent >= 100
        percent = min(100 if complete else 99, math.floor(percent + 0.5))
        completion_dates = []
        for cube in real:
            for cid in set(cube.get("source_ids") or [cube["id"]]) | {cube["id"]}:
                report = planning.get("completed_cubes", {}).get(cid, {})
                if report.get("percent", 0) >= 100 and report.get("date"):
                    completion_dates.append(report["date"])
            if cube.get("report_date") and cube.get("progress_percent", 0) >= 100:
                completion_dates.append(cube["report_date"])
        legacy_date = max(completion_dates, default=None) if real else (baseline[0] if baseline and baseline[2] else None)
        today = datetime.now(ZoneInfo(planning["settings"]["timezone"])).date().isoformat()
        # Legacy reports contain scheduled dates, not necessarily actual completion.
        # Preserve earlier dates where available, but never infer a future completion.
        completed_on = min(ext.get("goal_completed_on", {}).get(tid) or legacy_date or today, today) if complete else None
        tasks.append({"id": tid, "project_id": fields.get("project_id", native["project_id"]),
            "title": ext.get("task_titles", {}).get(tid, native["title"]),
            "start": explicit["start"] if explicit else min(dates) if dates else native.get("methodist_start"),
            "end": explicit["end"] if explicit else max(dates) if dates else native.get("due"),
            "deadline": ext.get("deadlines", {}).get(tid, fields.get("deadline", native.get("due"))), "deadline_kind": native.get("due_status", "unknown"),
            "minutes": total, "source_minutes": source_total, "remaining_minutes": left,
            "cancelled_minutes": native_cancelled.get(tid, 0) + overlay_cancelled.get(tid, 0),
            "effective_remaining_minutes": max(0, left-native_cancelled.get(tid, 0)-overlay_cancelled.get(tid, 0)),
            "progress_percent": percent,
            "readiness_cube_ids": [cube["id"] for cube in real],
            "status": "completed" if complete else "started" if percent > 0 else "available",
            # The day of an explicit "done" outcome; reopening replaces exactly that fact.
            "completed_on": completed_on,
            "volume_by_cubes": bool(ext.get("volume_by_cubes", {}).get(tid)),
            "hidden": bool(ext.get("hidden_goals", {}).get(tid)),
            "period_explicit": bool(explicit), "source_status": source.get("status"),
            # A manual task is the owner's own: all its fields are editable in Week.
            "origin": native.get("origin", "agent"),
            "owned": bool(source.get("owned_by_user", True) and not wp._scope_exempt(planning, tid))})
    task_map = {row["id"]: row for row in tasks}
    projects = []
    for native in state["projects"]:
        pid = native["id"]
        if ext.get("deleted_projects", {}).get(pid):
            continue
        project_tasks = []
        seen_tasks = set()
        for source in source_items:
            if ext.get("deleted_tasks", {}).get(source.get("work_task_id")):
                continue
            linked = task_map.get(source.get("work_task_id"))
            if (linked.get("project_id") if linked else source.get("project_id")) != pid:
                continue
            seen_tasks.add(source.get("work_task_id"))
            project_tasks.append({"id": linked["id"] if linked else source["id"],
                "title": linked["title"] if linked else source["title"],
                "date": linked["start"] if linked else source.get("due"),
                "end": linked["end"] if linked else source.get("due"),
                "status": linked["status"] if linked else source.get("status", "unknown"), "source_status": source.get("status", "unknown"),
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
    projects = [project for project in projects if not ext.get("deleted_projects", {}).get(project["id"])]
    projects = color_rules.enrich(state, projects, native_events, events)
    date_overrides ={task.get("source_work_task_id"): task for project in projects for task in project.get("tasks", [])
        if task.get("owner_date_override") and task.get("source_work_task_id") and not task.get("package_id")}
    for task in tasks:
        if task["id"] in date_overrides and "deadline" not in ext.get("task_fields", {}).get(task["id"], {}) and task["id"] not in ext.get("deadlines", {}):
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
        "repeatables": _repeatable_templates(state),
        "prep_cleanup_preview": _prep_cleanup_preview(state, prep_rows),
        "calendar_coverage": calendar_rules.coverage(state),
        "calendar_palette": color_rules.palette(state, native_events, projects),
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
        "settings": {"timezone": settings["timezone"], "start": settings["start"], "end": settings["end"], "day_start": settings["work_start"],
            "day_end": settings["work_end"], "daily_limit_minutes": settings["day_limit_minutes"],
            "daily_limit_minutes_by_date": {
                **{key: value["minutes"] for key, value in settings.get("day_capacity_overrides", {}).items()},
                **{key: value["budget_minutes"] for key, value in planning.get("manual_days", {}).items()}}}}


def apply_context(state, data, library_notes=None):
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
    model = read_model({"state": state, "revision": 0, "library_notes": library_notes})
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
    if not placement and (row.get("actual_minutes") or row.get("progress_percent")):
        raise ValidationError("У кубика уже есть факты: менять его состав нельзя")
    if not row.get("available") and not (placement and row.get("kind") == "repeatable" and row.get("reselectable")):
        raise ValidationError(row.get("availability_block_reason") or "Кубик недоступен: проверьте остаток и границы ответственности")


def _place(state, model, row, target, start, *, follow_period=False):
    day(target)
    calendar_offer = row["kind"] == "calendar_optional"
    linked_calendar_prep = row["kind"] == "prep" and any(event["id"] == row.get("calendar_event_id") and event.get("calendar_role") == "flexible" and target in {event["date"], row.get("selected_date")} for event in model["events"])
    if not calendar_offer and not linked_calendar_prep and not model["settings"]["start"] <= target <= model["settings"]["end"]:
        raise ValidationError("Дата за пределами горизонта Work")
    if calendar_offer and row.get("duration_unknown"):
        raise ValidationError("В Calendar не указана длительность: сначала задайте минуты предложения явно")
    occurrence_day = target == row.get("source_date") or (row.get("source_date") and
        target == (date.fromisoformat(row["source_date"]) - timedelta(days=1)).isoformat()) or (row.get("all_day") and row.get("source_end_date")
        and row["source_date"] <= target < row["source_end_date"])
    if (calendar_offer and row.get("availability") == "day" and not occurrence_day
            and not row.get("selected_date") and not row.get("returned")):
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
        # Owner decision 2026-10-28: a chosen stage beyond the remaining volume is allowed.
    calendar_rules.validate_slot(model, target, row["minutes"], start, exclude_cube=row["id"], work_window=not calendar_offer, allow_zero=row["kind"] == "prep")
    ext = state.setdefault(NAMESPACE, {})
    ext.setdefault("placements", {})[row["id"]] = {"date": target, "start": start}
    if row.get("kind") == "task" and row.get("task_id"):
        task = next((item for item in model["tasks"] if item["id"] == row["task_id"]), None)
        if task:
            first, last = task.get("start"), task.get("end")
            independent = bool(not follow_period and ((first and target < first) or (last and target > last)))
            flags = ext.setdefault("independent_cubes", {})
            if independent:
                flags[row["id"]] = True
            else:
                flags.pop(row["id"], None)
            if not first and not last:
                ext.setdefault("periods", {})[task["id"]] = {"start": target, "end": target}


def apply_command(state, action, data, library_notes=None):
    if action not in ACTIONS:
        raise ValidationError("Неизвестная команда периодного плана")
    model = read_model({"state": state, "revision": 0, "library_notes": library_notes, "_include_hidden_prep": True})
    ext = state.setdefault(NAMESPACE, {})
    if action == "cube_activity_set":
        _fields(data, {"cube_id", "activity_kind"})
        key = identifier(data["cube_id"], "cube_id")
        if data["activity_kind"] not in ("work", "personal"):
            raise ValidationError("activity_kind: требуется work или personal")
        selected = next((cube for cube in model["cubes"] if cube["id"] == key and cube.get("status") != "cancelled"), None)
        if not selected:
            raise ValidationError("Кубик не найден; обновите экран")
        if selected.get("standalone"):
            raise ValidationError("Категория связанного кубика меняется через standalone_update")
        ext.setdefault("cube_activity", {})[key] = data["activity_kind"]
        return state
    if action == "cube_pin":
        _fields(data, {"cube_id", "pinned"})
        key = identifier(data["cube_id"], "cube_id")
        if type(data["pinned"]) is not bool:
            raise ValidationError("pinned: требуется true/false")
        if key.startswith("repeatable:"):
            if not any(template["id"] == key[len("repeatable:"):] for template in model["repeatables"]):
                raise ValidationError("Шаблон не найден; обновите экран")
        elif not any(cube["id"] == key and cube.get("status") != "cancelled" for cube in model["cubes"]):
            raise ValidationError("Кубик не найден; обновите экран")
        pins = ext.setdefault("cube_pins", {})
        if data["pinned"]:
            pins[key] = True
        else:
            pins.pop(key, None)
        return state
    if action in {"repeatable_save", "repeatable_delete", "repeatable_drop", "prep_repeatable_cleanup"}:
        return _repeatable_command(state, action, data, model)
    if action == "cube_reassign":
        _fields(data, {"cube_id", "task_id"})
        cube = next((row for row in model["cubes"] if row["id"] == data["cube_id"]), None)
        task = next((row for row in model["tasks"] if row["id"] == data["task_id"] and row["owned"]), None)
        if not cube or not task or cube.get("kind") != "task":
            raise ValidationError("Нужны собственный кубик задачи и доступная цель")
        if cube.get("status") == "cancelled" or cube.get("source_unavailable") or cube.get("allocation_stale"):
            raise ValidationError("Кубик или его исходный состав недоступен")
        if not cube.get("available") and cube.get("status") != "completed":
            raise ValidationError("Кубик недоступен или находится вне вашей работы")
        source_ids = {cube.get("source_task_id") or cube.get("task_id")}
        source_ids.update(part.get("task_id") for part in cube.get("source_task_allocations", []))
        source_owners = {row.get("work_task_id"): row.get("owned_by_user", True) for row in course_rules.catalog_items(state)}
        if any(tid and (wp._scope_exempt(state["planning"], tid) or source_owners.get(tid) is False) for tid in source_ids):
            raise ValidationError("Исходный кубик находится вне вашей работы")
        ext.setdefault("cube_tasks", {})[cube["id"]] = task["id"]
        if cube.get("selected_date"):
            target = cube["selected_date"]
            first, last = task.get("start"), task.get("end")
            flags = ext.setdefault("independent_cubes", {})
            if (first and target < first) or (last and target > last):
                flags[cube["id"]] = True
            else:
                flags.pop(cube["id"], None)
            if not first and not last:
                ext.setdefault("periods", {})[task["id"]] = {"start": target, "end": target}
        if not ext.get("volume_by_cubes", {}).get(task["id"]):
            total = sum(item["minutes"] for item in model["cubes"]
                if item["id"] != cube["id"] and item.get("task_id") == task["id"] and _real_task_cube(state, item))
            if _real_task_cube(state, cube):
                total += cube["minutes"]
            total = -(-total // 5) * 5
            if total > task["minutes"]:
                from ..work_manual import VolumeWarning, note_warnings
                ext.setdefault("task_fields", {}).setdefault(task["id"], {})["minutes"] = total
                note_warnings([VolumeWarning(f"Объём цели «{task['title']}» вырос на {total-task['minutes']} мин: кубики заняли больше, чем было запланировано.", task["id"])])
        return state
    if action == "cube_drop":
        _fields(data, {"cube_id", "date", "start"}, {"minutes"})
        row = next((cube for cube in model["cubes"] if cube["id"] == data["cube_id"]), None)
        if row is None:
            raise ValidationError("Кубик не найден; обновите экран")
        _editable_cube(row, placement=True)
        day(data["date"])
        if data["start"] is not None:
            clock(data["start"])
        if row["kind"] == "calendar_optional" and row.get("duration_unknown"):
            # The owner's direct drop explicitly authorizes a provisional local
            # duration. Fit only at this exact start, never jump to another slot.
            failure = None
            if "minutes" in data:
                requested = data["minutes"]
                if type(requested) is not int or not 5 <= requested <= 1440 or requested % 5:
                    raise ValidationError("Длительность предложения: 5–1440 минут, шаг 5 минут")
                choices = (requested,)
            else:
                choices = (30, 25, 20, 15, 10, 5)
            for minutes in choices:
                proposed = {**row, "minutes": minutes, "duration_unknown": False, "requires_estimate": False}
                try:
                    _place(state, model, proposed, data["date"], data["start"])
                except ValidationError as exc:
                    failure = exc
                    continue
                ext.setdefault("calendar_offer_estimates", {})[row["id"]] = minutes
                return state
            raise failure or ValidationError("В выбранной точке нет пяти свободных минут")
        if "minutes" in data:
            raise ValidationError("Длительность можно задать только предложению без известной длительности")
        library_rules.retain(state, row)
        _place(state, model, row, data["date"], data["start"])
        return state
    if action in ruler_rules.ACTIONS:
        return ruler_rules.apply(state, action, data, model)
    if action in color_rules.ACTIONS:
        return color_rules.apply(state, action, data, model)
    if action == "cube_delete":
        return cube_ops.delete(state, data, model)
    if action == "cube_merge":
        return cube_ops.merge(state, data, model, _place)
    if action == "cube_resize":
        row = next((cube for cube in model["cubes"] if cube["id"] == data.get("cube_id")), None)
        if row:
            library_rules.retain(state, row)
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
    if action in {"period_set", "period_shift", "period_clear", "task_rename"}:
        _fields(data, {"task_id", "start", "end"} if action in {"period_set", "period_shift"} else {"task_id", "title"} if action == "task_rename" else {"task_id"})
        task = next((t for t in model["tasks"] if t["id"] == data["task_id"]), None)
        if not task:
            raise ValidationError("Задача не найдена")
        if not task["owned"]:
            raise ValidationError("Справочная задача вне вашей работы: доступно только чтение")
        if action in {"period_set", "period_shift"}:
            first, last = day(data["start"]), day(data["end"])
            if first > last:
                raise ValidationError("Конец периода раньше начала")
            if action == "period_shift":
                if not task.get("start") or not task.get("end"):
                    raise ValidationError("У цели нет периода для переноса")
                delta = date.fromisoformat(first) - date.fromisoformat(task["start"])
                if date.fromisoformat(task["end"]) + delta != date.fromisoformat(last):
                    raise ValidationError("Перенос должен сохранять длину периода")
                if task.get("deadline"):
                    ext.setdefault("deadlines", {})[task["id"]] = (date.fromisoformat(task["deadline"]) + delta).isoformat()
                moving = [c for c in model["cubes"] if c.get("task_id") == task["id"] and c.get("selected_date") and not c.get("independent") and c.get("status") not in {"completed", "cancelled"}]
                for cube in moving:
                    ext.setdefault("placements", {})[cube["id"]] = {"date": None, "start": None}
                for cube in moving:
                    target = (date.fromisoformat(cube["selected_date"]) + delta).isoformat()
                    fresh = read_model({"state": state, "revision": 0, "library_notes": library_notes})
                    try:
                        _place(state, fresh, cube, target, cube.get("start"), follow_period=True)
                    except ValidationError:
                        _place(state, fresh, cube, target, None, follow_period=True)
            else:
                for cube in model["cubes"]:
                    if cube.get("task_id") == task["id"] and cube.get("selected_date") and not cube.get("independent") and not first <= cube["selected_date"] <= last and cube.get("status") not in {"completed", "cancelled"}:
                        ext.setdefault("placements", {})[cube["id"]] = {"date": None, "start": None}
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
    if action == "goal_hide":
        # Owner 2026-10-28: hiding only moves a goal out of the Week chart into a
        # thin reminder line; cubes, facts and percentages are untouched.
        _fields(data, {"task_id", "hidden"})
        if data["task_id"] not in {t["id"] for t in model["tasks"]} or type(data["hidden"]) is not bool:
            raise ValidationError("Требуются существующая цель и hidden=true/false")
        hidden = ext.setdefault("hidden_goals", {})
        if data["hidden"]:
            hidden[data["task_id"]] = True
        else:
            hidden.pop(data["task_id"], None)
        return state
    required = {"cube_id", "date"} if action in {"cube_select", "cube_move"} else {"cube_id", "start"} if action == "cube_time" else {"cube_id", "title"} if action == "cube_rename" else {"cube_id", "reason"} if action == "cube_cancel" else {"cube_id"}
    _fields(data, required, {"start"} if action in {"cube_select", "cube_move"} else ())
    row = next((c for c in model["cubes"] if c["id"] == data["cube_id"]), None)
    if row is None:
        raise ValidationError("Кубик не найден или уже объединён; обновите экран")
    library_rules.retain(state, row)
    if action == "cube_rename":
        if row.get("source_unavailable"):
            raise ValidationError(row.get("availability_block_reason") or "Исходная задача Brainalot недоступна")
        title = text(data["title"])
        ext.setdefault("cube_titles", {})[row["id"]] = title
        if row.get("kind") == "calendar_optional":
            for occurrence in row.get("source_occurrences", []):
                ext.setdefault("calendar_event_titles", {})[occurrence["event_id"]] = title
        return state
    if not (action in {"cube_return", "cube_cancel"} and library_rules.can_cleanup(row)):
        _editable_cube(row, placement=action != "cube_cancel")
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
    result["planning_initialized"] = False
    return result


_MISSING = object()


def _conditional_reverse(current, before, after):
    """Reverse only fields still equal to the action result; arrays are atomic."""
    def visit(now, old, new):
        if old == new:
            return deepcopy(now)
        if isinstance(old, dict) and isinstance(new, dict):
            if not isinstance(now, dict):
                raise ConflictError("Объект изменился после действия; отмена невозможна")
            result = deepcopy(now)
            for key in old.keys() | new.keys():
                a, b = old.get(key, _MISSING), new.get(key, _MISSING)
                if a is not _MISSING and b is not _MISSING and a == b:
                    continue
                actual = now.get(key, _MISSING)
                if a is _MISSING and isinstance(b, dict) and isinstance(actual, dict):
                    restored = visit(actual, {}, b)
                    if restored:
                        result[key] = restored
                    else:
                        result.pop(key, None)
                elif a is not _MISSING and b is not _MISSING and isinstance(a, dict) and isinstance(b, dict):
                    result[key] = visit(actual, a, b)
                else:
                    if actual != b:
                        raise ConflictError("Данные изменились после действия; отмена невозможна")
                    if a is _MISSING:
                        result.pop(key, None)
                    else:
                        result[key] = deepcopy(a)
            return result
        if now != new:
            raise ConflictError("Данные изменились после действия; отмена невозможна")
        return deepcopy(old)
    return visit(current, before, after)



def _timer_source_ids(state, library_notes):
    """Effective sources still usable by a timer; read-only preflight for undo."""
    if not isinstance((state or {}).get("planning"), dict):
        return set()
    projection = read_model({"state": state, "revision": 0, "library_notes": library_notes})
    return {cube["id"] for cube in projection["cubes"]
        if cube.get("status") != "cancelled" and not cube.get("allocation_stale") and not cube.get("source_unavailable")}

def record_goal_transitions(before, state, library_notes, *, imported=False):
    """Persist actual completion day alongside the state change for undo."""
    def statuses(document):
        if not isinstance(document.get("planning"), dict):
            return {}
        snapshot = {"state": deepcopy(document), "revision": 0, "initialized": True, "library_notes": library_notes}
        return {row["id"]: row for row in read_model(snapshot)["tasks"]}
    prior, after = statuses(before), statuses(state)
    dates = deepcopy(state.get(NAMESPACE, {}).get("goal_completed_on", {}))
    today = datetime.now(ZoneInfo(state["planning"]["settings"]["timezone"])).date().isoformat()
    for task_id, row in after.items():
        status = row["status"]
        if status == "completed" and prior.get(task_id, {}).get("status") != "completed":
            dates[task_id] = row["completed_on"] if imported else today
        elif status == "completed" and imported and task_id not in dates:
            dates[task_id] = row["completed_on"]
        elif status != "completed":
            dates.pop(task_id, None)
    if dates:
        state.setdefault(NAMESPACE, {})["goal_completed_on"] = dates
    elif NAMESPACE in state:
        state[NAMESPACE].pop("goal_completed_on", None)


class PeriodPlanner:
    """Typed namespace edits sharing WorkStore's CAS, receipts and delete recovery.

    No native planning field is rewritten. The effective model combines current
    native sources and facts with the period_planner display/scheduling overlay.
    """

    def __init__(self, store):
        self.store = store
        self.library = library_rules.LibraryReader(store.root)

    def _model(self, snapshot, library_notes=None):
        if library_notes is None:
            library_notes = self.library.notes()
        snapshot = {**snapshot, "library_notes": library_notes}
        ready = isinstance((snapshot.get("state") or {}).get("planning"), dict)
        result = read_model(snapshot) if ready else _empty_projection(snapshot)
        result["initialized"] = snapshot["initialized"]
        result["planning_initialized"] = ready
        result["provenance"] = {"source": "work", "training_copy": False,
            "source_revision": snapshot["revision"]}
        source_rows = [{key: note.get(key) for key in ("id", "version", "kind", "status", "title", "due", "planning_horizon")}
            for note in sorted(library_notes, key=lambda note: note["id"])]
        result["source_fingerprint"] = hashlib.sha256(encoded(source_rows).encode("utf-8")).hexdigest()
        if snapshot.get("deletion_state") is not None:
            result["deletion_state"] = deepcopy(snapshot["deletion_state"])
        return result

    def snapshot(self):
        return self.store.read()

    def model(self):
        return self._model(self.snapshot())

    def day_model(self):
        from .day import day_model
        return day_model({**self.snapshot(), "library_notes": self.library.notes()})

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

    def _write(self, command, scope, transform, *, context_only=False, library_notes=None, allow_native=False):
        from ..work_delete import recover, ensure_writable
        from ..work_manual import take_warnings, filter_volume_warnings
        take_warnings()
        if library_notes is None:
            library_notes = self.library.notes()
        recover(self.store)
        deleted_ids, return_ids, cleanup_rows = [], [], []
        undo_removed = None
        if command.get("action") == "undo":
            # Replay must win before preflight: the original receipt is durable
            # even though its reverse no longer applies to the latest state.
            with self.store.db() as db:
                old = self.store.receipt(db, scope, command)
                if old is not None:
                    return old
                current = self.store._version(db)
                self.store.check_revision(command, current["revision"] if current else 0)
                preflight = self.store._read(db, current)["state"]
            if not isinstance((preflight or {}).get("planning"), dict):
                raise ValidationError("План Work ещё не инициализирован")
            projected = transform(deepcopy(preflight))
            undo_removed = _timer_source_ids(preflight, library_notes) - _timer_source_ids(projected, library_notes)
            deleted_ids.extend(undo_removed)
        data = command.get("data") if isinstance(command.get("data"), dict) else {}
        action = command.get("action")
        if action == "cube_delete":
            deleted_ids.append(data.get("cube_id"))
        if action == "cube_merge" and isinstance(data.get("cube_ids"), list):
            deleted_ids.extend(data["cube_ids"])
        if action == "cube_return":
            return_ids.append(data.get("cube_id"))
        steps = data.get("commands", []) if action == "ui_batch" else []
        for step in steps:
            if not isinstance(step, dict) or not isinstance(step.get("data"), dict):
                continue
            cid = step["data"].get("cube_id")
            if step.get("action") == "cube_delete":
                deleted_ids.append(cid)
            elif step.get("action") == "cube_merge" and isinstance(step["data"].get("cube_ids"), list):
                deleted_ids.extend(step["data"]["cube_ids"])
            elif step.get("action") in {"cube_return", "constructor_return"}:
                return_ids.append(cid)
        cleanup = action == "prep_repeatable_cleanup" or any(
            isinstance(step, dict) and step.get("action") == "prep_repeatable_cleanup" for step in steps)
        return_ids = [cid for cid in return_ids if isinstance(cid, str) and cid != "$created_cube"]
        retained_rows = []
        if return_ids or cleanup:
            preview_state = self.store.read().get("state") or {}
            if isinstance(preview_state.get("planning"), dict):
                raw_rows = cube_rows(preview_state)
                retained_rows = [row for row in raw_rows if row["id"] in return_ids and row.get("kind") in {"prep", "repeatable"}]
                if cleanup:
                    safe_native = set(_safe_prep_ids(preview_state))
                    cleanup_rows = [row for row in raw_rows if _disposable_prep(preview_state, row, safe_native)]
                    deleted_ids.extend(safe_native)
        retained_rows.extend(cleanup_rows)
        tracked_aliases = {row["id"] for row in retained_rows}
        tracked_aliases.update(row["source_ref"] for row in retained_rows if row.get("kind") == "prep" and row.get("source_ref"))
        deleted_ids.extend(tracked_aliases)
        deleted_ids = [cid for cid in deleted_ids if isinstance(cid, str) and cid != "$created_cube"]
        if deleted_ids:
            from ..work_delete import timer_reservation_many
            reservation = timer_reservation_many(self.store.root, deleted_ids)
        else:
            reservation = nullcontext()
        with reservation, self.store.db() as db:
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
                candidate = deepcopy(before)
                if retained_rows:
                    from ..work_delete import tracked_work_cube_ids
                    tracked = tracked_work_cube_ids(self.store.root, tracked_aliases)
                    flags = candidate.setdefault(NAMESPACE, {}).setdefault("time_retained", {})
                    for row in retained_rows:
                        if row["id"] in tracked or row.get("kind") == "prep" and row.get("source_ref") in tracked:
                            flags[row["id"]] = True
                    if not flags:
                        candidate[NAMESPACE].pop("time_retained", None)
                state = transform(candidate)
                if undo_removed is not None:
                    current_removed = _timer_source_ids(before, library_notes) - _timer_source_ids(state, library_notes)
                    if not current_removed <= undo_removed:
                        raise ConflictError("Состав источников изменился; повторите отмену после обновления")
            except ValidationError:
                raise
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                raise ValidationError("Неверные данные периодного плана: " + str(exc)) from None
            # Enforce the boundary before the explicitly permitted quick-volume sync.
            if not allow_native and ({key: value for key, value in state.items() if key != NAMESPACE}
                    != {key: value for key, value in before.items() if key != NAMESPACE}):
                raise ValidationError("Команда периодного плана не может менять исходные данные Work")
            from ..work_manual import sync_volume_by_cubes
            sync_volume_by_cubes(state)
            if action != "undo" and not context_only:
                record_goal_transitions(before, state, library_notes)
            self.store._guard_deletions(before["planning"], state["planning"])
            raw = encoded(state).encode("utf-8")
            digest = hashlib.sha256(raw).hexdigest()
            db.execute("INSERT OR IGNORE INTO work_blobs VALUES(?,?)", (digest, raw))
            # Work validates descriptors as exactly id/blob/mime. Preserve every
            # descriptor, including images currently unattached to any board.
            images = [{key: image[key] for key in ("id", "blob", "mime")} for image in snapshot["images"]]
            descriptors = self.store._validate(db, digest, images)
            db.execute("INSERT INTO work_versions VALUES(?,?,?,?)", (revision + 1, int(time.time()*1000), digest, encoded(descriptors)))
            result = {"context": context(state), "revision": revision + 1} if context_only else self._model(self.store._read(db), library_notes)
            warnings = filter_volume_warnings(state, take_warnings())
            if warnings:
                result["warnings"] = list(dict.fromkeys([*result.get("warnings", []), *warnings]))
            return self.store.remember(db, scope, command, result)

    def command(self, command):
        self._envelope(command, {"request_id", "expected_revision", "action", "data"})
        if isinstance(command.get("action"), str) and command["action"] in {"standalone_create", "standalone_update", "standalone_delete", "standalone_undo"}:
            from ..work_library_link import WorkLibraryLink
            return WorkLibraryLink(self).command(command)
        if not isinstance(command["action"], str) or command["action"] not in ACTIONS or not isinstance(command["data"], dict):
            raise ValidationError("Нужны известная команда периодного плана и объект data")
        library_notes = self.library.notes()
        if command["action"] == "ui_batch":
            payload = command["data"]
            if set(payload) != {"commands"} or not isinstance(payload["commands"], list) or not 1 <= len(payload["commands"]) <= 12:
                raise ValidationError("ui_batch: требуется список команд")
            def batch(state):
                from .day import _transform
                from ..work_manual import ACTIONS as MANUAL_ACTIONS, apply as manual_apply, absorb_overflow, note_warnings
                created = None
                created_project = created_task = None
                for step in payload["commands"]:
                    if not isinstance(step, dict) or set(step) != {"route", "action", "data"} or not isinstance(step["data"], dict):
                        raise ValidationError("ui_batch: неверная команда")
                    route, action, data = step["route"], step["action"], deepcopy(step["data"])
                    if data.get("project_id") == "$created_project":
                        if not created_project:
                            raise ValidationError("ui_batch: проект ещё не создан")
                        data["project_id"] = created_project
                    if data.get("task_id") == "$created_task":
                        if not created_task:
                            raise ValidationError("ui_batch: цель ещё не создана")
                        data["task_id"] = created_task
                    if data.get("cube_id") == "$created_cube":
                        if not created:
                            raise ValidationError("ui_batch: кубик ещё не создан")
                        data["cube_id"] = created
                    if route == "period" and action in ACTIONS - {"undo", "ui_batch"}:
                        state = apply_command(state, action, data, library_notes)
                    elif route == "planning" and action in MANUAL_ACTIONS:
                        prior = set(state["planning"].get("constructor", {}).get("cubes", {}))
                        prior_projects = {p["id"] for p in state.get("projects", [])}
                        prior_tasks = {t["id"] for t in state["planning"].get("tasks", [])}
                        today = datetime.now(ZoneInfo(state["planning"]["settings"]["timezone"])).date()
                        state["planning"] = manual_apply(state, state["planning"], action, data, today)
                        if action == "manual_project" and "project_id" not in data:
                            added = {p["id"] for p in state.get("projects", [])} - prior_projects
                            if len(added) != 1: raise ValidationError("ui_batch: не удалось определить новый проект")
                            created_project = added.pop()
                        if action == "manual_task" and "task_id" not in data:
                            added = {t["id"] for t in state["planning"].get("tasks", [])} - prior_tasks
                            if len(added) != 1: raise ValidationError("ui_batch: не удалось определить новую цель")
                            created_task = added.pop()
                        if action == "manual_cube":
                            added = set(state["planning"].get("constructor", {}).get("cubes", {})) - prior
                            if len(added) != 1:
                                raise ValidationError("ui_batch: не удалось определить новый кубик")
                            created = added.pop()
                    elif route == "planning" and action in {"constructor_cube", "constructor_select", "constructor_return", "constructor_timeline", "cube_progress", "cube_delete", "constructor_goal_status"}:
                        state = _transform(state, {"action": action, "data": data, "_library_notes": library_notes})
                    else:
                        raise ValidationError("ui_batch: неподдерживаемая команда")
                note_warnings(absorb_overflow(state["planning"]))
                wp.validate_planning(state["planning"], state)
                return state
            return self._write(command, "period-planner", batch, library_notes=library_notes, allow_native=True)
        if command["action"] == "undo":
            data = command["data"]
            if set(data) != {"revision"} or type(data["revision"]) is not int or data["revision"] < 2:
                raise ValidationError("undo: требуется ревизия действия")
            with self.store.db() as db:
                prior_row = self.store._version(db, data["revision"] - 1)
                after_row = self.store._version(db, data["revision"])
                if not prior_row or not after_row:
                    raise ValidationError("Ревизия действия не найдена")
                prior, after = self.store._read(db, prior_row), self.store._read(db, after_row)
            if prior["images"] != after["images"] or not prior["state"] or not after["state"]:
                raise ValidationError("Это действие нельзя безопасно отменить")
            def reverse(state):
                return _conditional_reverse(state, prior["state"], after["state"])
            return self._write(command, "period-planner", reverse, library_notes=library_notes, allow_native=True)
        return self._write(command, "period-planner", lambda state: apply_command(state, command["action"], command["data"], library_notes), library_notes=library_notes, allow_native=command["action"] in {"repeatable_drop", "prep_repeatable_cleanup"})

    def set_context(self, command):
        self._envelope(command, {"request_id", "expected_revision", "patch"})
        if not isinstance(command["patch"], dict):
            raise ValidationError("Контекст: нужен объект patch")
        library_notes = self.library.notes()
        return self._write(command, "period-planner-context", lambda state: apply_context(state, command["patch"], library_notes), context_only=True, library_notes=library_notes)

    def adopt(self, command):
        """Adopt an explicitly reviewed namespace blob without importing native Work."""
        from .adoption import adopt_namespace
        self._envelope(command, {"request_id", "expected_revision", "source_blob"})
        self.store._hash(command["source_blob"])
        # Blob reading is immutable; the live state is checked in _write's CAS.
        with self.store.db() as db:
            raw = self.store._blob(db, command["source_blob"])
        return self._write(command, "period-planner-adopt", lambda state: adopt_namespace(state, raw))
