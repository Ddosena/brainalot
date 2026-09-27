"""Small deterministic English dictation grammar; no model or network calls."""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


_DAYS = {name: index for index, name in enumerate(
    ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"))}
_BYDAY = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
_PROTECTED = re.compile(
    r"(?:https?://|www\.)[^\s<>\"“”`]+|`[^`]*`|\"[^\"]*\"|“[^”]*”|(?<!\w)'[^']+'(?!\w)", re.I)
_ISO = re.compile(r"(?<![\w/])\d{4}-\d{2}-\d{2}(?![\w/])")
_RELATIVE = re.compile(r"\b(?:today|tomorrow)\b", re.I)
_NEXT_DAY = re.compile(r"\bnext\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.I)
_IN_DAYS = re.compile(r"\bin\s+(\d{1,4})\s+days?\b", re.I)
_IN_SHORT = re.compile(r"\bin\s+(\d{1,4})\s+(minutes?|mins?|hours?|hrs?)\b", re.I)
_AT_TIME = re.compile(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.I)
_AMPM = re.compile(r"(?<![\w:])(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.I)
_REPEAT = re.compile(
    r"\b(?:every\s+day|daily|every\s+week|weekly|every\s+"
    r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b", re.I)
_TAG = re.compile(r"(?<!\w)#([^\W_][\w/-]*)")
_PREFIX = re.compile(
    r"^(?:(?:add|create|save)\s+(?:a\s+)?)?"
    r"(task|to[ -]?do|idea|note|thought|movie|film|series|book|game|podcast|article|media)\s*[:\-]\s*",
    re.I)
_REQUEST = re.compile(r"^(?:remind\s+me\s+to|i\s+need\s+to|please)\s+", re.I)
_VERBS = {"call", "email", "send", "buy", "order", "pick", "pay", "check", "prepare", "clean",
          "book", "schedule", "write", "read", "watch", "listen", "visit", "finish", "fix", "update",
          "review", "meet", "contact", "submit", "get", "take", "make", "renew", "cancel", "print"}
_EVENT_NOUNS = {"call", "meeting", "appointment", "interview", "class", "lesson", "dentist", "doctor",
                "flight", "train", "concert", "webinar", "workout"}
_MEDIA = {"movie": "movie", "film": "movie", "series": "series", "book": "book",
          "game": "game", "podcast": "podcast", "article": "article"}


def _title(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip(" ,.;:!?-–—")
    if len(text) > 200:
        text = (text[:199].rsplit(" ", 1)[0] or text[:199]) + "…"
    return text[:1].upper() + text[1:]


def _local(day: date, hour: int, minute: int, zone: ZoneInfo) -> datetime | None:
    """Reject DST gaps and folds rather than silently inventing a wall-clock time."""
    naive = datetime.combine(day, time(hour, minute))
    first = naive.replace(tzinfo=zone, fold=0)
    second = naive.replace(tzinfo=zone, fold=1)
    if first.utcoffset() != second.utcoffset():
        return None
    if first.astimezone(ZoneInfo("UTC")).astimezone(zone).replace(tzinfo=None) != naive:
        return None
    return first


def _after(moment: datetime, minutes: int, zone: ZoneInfo) -> datetime:
    return (moment.astimezone(timezone.utc) + timedelta(minutes=minutes)).astimezone(zone)


def parse_english(text: str, now: datetime, zone: ZoneInfo, contexts: list,
                  event_minutes: int) -> dict:
    now = now.astimezone(zone).replace(second=0, microsecond=0)
    today = now.date()
    warnings: list[str] = []
    spans: list[dict] = []
    occupied: list[tuple[int, int]] = []
    protected = list(_PROTECTED.finditer(text))
    masked = list(text)
    for match in protected:
        masked[match.start():match.end()] = " " * (match.end() - match.start())
    visible = "".join(masked)
    url_match = next((match for match in protected if re.match(r"(?:https?://|www\.)", match.group(), re.I)), None)
    url = url_match.group().rstrip(".,;:!?)") if url_match else None
    if url and url.startswith("www."):
        url = "https://" + url

    def claim(match: re.Match, field: str) -> bool:
        start, end = match.span()
        if any(start < right and end > left for left, right in occupied):
            return False
        occupied.append((start, end))
        spans.append({"field": field, "text": text[start:end], "start": start, "end": end})
        return True

    date_candidates: list[tuple[int, date, re.Match]] = []
    invalid_date = False
    for match in _ISO.finditer(visible):
        try:
            found = date.fromisoformat(match.group())
        except ValueError:
            warnings.append("Invalid ISO date: " + match.group())
            invalid_date = True
            continue
        date_candidates.append((match.start(), found, match))
    for match in _NEXT_DAY.finditer(visible):
        target = _DAYS[match.group().split()[-1].lower()]
        distance = (target - today.weekday()) % 7 or 7
        date_candidates.append((match.start(), today + timedelta(days=distance), match))
    for match in _RELATIVE.finditer(visible):
        date_candidates.append((match.start(), today + timedelta(days=match.group().lower() == "tomorrow"), match))
    for match in _IN_DAYS.finditer(visible):
        amount = int(match.group(1))
        if 1 <= amount <= 3660:
            date_candidates.append((match.start(), today + timedelta(days=amount), match))
    day = None
    ambiguous_time = invalid_date
    for _, found, match in sorted(date_candidates, key=lambda item: item[0]):
        if claim(match, "date"):
            if day is None:
                day = found
            elif day != found:
                warnings.append("Multiple dates; clarify the intended date")
                ambiguous_time = True

    offset = None
    for match in _IN_SHORT.finditer(visible):
        amount = int(match.group(1))
        minutes = amount * (60 if match.group(2).lower().startswith(("hour", "hr")) else 1)
        if 1 <= minutes <= 1440 and claim(match, "time"):
            if offset is None:
                offset = _after(now, minutes, zone)
            else:
                warnings.append("Multiple times; clarify the intended time")
                ambiguous_time = True

    clock = None
    for pattern in (_AT_TIME, _AMPM):
        for match in pattern.finditer(visible):
            hour, minute = int(match.group(1)), int(match.group(2) or 0)
            meridian = match.group(3)
            if meridian:
                if not 1 <= hour <= 12:
                    continue
                hour = hour % 12 + (12 if meridian.lower() == "pm" else 0)
            if hour > 23 or minute > 59 or not claim(match, "time"):
                continue
            if clock is None:
                clock = (hour, minute)
            else:
                warnings.append("Multiple times; clarify the intended time")
                ambiguous_time = True
    if offset is not None and (clock is not None or day is not None):
        warnings.append("Relative and absolute times conflict; no calendar event was created")
        ambiguous_time = True

    frequency = None
    byday = None
    for match in _REPEAT.finditer(visible):
        if not claim(match, "recurrence"):
            continue
        phrase = match.group().lower()
        found_frequency = "DAILY" if phrase in {"every day", "daily"} else "WEEKLY"
        found_day = _DAYS.get(phrase.removeprefix("every "))
        if frequency is None:
            frequency, byday = found_frequency, found_day
        elif (frequency, byday) != (found_frequency, found_day):
            warnings.append("Multiple repeat rules; clarify the intended repeat")
            ambiguous_time = True

    tags = []
    for match in _TAG.finditer(visible):
        if claim(match, "tag"):
            tags.append(match.group(1))

    remaining = list(text)
    for left, right in occupied:
        remaining[left:right] = " " * (right - left)
    stripped = re.sub(r"\s+", " ", "".join(remaining)).strip(" ,.;:!?-–—")
    if day is not None or clock is not None:
        stripped = re.sub(r"\b(?:on|at)\s*$", "", stripped, flags=re.I).strip()
    label = None
    prefix = _PREFIX.match(stripped)
    if prefix:
        label = prefix.group(1).lower().replace(" ", "").replace("-", "")
        stripped = stripped[prefix.end():].strip()
    request = _REQUEST.match(stripped)
    if request:
        label = label or "task"
        stripped = stripped[request.end():].strip()

    context = None
    for value in contexts:
        pattern = r"(?<!\w)" + re.escape(value.title) + r"(?!\w)"
        if re.search(pattern, stripped, re.I):
            if context is not None:
                context = None
                warnings.append("Multiple contexts; none was selected")
                break
            context = value

    words = re.findall(r"[A-Za-z]+", stripped.lower())
    first = words[0] if words else ""
    explicit = label is not None
    if label in _MEDIA:
        kind, media_type = "media", _MEDIA[label]
    elif label in {"note", "thought"}:
        kind, media_type = "thought", None
    elif label in {"idea", "task", "todo"}:
        kind, media_type = ("task" if label == "todo" else label), None
    elif label == "media":
        kind, media_type = "inbox", None
        warnings.append("Specify movie, book, series, game, podcast or article")
    elif first in {"buy", "order"}:
        kind, media_type = "purchase", None
    elif first in _VERBS:
        kind, media_type = "task", None
    elif (day is not None or clock is not None or frequency is not None) and any(
            word in _EVENT_NOUNS for word in words[:4]):
        kind, media_type = "task", None
    elif url is not None:
        kind, media_type = "link", None
    else:
        kind, media_type = "inbox", None

    confident = kind != "inbox" and (explicit or first in _VERBS or first in {"buy", "order"}
                                     or any(word in _EVENT_NOUNS for word in words[:4]) or url is not None)
    if kind == "inbox" and (day is not None or clock is not None or frequency is not None or offset is not None):
        warnings.append("Date or time found, but the intent is unclear")
    title = _title(stripped) or _title(text) or "Note"
    if kind == "link" and title.lower() == (url or "").lower():
        title = _title(urlsplit(url).netloc or url)

    event = None
    due = None
    if kind in {"task", "purchase"} and not ambiguous_time:
        if frequency is not None:
            rrule = "RRULE:FREQ=" + frequency + (";BYDAY=" + _BYDAY[byday] if byday is not None else "")
        else:
            rrule = None
        if offset is not None:
            start = offset
            event = {"title": title, "start": start.isoformat(),
                     "end": _after(start, event_minutes, zone).isoformat(), "all_day": False,
                     "recurrence": [rrule] if rrule else None, "time_zone": zone.key}
        elif clock is not None:
            if day is None:
                day = today
                if datetime.combine(day, time(*clock), zone) <= now:
                    day += timedelta(days=1)
            if byday is not None:
                day += timedelta(days=(byday - day.weekday()) % 7)
                if day == today and datetime.combine(day, time(*clock), zone) <= now:
                    day += timedelta(days=7)
            start = _local(day, *clock, zone)
            if start is None:
                warnings.append("Ambiguous or nonexistent local time; no calendar event was created")
            else:
                event = {"title": title, "start": start.isoformat(),
                         "end": _after(start, event_minutes, zone).isoformat(), "all_day": False,
                         "recurrence": [rrule] if rrule else None, "time_zone": zone.key}
                if day == today and start < now and frequency is None:
                    warnings.append("Time has already passed")
        elif frequency is not None:
            start_day = day or today
            if byday is not None:
                start_day += timedelta(days=(byday - start_day.weekday()) % 7)
            event = {"title": title, "start": start_day.isoformat(),
                     "end": (start_day + timedelta(days=1)).isoformat(), "all_day": True,
                     "recurrence": [rrule], "time_zone": zone.key}
        elif day is not None:
            due = day.isoformat()
        if event is not None and frequency is None:
            due = event["start"][:10]
    elif day is not None and kind not in {"inbox", "task", "purchase"}:
        warnings.append("Date is only saved for tasks and purchases")
    if ambiguous_time:
        warnings.append("Please clarify the date or time before saving")

    return {"text": text, "title": title, "kind": kind, "confident": confident,
            "due": due, "planning_horizon": None, "media_type": media_type,
            "importance": "normal", "context_id": context.id if context else None,
            "context_title": context.title if context else None,
            "tags": tags, "url": url, "route": "calendar" if event else "note", "event": event,
            "deadline": None, "spans": sorted(spans, key=lambda item: item["start"]), "warnings": warnings}
