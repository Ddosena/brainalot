"""Local OAuth 2.0 client for writing Google Calendar events.

The user supplies a Desktop OAuth client JSON. Client data and tokens stay in
``data/state`` and are never included in Brainalot backups or public packages.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .core import ConflictError, ValidationError


AUTH_SCOPE = "https://www.googleapis.com/auth/calendar.events"
# Google Desktop OAuth loopback redirects use the loopback host and ephemeral or
# fixed port as the complete redirect URI.  The local API therefore accepts the
# callback on its root path instead of relying on a registered web-style path.
REDIRECT_URI = "http://127.0.0.1:8765"
AUTH_HOST = "accounts.google.com"
TOKEN_HOST = "oauth2.googleapis.com"
API_HOST = "www.googleapis.com"


class GoogleCalendarConflict(ValidationError):
    """Google answered 409: the requested event ID already exists."""


class GoogleCalendarReauthorizationRequired(ValidationError):
    """Google rejected the saved refresh token; another consent is needed."""


def event_id_for(external_id: str) -> str:
    """A stable Google event ID (base32hex, 52 chars) for one dictated capture."""
    digest = hashlib.sha256(("mmm-dictation:" + external_id).encode("utf-8")).digest()
    return base64.b32hexencode(digest).decode("ascii").rstrip("=").lower()


def _wall_time(day: date, old: datetime, zone: ZoneInfo) -> datetime:
    """Move a local clock reading to a day without silently crossing a DST gap/fold."""
    wall = datetime.combine(day, old.astimezone(zone).time())
    first, second = wall.replace(tzinfo=zone, fold=0), wall.replace(tzinfo=zone, fold=1)
    valid_first = first.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == wall
    valid_second = second.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == wall
    if not valid_first and not valid_second:
        raise ValidationError("В выбранный день такого местного времени нет из-за перевода часов")
    if valid_first and valid_second and first.utcoffset() != second.utcoffset():
        raise ValidationError("В выбранный день местное время повторяется при переводе часов")
    return first if valid_first else second


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".google-calendar-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class GoogleCalendarApi:
    def __init__(self, root: str | Path, port: int = 8765):
        # Google's Desktop clients accept any loopback port: use the one the service really has.
        self.redirect_uri = REDIRECT_URI if port == 8765 else f"http://127.0.0.1:{port}"
        state = Path(root) / "state"
        state.mkdir(parents=True, exist_ok=True)
        self.client_path = state / "google-calendar-oauth-client.json"
        self.token_path = state / "google-calendar-oauth-token.json"
        self.pending_path = state / "google-calendar-oauth-pending.json"

    @staticmethod
    def _read(path: Path) -> dict:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def status(self) -> dict:
        client, token = self._read(self.client_path), self._read(self.token_path)
        expired = bool(token.get("reauthorization_required"))
        status = {"client_configured": bool(client.get("client_id")),
                  "authorized": bool(token.get("refresh_token") or token.get("access_token")) and not expired}
        if expired:
            status["reauthorization_required"] = True
        return status

    def configure_client(self, credentials: dict) -> dict:
        if not isinstance(credentials, dict):
            raise ValidationError("Нужен JSON OAuth-клиента Google")
        source = credentials.get("installed")
        if not isinstance(source, dict):
            raise ValidationError("Создайте в Google Cloud OAuth-клиент типа Desktop app")
        client_id = source.get("client_id")
        client_secret = source.get("client_secret", "")
        auth_uri = source.get("auth_uri", "https://accounts.google.com/o/oauth2/auth")
        token_uri = source.get("token_uri", "https://oauth2.googleapis.com/token")
        if (not isinstance(client_id, str) or not client_id.endswith(".apps.googleusercontent.com")
                or len(client_id) > 300 or not isinstance(client_secret, str)
                or len(client_secret) > 500):
            raise ValidationError("Некорректный Desktop OAuth-клиент Google")
        auth_parts, token_parts = urlsplit(auth_uri), urlsplit(token_uri)
        if auth_parts.scheme != "https" or auth_parts.hostname != AUTH_HOST:
            raise ValidationError("Некорректный адрес авторизации Google")
        if token_parts.scheme != "https" or token_parts.hostname != TOKEN_HOST:
            raise ValidationError("Некорректный адрес токена Google")
        _atomic_json(self.client_path, {"client_id": client_id, "client_secret": client_secret,
                                       "auth_uri": auth_uri, "token_uri": token_uri})
        self.token_path.unlink(missing_ok=True)
        self.pending_path.unlink(missing_ok=True)
        return self.status()

    def start(self) -> dict:
        client = self._read(self.client_path)
        if not client.get("client_id"):
            raise ValidationError("Сначала загрузите JSON OAuth-клиента Google")
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        _atomic_json(self.pending_path, {"state": state, "verifier": verifier, "created": time.time(),
                                         "redirect_uri": self.redirect_uri})
        query = urlencode({
            "client_id": client["client_id"], "redirect_uri": self.redirect_uri,
            "response_type": "code", "scope": AUTH_SCOPE, "access_type": "offline",
            "prompt": "consent", "include_granted_scopes": "true", "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256",
        })
        return {"authorization_url": client["auth_uri"] + "?" + query}

    def _form(self, url: str, values: dict) -> dict:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname != TOKEN_HOST:
            raise ValidationError("Недопустимый адрес OAuth Google")
        request = Request(url, data=urlencode(values).encode(), method="POST",
                          headers={"Content-Type": "application/x-www-form-urlencoded",
                                   "Accept": "application/json", "User-Agent": "Brainalot/0.1"})
        try:
            with urlopen(request, timeout=15) as response:
                result = json.loads(response.read(1024 * 1024).decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 400:
                try:
                    reason = json.loads(exc.read(4096)).get("error")
                except (OSError, ValueError, AttributeError):
                    reason = None
                if reason == "invalid_grant":
                    raise GoogleCalendarReauthorizationRequired(
                        "Google больше не принимает подключение. Подключите Google API заново.") from exc
            raise ValidationError("Google не подтвердил авторизацию") from exc
        except (OSError, ValueError) as exc:
            raise ValidationError("Google не подтвердил авторизацию") from exc
        if not isinstance(result, dict):
            raise ValidationError("Google вернул некорректный ответ авторизации")
        return result

    def callback(self, state: str | None, code: str | None, error: str | None = None) -> None:
        if error:
            raise ValidationError("Google отклонил авторизацию")
        pending, client = self._read(self.pending_path), self._read(self.client_path)
        try:
            pending_age = time.time() - float(pending.get("created", 0))
        except (TypeError, ValueError):
            pending_age = 601
        if (not isinstance(state, str) or not isinstance(code, str)
                or not hmac.compare_digest(state, str(pending.get("state", "")))
                or pending_age > 600):
            raise ValidationError("Ссылка авторизации устарела; начните подключение заново")
        token_request = {
            "code": code, "client_id": client["client_id"],
            "redirect_uri": pending.get("redirect_uri") or self.redirect_uri,
            "grant_type": "authorization_code", "code_verifier": pending["verifier"],
        }
        if client.get("client_secret"):
            token_request["client_secret"] = client["client_secret"]
        result = self._form(client["token_uri"], token_request)
        if not isinstance(result.get("access_token"), str):
            raise ValidationError("Google не вернул токен доступа")
        token = {"access_token": result["access_token"],
                 "refresh_token": result.get("refresh_token"),
                 "expires_at": time.time() + int(result.get("expires_in", 3600)),
                 "scope": result.get("scope", AUTH_SCOPE)}
        _atomic_json(self.token_path, token)
        self.pending_path.unlink(missing_ok=True)

    def disconnect(self) -> dict:
        self.token_path.unlink(missing_ok=True)
        self.pending_path.unlink(missing_ok=True)
        return self.status()

    def _access_token(self) -> str:
        token, client = self._read(self.token_path), self._read(self.client_path)
        if token.get("reauthorization_required"):
            raise GoogleCalendarReauthorizationRequired(
                "Google больше не принимает подключение. Подключите Google API заново.")
        access = token.get("access_token")
        if isinstance(access, str) and float(token.get("expires_at", 0)) > time.time() + 60:
            return access
        refresh = token.get("refresh_token")
        if not isinstance(refresh, str):
            raise ValidationError("Подключите управление Google Календарём")
        refresh_request = {"client_id": client["client_id"],
                           "refresh_token": refresh, "grant_type": "refresh_token"}
        if client.get("client_secret"):
            refresh_request["client_secret"] = client["client_secret"]
        try:
            result = self._form(client["token_uri"], refresh_request)
        except GoogleCalendarReauthorizationRequired:
            token["reauthorization_required"] = True
            _atomic_json(self.token_path, token)
            raise
        access = result.get("access_token")
        if not isinstance(access, str):
            raise ValidationError("Не удалось обновить доступ к Google Календарю")
        token.update({"access_token": access,
                      "expires_at": time.time() + int(result.get("expires_in", 3600))})
        _atomic_json(self.token_path, token)
        return access

    def request(self, method: str, path: str, *, query: dict | None = None,
                body: dict | None = None):
        if not path.startswith("/calendar/v3/"):
            raise ValidationError("Недопустимый путь Google Calendar API")
        url = f"https://{API_HOST}{path}"
        if query:
            url += "?" + urlencode(query)
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(url, data=data, method=method,
                          headers={"Authorization": "Bearer " + self._access_token(),
                                   "Accept": "application/json", "Content-Type": "application/json",
                                   "User-Agent": "Brainalot/0.1"})
        try:
            with urlopen(request, timeout=15) as response:
                raw = response.read(2 * 1024 * 1024)
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise ValidationError("Google не разрешил изменить это событие") from exc
            if exc.code == 404:
                raise ValidationError("Событие уже не найдено в Google Календаре") from exc
            if exc.code == 409:
                raise GoogleCalendarConflict("Событие с таким ID уже есть в Google Календаре") from exc
            if exc.code == 429:
                raise ValidationError("Google Calendar API временно ограничил запросы") from exc
            raise ValidationError("Google Calendar API временно недоступен") from exc
        except OSError as exc:
            raise ValidationError("Нет связи с Google Calendar API") from exc
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError("Google Calendar API вернул некорректный ответ") from exc

    def list_events(self, calendar_id: str, time_min: datetime, time_max: datetime) -> list[dict]:
        """Return expanded events in a bounded time range without exposing IDs.

        The caller owns conversion to the side panel's opaque event IDs.  This
        keeps pagination and OAuth access in one place while leaving the local
        calendar cache format independent of Google response details.
        """
        if not isinstance(calendar_id, str) or not calendar_id:
            raise ValidationError("Не удалось определить календарь Google")
        if time_min.tzinfo is None or time_max.tzinfo is None or time_max <= time_min:
            raise ValidationError("Некорректный период Google Календаря")
        encoded_calendar = quote(calendar_id, safe="")
        query = {
            "singleEvents": "true", "showDeleted": "false", "orderBy": "startTime",
            "timeMin": time_min.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "timeMax": time_max.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "maxResults": 2500,
        }
        events, page_token = [], None
        for _ in range(20):
            page_query = dict(query)
            if page_token:
                page_query["pageToken"] = page_token
            result = self.request("GET", f"/calendar/v3/calendars/{encoded_calendar}/events",
                                  query=page_query)
            if not isinstance(result, dict):
                raise ValidationError("Google Calendar API вернул некорректный список событий")
            items = result.get("items", [])
            if not isinstance(items, list):
                raise ValidationError("Google Calendar API вернул некорректный список событий")
            events.extend(item for item in items if isinstance(item, dict))
            page_token = result.get("nextPageToken")
            if not page_token:
                return events
            if not isinstance(page_token, str):
                raise ValidationError("Google Calendar API вернул некорректную страницу событий")
        raise ValidationError("Google Calendar API вернул слишком много страниц событий")

    @staticmethod
    def _date_time(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    def _event(self, reference: dict) -> tuple[str, dict]:
        expected = self._date_time(reference["start"])
        first = (expected.astimezone(timezone.utc) - timedelta(days=2)).isoformat().replace("+00:00", "Z")
        last = (expected.astimezone(timezone.utc) + timedelta(days=2)).isoformat().replace("+00:00", "Z")
        calendar_id = quote(reference["google_calendar_id"], safe="")
        result = self.request("GET", f"/calendar/v3/calendars/{calendar_id}/events", query={
            "iCalUID": reference["ical_uid"], "singleEvents": "true", "showDeleted": "false",
            "timeMin": first, "timeMax": last, "maxResults": 50,
        })
        items = result.get("items", []) if isinstance(result, dict) else []
        for item in items:
            start = item.get("start", {})
            if reference["all_day"]:
                matches = start.get("date") == expected.date().isoformat()
            else:
                actual = start.get("dateTime")
                matches = isinstance(actual, str) and self._date_time(actual) == expected
            if matches and isinstance(item.get("id"), str):
                return calendar_id, item
        raise ValidationError("Не удалось сопоставить выбранный экземпляр события в Google")

    def delete_event(self, reference: dict) -> None:
        calendar_id, event = self._event(reference)
        event_id = quote(event["id"], safe="")
        self.request("DELETE", f"/calendar/v3/calendars/{calendar_id}/events/{event_id}",
                     query={"sendUpdates": "none"})

    def move_event(self, reference: dict, new_date: str) -> None:
        try:
            target = date.fromisoformat(new_date)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Новая дата должна быть YYYY-MM-DD") from exc
        calendar_id, event = self._event(reference)
        start, end = event.get("start", {}), event.get("end", {})
        if reference["all_day"]:
            try:
                old_start, old_end = date.fromisoformat(start["date"]), date.fromisoformat(end["date"])
            except (KeyError, ValueError) as exc:
                raise ValidationError("Google вернул некорректную дату события") from exc
            payload = {"start": {"date": target.isoformat()},
                       "end": {"date": (target + (old_end - old_start)).isoformat()}}
        else:
            try:
                old_start, old_end = self._date_time(start["dateTime"]), self._date_time(end["dateTime"])
            except (KeyError, ValueError) as exc:
                raise ValidationError("Google вернул некорректное время события") from exc
            if old_start.tzinfo is None or old_end.tzinfo is None:
                raise ValidationError("Google вернул время события без часового пояса")
            if any(value is not None and not isinstance(value, str)
                   for value in (start.get("timeZone"), end.get("timeZone"))):
                raise ValidationError("Google вернул некорректный часовой пояс события")
            start_zone_name = start.get("timeZone") or end.get("timeZone")
            end_zone_name = end.get("timeZone") or start_zone_name
            if start_zone_name:
                try:
                    start_zone, end_zone = ZoneInfo(start_zone_name), ZoneInfo(end_zone_name)
                except (ZoneInfoNotFoundError, ValueError) as exc:
                    raise ValidationError("Часовой пояс события Google не найден на компьютере") from exc
                moved_start = _wall_time(target, old_start, start_zone)
                # Keep the original elapsed duration, including an event that
                # crossed a DST change, while preserving the start's wall clock.
                duration = old_end.astimezone(timezone.utc) - old_start.astimezone(timezone.utc)
                if duration <= timedelta(0):
                    raise ValidationError("Google вернул событие с неверной длительностью")
                moved_end = (moved_start.astimezone(timezone.utc) + duration).astimezone(end_zone)
            else:
                # Older events with only numeric offsets carry no DST rules.
                moved_start = old_start.replace(year=target.year, month=target.month, day=target.day)
                moved_end = moved_start + (old_end - old_start)
            payload = {"start": {"dateTime": moved_start.isoformat()},
                       "end": {"dateTime": moved_end.isoformat()}}
            if isinstance(start.get("timeZone"), str):
                payload["start"]["timeZone"] = start["timeZone"]
            if isinstance(end.get("timeZone"), str):
                payload["end"]["timeZone"] = end["timeZone"]
        event_id = quote(event["id"], safe="")
        self.request("PATCH", f"/calendar/v3/calendars/{calendar_id}/events/{event_id}",
                     query={"sendUpdates": "none"}, body=payload)

    def insert_event(self, google_calendar_id: str, event: dict, *, event_id: str,
                     description: str | None = None) -> dict:
        """Create one event from a parsed dictation and return Google's copy.

        ``event`` is the ``event`` object of :func:`megamozg.dictation.parse`.
        The caller-chosen ``event_id`` makes a retried delivery return the event
        created by the first attempt instead of a duplicate.
        """
        if not isinstance(google_calendar_id, str) or not google_calendar_id:
            raise ValidationError("Не удалось определить календарь Google")
        if not isinstance(event_id, str) or not re.fullmatch(r"[a-v0-9]{5,1024}", event_id):
            raise ValidationError("Некорректный ID события Google")
        title = event.get("title")
        if not isinstance(title, str) or not title.strip() or len(title) > 1024:
            raise ValidationError("У события нет названия")
        zone = event.get("time_zone")
        if event.get("all_day"):
            try:
                start, end = date.fromisoformat(event["start"]), date.fromisoformat(event["end"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValidationError("Некорректные даты события") from exc
            timing = {"start": {"date": start.isoformat()}, "end": {"date": end.isoformat()}}
        else:
            try:
                start, end = self._date_time(event["start"]), self._date_time(event["end"])
            except (KeyError, TypeError, AttributeError, ValueError) as exc:
                raise ValidationError("Некорректное время события") from exc
            if start.tzinfo is None or end.tzinfo is None:
                raise ValidationError("Время события должно содержать часовой пояс")
            timing = {"start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()}}
            if isinstance(zone, str):
                timing["start"]["timeZone"] = timing["end"]["timeZone"] = zone
        if end <= start:
            raise ValidationError("Событие должно заканчиваться позже начала")
        recurrence = event.get("recurrence")
        if recurrence is not None and (not isinstance(recurrence, list) or any(
                not isinstance(rule, str) or not re.fullmatch(r"RRULE:[A-Z0-9=;,+-]{1,200}", rule)
                for rule in recurrence)):
            raise ValidationError("Некорректное правило повтора")
        if recurrence and not event.get("all_day") and not isinstance(zone, str):
            raise ValidationError("Для повторяющегося события нужен часовой пояс")
        body = {"id": event_id, "summary": title.strip(), **timing}
        if recurrence:
            body["recurrence"] = recurrence
        if description:
            body["description"] = description[:8000]
        calendar_id = quote(google_calendar_id, safe="")
        try:
            created = self.request("POST", f"/calendar/v3/calendars/{calendar_id}/events",
                                   query={"sendUpdates": "none"}, body=body)
        except GoogleCalendarConflict:
            created = self.request("GET", f"/calendar/v3/calendars/{calendar_id}/events/{event_id}")
            if isinstance(created, dict) and created.get("status") == "cancelled":
                raise ValidationError("Это событие уже создавалось и было удалено в Google Календаре")
            if not isinstance(created, dict) or created.get("id") != event_id:
                raise ConflictError("Событие с этим ID в Google Календаре не совпало")
            if created.get("summary") != body["summary"] or created.get("description") != body.get("description"):
                raise ConflictError("Событие с этим ID в Google Календаре уже имеет другое содержание")
            if created.get("recurrence", []) != body.get("recurrence", []):
                raise ConflictError("Событие с этим ID в Google Календаре уже имеет другой повтор")
            for edge in ("start", "end"):
                expected, actual = body[edge], created.get(edge)
                if not isinstance(actual, dict):
                    raise ConflictError("Событие с этим ID в Google Календаре имеет другое время")
                if "date" in expected:
                    same_time = actual.get("date") == expected["date"]
                else:
                    try:
                        same_time = (self._date_time(actual["dateTime"]).astimezone(timezone.utc)
                                     == self._date_time(expected["dateTime"]).astimezone(timezone.utc))
                    except (KeyError, AttributeError, TypeError, ValueError):
                        same_time = False
                    if same_time and "timeZone" in actual:
                        same_time = actual["timeZone"] == expected.get("timeZone")
                if not same_time:
                    raise ConflictError("Событие с этим ID в Google Календаре имеет другое время")
        if not isinstance(created, dict) or created.get("id") != event_id:
            raise ValidationError("Google Calendar API вернул некорректное событие")
        return created
