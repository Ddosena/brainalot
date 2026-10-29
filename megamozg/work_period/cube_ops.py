"""Cube edits confined to the Work period_planner namespace.

Both operations return a deep copied state. Validation failures leave the input
state and read model untouched; WorkStore owns revision checks and replay.
"""
from copy import deepcopy
import hashlib

from ..core import ValidationError
from ..shared_state import encoded
from .. import work_planning as wp
from . import library_rules

NAMESPACE = "period_planner"
DELETE_REASON = "Удалено пользователем"
DETAIL_FIELDS = frozenset({"output", "steps", "comment", "links", "groups", "resource_drafts", "planned_date", "sources"})


def validate_details(row, patch, planning):
    """Validate descriptive Work metadata without accepting identity or facts."""
    if not isinstance(patch, dict) or set(patch) - DETAIL_FIELDS:
        raise ValidationError("Недопустимые поля содержимого кубика")
    if row.get("source_unavailable"):
        raise ValidationError(row.get("availability_block_reason") or "Исходная задача Brainalot недоступна")
    for field, maximum in (("output", 1000), ("comment", 4000)):
        if field in patch and (not isinstance(patch[field], str) or len(patch[field]) > maximum):
            raise ValidationError(f"constructor {field}: неверный текст")
    if "steps" in patch:
        steps = patch["steps"]
        if not isinstance(steps, list) or len(steps) > 20 or any(not isinstance(step, str) or not step.strip() or len(step) > 500 for step in steps):
            raise ValidationError("constructor steps: до20 шагов")
    if "sources" in patch:
        from urllib.parse import urlparse
        sources = patch["sources"]
        if not isinstance(sources, list) or len(sources) > 100:
            raise ValidationError("Источники: нужен список до 100 записей")
        seen = set()
        for source in sources:
            if (not isinstance(source, dict) or set(source) != {"id", "url", "text"}
                    or not isinstance(source["id"], str) or not source["id"] or len(source["id"]) > 100
                    or source["id"] in seen or not isinstance(source["text"], str)
                    or len(source["text"]) > 1000 or not isinstance(source["url"], str)
                    or len(source["url"]) > 2048 or not (source["text"].strip() or source["url"])):
                raise ValidationError("Неверный источник кубика")
            if source["url"]:
                parsed = urlparse(source["url"])
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    raise ValidationError("Ссылка источника должна быть http(s)")
            seen.add(source["id"])
    wp._constructor_resources({**row, **patch})
    if "resource_drafts" in patch:
        wp._constructor_resources(patch["resource_drafts"], drafts=True)
    if "planned_date" in patch:
        if row.get("kind") not in {"task", "brainalot"}:
            raise ValidationError("Дата этого источника задаётся связанным событием; выберите день размещения")
        if patch["planned_date"] is not None and wp._day(patch["planned_date"], "planned_date") not in wp._dates(planning["settings"]):
            raise ValidationError("planned_date: вне горизонта")
    return deepcopy(patch)


def enrich_details(state, cubes):
    overrides = state.get(NAMESPACE, {}).get("cube_details", {})
    return [{**deepcopy(row), **{key: deepcopy(value) for key, value in overrides.get(row["id"], {}).items()
        if key in DETAIL_FIELDS and not (row.get("standalone") and key == "sources")}} for row in cubes]


def _fields(data, fields):
    if not isinstance(data, dict) or set(data) != set(fields):
        raise ValidationError("Неверные поля команды: " + ", ".join(sorted(fields)))


def _title(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 1000:
        raise ValidationError("Нужен непустой текст до 1000 символов")
    return value.strip()


def fact_delete_allowed(state, row):
    """Owner 2026-10-29: a done or started goal/repeatable cube (also a merged one) may be deleted.
    It is the usual cancellation overlay, so native facts stay stored and undo restores them.
    Library cubes keep their own deletion path through the Note."""
    return row.get("kind") in {"task", "repeatable"} and not row.get("standalone")


def _factual(row):
    return bool(row.get("progress_percent") or row.get("actual_minutes")
            or row.get("historical_facts") or row.get("factual") or row.get("started")
            or row.get("status") in {"started", "completed"})


def _unstarted(row, *, inactive_cleanup=False, stale_cleanup=False, allow_facts=False):
    if row.get("status") == "cancelled":
        raise ValidationError("Кубик уже удалён или отменён; его история сохранена")
    factual = _factual(row)
    if factual and not allow_facts:
        raise ValidationError("У кубика уже есть факты выполнения: удаление и объединение запрещены")
    # A completed cube is unavailable only because it is done; the owner may still delete it.
    if (not row.get("available") and not (allow_facts and factual) and not stale_cleanup
            and not (inactive_cleanup and library_rules.can_cleanup(row))):
        raise ValidationError("Кубик недоступен: проверьте остаток и границы ответственности")


def _placement(row):
    return {"date": row.get("selected_date"), "start": row.get("start")}



def stale_cleanup_allowed(state, row):
    """Only hide an unavailable composition when every retained source is unstarted."""
    ext = state.get(NAMESPACE, {})
    cube_id = row.get("id")
    if not row.get("allocation_stale") or cube_id not in ext.get("merged_cubes", {}):
        return False
    if row.get("status") in {"started", "completed", "cancelled"} or any(row.get(field) for field in
            ("progress_percent", "actual_minutes", "historical_facts", "factual", "started")):
        return False
    aliases = set(row.get("source_ids") or [])
    aliases.update(cid[len("planned-"):] for cid in list(aliases) if cid.startswith("planned-"))
    reports = state["planning"].get("completed_cubes", {})
    native = state["planning"].get("constructor", {}).get("cubes", {})
    if any(cid in reports for cid in aliases):
        return False
    if any(cube.get("blueprint_ref") in aliases and cid in reports for cid, cube in native.items()):
        return False
    pending, visited = [cube_id], set()
    merges = ext.get("merges", {})
    while pending:
        key = pending.pop()
        if key in visited:
            continue
        visited.add(key)
        record = merges.get(key)
        if not isinstance(record, dict) or not isinstance(record.get("sources"), list) or not record["sources"]:
            return False
        for source in record["sources"]:
            if not isinstance(source, dict) or any(source.get(field) for field in
                    ("progress_percent", "actual_minutes", "historical_facts", "factual", "started")):
                return False
            if source.get("id") in merges:
                pending.append(source["id"])
    return True

def delete(state, data, model):
    """Cancel exactly one unstarted cube, including a cube selected on a day."""
    _fields(data, {"cube_id"})
    cube_id = data["cube_id"]
    if not isinstance(cube_id, str) or not cube_id:
        raise ValidationError("Нужен ID кубика")
    ext = state.get(NAMESPACE, {})
    if cube_id in ext.get("cancellations", {}):
        raise ValidationError("Кубик уже удалён или отменён; его история сохранена")
    row = next((cube for cube in model["cubes"] if cube["id"] == cube_id), None)
    if row is None:
        if any(event["id"] == cube_id for event in model["events"]):
            raise ValidationError("Календарное событие нельзя удалить действием для кубика")
        raise ValidationError("Кубик не найден или уже объединён; обновите экран")
    if row.get("kind") not in {"task", "prep", "weekly_review", "calendar_optional", "brainalot", "repeatable"}:
        raise ValidationError("Этот источник нельзя удалить действием для кубика")
    stale_cleanup = stale_cleanup_allowed(state, row)
    # Owner 2026-10-29: completed or started cubes can be deleted too. Deletion is the usual
    # cancellation overlay: native facts stay stored (undo restores them), the cube and its
    # credit leave the goal.
    _unstarted(row, inactive_cleanup=True, stale_cleanup=stale_cleanup, allow_facts=fact_delete_allowed(state, row))
    result = deepcopy(state)
    ext = result.setdefault(NAMESPACE, {})
    ext.setdefault("cancellations", {})[cube_id] = {
        "reason": DELETE_REASON, "minutes": row["minutes"],
        "source_ids": deepcopy(row.get("source_ids") or [cube_id]),
        "source": deepcopy(row), "prior_placement": _placement(row),
        "prior_placement_override": deepcopy(ext.get("placements", {}).get(cube_id)),
    }
    if row.get("source_task_allocations"):
        # A display-duration override does not change native source allocations.
        original_minutes = sum(part["minutes"] for part in row["source_task_allocations"])
        ext["cancellations"][cube_id]["source_task_allocations"] = deepcopy(_row_allocations({**row, "minutes": original_minutes}))
    # A null override also masks a preserved native lock/source selection.
    ext.setdefault("placements", {})[cube_id] = {"date": None, "start": None}
    if ext.get("context", {}).get("cube_id") == cube_id:
        ext["context"]["cube_id"] = None
    return result


def _row_allocations(row):
    """Return leaf allocations, accepting older same-task merged rows as well."""
    leaves = list(dict.fromkeys(row.get("source_ids") or [row["id"]]))
    parts = row.get("source_task_allocations") or [{"task_id": row.get("task_id"),
        "project_id": row.get("project_id"), "minutes": row["minutes"], "source_ids": leaves}]
    if (any(not isinstance(part.get("task_id"), str) or not part["task_id"]
            or type(part.get("minutes")) is not int or part["minutes"] <= 0
            or not isinstance(part.get("source_ids"), list) or not part["source_ids"] for part in parts)
            or sum(part["minutes"] for part in parts) != row["minutes"]):
        raise ValidationError("Для объединения нужны кубики Work с сохранённым объёмом родительских задач")
    allocated = [source_id for part in parts for source_id in part["source_ids"]]
    if (any(not isinstance(source_id, str) or not source_id for source_id in allocated)
            or len(allocated) != len(set(allocated)) or set(allocated) != set(leaves)):
        raise ValidationError("Источники родительских задач пересекаются или неполны; объединение небезопасно")
    return deepcopy(parts)


def _allocations(rows):
    result = []
    for row in rows:
        for part in _row_allocations(row):
            saved = next((item for item in result if item["task_id"] == part["task_id"]), None)
            if saved is None:
                result.append(deepcopy(part))
                continue
            if saved.get("project_id") != part.get("project_id"):
                raise ValidationError("У одной задачи разные проекты в истории; объединение небезопасно")
            saved["minutes"] += part["minutes"]
            saved["source_ids"].extend(part["source_ids"])
    return result


def _segments(rows):
    """Keep original per-segment volume through any number of nested merges."""
    result = []
    for row in rows:
        parts = row.get("source_segments") or [{"task_id": row.get("task_id"), "segment_id": row.get("segment_id"),
            "minutes": row["minutes"], "source_ids": row.get("source_ids") or [row["id"]]}]
        if sum(part["minutes"] for part in parts) != row["minutes"]:
            raise ValidationError("История этапов не совпадает с объёмом кубика; объединение небезопасно")
        for part in parts:
            task_id = part.get("task_id") or row.get("task_id")
            if not task_id:
                raise ValidationError("В истории этапа потеряна родительская задача; объединение небезопасно")
            target = next((saved for saved in result
                if (saved["task_id"], saved["segment_id"]) == (task_id, part.get("segment_id"))), None)
            if target is None:
                target = {"task_id": task_id, "segment_id": part.get("segment_id"), "minutes": 0, "source_ids": []}
                result.append(target)
            target["minutes"] += part["minutes"]
            for source_id in part["source_ids"]:
                if source_id not in target["source_ids"]:
                    target["source_ids"].append(source_id)
    return result


def merge(state, data, model, place):
    """Compose Work task cubes; the first ID is the drop target and owns placement."""
    _fields(data, {"cube_ids", "title"})
    ids = data["cube_ids"]
    if (not isinstance(ids, list) or not 2 <= len(ids) <= 10
            or any(not isinstance(cid, str) or not cid for cid in ids)
            or len(ids) != len(set(ids))):
        raise ValidationError("Для объединения нужны 2–10 разных кубиков")
    title = _title(data["title"])
    found = {cube["id"]: cube for cube in model["cubes"]}
    if any(cid not in found for cid in ids):
        raise ValidationError("Кубик объединения не найден или уже объединён; обновите экран")
    rows = [found[cid] for cid in ids]
    for row in rows:
        _unstarted(row)
        if row.get("kind") != "task":
            raise ValidationError("Объединяются только кубики задач Work с сохранённым родительским объёмом")
    source_ids = []
    for row in rows:
        leaves = list(dict.fromkeys(row.get("source_ids") or [row["id"]]))
        if set(leaves) & set(source_ids):
            raise ValidationError("Кубики содержат общий исходный объём; повторное объединение небезопасно")
        source_ids.extend(leaves)
    cancelled_ids = {source_id for cube_id, record in state.get(NAMESPACE, {}).get("cancellations", {}).items()
        for source_id in [cube_id, *record.get("source_ids", [])]}
    if cancelled_ids & set(source_ids):
        raise ValidationError("В составе есть удалённый или отменённый кубик; его объём нельзя объединять")
    allocations = _allocations(rows)
    segments = _segments(rows)
    target = rows[0]
    merged_id = "merged-" + hashlib.sha256(encoded(ids).encode()).hexdigest()[:20]
    planned_date = target.get("planned_date") if len({row.get("planned_date") for row in rows}) == 1 else None
    merged = deepcopy(target)
    merged.update(id=merged_id, canonical_id=merged_id, blueprint_ref=None,
        task_id=allocations[0]["task_id"] if len(allocations) == 1 else None,
        project_id=allocations[0].get("project_id") if len({part.get("project_id") for part in allocations}) == 1 else None,
        title=title, source_title=title, minutes=sum(row["minutes"] for row in rows),
        source_minutes=sum(row.get("source_minutes", row["minutes"]) for row in rows),
        source_ids=source_ids, source_segments=deepcopy(segments), source_task_allocations=deepcopy(allocations),
        segment_id=segments[0]["segment_id"] if len(segments) == 1 else None,
        candidate=False, selected_date=target.get("selected_date"),
        start=target.get("start") if target.get("selected_date") else None, end=None, end_date=None,
        planned_date=planned_date, date=planned_date,
        status="selected" if target.get("selected_date") else "available")
    merged["output"] = "\n".join(dict.fromkeys(row.get("output", "") for row in rows if row.get("output")))
    merged["steps"] = []
    for row in rows:
        for step in row.get("steps", []):
            if step not in merged["steps"]:
                merged["steps"].append(deepcopy(step))
    result = deepcopy(state)
    # Exclude every absorbed source, including sources selected on another day,
    # before checking the merged target's complete load and occupied interval.
    if target.get("selected_date"):
        validation_model = {**model, "cubes": [cube for cube in model["cubes"] if cube["id"] not in ids]}
        place(result, validation_model, merged, target["selected_date"], target.get("start"))
    ext = result.setdefault(NAMESPACE, {})
    ext.setdefault("merged_cubes", {})[merged_id] = merged
    ext.setdefault("merges", {})[merged_id] = {
        "source_ids": deepcopy(ids), "original_source_ids": source_ids,
        "sources": deepcopy(rows), "source_segments": deepcopy(segments),
        "source_task_allocations": deepcopy(allocations),
        "prior_placements": {row["id"]: _placement(row) for row in rows},
    }
    placements = ext.setdefault("placements", {})
    for cube_id in ids:
        placements[cube_id] = {"date": None, "start": None}
    if not target.get("selected_date"):
        placements[merged_id] = {"date": None, "start": None}
    # Inferred dates otherwise depend on the now absorbed forecast rows. Keep
    # the current task range without changing its deadline or source estimate.
    for allocation in allocations:
        task_id = allocation["task_id"]
        task = next((task for task in model["tasks"] if task["id"] == task_id), None)
        dates = [cube["planned_date"] for cube in [*model["cubes"], merged]
            if cube.get("task_id") == task_id and cube["id"] not in ids and cube.get("planned_date")]
        range_changed = not dates or (min(dates), max(dates)) != ((task or {}).get("start"), (task or {}).get("end"))
        if (task and range_changed and task.get("start") and task.get("end")
                and task_id not in ext.get("periods", {})):
            ext.setdefault("periods", {})[task_id] = {"start": task["start"], "end": task["end"]}
    if ext.get("context", {}).get("cube_id") in ids:
        ext["context"]["cube_id"] = None
    return result
