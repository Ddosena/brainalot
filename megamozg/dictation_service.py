"""Apply a parsed dictation: a Google Calendar event or a Brainalot note.

:mod:`megamozg.dictation` decides what was said; this module decides where it
goes.  A phrase with an exact time or a repeat becomes an event in the first
configured Google Calendar when write access is connected, so it appears in
«Сегодня» through the panel's own calendar feed.  Without write access, and for
everything else, the phrase becomes a note through the same Store boundary as
manual captures.  A delivery key (``external_id``) makes both routes safe to
retry.

The capture window shows :meth:`DictationService.preview` while the user types
and saves with :meth:`DictationService.apply`; both resolve the text, the
user's corrections and the route with the same code, so the preview is the
record that will be saved.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

from filelock import FileLock

from . import dictation
from .core import ConflictError, ValidationError
from .google_calendar_api import event_id_for


OVERRIDE_FIELDS = {"kind", "title", "due", "planning_horizon", "media_type", "importance", "context_id",
                   "tags", "route", "event"}
TASK_KINDS = {"task", "purchase"}
_EXTERNAL_ID = re.compile(r"[\w:.-]{1,160}")


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".dictation-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _public_target(target: dict | None) -> dict:
    if target is None:
        return {"available": False, "calendar_id": None, "name": None}
    return {"available": True, "calendar_id": target["calendar_id"], "name": target["name"]}


def _day(value) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValidationError("Дата должна быть YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError("Такой даты нет") from exc


def event_on(event: dict, day: date) -> dict:
    """The same event on another day: one occurrence at the same local time."""
    if event.get("all_day"):
        length = date.fromisoformat(event["end"]) - date.fromisoformat(event["start"])
        return {**event, "start": day.isoformat(), "end": (day + length).isoformat(), "recurrence": None}
    zone = dictation._zone(event.get("time_zone"))
    start = datetime.fromisoformat(event["start"]).astimezone(zone)
    end = datetime.fromisoformat(event["end"]).astimezone(zone)
    moved = datetime.combine(day, start.time(), zone)
    return {**event, "start": moved.isoformat(), "end": (moved + (end - start)).isoformat(), "recurrence": None}


def merge_overrides(parsed: dict, overrides: dict | None) -> dict:
    """Apply the user's corrections from the capture window to a parse result.

    A chosen date moves a spoken time to that day as a single event; «no date»
    or a planning basket drops the time, and so does a kind other than a task
    or a purchase.  A date or a basket turns an untyped phrase into a task.
    """
    plan = dict(parsed)
    if overrides is None:
        return plan
    if not isinstance(overrides, dict) or set(overrides) - OVERRIDE_FIELDS:
        raise ValidationError("Неизвестные поля исправления диктовки")
    if "route" in overrides and overrides["route"] not in {"note", "calendar"}:
        raise ValidationError("Маршрут диктовки: note или calendar")
    changes = {key: value for key, value in overrides.items() if key != "event"}
    event = parsed.get("event")
    explicit = "event" in overrides
    if explicit:
        event = overrides["event"]
        if event is not None and (not isinstance(event, dict) or not {"start", "end"} <= set(event)
                                  or set(event) - {"title", "start", "end", "all_day", "recurrence", "time_zone"}):
            raise ValidationError("Событие: start, end, all_day, recurrence и time_zone")
        if event is not None:
            base = parsed.get("event") or {}
            event = {
                "title": event.get("title", overrides.get("title", parsed["title"])),
                "start": event["start"], "end": event["end"], "all_day": bool(event.get("all_day", False)),
                "recurrence": event.get("recurrence", base.get("recurrence")),
                "time_zone": event.get("time_zone", base.get("time_zone", dictation.DEFAULT_TIMEZONE))}
    if "due" in overrides or "planning_horizon" in overrides:
        due, horizon = overrides.get("due"), overrides.get("planning_horizon")
        day = _day(due) if due is not None else None
        changes["due"], changes["planning_horizon"] = due, horizon
        if not explicit and event is not None:
            event = event_on(event, day) if day is not None else None
        if (due is not None or horizon is not None) and "kind" not in overrides and parsed["kind"] not in TASK_KINDS:
            changes["kind"] = "task"
    if not explicit and event is not None and "kind" in overrides and overrides["kind"] not in TASK_KINDS:
        event = None
    if not explicit and event is not None and "title" in overrides:
        event = {**event, "title": overrides["title"]}
    plan.update(changes)
    plan["event"] = event
    if "route" not in overrides:
        plan["route"] = "calendar" if event else "note"
    if plan["route"] == "calendar" and not event:
        raise ValidationError("Для календаря нужно время события")
    return plan


def note_plan(plan: dict, warnings: list[str], reason: str = "write_access") -> dict:
    """Turn a calendar plan into a dated task when the event cannot be created."""
    event = plan.get("event")
    kind = plan["kind"] if plan["kind"] in TASK_KINDS else "task"
    title, due = plan["title"], plan.get("due")
    if event and event.get("recurrence"):
        if reason == "write_access":
            warnings.append("Повтор не сохранён: подключите запись в Google Календарь")
        else:
            warnings.append("Повтор не сохранён: повторяются только события Google Календаря")
        due = None
    elif event and event.get("all_day"):
        due = event["start"][:10]
    elif event:
        start, end = datetime.fromisoformat(event["start"]), datetime.fromisoformat(event["end"])
        due = start.date().isoformat()
        title = f"{title} ({start:%H:%M}–{end:%H:%M})"
    title = " ".join(title.split())
    if len(title) > dictation.MAX_TITLE:
        title = title[:dictation.MAX_TITLE - 1].rstrip() + "…"
    return {**plan, "kind": kind, "title": title, "due": due, "planning_horizon": None, "media_type": None,
            "route": "note", "event": None}


def finish_plan(plan: dict) -> dict:
    """Drop fields the Store accepts only for some kinds of record."""
    kind = plan["kind"]
    final = dict(plan)
    if kind not in TASK_KINDS:
        final["due"] = final["planning_horizon"] = None
    elif final.get("due") is not None:
        final["planning_horizon"] = None
    if kind != "media":
        final["media_type"] = None
    if final.get("importance") == "critical" and kind not in TASK_KINDS:
        final["importance"] = "high"
    return final


class DictationService:
    def __init__(self, store, calendar, google_calendar, *, time_zone: str | None = None):
        self.store = store
        self.calendar = calendar
        self.google = google_calendar
        self.time_zone = time_zone or os.environ.get("MMM_TIMEZONE") or dictation.DEFAULT_TIMEZONE
        self.journal_path = store.state / "dictation-events.json"
        self.lock = FileLock(str(store.state / "dictation.lock"), timeout=15)

    def contexts(self) -> list[dict]:
        return [{"id": note["id"], "title": note["title"], "kind": note["kind"], "status": note["status"]}
                for note in self.store.list_notes() if note["kind"] in {"project", "person"}]

    def moment(self, captured_at) -> datetime | None:
        """When a queued capture was made, so «завтра» keeps the day the user meant."""
        if captured_at is None:
            return None
        if not isinstance(captured_at, str):
            raise ValidationError("Время записи должно быть строкой ISO 8601")
        try:
            value = datetime.fromisoformat(captured_at)
        except ValueError as exc:
            raise ValidationError("Время записи должно быть в формате ISO 8601") from exc
        if value.tzinfo is None:
            raise ValidationError("Время записи должно содержать часовой пояс")
        return min(value, self.store.clock())

    def parse(self, text, *, time_zone: str | None = None, now: datetime | None = None,
              contexts: list[dict] | None = None) -> dict:
        if not isinstance(text, str):
            raise ValidationError("Текст диктовки должен быть строкой")
        if time_zone is not None and not isinstance(time_zone, str):
            raise ValidationError("Часовой пояс должен быть строкой")
        try:
            return dictation.parse(text, now=now or self.store.clock(), time_zone=time_zone or self.time_zone,
                                   contexts=self.contexts() if contexts is None else contexts)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    def target(self, calendar_id: str | None = None) -> dict | None:
        if calendar_id is not None and (not isinstance(calendar_id, str)
                                        or not re.fullmatch(r"[0-9a-f]{32}", calendar_id)):
            raise ValidationError("Некорректный ID календаря")
        if not self.google.status()["authorized"]:
            return None
        return self.calendar.write_target(calendar_id)

    def resolve(self, text, *, overrides: dict | None = None, calendar_id: str | None = None,
                time_zone: str | None = None, now: datetime | None = None, with_attachments: bool = False,
                announce: bool = True, contexts: list[dict] | None = None) -> dict:
        """Everything «Сохранить» would do, without doing it.

        ``reason`` says why a spoken time became a task: ``write_access`` (no
        Google write access), ``attachments`` (files stay in a Brainalot record) or
        ``manual`` (the user chose a task).  ``announce`` adds the route warning
        for the saved result; the preview shows the reason instead.
        """
        parsed = self.parse(text, time_zone=time_zone, now=now, contexts=contexts)
        plan = merge_overrides(parsed, overrides)
        warnings = list(parsed["warnings"])
        target, reason = None, None
        if plan["route"] == "calendar":
            target = self.target(calendar_id)
            reason = "attachments" if with_attachments else None if target is not None else "write_access"
        elif plan.get("event"):
            reason = "manual"
        if reason is not None:
            if announce and reason == "write_access":
                warnings.append("Запись в Google Календарь не подключена: сохранено как дело")
            elif announce and reason == "attachments":
                warnings.append("С вложениями сохранено как дело: событие в календаре не создано")
            plan = note_plan(plan, warnings, reason)
        return {"parsed": parsed, "plan": finish_plan(plan), "target": target, "reason": reason,
                "warnings": warnings}

    def preview(self, text, *, time_zone: str | None = None, calendar_id: str | None = None,
                overrides: dict | None = None, with_attachments: bool = False) -> dict:
        """Parse without side effects and say where «Сохранить» would put it.

        The top level is the plain parse; ``plan`` is the record after the
        user's corrections and the routing, exactly as :meth:`apply` saves it.
        """
        if not isinstance(with_attachments, bool):
            raise ValidationError("with_attachments должен быть true или false")
        contexts = self.contexts()
        resolved = self.resolve(text, overrides=overrides, calendar_id=calendar_id, time_zone=time_zone,
                                with_attachments=with_attachments, announce=False, contexts=contexts)
        parsed, plan, target = resolved["parsed"], resolved["plan"], resolved["target"]
        if target is None and parsed["route"] == "calendar" and plan["route"] == "note":
            target = self.target(calendar_id)
        context = next((item for item in contexts if item["id"] == plan.get("context_id")), None)
        return {**parsed, "calendar": _public_target(target), "effective_route": plan["route"], "plan": {
            key: plan.get(key) for key in ("route", "kind", "title", "due", "planning_horizon", "media_type",
                                           "importance", "context_id", "tags", "url", "event")
        } | {"context_title": context["title"] if context else None, "context_kind": context["kind"] if context else None,
             "reason": resolved["reason"], "warnings": resolved["warnings"]}}

    def _journal(self) -> dict:
        try:
            value = json.loads(self.journal_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            raise ConflictError("Журнал доставок диктовки недоступен или повреждён") from exc
        if not isinstance(value, dict):
            raise ConflictError("Журнал доставок диктовки повреждён")
        return value

    def apply(self, text, *, external_id: str | None = None, source: str = "dictation",
              overrides: dict | None = None, calendar_id: str | None = None,
              time_zone: str | None = None, captured_at: str | None = None, attachments: list | None = None,
              topic: str | None = None, show_in_unscheduled: bool = False) -> dict:
        """Save one dictation and report the route taken, with the parse for undo UI.

        ``captured_at`` is when the user saved it (the offline queue may deliver
        it later); files, a topic and «also in undated tasks» go to the note.
        """
        if external_id is None:
            external_id = uuid4().hex
        if not isinstance(external_id, str) or not _EXTERNAL_ID.fullmatch(external_id):
            raise ValidationError("Некорректный ID события")
        if not isinstance(source, str) or not re.fullmatch(r"[\w-]{1,40}", source):
            raise ValidationError("Некорректный источник")
        if attachments is not None and not isinstance(attachments, list):
            raise ValidationError("Вложения должны быть списком")
        if not isinstance(show_in_unscheduled, bool):
            raise ValidationError("Показ в несрочных должен быть true или false")
        if isinstance(topic, str):
            topic = topic.strip() or None
        now = self.moment(captured_at)
        request_payload = {"text": text, "source": source, "overrides": overrides or {},
                           "calendar_id": calendar_id, "time_zone": time_zone,
                           "captured_at": captured_at, "attachments": attachments or [],
                           "topic": topic, "show_in_unscheduled": show_in_unscheduled}
        try:
            # Normalize tuples and object-key order before durable comparison.
            request_payload = json.loads(json.dumps(request_payload, sort_keys=True,
                                                    ensure_ascii=False, allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise ValidationError("Некорректное содержимое диктовки") from exc
        with self.lock:
            journal = self._journal()
            delivered = journal.get(external_id)
            if delivered is not None:
                # Old entries recorded only text. Keep their retry behavior;
                # new entries compare every input that can affect the result.
                if delivered.get("payload") is not None:
                    if delivered["payload"] != request_payload:
                        raise ConflictError("Этот ID события уже использован для другого содержания")
                elif delivered.get("text") != text:
                    raise ConflictError("Этот ID события уже использован для другой диктовки")
                if "result" in delivered:
                    return delivered["result"]
                resolved = delivered.get("resolved")
                if not isinstance(resolved, dict):
                    raise ConflictError("Незавершённая диктовка повреждена; проверьте журнал")
            else:
                resolved = self.resolve(text, overrides=overrides, calendar_id=calendar_id,
                                        time_zone=time_zone, now=now, with_attachments=bool(attachments))
                journal[external_id] = {"text": text, "payload": request_payload,
                                        "created": self.store.clock().isoformat(),
                                        "resolved": resolved, "status": "pending"}
                # Keep delivery identities indefinitely. Dropping an old ID
                # could create an event in another calendar on a late retry.
                _write_json(self.journal_path, journal)
        parsed, plan, target, warnings = resolved["parsed"], resolved["plan"], resolved["target"], resolved["warnings"]
        if plan["route"] == "calendar":
            created = self.google.insert_event(
                target["google_calendar_id"], plan["event"], event_id=event_id_for(external_id),
                description="Добавлено в Brainalot из диктовки:\n" + text)
            self.calendar.invalidate(target["calendar_id"])
            event = plan["event"]
            link = created.get("htmlLink")
            result = {"route": "calendar", "note": None, "parsed": parsed, "warnings": warnings, "event": {
                "calendar_id": target["calendar_id"], "calendar_name": target["name"],
                "google_event_id": created["id"], "title": event["title"], "start": event["start"],
                "end": event["end"], "all_day": event["all_day"], "recurrence": event["recurrence"],
                "html_link": link if isinstance(link, str) and link.startswith("https://") else None}}
        else:
            note = self.store.capture(
                text, kind=plan["kind"], title=plan["title"], due=plan.get("due"),
                media_type=plan.get("media_type"), url=plan.get("url"), tags=plan.get("tags") or [],
                source=source, external_id=external_id, context_id=plan.get("context_id"),
                importance=plan.get("importance", "normal"), planning_horizon=plan.get("planning_horizon"),
                attachments=attachments or None, topic=topic,
                show_in_unscheduled=show_in_unscheduled and plan["kind"] == "thought")
            result = {"route": "note", "note": note, "event": None, "parsed": parsed, "warnings": warnings}
        with self.lock:
            journal = self._journal()
            delivered = journal[external_id]
            if "result" in delivered:
                return delivered["result"]
            delivered["result"] = result
            delivered["status"] = "applied"
            delivered.pop("resolved", None)
            _write_json(self.journal_path, journal)
        return result
