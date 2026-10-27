"""Read-only Google Calendar bridge through the owner's own Apps Script web app.

Google gives OAuth clients in Testing a refresh token for only 7 days. A script
in the owner's account has no such limit: Brainalot posts a local secret key to
the script's web app and receives events with their colours. The bridge never
writes to Google. Its URL and key stay in ``data/state``, like iCal links; the
script itself is ``extension/calendar-bridge.js``.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from filelock import FileLock

from .core import ValidationError

PROTOCOL = 1
URL = re.compile(r"https://script\.google\.com/(?:a/macros/[A-Za-z0-9.-]{1,253}|macros)/s/[A-Za-z0-9_-]{20,200}/exec")
# Apps Script answers on script.google.com and redirects to the echo host.
HOSTS = frozenset({"script.google.com", "script.googleusercontent.com"})
TIMEOUT = 20
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_EVENTS = 10000
# Only what Brainalot reads; descriptions, guests and links never reach the cache.
EVENT_FIELDS = frozenset({"id", "iCalUID", "recurringEventId", "originalStartTime", "start", "end",
                          "summary", "status", "eventType", "colorId", "eventLabelId",
                          "labelColor", "labelName"})
ERRORS = {
    "key": "Ключ моста не совпал: скопируйте скрипт из Brainalot заново и обновите развёртывание",
    "calendar": "Мост не видит этот календарь в вашем Google-аккаунте",
    "request": "Мост не понял запрос: скопируйте скрипт из Brainalot заново и обновите развёртывание",
    "method": "Мост принимает только запросы Brainalot",
    "api": "Google Calendar ответил мосту ошибкой",
}


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".calendar-bridge-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class _Redirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        parts = urlsplit(newurl)
        if parts.hostname == "accounts.google.com":
            raise ValidationError("Мост просит вход в Google: при развёртывании выберите доступ «Все»")
        if parts.scheme != "https" or parts.hostname not in HOSTS:
            raise ValidationError("Мост перенаправил запрос на чужой адрес")
        # urllib turns the POST into the GET Apps Script expects for its echo URL.
        return super().redirect_request(request, fp, code, msg, headers, newurl)


class CalendarBridge:
    def __init__(self, root: str | Path):
        state = Path(root) / "state"
        state.mkdir(parents=True, exist_ok=True)
        self.config_path = state / "calendar-bridge.json"
        self.status_path = state / "calendar-bridge-status.json"
        self.lock = FileLock(str(state / "calendar-bridge.lock"))

    @staticmethod
    def _read(path: Path) -> dict:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def status(self) -> dict:
        config, state = self._read(self.config_path), self._read(self.status_path)
        return {"configured": bool(config.get("url") and config.get("key")),
                "key_ready": bool(config.get("key")),
                "last_success": state.get("last_success"), "last_error": state.get("last_error")}

    def configured(self) -> bool:
        return self.status()["configured"]

    def prepare(self) -> dict:
        """Return the key to embed in the script, creating it on first setup."""
        with self.lock:
            config = self._read(self.config_path)
            if not config.get("key"):
                config = {"key": secrets.token_urlsafe(32), "url": None}
                _atomic_json(self.config_path, config)
        return {"key": config["key"]}

    def connect(self, url: str) -> dict:
        if not isinstance(url, str) or not URL.fullmatch(url.strip()):
            raise ValidationError("Нужен URL веб-приложения Apps Script: https://script.google.com/…/exec")
        url = url.strip()
        key = self.prepare()["key"]
        answer = self._post(url, {"action": "ping", "key": key})
        if answer.get("protocol") != PROTOCOL:
            raise ValidationError("Скрипт моста устарел: скопируйте его из Brainalot заново")
        with self.lock:
            config = self._read(self.config_path)
            if config.get("key") != key:
                raise ValidationError("Ключ моста сменился во время подключения; повторите")
            _atomic_json(self.config_path, {**config, "url": url})
            _atomic_json(self.status_path, {"last_success": datetime.now(timezone.utc).isoformat(),
                                            "last_error": None})
        return self.status()

    def disconnect(self) -> dict:
        with self.lock:
            self.config_path.unlink(missing_ok=True)
            self.status_path.unlink(missing_ok=True)
        return self.status()

    def _post(self, url: str, body: dict) -> dict:
        request = Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                          headers={"Content-Type": "application/json", "Accept": "application/json",
                                   "User-Agent": "Brainalot/0.1"})
        try:
            with build_opener(_Redirects()).open(request, timeout=TIMEOUT) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except ValidationError:
            raise
        except HTTPError as exc:
            raise ValidationError(f"Мост Google ответил HTTP {exc.code}; проверьте развёртывание") from exc
        except (OSError, ValueError) as exc:
            raise ValidationError("Нет связи с мостом Google") from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValidationError("Мост Google вернул слишком большой ответ")
        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError("Мост ответил не данными: при развёртывании выберите «Веб-приложение» и доступ «Все»") from exc
        if not isinstance(result, dict):
            raise ValidationError("Мост Google вернул некорректный ответ")
        if result.get("ok") is not True:
            message = ERRORS.get(result.get("error"), "Мост Google вернул ошибку")
            detail = result.get("message")
            if isinstance(detail, str) and detail.strip():
                message += ": " + detail.strip()[:200]
            raise ValidationError(message)
        return result

    def list_events(self, calendar_id: str, time_min: datetime, time_max: datetime) -> list[dict]:
        """Expanded events of one calendar with colours, in Calendar API shape."""
        if not isinstance(calendar_id, str) or not calendar_id:
            raise ValidationError("Не удалось определить календарь Google")
        if time_min.tzinfo is None or time_max.tzinfo is None or time_max <= time_min:
            raise ValidationError("Некорректный период Google Календаря")
        config = self._read(self.config_path)
        if not config.get("url") or not config.get("key"):
            raise ValidationError("Мост Google не подключён")
        now = datetime.now(timezone.utc).isoformat()
        try:
            result = self._post(config["url"], {
                "action": "events", "key": config["key"], "calendar": calendar_id,
                "from": time_min.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "to": time_max.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")})
            events = result.get("events")
            if not isinstance(events, list) or len(events) > MAX_EVENTS:
                raise ValidationError("Мост Google вернул некорректный список событий")
            if result.get("truncated") is True:
                raise ValidationError("В календаре слишком много событий для одного чтения моста")
        except ValidationError as exc:
            with self.lock:
                _atomic_json(self.status_path, {**self._read(self.status_path), "last_error": str(exc),
                                                "last_attempt": now})
            raise
        with self.lock:
            _atomic_json(self.status_path, {"last_success": now, "last_error": None, "last_attempt": now})
        return [{key: value for key, value in row.items() if key in EVENT_FIELDS}
                for row in events if isinstance(row, dict)]
