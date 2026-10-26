"""Duration edits in the Work period planner; native Work volume and facts stay intact."""
from copy import deepcopy
from datetime import datetime, timedelta
from ..core import ValidationError


def capabilities(cube):
    if cube.get("status") == "cancelled" or not cube.get("available", True):
        return False, "Кубик недоступен или отменён"
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
        if cube["id"] in overrides and cube.get("kind") in {"prep", "weekly_review", "brainalot"}:
            cube["minutes"] = overrides[cube["id"]]
        cube["source_duration_unknown"] = cube.get("kind") == "calendar_optional" and cube.get("source_duration_minutes") is None
        cube["can_resize"], cube["resize_block_reason"] = capabilities(cube)
        factual = bool(cube.get("actual_minutes") or cube.get("progress_percent") or cube.get("historical_facts") or cube.get("factual") or cube.get("started") or cube.get("status") in {"started", "completed"})
        cube["can_delete"] = bool(cube.get("available", True) and cube.get("status") != "cancelled" and not factual)
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
    if not cube.get("selected_date") or not cube.get("start"):
        raise ValidationError("Сначала разместите кубик на времени")
    if not isinstance(data["start"], str) or not data["start"]:
        raise ValidationError("Для изменения границы укажите точное начало ЧЧ:ММ")
    minimum = 0 if cube["kind"] in {"prep", "weekly_review"} else 1
    if type(data["minutes"]) is not int or not minimum <= data["minutes"] <= 1440:
        raise ValidationError(f"Укажите целое число минут от {minimum} до 1440")
    ext = state.setdefault("period_planner", {})
    field = "calendar_offer_estimates" if cube["kind"] == "calendar_optional" else "cube_durations"
    ext.setdefault(field, {})[cube["id"]] = data["minutes"]
    # The caller owns a copied state and the WorkStore CAS transaction.
    place(state, model, {**cube, "minutes": data["minutes"]}, data["date"], data["start"])
    return state
