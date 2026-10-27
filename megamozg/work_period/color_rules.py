"""Calendar colour → project points on the period planner rulers.

The owner marks important events with a Google Calendar colour; an assigned
colour puts every event of that colour as a point on the project's ruler.
Only the assignment is stored (``period_planner.calendar_colors``: colour →
project). Points stay read-only Calendar data and follow every calendar sync.
"""
from __future__ import annotations

from ..calendar_palette import HEX, NAMES, STANDARD
from ..core import ValidationError
from ..shared_state import identifier

ACTIONS = frozenset({"calendar_color_assign"})


def _owners(state, projects):
    known = {project["id"] for project in projects}
    colors = state.get("period_planner", {}).get("calendar_colors", {})
    # A hidden or deleted project keeps its colour; it simply shows no points.
    return {color: pid for color, pid in colors.items() if pid in known}


def palette(state, events, projects):
    """Standard colours, then custom ones seen in Calendar, with counts and owners."""
    owners = _owners(state, projects)
    counts, names = {}, {}
    for event in events:
        color = event.get("color") if event.get("kind") == "calendar" else None
        if color:
            counts[color] = counts.get(color, 0) + 1
            if event.get("color_name"):
                names.setdefault(color, event["color_name"])
    rows = [{"color": color, "name": names.get(color, name), "standard": color not in names,
             "count": counts.get(color, 0), "project_id": owners.get(color)} for color, name in STANDARD]
    rows.extend({"color": color, "name": names.get(color, color), "standard": False,
                 "count": count, "project_id": owners.get(color)}
                for color, count in sorted(counts.items()) if color not in NAMES)
    return rows


def enrich(state, projects, native_events, events):
    """Attach ``calendar_points`` to every project from its assigned colours.

    ``native_events`` still holds events that became optional offers; ``events``
    are the ones the planner can open in its calendar inspector.
    """
    owners = _owners(state, projects)
    shown = {event["id"]: event for event in events}
    points = {}
    for event in native_events:
        pid = owners.get(event.get("color")) if event.get("kind") == "calendar" else None
        if pid is None or not event.get("date"):
            continue
        title = shown.get(event["id"], event)["title"]
        points.setdefault(pid, []).append({
            "id": "calendar:" + event["id"], "calendar_event_id": event["id"], "project_id": pid,
            "title": title, "date": event["date"], "end_date": event.get("end_date"),
            "start": event.get("start"), "end": event.get("end"), "all_day": bool(event.get("all_day")),
            "color": event["color"], "color_name": event.get("color_name") or NAMES.get(event["color"]),
            "event_kind": "calendar", "can_open": event["id"] in shown, "read_only": True})
    for project in projects:
        project["calendar_points"] = sorted(points.get(project["id"], []),
                                            key=lambda row: (row["date"], row["start"] or "", row["id"]))
    return projects


def apply(state, action, data, model):
    """Assign a colour to one project (moving it from another) or release it with null."""
    if action not in ACTIONS or not isinstance(data, dict) or set(data) != {"color", "project_id"}:
        raise ValidationError("calendar_color_assign: нужны color и project_id")
    color = data["color"].lower() if isinstance(data["color"], str) else None
    if color is None or not HEX.fullmatch(color):
        raise ValidationError("Цвет календаря: требуется #rrggbb")
    colors = state.setdefault("period_planner", {}).setdefault("calendar_colors", {})
    if data["project_id"] is None:
        colors.pop(color, None)
        return state
    pid = identifier(data["project_id"], "project_id")
    if not any(project["id"] == pid for project in model["projects"]):
        raise ValidationError("Проект не найден; перечитайте план")
    colors[color] = pid
    return state
