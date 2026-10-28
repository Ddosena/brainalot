"""Duration edits in the Work period planner; native Work volume and facts stay intact."""
from copy import deepcopy
from datetime import datetime, timedelta
from ..core import ValidationError


def capabilities(cube):
    if cube.get("status") == "cancelled" or not cube.get("available", True):
        return False, "Кубик недоступен или отменён"
    if cube.get("kind") == "task":
        return True, None
    if cube.get("actual_minutes") or cube.get("progress_percent"):
        return False, "У кубика уже есть выполненная работа; её длительность здесь не меняется"
    if cube.get("kind") in {"prep", "weekly_review", "brainalot"}:
        return True, None
    if cube.get("kind") == "calendar_optional":
        if cube.get("source_duration_minutes") is not None:
            return False, "Длительность указана в календаре и сохраняется"
        if cube.get("duration_unknown"):
            return False, "Сначала укажите длительность предложения"
        return True, None
    return False, "Объём этапа связан с задачей; изменение размера требует перераспределения её объёма"


def enrich(state, cubes):
    result = deepcopy(cubes)
    overrides = state.get("period_planner", {}).get("cube_durations", {})
    for cube in result:
        cube.setdefault("source_minutes", cube["minutes"])
        if not cube.get("standalone") and cube["id"] in overrides and cube.get("kind") in {"task", "prep", "weekly_review", "brainalot"}:
            cube["minutes"] = overrides[cube["id"]]
        cube["source_duration_unknown"] = cube.get("kind") == "calendar_optional" and cube.get("source_duration_minutes") is None
        cube["can_resize"], cube["resize_block_reason"] = capabilities(cube)
        factual = bool(cube.get("actual_minutes") or cube.get("progress_percent") or cube.get("historical_facts") or cube.get("factual") or cube.get("started") or cube.get("status") in {"started", "completed"})
        from .cube_ops import stale_cleanup_allowed
        cube["can_delete"] = bool((cube.get("available", True) and cube.get("status") != "cancelled" and not factual)
                                  or stale_cleanup_allowed(state, cube))
        cube["delete_block_reason"] = None if cube["can_delete"] else "У кубика есть факты выполнения" if factual else "Кубик недоступен или уже удалён"
        if cube.get("selected_date") and cube.get("start"):
            end = datetime.fromisoformat(cube["selected_date"] + "T" + cube["start"]) + timedelta(minutes=cube["minutes"])
            cube.update(end=end.strftime("%H:%M"), end_date=end.date().isoformat())
    return result


def resize(state, data, model, place):
    if not isinstance(data, dict) or set(data) != {"cube_id", "date", "start", "minutes"}:
        raise ValidationError("cube_resize: нужны cube_id, date, start, minutes")
    cube = next((row for row in model["cubes"] if row["id"] == data["cube_id"]), None)
    if cube is None:
        raise ValidationError("Кубик не найден; перечитайте план")
    allowed, reason = capabilities(cube)
    if not allowed:
        raise ValidationError(reason)
    if not cube.get("selected_date"):
        if cube.get("kind") not in {"task", "brainalot", "prep", "weekly_review"} or data["date"] is not None or data["start"] is not None:
            raise ValidationError("Сначала разместите кубик на времени")
    elif cube.get("start") is not None and (not isinstance(data["start"], str) or not data["start"]):
        raise ValidationError("Для изменения границы укажите точное начало ЧЧ:ММ")
    elif data["start"] is not None and (not isinstance(data["start"], str) or not data["start"]):
        raise ValidationError("Для изменения границы укажите точное начало ЧЧ:ММ")
    minimum = 0 if cube["kind"] in {"prep", "weekly_review"} else 1
    if cube["kind"] == "task":
        if type(data["minutes"]) is not int or not 15 <= data["minutes"] <= 240 or data["minutes"] % 15:
            raise ValidationError("Длительность кубика: 15–240 минут, шаг 15 минут")
    elif type(data["minutes"]) is not int or not minimum <= data["minutes"] <= 1440:
        raise ValidationError(f"Укажите целое число минут от {minimum} до 1440")
    # Validate the final interval before changing native duration or allocation.
    if cube.get("selected_date"):
        place(state, model, {**cube, "minutes": data["minutes"]}, data["date"], data["start"])
    ext = state.setdefault("period_planner", {})
    if cube["kind"] == "task":
        from ..work_manual import note_warnings, VolumeWarning
        ext.setdefault("cube_durations", {})[cube["id"]] = data["minutes"]
        from .model import _real_task_cube
        def portions(item):
            raw = ([{"task_id": item.get("task_id"), "minutes": item["minutes"]}] if item.get("owner_task_override")
                   else item.get("source_task_allocations") or [{"task_id": item.get("task_id"), "minutes": item["minutes"]}])
            base = sum(part.get("minutes", 0) for part in raw)
            if not base:
                return []
            size = data["minutes"] if item["id"] == cube["id"] else item["minutes"]
            return [(part.get("task_id"), size * part["minutes"] / base) for part in raw if part.get("task_id")]
        affected = {tid for tid, _ in portions(cube)}
        totals = {tid: 0 for tid in affected}
        for item in model["cubes"]:
            if not _real_task_cube(state, item):
                continue
            for tid, amount in portions(item):
                if tid in totals:
                    totals[tid] += amount
        tasks = {task["id"]: task for task in model["tasks"]}
        for tid, amount in totals.items():
            task = tasks.get(tid)
            if not task or ext.get("volume_by_cubes", {}).get(tid):
                continue
            total = int(-(-amount // 5) * 5)
            if total > task["minutes"]:
                ext.setdefault("task_fields", {}).setdefault(tid, {})["minutes"] = total
                note_warnings([VolumeWarning(f"Объём цели «{task['title']}» вырос на {total - task['minutes']} мин: кубики заняли больше, чем было запланировано.", tid)])
    else:
        field = "calendar_offer_estimates" if cube["kind"] == "calendar_optional" else "cube_durations"
        ext.setdefault(field, {})[cube["id"]] = data["minutes"]
    return state
