"""Read-only, local Google Calendar iCal feeds for the side panel.

Private feed URLs never leave the state directory. Each feed has its own cache,
so a broken calendar cannot hide events saved for another one.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
import tempfile
import uuid
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from filelock import FileLock
from icalendar import Calendar
import recurring_ical_events

from .core import ValidationError

# iCal feeds are a compatibility fallback. Google can rate-limit frequent
# polling, so successful reads stay moderate and failures back off further.
ICAL_REFRESH_INTERVAL = timedelta(minutes=1)
ICAL_FAILURE_BACKOFF_MAX = timedelta(hours=2)
# Authorized OAuth reads use Events.list and have a small local cache. This is
# deliberately separate from the iCal cadence and its quota backoff.
API_REFRESH_INTERVAL = timedelta(seconds=10)
API_FAILURE_BACKOFF = timedelta(minutes=2)
MAX_FEED_BYTES = 5 * 1024 * 1024


def _validate_url(url: str) -> str:
    if not isinstance(url, str) or len(url) > 4096 or any(c.isspace() for c in url):
        raise ValidationError("Нужна секретная iCal-ссылка Google Календаря")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise ValidationError("Неверная iCal-ссылка") from exc
    if (parts.scheme != "https" or parts.hostname != "calendar.google.com"
            or port not in (None, 443) or parts.username or parts.password
            or parts.fragment or not parts.path.startswith("/calendar/ical/")
            or not parts.path.endswith(".ics")):
        raise ValidationError("Укажите секретную iCal-ссылку из Google Календаря")
    return url


class _GoogleRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        _validate_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".calendar-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _as_datetime(value: date | datetime, local_zone) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=local_zone) if value.tzinfo is None else value.astimezone(local_zone)
    return datetime.combine(value, time.min, local_zone)


def _event_days(start: datetime, end: datetime, first: date, last: date) -> list[str]:
    final_day = (end - timedelta(microseconds=1)).date() if end > start else start.date()
    day, final_day = max(start.date(), first), min(final_day, last)
    days = []
    while day <= final_day:
        days.append(day.isoformat())
        day += timedelta(days=1)
    return days


class CalendarFeed:
    def __init__(self, root: str | Path):
        self.state = Path(root) / "state"
        self.state.mkdir(parents=True, exist_ok=True)
        self.config_path = self.state / "calendar.json"
        self.cache_path = self.state / "calendar-cache.ics"  # v1 migration source
        self.meta_path = self.state / "calendar-meta.json"  # v1 migration source
        self.lock = FileLock(str(self.state / "calendar.lock"))
        # Parsed occurrences of unchanged feeds: the panel asks every 10 s, and a 2 MB feed takes
        # 0.5-0.8 s to parse and expand (research, section 4.4). Keyed by the feed bytes' hash.
        self._parsed: dict[tuple, list[dict]] = {}

    def _calendar_id(self, url: str) -> str:
        # This value is returned to the client, so it must not be derived from
        # the private URL (even as a stable hash).
        return uuid.uuid4().hex

    def _cache_path(self, calendar_id: str) -> Path:
        return self.state / f"calendar-cache-{calendar_id}.ics"

    def _meta_path(self, calendar_id: str) -> Path:
        return self.state / f"calendar-meta-{calendar_id}.json"

    def _api_cache_path(self, calendar_id: str) -> Path:
        return self.state / f"calendar-api-cache-{calendar_id}.json"

    def _api_meta_path(self, calendar_id: str) -> Path:
        return self.state / f"calendar-api-meta-{calendar_id}.json"

    def _read_config(self) -> dict:
        if not self.config_path.exists():
            return {"calendars": []}
        try:
            value = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"calendars": []}
        if isinstance(value, dict) and isinstance(value.get("calendars"), list):
            return value
        url = value.get("ical_url") if isinstance(value, dict) else None
        if not isinstance(url, str):
            return {"calendars": []}
        item = {"id": self._calendar_id(url), "ical_url": url, "name": "Календарь"}
        calendar_id = item["id"]
        # Copy first: a failed config write must leave the v1 state fully
        # usable on the next start.  Only remove its files after the v2 config
        # is durably in place.
        if self.cache_path.exists():
            _atomic_write(self._cache_path(calendar_id), self.cache_path.read_bytes())
        if self.meta_path.exists():
            _atomic_write(self._meta_path(calendar_id), self.meta_path.read_bytes())
        config = {"calendars": [item]}
        _atomic_write(self.config_path, json.dumps(config, ensure_ascii=False).encode("utf-8"))
        self.cache_path.unlink(missing_ok=True)
        self.meta_path.unlink(missing_ok=True)
        return config

    def _meta(self, calendar_id: str) -> dict:
        return self._read_json(self._meta_path(calendar_id))

    @staticmethod
    def _read_json(path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _api_cache(self, calendar_id: str) -> dict:
        return self._read_json(self._api_cache_path(calendar_id))

    def _last_updated(self, calendar_id: str) -> str | None:
        values = (self._meta(calendar_id).get("last_updated"),
                  self._read_json(self._api_meta_path(calendar_id)).get("last_updated"))
        return max((value for value in values if isinstance(value, str)), default=None)

    def _public_status(self, config: dict) -> dict:
        calendars = [{"id": item["id"], "name": item["name"],
                      "last_updated": self._last_updated(item["id"])}
                     for item in config["calendars"]]
        last_updated = max((entry["last_updated"] for entry in calendars if entry["last_updated"]), default=None)
        return {"configured": bool(calendars), "calendar_count": len(calendars),
                "calendars": calendars, "last_updated": last_updated}

    def status(self) -> dict:
        with self.lock:
            return self._public_status(self._read_config())

    def configure(self, url: str | None, name: str | None = None) -> dict:
        if url is not None:
            url = _validate_url(url)
        if name is not None and (not isinstance(name, str) or len(name.strip()) > 200):
            raise ValidationError("Название календаря должно быть строкой до 200 символов")
        with self.lock:
            config = self._read_config()
            if url is None:
                # Commit the empty configuration before deleting the last good
                # copies. If unlinking the config fails, all feed state stays
                # intact and the operation can be retried safely.
                self.config_path.unlink(missing_ok=True)
                for item in config["calendars"]:
                    self._cache_path(item["id"]).unlink(missing_ok=True)
                    self._meta_path(item["id"]).unlink(missing_ok=True)
                    self._api_cache_path(item["id"]).unlink(missing_ok=True)
                    self._api_meta_path(item["id"]).unlink(missing_ok=True)
                return self._public_status({"calendars": []})
            existing = next((item for item in config["calendars"] if item["ical_url"] == url), None)
            if existing:
                if name is not None:
                    existing["name"] = name.strip() or "Календарь"
                    _atomic_write(self.config_path, json.dumps(config, ensure_ascii=False).encode("utf-8"))
                return self._public_status(config)
            config["calendars"].append({"id": self._calendar_id(url), "ical_url": url,
                                        "name": name.strip() if name and name.strip() else "Календарь"})
            _atomic_write(self.config_path, json.dumps(config, ensure_ascii=False).encode("utf-8"))
            return self._public_status(config)

    def remove(self, calendar_id: str) -> dict:
        with self.lock:
            config = self._read_config()
            selected = next((item for item in config["calendars"] if item["id"] == calendar_id), None)
            if selected is None:
                raise ValidationError("Календарь не найден")
            config["calendars"].remove(selected)
            if config["calendars"]:
                _atomic_write(self.config_path, json.dumps(config, ensure_ascii=False).encode("utf-8"))
            else:
                self.config_path.unlink(missing_ok=True)
            self._cache_path(calendar_id).unlink(missing_ok=True)
            self._meta_path(calendar_id).unlink(missing_ok=True)
            self._api_cache_path(calendar_id).unlink(missing_ok=True)
            self._api_meta_path(calendar_id).unlink(missing_ok=True)
            return self._public_status(config)

    def _download(self, url: str) -> bytes:
        opener = build_opener(_GoogleRedirects())
        request = Request(url, headers={"Accept": "text/calendar", "User-Agent": "Brainalot/0.1"})
        with opener.open(request, timeout=10) as response:
            data = response.read(MAX_FEED_BYTES + 1)
        if len(data) > MAX_FEED_BYTES:
            raise ValueError("feed too large")
        return data

    def _refresh(self, item: dict, first: date, last: date, *, force: bool = False) -> tuple[bytes | None, dict, bool]:
        calendar_id, meta = item["id"], self._meta(item["id"])
        cache_path, meta_path = self._cache_path(calendar_id), self._meta_path(calendar_id)
        cached = cache_path.read_bytes() if cache_path.exists() else None
        now, last_attempt = datetime.now().astimezone(), meta.get("last_attempt")
        if last_attempt and not force:
            try:
                failures = max(int(meta.get("failure_count", 0)), 0)
                interval = ICAL_REFRESH_INTERVAL
                if meta.get("refresh_failed"):
                    interval = min(ICAL_REFRESH_INTERVAL * (2 ** max(failures - 1, 0)),
                                   ICAL_FAILURE_BACKOFF_MAX)
                if now - datetime.fromisoformat(last_attempt) < interval:
                    return cached, meta, bool(meta.get("refresh_failed"))
            except (ValueError, TypeError, OverflowError):
                pass
        try:
            incoming = self._download(item["ical_url"])
            if Calendar.from_ical(incoming).name != "VCALENDAR":
                raise ValueError("not a calendar")
            # Parsing an envelope is insufficient: a malformed VEVENT can fail
            # expansion later and hide every event after replacing a good cache.
            # Preflight the same range before committing the new feed.
            self._parse_events_cached(incoming, item, first, last)
            _atomic_write(cache_path, incoming)
            meta, failed, cached = {"last_attempt": now.isoformat(), "last_updated": now.isoformat(),
                                    "refresh_failed": False, "failure_count": 0}, False, incoming
        except Exception:
            try:
                failures = max(int(meta.get("failure_count", 0)), 0) + 1
            except (ValueError, TypeError):
                failures = 1
            meta, failed = {"last_attempt": now.isoformat(), "last_updated": meta.get("last_updated"),
                            "refresh_failed": True, "failure_count": failures}, True
        _atomic_write(meta_path, json.dumps(meta).encode("utf-8"))
        return cached, meta, failed

    @staticmethod
    def _google_calendar_id(item: dict) -> str:
        parts = urlsplit(item["ical_url"]).path.strip("/").split("/")
        if len(parts) < 3 or parts[:2] != ["calendar", "ical"]:
            raise ValidationError("Не удалось определить календарь Google")
        return unquote(parts[2])

    @staticmethod
    def _api_datetime(value: dict, local_zone) -> datetime:
        if not isinstance(value, dict):
            raise ValueError("event time is not an object")
        day = value.get("date")
        if isinstance(day, str):
            return datetime.combine(date.fromisoformat(day), time.min, local_zone)
        raw = value.get("dateTime")
        if not isinstance(raw, str):
            raise ValueError("missing event time")
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            zone = value.get("timeZone")
            if isinstance(zone, str):
                try:
                    parsed = parsed.replace(tzinfo=ZoneInfo(zone))
                except ZoneInfoNotFoundError:
                    pass
        return _as_datetime(parsed, local_zone)

    def _parse_api_events(self, source: list[dict], item: dict, first: date,
                          last: date) -> tuple[list[dict], dict[str, dict]]:
        local_zone = datetime.now().astimezone().tzinfo
        events, references = [], {}
        for raw in source:
            try:
                start = self._api_datetime(raw.get("start"), local_zone)
                all_day = isinstance(raw.get("start", {}).get("date"), str)
                end = self._api_datetime(raw.get("end"), local_zone)
                days = _event_days(start, end, first, last)
                uid = raw.get("iCalUID")
                if not days or not isinstance(uid, str) or not uid:
                    continue
                slot = "single"
                if isinstance(raw.get("recurringEventId"), str):
                    try:
                        slot = self._api_datetime(raw.get("originalStartTime"), local_zone).isoformat()
                    except (TypeError, ValueError):
                        slot = start.isoformat()
            except (AttributeError, TypeError, ValueError):
                continue
            event_id = hashlib.sha256((item["id"] + "|" + uid + "|" + start.isoformat()).encode("utf-8")).hexdigest()[:24]
            occurrence_id = hashlib.sha256((item["id"] + "|" + uid + "|" + slot).encode("utf-8")).hexdigest()[:24]
            events.append({"id": event_id, "occurrence_id": occurrence_id,
                           "title": str(raw.get("summary", "Событие"))[:200],
                           "start": start.isoformat(), "end": end.isoformat(), "all_day": all_day,
                           "dates": days, "calendar_id": item["id"], "calendar_name": item["name"]})
            references[event_id] = {"ical_uid": uid, "start": start.isoformat(),
                                    "end": end.isoformat(), "all_day": all_day}
        return events, references

    def _refresh_api(self, item: dict, first: date, last: date, google_calendar,
                     *, force: bool = False) -> tuple[dict | None, dict, bool]:
        calendar_id = item["id"]
        cache_path, meta_path = self._api_cache_path(calendar_id), self._api_meta_path(calendar_id)
        cache, meta = self._api_cache(calendar_id), self._read_json(meta_path)
        matching_cache = (cache if cache.get("start") == first.isoformat() and cache.get("end") == last.isoformat()
                          and isinstance(cache.get("events"), list) and isinstance(cache.get("references"), dict)
                          else None)
        now, last_attempt = datetime.now().astimezone(), meta.get("last_attempt")
        if last_attempt and not force:
            try:
                elapsed = now - datetime.fromisoformat(last_attempt)
                if meta.get("refresh_failed") and elapsed < API_FAILURE_BACKOFF:
                    return matching_cache, meta, True
                if matching_cache and elapsed < API_REFRESH_INTERVAL:
                    return matching_cache, meta, bool(meta.get("refresh_failed"))
            except (ValueError, TypeError, OverflowError):
                pass
        local_zone = datetime.now().astimezone().tzinfo
        time_min = datetime.combine(first, time.min, local_zone)
        time_max = datetime.combine(last + timedelta(days=1), time.min, local_zone)
        try:
            source = google_calendar.list_events(self._google_calendar_id(item), time_min, time_max)
            events, references = self._parse_api_events(source, item, first, last)
            matching_cache = {"start": first.isoformat(), "end": last.isoformat(),
                              "events": events, "references": references}
            _atomic_write(cache_path, json.dumps(matching_cache, ensure_ascii=False).encode("utf-8"))
            meta, failed = {"last_attempt": now.isoformat(), "last_updated": now.isoformat(),
                            "refresh_failed": False}, False
        except Exception:
            meta, failed = {"last_attempt": now.isoformat(), "last_updated": meta.get("last_updated"),
                            "refresh_failed": True}, True
        _atomic_write(meta_path, json.dumps(meta).encode("utf-8"))
        return matching_cache, meta, failed

    def _parse_events(self, raw: bytes, item: dict, first: date, last: date) -> list[dict]:
        calendar, local_zone = Calendar.from_ical(raw), datetime.now().astimezone().tzinfo
        for component in calendar.walk("VEVENT"):
            # DTSTART is mandatory even if this occurrence falls outside the
            # visible range; an incomplete feed must not become last-good data.
            start = component.decoded("DTSTART")
            if not isinstance(start, (date, datetime)):
                raise ValueError("invalid event start")
            if "DTEND" in component and not isinstance(component.decoded("DTEND"), (date, datetime)):
                raise ValueError("invalid event end")
        start_bound = datetime.combine(first, time.min, local_zone)
        end_bound = datetime.combine(last + timedelta(days=1), time.min, local_zone)
        recurring_uids = {str(component.get("UID", "")) for component in calendar.walk("VEVENT")
                          if any(field in component for field in ("RRULE", "RDATE", "RECURRENCE-ID"))}
        events = []
        for component in recurring_ical_events.of(calendar).between(start_bound, end_bound):
            start_value = component.decoded("DTSTART")
            all_day = not isinstance(start_value, datetime)
            end_value = component.decoded("DTEND") if "DTEND" in component else start_value + (timedelta(days=1) if all_day else timedelta(hours=1))
            start, end = _as_datetime(start_value, local_zone), _as_datetime(end_value, local_zone)
            days = _event_days(start, end, first, last)
            if not days:
                continue
            uid = str(component.get("UID", ""))
            event_id = hashlib.sha256((item["id"] + "|" + uid + "|" + start.isoformat()).encode("utf-8")).hexdigest()[:24]
            # DTSTART changes on a move; the original recurrence slot does not.
            slot = (_as_datetime(component.decoded("RECURRENCE-ID"), local_zone).isoformat()
                    if uid in recurring_uids and "RECURRENCE-ID" in component else "single")
            occurrence_id = hashlib.sha256((item["id"] + "|" + uid + "|" + slot).encode("utf-8")).hexdigest()[:24]
            events.append({"id": event_id, "occurrence_id": occurrence_id, "title": str(component.get("SUMMARY", "Событие"))[:200],
                           "start": start.isoformat(), "end": end.isoformat(), "all_day": all_day,
                           "dates": days, "calendar_id": item["id"], "calendar_name": item["name"]})
        return events

    def _parse_events_cached(self, raw: bytes, item: dict, first: date, last: date) -> list[dict]:
        zone = datetime.now().astimezone().tzinfo
        key = (item["id"], item["name"], hashlib.sha256(raw).digest(), first, last, str(zone),
               datetime.now().astimezone().utcoffset())
        events = self._parsed.get(key)
        if events is None:
            events = self._parse_events(raw, item, first, last)
            if len(self._parsed) >= 16:
                self._parsed.pop(next(iter(self._parsed)))
            self._parsed[key] = events
        return [{**event, "dates": list(event["dates"])} for event in events]

    def event_reference(self, calendar_id: str, event_id: str, occurrence_start: str) -> dict:
        """Resolve an opaque panel event to the identifiers needed by Calendar API."""
        if (not isinstance(calendar_id, str) or not re.fullmatch(r"[0-9a-f]{32}", calendar_id)
                or not isinstance(event_id, str) or not re.fullmatch(r"[0-9a-f]{24}", event_id)):
            raise ValidationError("Некорректный ID события календаря")
        try:
            occurrence = datetime.fromisoformat(occurrence_start)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Некорректное время события календаря") from exc
        local_zone = datetime.now().astimezone().tzinfo
        occurrence = occurrence.replace(tzinfo=local_zone) if occurrence.tzinfo is None else occurrence.astimezone(local_zone)
        with self.lock:
            config = self._read_config()
            item = next((entry for entry in config["calendars"] if entry["id"] == calendar_id), None)
            cache = self._cache_path(calendar_id)
            if item is None:
                raise ValidationError("Календарь или его копия не найдены")
            references = self._api_cache(calendar_id).get("references", {})
            api_reference = references.get(event_id) if isinstance(references, dict) else None
            raw = cache.read_bytes() if cache.exists() else None
        if isinstance(api_reference, dict) and api_reference.get("start") == occurrence.isoformat():
            uid = api_reference.get("ical_uid")
            if isinstance(uid, str) and uid:
                return {"google_calendar_id": self._google_calendar_id(item), "ical_uid": uid,
                        "start": api_reference["start"], "end": api_reference.get("end"),
                        "all_day": bool(api_reference.get("all_day"))}
        if raw is None:
            raise ValidationError("Календарь или его копия не найдены")
        first, last = occurrence.date() - timedelta(days=1), occurrence.date() + timedelta(days=1)
        start_bound = datetime.combine(first, time.min, local_zone)
        end_bound = datetime.combine(last + timedelta(days=1), time.min, local_zone)
        calendar = Calendar.from_ical(raw)
        for component in recurring_ical_events.of(calendar).between(start_bound, end_bound):
            start_value = component.decoded("DTSTART")
            all_day = not isinstance(start_value, datetime)
            end_value = component.decoded("DTEND") if "DTEND" in component else start_value + (timedelta(days=1) if all_day else timedelta(hours=1))
            start, end = _as_datetime(start_value, local_zone), _as_datetime(end_value, local_zone)
            uid = str(component.get("UID", ""))
            opaque = hashlib.sha256((item["id"] + "|" + uid + "|" + start.isoformat()).encode("utf-8")).hexdigest()[:24]
            if opaque != event_id or start.isoformat() != occurrence.isoformat():
                continue
            if not uid:
                raise ValidationError("Не удалось определить событие Google Календаря")
            return {"google_calendar_id": self._google_calendar_id(item), "ical_uid": uid,
                    "start": start.isoformat(), "end": end.isoformat(), "all_day": all_day}
        raise ValidationError("Событие изменилось; обновите календарь и повторите")

    def write_target(self, calendar_id: str | None = None) -> dict | None:
        """Return the configured calendar that receives new events.

        Without an explicit ``calendar_id`` the first configured calendar is
        used, so a created event also appears in the panel's own feed.
        """
        with self.lock:
            calendars = self._read_config()["calendars"]
        if calendar_id is not None:
            item = next((entry for entry in calendars if entry["id"] == calendar_id), None)
            if item is None:
                raise ValidationError("Календарь не найден")
        else:
            item = calendars[0] if calendars else None
        if item is None:
            return None
        try:
            google_id = self._google_calendar_id(item)
        except ValidationError:
            return None
        return {"calendar_id": item["id"], "name": item["name"], "google_calendar_id": google_id}

    def invalidate(self, calendar_id: str) -> None:
        with self.lock:
            self._cache_path(calendar_id).unlink(missing_ok=True)
            self._meta_path(calendar_id).unlink(missing_ok=True)
            self._api_cache_path(calendar_id).unlink(missing_ok=True)
            self._api_meta_path(calendar_id).unlink(missing_ok=True)

    def events(self, first: date, last: date, *, force: bool = False,
               google_calendar=None) -> dict:
        if last < first or (last - first).days > 62:
            raise ValidationError("Период календаря: от 1 до 63 дней")
        with self.lock:
            config = self._read_config()
            if not config["calendars"]:
                return {"configured": False, "calendar_count": 0, "calendars": [], "events": [], "last_updated": None, "stale": False, "error": None}
            refreshed = []
            for item in config["calendars"]:
                if google_calendar is not None:
                    api_cache, _meta, api_failed = self._refresh_api(item, first, last, google_calendar,
                                                                       force=force)
                    if api_cache is not None:
                        refreshed.append(("api", item, api_cache, api_failed))
                        continue
                raw, _meta, failed = self._refresh(item, first, last, force=force)
                refreshed.append(("ical", item, raw, failed))
            status = self._public_status(config)
        events, stale = [], False
        for source, item, payload, failed in refreshed:
            stale |= failed
            if payload is None:
                continue
            try:
                if source == "api":
                    events.extend(payload["events"])
                else:
                    events.extend(self._parse_events_cached(payload, item, first, last))
            except Exception:
                stale = True
        events.sort(key=lambda event: (event["start"], event["title"], event["calendar_id"]))
        return {**status, "events": events, "stale": stale,
                "error": "Не удалось обновить часть календарей. Показаны сохранённые события." if stale else None}
