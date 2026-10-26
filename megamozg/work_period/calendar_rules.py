"""Private Calendar colour metadata and local scheduling semantics.

Only exact native hash identities join provider rows to the preserved snapshot.
Titles are descriptive data and never participate in a match or role decision.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib
import json
import re
from zoneinfo import ZoneInfo

from ..core import ValidationError
from .. import work_planning as wp

ROLES = {"#009688": "fixed", "#039be5": "fixed", "#ad1457": "flexible", "#d50000": "flexible",
    "#d81b60": "flexible", "#e67c73": "flexible", "#a79b8e": "optional_day", "#616161": "optional_day",
    "#33b679": "evergreen"}
LEGACY_COLORS = {"2": "#33b679", "8": "#616161", "4": "#e67c73", "11": "#d50000", "7": "#039be5"}
OPTIONAL = {"optional_day", "evergreen"}


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _dt(value, zone):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return result.replace(tzinfo=zone) if result.tzinfo is None else result.astimezone(zone)


def _minutes(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise ValidationError("Требуется время ЧЧ:ММ")
    return int(value[:2])*60 + int(value[3:])


def _iso_day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValidationError("Требуется ISO-дата")
    return value


def _uids(event):
    values = {event.get("ical_uid"), event.get("iCalUID")}
    for value in (event.get("id"), event.get("recurring_event_id")):
        if not isinstance(value, str) or not value:
            continue
        values.add(value if "@" in value else value + "@google.com")
        base = value.split("_R", 1)[0]
        base = re.sub(r"_\d{8}(?:T\d{6}Z)?$", "", base)
        values.add(base if "@" in base else base + "@google.com")
    return sorted(value for value in values if isinstance(value, str) and value)


def _series(event):
    value = event.get("recurring_event_id")
    return value.split("_R", 1)[0] if isinstance(value, str) and value else None


def source_role(event, labels):
    label = event.get("event_label_id")
    if label is not None:
        color = labels.get(label)
        return ROLES.get(color, "unknown"), color, "custom_label"
    legacy = event.get("color_id")
    if legacy is not None:
        color = LEGACY_COLORS.get(str(legacy))
        return ROLES.get(color, "unknown"), color, "legacy_color_id"
    if "event_label_id" in event and "color_id" in event:
        return "fixed", None, "default"
    return "unknown", None, "metadata_unavailable"


def prepare_import(state, source):
    if (not isinstance(source, dict) or not isinstance(source.get("events"), list)
            or len(source["events"]) > 2000 or not isinstance(source.get("labels"), list)
            or len(source["labels"]) > 1000):
        raise ValidationError("Calendar source: нужны events и labels")
    zone = ZoneInfo(source.get("timezone", state["planning"]["settings"]["timezone"]))
    captured = datetime.fromisoformat(source["captured_at"].replace("Z", "+00:00"))
    if captured.tzinfo is None:
        raise ValidationError("Calendar captured_at требует часовой пояс")
    coverage = source.get("coverage", {})
    if _iso_day(coverage.get("start")) > _iso_day(coverage.get("end")):
        raise ValidationError("Неверный диапазон источника Calendar")
    provider_calendar = source.get("calendar_id")
    if not isinstance(provider_calendar, str) or not provider_calendar:
        raise ValidationError("Calendar source calendar_id обязателен")
    labels = {row["id"]: str(row.get("background_color", "")).lower() for row in source["labels"]}
    native = state["planning"]["calendar"]["events"]
    indexed = {row["id"]: row for row in native}
    occurrences = {}
    for row in native:
        occurrences.setdefault(row.get("occurrence_id"), []).append(row)
    calendars = {row["calendar_id"] for row in native if row.get("calendar_id")}
    previous = state.get("period_planner", {}).get("calendar_source", {}).get("events", {})
    canonical_ids = {row["native_event_id"]: row["id"] for row in previous.values() if row.get("native_event_id")}
    known_provider = {(row.get("provider_calendar_id"), row.get("provider_id")): row
        for row in previous.values() if not row.get("native_event_id")}
    records, matched_provider, seen_provider = {}, set(), set()
    warnings = []
    for event in source["events"]:
        if not isinstance(event, dict) or not isinstance(event.get("id"), str):
            raise ValidationError("Provider event ID обязателен")
        seen_provider.add(event["id"])
        if event.get("status") == "cancelled":
            continue
        first, last = _dt(event["start"], zone), _dt(event["end"], zone)
        if last <= first:
            raise ValidationError("Provider event: конец раньше начала")
        role, color, color_basis = source_role(event, labels)
        matches = {}
        # Production Calendar snapshots may already use provider IDs. Reuse the
        # reviewed canonical mapping or an exact calendar/provider identity.
        direct = indexed.get(event["id"])
        approved = next((row for row in previous.values() if row.get("provider_id") == event["id"]
            and row.get("provider_calendar_id") == provider_calendar
            and row.get("native_event_id") in indexed), None)
        if approved is not None:
            direct = indexed[approved["native_event_id"]]
        if direct is not None and (approved is not None or direct.get("calendar_id") == provider_calendar):
            matches[direct["id"]] = (direct, next(iter(_uids(event)), event["id"]), "provider_identity")
        for uid in _uids(event):
            for calendar_id in calendars:
                for raw_first in {first.isoformat(), datetime.fromisoformat(event["start"].replace("Z", "+00:00")).isoformat()}:
                    event_hash = _digest(calendar_id + "|" + uid + "|" + raw_first)
                    if event_hash in indexed:
                        matches[event_hash] = (indexed[event_hash], uid, "event_hash")
                slots = ["single"] if not event.get("recurring_event_id") else [_dt(event.get("original_start_time") or event["start"], zone).isoformat()]
                for slot in slots:
                    occurrence_hash = _digest(calendar_id + "|" + uid + "|" + slot)
                    for row in occurrences.get(occurrence_hash, []):
                        matches[row["id"]] = (row, uid, "occurrence_hash")
        if len(matches) > 1:
            warnings.append("Неоднозначная криптографическая связь Calendar: " + event["id"])
            continue
        matched = next(iter(matches.values()), None)
        if matched:
            native_event, uid, method = matched
            event_id = canonical_ids.get(native_event["id"], native_event["id"])
            matched_provider.add(event["id"])
            native_first, native_last = _dt(native_event["start"], zone), _dt(native_event["end"], zone)
            all_day = bool(native_event.get("all_day"))
            duration = None if all_day else round((native_last-native_first).total_seconds()/60)
        elif role in OPTIONAL or (provider_calendar, event["id"]) in known_provider:
            # Provider-only optional offers are explicitly permitted; fixed/flex
            # unmatched events never replace or bulk-refresh the native snapshot.
            event_id = "provider-" + _digest(provider_calendar + "|" + event["id"])
            uid, method = (_series(event) or event["id"]) + "@google.com", "provider_only_optional"
            raw_first = datetime.fromisoformat(event["start"].replace("Z", "+00:00"))
            raw_last = datetime.fromisoformat(event["end"].replace("Z", "+00:00"))
            # The connector omits all_day. Naive midnight/date spans are not a
            # verified timed duration; require an estimate rather than invent24h.
            all_day = bool(event.get("all_day") or "T" not in event["start"] or
                raw_first.tzinfo is None and raw_first.time() == datetime.min.time() and raw_last.date() > raw_first.date())
            duration = None if all_day else round((last-first).total_seconds()/60)
            native_event = None
        else:
            continue
        record = {"id": event_id, "provider_id": event["id"], "provider_calendar_id": provider_calendar,
            "provider_series_id": _series(event), "provider_uid": uid, "identity_match": method,
            "source_title": event.get("summary") or event.get("title") or (native_event or {}).get("title", "Событие"),
            "source_date": first.date().isoformat(), "source_start": None if all_day else first.strftime("%H:%M"),
            "source_end": None if all_day else (first+timedelta(minutes=duration)).strftime("%H:%M"),
            "source_end_date": last.date().isoformat() if all_day else (first+timedelta(minutes=duration)).date().isoformat(),
            "all_day": all_day, "duration_minutes": duration, "calendar_role": role,
            "source_color": color, "color_basis": color_basis, "color_id": event.get("color_id"),
            "event_label_id": event.get("event_label_id"), "native_event_id": native_event["id"] if native_event else None,
            "source_occurrence_id": (native_event or {}).get("occurrence_id"), "metadata_available": True,
            "provider_source": deepcopy(event)}
        records[event_id] = record
    stale = 0
    for event_id, old in previous.items():
        if event_id not in records and old.get("provider_id") not in seen_provider:
            records[event_id] = {**deepcopy(old), "metadata_stale": True}
            stale += 1
    if stale:
        warnings.append(f"Для {stale} ранее связанных событий нет новой строки в снимке; прежние данные и выбор сохранены.")
    unavailable = sorted({canonical_ids.get(key, key) for key in indexed}-set(records))
    if unavailable:
        warnings.append(f"Для {len(unavailable)} сохранённых событий цвет Calendar не удалось связать по ID; они доступны только для чтения.")
    unknown = sum(row["calendar_role"] == "unknown" for row in records.values())
    if unknown:
        warnings.append(f"Для {unknown} событий цвет не имеет утверждённого значения; они доступны только для чтения.")
    return {"captured_at": source["captured_at"], "coverage": deepcopy(coverage), "calendar_id": provider_calendar,
        "source_sha256": hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
        "events": records, "unmatched_native_ids": unavailable, "warnings": warnings,
        "matched_count": len(matched_provider), "provider_only_offer_count": sum(not r["native_event_id"] for r in records.values())}


def offer_id(record):
    if record["calendar_role"] == "evergreen" and record.get("provider_series_id"):
        return "calendar-offer-series-" + _digest(record["provider_calendar_id"] + "|" + record["provider_series_id"])
    return "calendar-offer-" + record["id"]


def apply_import(state, source):
    incoming = prepare_import(state, source)
    ext = state.setdefault("period_planner", {})
    previous = ext.get("calendar_source", {}).get("events", {})
    incoming_offer_ids = {offer_id(record) for record in incoming["events"].values() if record["calendar_role"] in OPTIONAL}
    transition_warnings = []
    for event_id, record in incoming["events"].items():
        old = previous.get(event_id)
        if record["calendar_role"] in OPTIONAL and ext.get("context", {}).get("calendar_event_id") == event_id:
            ext["context"]["calendar_event_id"] = None
        if not old:
            continue
        if old["calendar_role"] in OPTIONAL and record["calendar_role"] not in OPTIONAL:
            alias = ext.get("cube_titles", {}).get(offer_id(old))
            if alias is not None:
                ext.setdefault("calendar_event_titles", {})[event_id] = alias
        elif old["calendar_role"] not in OPTIONAL and record["calendar_role"] in OPTIONAL:
            alias = ext.get("calendar_event_titles", {}).get(event_id)
            if alias is not None:
                ext.setdefault("cube_titles", {})[offer_id(record)] = alias
        if old["calendar_role"] == "flexible" and record["calendar_role"] != "flexible":
            placement = ext.get("calendar_event_placements", {}).pop(event_id, None)
            if placement:
                ext.setdefault("calendar_placement_archive", []).append({"event_id": event_id, "placement": placement,
                    "reason": "source_role_changed", "previous_role": old["calendar_role"], "new_role": record["calendar_role"],
                    "captured_at": source["captured_at"]})
                transition_warnings.append("Цвет события изменился в Calendar: локальный перенос сохранён в истории; используется дата и время источника.")
        if old["calendar_role"] in OPTIONAL and (record["calendar_role"] not in OPTIONAL or offer_id(old) != offer_id(record)):
            cube_id = offer_id(old)
            if cube_id not in incoming_offer_ids and ext.get("context", {}).get("cube_id") == cube_id:
                ext["context"]["cube_id"] = None
            placement = ext.get("placements", {}).pop(cube_id, None)
            if placement:
                ext.setdefault("calendar_placement_archive", []).append({"event_id": event_id, "cube_id": cube_id,
                    "placement": placement, "reason": "optional_became_mandatory", "captured_at": source["captured_at"]})
                transition_warnings.append("Роль предложения изменилась в Calendar; прежний выбор сохранён в истории, используется новая роль источника.")
    incoming["warnings"] = list(dict.fromkeys(incoming["warnings"] + transition_warnings))
    ext["calendar_source"] = incoming
    return state


def coverage(state):
    """Only the intersection actually projected can establish calendar coverage."""
    native = state.get("planning", {}).get("calendar", {})
    imported = state.get("period_planner", {}).get("calendar_source", {}).get("coverage", {})
    def bounds(value):
        return {key: value[key] for key in ("start", "end")} if value.get("start") and value.get("end") else None
    native, imported = bounds(native), bounds(imported)
    effective = native
    if native and imported:
        first, last = max(native["start"], imported["start"]), min(native["end"], imported["end"])
        effective = {"start": first, "end": last} if first <= last else None
    return {"native": native, "metadata": imported, "effective": effective}


def enrich(state, events, cubes):
    ext, planning = state.get("period_planner", {}), state["planning"]
    source = ext.get("calendar_source", {})
    metadata = deepcopy(source.get("events", {}))
    projected, warnings = [], list(source.get("warnings", []))
    if events and not source:
        warnings.append("Цветовые метаданные Calendar ещё не импортированы; сохранённые события доступны только для чтения.")
    # A provider-only optional occurrence already introduced by explicit import
    # may later become fixed. Its stable proven provider identity is retained;
    # unrelated unmatched fixed provider rows are never added.
    events = list(events)
    for record in metadata.values():
        if not record.get("native_event_id") and record["calendar_role"] not in OPTIONAL:
            events.append({"id": record["id"], "title": record["source_title"], "date": record["source_date"],
                "start": record["source_start"], "end": record["source_end"], "end_date": record["source_end_date"],
                "duration_minutes": record["duration_minutes"], "all_day": record["all_day"], "kind": "calendar",
                "conflict": False, "alternatives": [], "project_id": None, "source_ids": [record["id"]]})
    for event in events:
        record = metadata.get(event["id"])
        if record and event.get("native_event_id"):
            # The current native Calendar snapshot owns date/time/duration.
            # The imported provider map supplies identity and colour semantics.
            record = {**record, "source_date": event["date"], "source_start": event.get("start"),
                "source_end": event.get("end"), "source_end_date": event.get("end_date"),
                "duration_minutes": event.get("duration_minutes"), "all_day": event.get("all_day", False),
                "source_title": event["title"]}
            metadata[event["id"]] = record
        if record and record["calendar_role"] in OPTIONAL:
            continue
        row = deepcopy(event)
        role = record["calendar_role"] if record else "unknown"
        row["source_title"] = (record or {}).get("source_title", row["title"])
        row["title"] = ext.get("calendar_event_titles", {}).get(row["id"], row["source_title"])
        row["can_rename"] = True
        row.update(calendar_role=role, source_color=(record or {}).get("source_color"), metadata_available=bool(record),
            movable=role == "flexible" and bool((record or {}).get("duration_minutes")) and not row["all_day"])
        row["provider_id"] = (record or {}).get("provider_id")
        row["source_occurrence_id"] = (record or {}).get("source_occurrence_id")
        row["metadata_stale"] = (record or {}).get("metadata_stale", False)
        row["source_date"] = (record or {}).get("source_date", row["date"])
        row["source_start"] = (record or {}).get("source_start", row.get("start"))
        row["source_end"] = (record or {}).get("source_end", row.get("end"))
        if record:
            row.update(date=record["source_date"], start=record["source_start"], end=record["source_end"],
                end_date=record["source_end_date"], duration_minutes=record["duration_minutes"])
        placement = ext.get("calendar_event_placements", {}).get(row["id"]) if role == "flexible" else None
        if placement:
            row.update(placement)
            row["locally_moved"] = True
        if row["all_day"] or not row.get("duration_minutes"):
            row["movable"] = False
            row["move_block_reason"] = "В источнике нет подтверждённой длительности и времени; перенос без них недоступен."
        elif not row["movable"]:
            row["move_block_reason"] = "Закреплено цветом Calendar" if role == "fixed" else "Цвет источника не подтверждён; доступно только чтение"
        if row.get("alternatives"):
            # Source conflict remains explicit. A moved flexible source changes
            # only its own calendar branch, never rewrites linked Work facts.
            for alternative in row["alternatives"]:
                if alternative["kind"] == "calendar":
                    alternative.update(date=row["date"], start=row["start"], end=row["end"], end_date=row.get("end_date"))
        projected.append(row)
    groups = {}
    for record in metadata.values():
        if record["calendar_role"] in OPTIONAL:
            groups.setdefault(offer_id(record), []).append(record)
    native_cubes = planning.get("constructor", {}).get("cubes", {})
    for cube_id, records in sorted(groups.items()):
        records = sorted(records, key=lambda r: (r["source_date"], r["id"]))
        record = records[0]
        source_ids = list(dict.fromkeys(value for r in records for value in (r["id"], r["provider_id"], r.get("source_occurrence_id")) if value))
        refs = {value for r in records for value in (r["id"], r.get("source_occurrence_id"),
            r.get("provider_id"), r.get("native_event_id")) if value}
        matching_cubes = [cube for cube in native_cubes.values() if cube.get("kind") == "calendar" and cube.get("source_ref") in refs]
        factual = [cube for cube in matching_cubes if wp._constructor_started(planning, cube)]
        chosen = factual[0] if factual else matching_cubes[0] if matching_cubes else None
        report = wp._constructor_report(planning, chosen) if chosen else {}
        original_selected = wp._constructor_selected_date(planning, chosen["id"]) if chosen else None
        original_start = planning.get("constructor", {}).get("days", {}).get(original_selected, {}).get("source_slots", {}).get(chosen["id"]) if chosen else None
        placement = ext.get("placements", {}).get(cube_id, {"date": original_selected, "start": original_start})
        estimates = ext.get("calendar_offer_estimates", {})
        minutes = estimates.get(cube_id, record["duration_minutes"])
        if minutes is None and chosen and (report.get("percent") or report.get("actual_minutes")):
            minutes = chosen["minutes"]  # durable historical planned volume, not a new estimate
        unknown = minutes is None
        cancelled = cube_id in ext.get("cancellations", {})
        row = {"id": cube_id, "task_id": None, "project_id": None, "title": ext.get("cube_titles", {}).get(cube_id, ext.get("calendar_event_titles", {}).get(record["id"], record["source_title"])),
            "minutes": minutes or 0, "kind": "calendar_optional", "calendar_role": record["calendar_role"], "source_title": record["source_title"],
            "availability": "any_day" if record["calendar_role"] == "evergreen" else "day",
            "planned_date": record["source_date"], "source_date": record["source_date"],
            "selected_date": placement.get("date"), "start": placement.get("start"), "candidate": False,
            "source_start": record["source_start"], "source_end": record["source_end"], "source_color": record["source_color"],
            "source_event_id": record["id"], "source_ids": source_ids + [cube["id"] for cube in matching_cubes],
            "source_occurrences": [{"event_id": r["id"], "provider_id": r["provider_id"], "date": r["source_date"], "identity_match": r["identity_match"]} for r in records],
            "historical_facts": [{"cube_id": cube["id"], "minutes": cube["minutes"], **deepcopy(wp._constructor_report(planning, cube))} for cube in factual],
            "duration_unknown": unknown, "requires_estimate": unknown, "all_day": record["all_day"],
            "returned": cube_id in ext.get("placements", {}) and placement.get("date") is None,
            "source_duration_minutes": record["duration_minutes"],
            "progress_percent": report.get("percent", 0), "actual_minutes": report.get("actual_minutes", 0),
            "available": not cancelled and report.get("percent", 0) < 100,
            "status": "cancelled" if cancelled else "completed" if report.get("percent", 0) >= 100 else "started" if report.get("percent") or report.get("actual_minutes") else "selected" if placement.get("date") else "available",
            "output": "", "steps": [], "end": None, "end_date": None}
        if row["start"] and not unknown:
            end = datetime.fromisoformat(row["selected_date"] + "T" + row["start"]) + timedelta(minutes=minutes)
            row.update(end=end.strftime("%H:%M"), end_date=end.date().isoformat())
        cubes.append(row)
        if len(factual) > 1:
            warnings.append("У объединённого предложения Calendar несколько исторических фактов; исходные записи сохранены, состав менять нельзя.")
    prep_index = {prep["id"]: prep for entry in planning.get("result", {}).get("days", []) for prep in entry.get("prep_cubes", [])}
    event_map = {row["id"]: row for row in projected}
    for cube in cubes:
        if cube.get("kind") != "prep":
            continue
        prep = prep_index.get(cube.get("source_ref") or cube["id"], {})
        linked = event_map.get(cube.get("calendar_event_id") or prep.get("calendar_event_id"))
        if linked and linked.get("locally_moved"):
            if not cube.get("selected_date") and not cube.get("progress_percent") and not cube.get("actual_minutes"):
                cube["planned_date"] = linked["date"]
                cube["calendar_event_id"] = linked["id"]
            elif cube.get("planned_date") != linked["date"]:
                warnings.append("Подготовка к перенесённому событию уже выбрана или начата; её дата и факты сохранены отдельно.")
    known_coverage = coverage(state)
    effective = known_coverage["effective"]
    if source.get("coverage"):
        selected = [ext.get("context", {}).get("date")]
        selected.extend(row.get("date") for row in ext.get("calendar_event_placements", {}).values())
        selected.extend(row.get("selected_date") for row in cubes if row.get("kind") == "calendar_optional")
        if effective is None or any(value and not effective["start"] <= value <= effective["end"] for value in selected):
            warnings.append("Выбранный день вне подтверждённого диапазона видимого Calendar; отсутствие событий не означает свободное время.")
    return projected, cubes, list(dict.fromkeys(warnings))


def _pieces(item, target, date_field="date"):
    if item.get("minutes") == 0 or item.get("duration_minutes") == 0 or not item.get("start") or not item.get("end"):
        return []
    first = datetime.fromisoformat(item[date_field] + "T" + item["start"])
    end_date = item.get("end_date") or item[date_field]
    last = datetime.fromisoformat(end_date + "T" + item["end"])
    if last <= first:
        last += timedelta(days=1)
    base = datetime.fromisoformat(target + "T00:00")
    a, b = max(0, int((first-base).total_seconds()/60)), min(1440, int((last-base).total_seconds()/60))
    return [(a, b)] if a < b else []


def event_intervals(event, target):
    if event.get("all_day"):
        return []
    return [piece for branch in event.get("alternatives") or [event] for piece in _pieces(branch, target)]


def validate_slot(model, target, minutes, start, *, exclude_cube=None, exclude_event=None, work_window=False, allow_zero=False):
    _iso_day(target)
    if type(minutes) is not int or minutes < 0 or minutes == 0 and not allow_zero or minutes > 1440:
        raise ValidationError("Укажите подтверждённую длительность в минутах")
    first = datetime.fromisoformat(target + "T" + start) if start is not None and _minutes(start) >= 0 else None
    last = first + timedelta(minutes=minutes) if first else None
    if work_window and first and (_minutes(start) < _minutes(model["settings"]["day_start"]) or _minutes(start)+minutes > _minutes(model["settings"]["day_end"])):
        raise ValidationError(f"Кубик выходит за рабочее окно {model['settings']['day_start']}–{model['settings']['day_end']}")
    if minutes == 0:
        return {"date": target, "start": start, "end": start, "end_date": target if start else None}
    targets = [target]
    if last and last.date().isoformat() != target and last.time() != datetime.min.time():
        targets.append(last.date().isoformat())
    for key in targets:
        events = [interval for event in model["events"] if event["id"] != exclude_event for interval in event_intervals(event, key)]
        union = []
        for a, b in sorted(events):
            if union and a <= union[-1][1]:
                union[-1] = (union[-1][0], max(b, union[-1][1]))
            else:
                union.append((a, b))
        selected = [cube for cube in model["cubes"] if cube["id"] != exclude_cube and cube.get("selected_date") and cube["status"] != "cancelled"]
        timed = [part for cube in selected if cube.get("start") for part in _pieces(cube, key, "selected_date")]
        floating_load = sum(cube["minutes"] for cube in selected if not cube.get("start") and cube["selected_date"] == key)
        proposal = {"date": target, "start": start, "end": last.strftime("%H:%M") if last else None, "end_date": last.date().isoformat() if last else None}
        pieces = _pieces(proposal, key) if first else []
        addition = sum(b-a for a, b in pieces) if first else minutes
        load = sum(b-a for a, b in union + timed) + floating_load + addition
        limit = model["settings"].get("daily_limit_minutes_by_date", {}).get(key, model["settings"]["daily_limit_minutes"])
        if load > limit:
            raise ValidationError(f"Дневной лимит {limit} минут превышен: {load} минут с событиями")
        if any(a < end and begin < b for a, b in pieces for begin, end in union + timed):
            raise ValidationError("Это время пересекается с событием или другим кубиком")
    return {"date": target, "start": start, "end": last.strftime("%H:%M") if last else None, "end_date": last.date().isoformat() if last else None}
