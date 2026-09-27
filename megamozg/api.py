"""Loopback HTTP adapter; all writes pass through Store."""
from __future__ import annotations

import hmac
import hashlib
import os
import math
import re
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response
from urllib.parse import quote

from . import API_VERSION, __version__
from .core import ConflictError, NotFoundError, Store, ValidationError
from .calendar import CalendarFeed
from .dictation_service import DictationService
from .google_calendar_api import GoogleCalendarApi
from .voice import VoiceService, VoiceUnavailableError
from . import focus_overlay


def create_app(root: str | Path, port: int = 8765) -> FastAPI:
    store = Store(root)
    calendar = CalendarFeed(root)
    google_calendar = GoogleCalendarApi(root, port=port)
    dictation = DictationService(store, calendar, google_calendar)
    voice = VoiceService(store)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = request.headers.get("host", "")
        if not re.fullmatch(r"127\.0\.0\.1:\d{1,5}", host):
            return JSONResponse({"detail": "Недопустимый Host"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and not (origin == f"http://{host}" or re.fullmatch(r"chrome-extension://[a-p]{32}", origin)):
            return JSONResponse({"detail": "Недопустимый Origin"}, status_code=403)
        if request.method == "OPTIONS" and origin:
            return JSONResponse({}, headers={"Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
                "Access-Control-Allow-Headers": "Authorization, Content-Type, If-None-Match", "Vary": "Origin"})
        if request.url.path not in {"/api/health", "/", "/welcome", "/welcome/icon.png"}:
            auth = request.headers.get("authorization", "")
            if not auth.startswith("Bearer ") or not hmac.compare_digest(auth[7:].encode("utf-8"), store.token().encode("utf-8")):
                return JSONResponse({"detail": "Нужен действительный токен"}, status_code=401)
        if request.method in {"POST", "PATCH", "PUT", "DELETE"}:
            large = {"/api/attachments", "/api/voice/transcribe"}
            limit = 14 * 1024 * 1024 if request.url.path in large and request.method == "POST" else 262144
            length = request.headers.get("content-length")
            if length and (not length.isdecimal() or int(length) > limit):
                return JSONResponse({"detail": "Слишком большой запрос"}, status_code=413)
            size = 0
            chunks = []
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    return JSONResponse({"detail": "Слишком большой запрос"}, status_code=413)
                chunks.append(chunk)
            request._body = b"".join(chunks)
        response = await call_next(request)
        if origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Expose-Headers"] = "ETag"
            response.headers["Vary"] = "Origin"
        return response

    @app.exception_handler(ValidationError)
    async def validation_handler(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(ConflictError)
    async def conflict_handler(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(NotFoundError)
    async def not_found_handler(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(VoiceUnavailableError)
    async def voice_unavailable_handler(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=503)

    async def payload(request: Request, allowed: set[str]) -> dict:
        try:
            value = await request.json()
        except Exception as exc:
            raise ValidationError("Ожидается JSON объект") from exc
        if not isinstance(value, dict) or set(value) - allowed:
            raise ValidationError("Неизвестные поля запроса")
        return value

    def conditional_json(request: Request, value: dict) -> Response:
        # Store has already checked the vault on this request, including edits made in Obsidian.
        response = JSONResponse(value)
        etag = '"' + hashlib.sha256(response.body).hexdigest() + '"'
        headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
        supplied = request.headers.get("if-none-match", "")
        if any(tag.strip().removeprefix("W/") == etag or tag.strip() == "*"
               for tag in supplied.split(",")):
            return Response(status_code=304, headers=headers)
        response.headers.update(headers)
        return response

    @app.get("/api/health")
    def health():
        return {"status": "ok", "app": "brainalot", "version": __version__, "api_version": API_VERSION}

    @app.get("/api/runtime")
    def runtime_info():
        # Authenticated: a second copy of Brainalot uses it to tell its own service from another user's.
        return {"pid": os.getpid(), "version": __version__, "api_version": API_VERSION, "port": port,
                "vault": str(store.vault)}

    @app.get("/welcome", response_class=HTMLResponse)
    def welcome(request: Request):
        from .welcome import page
        language = "ru" if request.headers.get("accept-language", "ru").lower().startswith("ru") else "en"
        return HTMLResponse(page(store.vault, port, language))

    @app.get("/welcome/icon.png")
    def welcome_icon():
        from .welcome import icon_bytes
        return Response(icon_bytes(), media_type="image/png")

    @app.get("/api/dashboard")
    def dashboard(request: Request):
        return conditional_json(request, store.dashboard())

    @app.post("/api/focus/overlay")
    async def focus_overlay_state(request: Request):
        data = await payload(request, {"state", "visible", "color"})
        state = data.get("state")
        color = data.get("color", focus_overlay.DEFAULT_NEON)
        if (not isinstance(data.get("visible"), bool) or not isinstance(state, dict) or
                not isinstance(color, str) or color not in focus_overlay.NEON_COLORS):
            raise ValidationError("Неверное состояние полосы таймера")
        required = {"durationMs", "elapsedMs", "startedAt", "status"}
        layout = {"panelWidthDip", "panelMeasuredAt"}
        if not required <= set(state) or set(state) - required - layout or ("panelWidthDip" in state) != ("panelMeasuredAt" in state):
            raise ValidationError("Неверное состояние полосы таймера")
        duration = state["durationMs"]
        elapsed = state["elapsedMs"]
        started = state["startedAt"]
        numeric = lambda value: type(value) in (int, float) and math.isfinite(value)
        if (not numeric(duration) or not numeric(elapsed) or
                not 60_000 <= duration <= 180 * 60_000 or not 0 <= elapsed <= duration or
                not isinstance(state["status"], str) or
                state["status"] not in {"idle", "running", "paused", "finished"} or
                (started is not None and not numeric(started)) or
                (state["status"] == "running" and started is None) or
                ("panelWidthDip" in state and (
                    not numeric(state["panelWidthDip"]) or
                    not 200 <= state["panelWidthDip"] <= 1200 or
                    not numeric(state["panelMeasuredAt"]) or
                    state["panelMeasuredAt"] < 0))):
            raise ValidationError("Неверное состояние полосы таймера")
        try:
            await run_in_threadpool(focus_overlay.update, state, data["visible"], color)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"ok": True, "rendererVersion": 2}

    @app.get("/api/notes")
    def notes(request: Request):
        return conditional_json(request, {"notes": store.list_notes()})

    @app.post("/api/attachments")
    async def upload_attachment(request: Request):
        data = await payload(request, {"name", "content_base64"})
        return await run_in_threadpool(store.upload_attachment, data.get("name"), data.get("content_base64"))

    @app.get("/api/attachments/{attachment_id}")
    def get_attachment(attachment_id: str):
        data, mime = store.get_attachment(attachment_id)
        return Response(data, media_type=mime, headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(attachment_id, safe=""),
        })

    @app.get("/api/calendar/config")
    def calendar_config():
        return {**calendar.status(), "write_access": google_calendar.status()}

    @app.post("/api/calendar/oauth/client")
    async def calendar_oauth_client(request: Request):
        data = await payload(request, {"credentials"})
        return await run_in_threadpool(google_calendar.configure_client, data.get("credentials"))

    @app.get("/api/calendar/oauth/status")
    def calendar_oauth_status():
        return google_calendar.status()

    @app.post("/api/calendar/oauth/start")
    def calendar_oauth_start():
        return google_calendar.start()

    @app.get("/", response_class=HTMLResponse)
    def calendar_oauth_callback(state: str | None = None, code: str | None = None,
                                error: str | None = None):
        try:
            google_calendar.callback(state, code, error)
        except ValidationError as exc:
            return HTMLResponse("<h1>Brainalot</h1><p>Подключение не завершено: "
                                + str(exc) + "</p>", status_code=400)
        return HTMLResponse("<h1>Brainalot</h1><p>Google Календарь подключён. Эту вкладку можно закрыть.</p>")

    @app.delete("/api/calendar/oauth")
    def calendar_oauth_disconnect():
        return google_calendar.disconnect()

    @app.post("/api/calendar/config")
    async def configure_calendar(request: Request):
        data = await payload(request, {"ical_url", "name"})
        if "ical_url" not in data or (data["ical_url"] is not None and not isinstance(data["ical_url"], str)):
            raise ValidationError("Требуется iCal-ссылка или null для отключения")
        if "name" in data and data["name"] is not None and not isinstance(data["name"], str):
            raise ValidationError("Название календаря должно быть строкой")
        return await run_in_threadpool(calendar.configure, data["ical_url"], data.get("name"))

    @app.delete("/api/calendar/config/{calendar_id}")
    def remove_calendar(calendar_id: str):
        return calendar.remove(calendar_id)

    @app.get("/api/calendar/events")
    def calendar_events(start: str, end: str, force: bool = False):
        try:
            first, last = date.fromisoformat(start), date.fromisoformat(end)
        except ValueError as exc:
            raise ValidationError("Даты календаря должны быть YYYY-MM-DD") from exc
        direct_api = google_calendar if google_calendar.status()["authorized"] else None
        return calendar.events(first, last, force=force, google_calendar=direct_api)

    @app.patch("/api/calendar/events/{event_id}")
    async def move_calendar_event(event_id: str, request: Request):
        data = await payload(request, {"calendar_id", "start", "new_date"})
        def move():
            reference = calendar.event_reference(data.get("calendar_id"), event_id, data.get("start"))
            google_calendar.move_event(reference, data.get("new_date"))
            calendar.invalidate(data["calendar_id"])
        await run_in_threadpool(move)
        return {"id": event_id, "moved": True, "date": data["new_date"]}

    @app.delete("/api/calendar/events/{event_id}")
    async def delete_calendar_event(event_id: str, request: Request):
        data = await payload(request, {"calendar_id", "start"})
        def remove():
            reference = calendar.event_reference(data.get("calendar_id"), event_id, data.get("start"))
            google_calendar.delete_event(reference)
            calendar.invalidate(data["calendar_id"])
        await run_in_threadpool(remove)
        return {"id": event_id, "deleted": True}

    @app.post("/api/captures")
    async def capture(request: Request):
        data = await payload(request, {"text", "kind", "title", "due", "media_type", "url", "tags", "source", "external_id", "source_capture_id", "context_id", "topic", "importance", "show_in_unscheduled", "planning_horizon", "attachments"})
        if "text" not in data:
            raise ValidationError("Требуется text")
        return await run_in_threadpool(lambda: store.capture(**data))

    @app.post("/api/dictation/parse")
    async def dictation_parse(request: Request):
        data = await payload(request, {"text", "time_zone", "calendar_id", "overrides", "with_attachments"})
        return await run_in_threadpool(lambda: dictation.preview(
            data.get("text"), time_zone=data.get("time_zone"), calendar_id=data.get("calendar_id"),
            overrides=data.get("overrides"), with_attachments=data.get("with_attachments", False)))

    @app.post("/api/dictation")
    async def dictation_apply(request: Request):
        data = await payload(request, {"text", "external_id", "source", "overrides", "calendar_id", "time_zone",
                                       "captured_at", "attachments", "topic", "show_in_unscheduled"})
        # Google Calendar is called here: a slow network must not stall every other request.
        return await run_in_threadpool(lambda: dictation.apply(
            data.get("text"), external_id=data.get("external_id"), source=data.get("source", "dictation"),
            overrides=data.get("overrides"), calendar_id=data.get("calendar_id"), time_zone=data.get("time_zone"),
            captured_at=data.get("captured_at"), attachments=data.get("attachments"), topic=data.get("topic"),
            show_in_unscheduled=data.get("show_in_unscheduled", False)))

    @app.get("/api/voice/status")
    def voice_status():
        return voice.status()

    @app.post("/api/voice/prepare")
    def voice_prepare():
        return voice.prepare()

    @app.post("/api/voice/transcribe")
    async def voice_transcribe(request: Request, language: str = "ru", preview: bool = False):
        # Raw audio body (audio/webm from MediaRecorder); the guard above caps its size.
        result = await run_in_threadpool(voice.transcribe, await request.body(),
                                         request.headers.get("content-type", ""), language=language)
        if preview:
            result["preview"] = await run_in_threadpool(dictation.preview, result["text"]) if result["text"] else None
        return result

    @app.patch("/api/notes/{note_id}")
    async def update(note_id: str, request: Request):
        data = await payload(request, {"expected_version", "patch"})
        if not isinstance(data.get("patch"), dict) or not isinstance(data.get("expected_version"), str):
            raise ValidationError("Требуются expected_version и patch")
        return await run_in_threadpool(store.update, note_id, data["patch"], data["expected_version"])

    @app.post("/api/tasks/reorder")
    async def reorder(request: Request):
        data = await payload(request, {"due", "ordered_ids", "expected_versions"})
        if "due" not in data or "ordered_ids" not in data or "expected_versions" not in data:
            raise ValidationError("Требуются due, ordered_ids и expected_versions")
        return await run_in_threadpool(store.reorder, data["due"], data["ordered_ids"], data["expected_versions"])

    @app.delete("/api/notes/{note_id}")
    async def delete(note_id: str, request: Request):
        data = await payload(request, {"expected_version"})
        if not isinstance(data.get("expected_version"), str):
            raise ValidationError("Требуется expected_version")
        return await run_in_threadpool(store.delete, note_id, data["expected_version"])

    @app.post("/api/notes/{note_id}/feedback")
    async def feedback(note_id: str, request: Request):
        data = await payload(request, {"expected_version", "action"})
        if not isinstance(data.get("expected_version"), str) or not isinstance(data.get("action"), str):
            raise ValidationError("Требуются expected_version и action")
        return await run_in_threadpool(store.feedback, note_id, data["action"], data["expected_version"])

    return app


def serve(root: str | Path, port: int | None = None) -> None:
    from .runtime import serve_forever
    serve_forever(Path(root), port)
