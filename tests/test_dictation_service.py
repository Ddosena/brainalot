import base64
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier, Event, Lock
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from megamozg import cli
from megamozg.api import create_app
from megamozg.calendar import CalendarFeed
from megamozg.core import ConflictError, Store, ValidationError
from megamozg.dictation_service import DictationService
from megamozg import dictation_service as dictation_service_module
from megamozg.google_calendar_api import GoogleCalendarApi, GoogleCalendarConflict, event_id_for


NOW = datetime(2026, 9, 24, 10, 0, tzinfo=ZoneInfo("Europe/Moscow"))
URL = "https://calendar.google.com/calendar/ical/user%40gmail.com/private-test/basic.ics"
URL_TWO = "https://calendar.google.com/calendar/ical/other%40gmail.com/private-test/basic.ics"


def authorize(root):
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "google-calendar-oauth-client.json").write_text(json.dumps({
        "client_id": "test.apps.googleusercontent.com", "client_secret": "",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}))
    (state / "google-calendar-oauth-token.json").write_text(json.dumps({
        "access_token": "access", "refresh_token": "refresh", "expires_at": 4102444800}))


@pytest.fixture
def service(tmp_path):
    store = Store(tmp_path, clock=lambda: NOW)
    feed = CalendarFeed(tmp_path)
    google = GoogleCalendarApi(tmp_path)
    return DictationService(store, feed, google)


def connect(service, monkeypatch, *, conflict=None):
    authorize(service.store.root)
    service.calendar.configure(URL, "Личный")
    calls = []

    def request(method, path, *, query=None, body=None):
        calls.append((method, path, query, body))
        if method == "POST" and conflict is not None:
            raise GoogleCalendarConflict("exists")
        if method == "GET":
            return conflict
        return {**body, "htmlLink": "https://www.google.com/calendar/event?eid=abc"}

    monkeypatch.setattr(service.google, "request", request)
    return calls


def test_timed_phrase_creates_one_google_event_and_refreshes_the_feed(service, monkeypatch):
    calls = connect(service, monkeypatch)
    calendar_id = service.calendar.status()["calendars"][0]["id"]
    service.calendar._api_cache_path(calendar_id).write_text("{}")
    result = service.apply("поставь мне на сегодня вынести мусор с 15 до 16", external_id="voice-1")
    assert result["route"] == "calendar" and result["note"] is None
    method, path, query, body = calls[-1]
    assert (method, path, query) == ("POST", "/calendar/v3/calendars/user%40gmail.com/events", {"sendUpdates": "none"})
    assert body["id"] == event_id_for("voice-1")
    assert body["summary"] == "Вынести мусор"
    assert body["start"] == {"dateTime": "2026-09-24T15:00:00+03:00", "timeZone": "Europe/Moscow"}
    assert body["end"] == {"dateTime": "2026-09-24T16:00:00+03:00", "timeZone": "Europe/Moscow"}
    assert "recurrence" not in body and "вынести мусор" in body["description"]
    assert result["event"] == {
        "calendar_id": calendar_id, "calendar_name": "Личный", "google_event_id": event_id_for("voice-1"),
        "title": "Вынести мусор", "start": "2026-09-24T15:00:00+03:00", "end": "2026-09-24T16:00:00+03:00",
        "all_day": False, "recurrence": None, "html_link": "https://www.google.com/calendar/event?eid=abc"}
    assert not service.calendar._api_cache_path(calendar_id).exists()
    assert service.store.list_notes() == []
    # A retried delivery is answered from the journal; Google is not asked again.
    assert service.apply("поставь мне на сегодня вынести мусор с 15 до 16", external_id="voice-1") == result
    assert len(calls) == 1
    with pytest.raises(ConflictError):
        service.apply("купить хлеб", external_id="voice-1")


def test_recurring_and_all_day_events_use_rrules_and_dates(service, monkeypatch):
    calls = connect(service, monkeypatch)
    service.apply("каждый понедельник в 9 планёрка", external_id="weekly")
    body = calls[-1][3]
    assert body["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=MO"]
    assert body["start"] == {"dateTime": "2026-09-28T09:00:00+03:00", "timeZone": "Europe/Moscow"}
    service.apply("каждую субботу уборка", external_id="saturdays")
    body = calls[-1][3]
    assert body["start"] == {"date": "2026-09-26"} and body["end"] == {"date": "2026-09-27"}


def test_second_calendar_can_be_chosen_explicitly(service, monkeypatch):
    calls = connect(service, monkeypatch)
    service.calendar.configure(URL_TWO, "Работа")
    work = next(item["id"] for item in service.calendar.status()["calendars"] if item["name"] == "Работа")
    result = service.apply("завтра в 9 созвон", external_id="work-1", calendar_id=work)
    assert calls[-1][1] == "/calendar/v3/calendars/other%40gmail.com/events"
    assert result["event"]["calendar_name"] == "Работа"
    with pytest.raises(ValidationError, match="Календарь не найден"):
        service.apply("завтра в 9 созвон", external_id="work-2", calendar_id="f" * 32)


def test_google_conflict_returns_the_event_created_by_the_first_attempt(service, monkeypatch):
    existing = {"id": event_id_for("retry"), "summary": "Вынести мусор", "status": "confirmed",
                "description": "Добавлено в Brainalot из диктовки:\nсегодня с 15 до 16 вынести мусор",
                "start": {"dateTime": "2026-09-24T15:00:00+03:00", "timeZone": "Europe/Moscow"},
                "end": {"dateTime": "2026-09-24T16:00:00+03:00", "timeZone": "Europe/Moscow"}}
    calls = connect(service, monkeypatch, conflict=existing)
    result = service.apply("сегодня с 15 до 16 вынести мусор", external_id="retry")
    assert result["event"]["google_event_id"] == event_id_for("retry")
    assert [call[0] for call in calls] == ["POST", "GET"]


def test_google_conflict_rejects_different_existing_event(service, monkeypatch):
    existing = {"id": event_id_for("occupied"), "summary": "Other event", "status": "confirmed"}
    connect(service, monkeypatch, conflict=existing)
    with pytest.raises(ConflictError, match="другое содержание"):
        service.apply("сегодня с 15 до 16 вынести мусор", external_id="occupied")


def test_calendar_delivery_compares_full_payload_and_keeps_first_target(service, monkeypatch):
    calls = connect(service, monkeypatch)
    service.calendar.configure(URL_TWO, "Работа")
    work = next(item["id"] for item in service.calendar.status()["calendars"] if item["name"] == "Работа")
    phrase = "завтра в 9 созвон"
    result = service.apply(phrase, external_id="same-target", calendar_id=work)
    assert result["event"]["calendar_id"] == work
    with pytest.raises(ConflictError):
        service.apply(phrase, external_id="same-target")
    with pytest.raises(ConflictError):
        service.apply(phrase, external_id="same-target", calendar_id=work,
                      overrides={"title": "Другой созвон"})
    assert len(calls) == 1


def test_pending_delivery_recovers_after_google_write_and_local_journal_failure(service, monkeypatch):
    connect(service, monkeypatch)
    saved, calls = {}, []

    def request(method, path, *, query=None, body=None):
        calls.append(method)
        if method == "POST":
            if saved:
                raise GoogleCalendarConflict("already created")
            saved.update(body)
            return dict(saved)
        return dict(saved)

    monkeypatch.setattr(service.google, "request", request)
    real_write = dictation_service_module._write_json

    def fail_completion(path, value):
        if value.get("after-crash", {}).get("status") == "applied":
            raise OSError("simulated local journal write failure")
        real_write(path, value)

    monkeypatch.setattr(dictation_service_module, "_write_json", fail_completion)
    phrase = "сегодня с 15 до 16 вынести мусор"
    with pytest.raises(OSError, match="simulated"):
        service.apply(phrase, external_id="after-crash")
    journal = json.loads(service.journal_path.read_text(encoding="utf-8"))
    assert journal["after-crash"]["status"] == "pending" and saved["id"] == event_id_for("after-crash")
    with pytest.raises(ConflictError):
        service.apply(phrase, external_id="after-crash", overrides={"title": "Other"})

    monkeypatch.setattr(dictation_service_module, "_write_json", real_write)
    restarted = DictationService(service.store, service.calendar, service.google)
    result = restarted.apply(phrase, external_id="after-crash")
    assert result["event"]["google_event_id"] == saved["id"]
    assert calls == ["POST", "POST", "GET"] and len(service.store.list_notes()) == 0
    assert restarted.apply(phrase, external_id="after-crash") == result
    assert calls == ["POST", "POST", "GET"]


def test_concurrent_changed_delivery_conflicts_before_second_google_call(service, monkeypatch):
    connect(service, monkeypatch)
    entered, release = Event(), Event()
    calls = []

    def request(method, path, *, query=None, body=None):
        calls.append(method)
        entered.set()
        assert release.wait(timeout=5)
        return dict(body)

    monkeypatch.setattr(service.google, "request", request)
    phrase = "завтра в 9 созвон"
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(service.apply, phrase, external_id="concurrent")
        assert entered.wait(timeout=5)
        with pytest.raises(ConflictError):
            service.apply(phrase, external_id="concurrent", overrides={"title": "Changed"})
        release.set()
        assert first.result(timeout=5)["route"] == "calendar"
    assert calls == ["POST"]


def test_concurrent_identical_delivery_uses_one_google_event(service, monkeypatch):
    connect(service, monkeypatch)
    arrived, lock = Barrier(2, timeout=5), Lock()
    saved = {}
    methods = []

    def request(method, path, *, query=None, body=None):
        if method == "POST":
            arrived.wait()
            with lock:
                methods.append(method)
                if saved:
                    raise GoogleCalendarConflict("already created")
                saved.update(body)
                return dict(saved)
        methods.append(method)
        return dict(saved)

    monkeypatch.setattr(service.google, "request", request)
    phrase = "завтра в 9 созвон"
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(service.apply, phrase, external_id="concurrent-same") for _ in range(2)]
        results = [future.result(timeout=7) for future in futures]
    assert results[0] == results[1]
    assert methods.count("POST") == 2 and methods.count("GET") == 1
    assert len(service._journal()) == 1 and saved["id"] == event_id_for("concurrent-same")


def test_a_deleted_event_is_not_silently_recreated(service, monkeypatch):
    connect(service, monkeypatch, conflict={"id": event_id_for("gone"), "status": "cancelled"})
    with pytest.raises(ValidationError, match="удалено"):
        service.apply("сегодня с 15 до 16 вынести мусор", external_id="gone")


def test_without_write_access_a_timed_phrase_becomes_a_dated_task(service):
    service.calendar.configure(URL, "Личный")
    result = service.apply("поставь мне на сегодня вынести мусор с 15 до 16", external_id="offline-1")
    assert result["route"] == "note" and result["event"] is None
    note = result["note"]
    assert (note["kind"], note["title"], note["due"]) == ("task", "Вынести мусор (15:00–16:00)", "2026-09-24")
    assert "Запись в Google Календарь не подключена: сохранено как дело" in result["warnings"]
    # The Store's own delivery key keeps the retry idempotent on this route too.
    again = service.apply("поставь мне на сегодня вынести мусор с 15 до 16", external_id="offline-1")
    assert again["note"]["id"] == note["id"] and len(service.store.list_notes()) == 1
    with pytest.raises(ConflictError):
        service.apply("поставь мне на сегодня вынести мусор с 15 до 16", external_id="offline-1",
                      topic="changed on retry")


def test_recurring_phrase_without_write_access_keeps_the_task_and_warns(service):
    result = service.apply("каждый понедельник в 9 планёрка", external_id="weekly-offline")
    assert result["note"]["kind"] == "task" and result["note"]["due"] is None
    assert "Повтор не сохранён: подключите запись в Google Календарь" in result["warnings"]


def test_plain_phrases_become_notes_with_parsed_fields(service):
    project = service.store.capture("Проект", kind="project", title="Мегамозг")
    purchase = service.apply("купить подарок маме к субботе очень срочно", external_id="p-1")["note"]
    assert (purchase["kind"], purchase["title"], purchase["due"], purchase["importance"]) == (
        "purchase", "Купить подарок маме", "2026-09-26", "critical")
    task = service.apply("по проекту Мегамозг на этой неделе написать тесты", external_id="t-1")["note"]
    assert (task["kind"], task["title"], task["context_id"], task["planning_horizon"]) == (
        "task", "Написать тесты", project["id"], "week")
    movie = service.apply("посмотреть фильм Дюна", external_id="m-1")["note"]
    assert (movie["kind"], movie["media_type"], movie["title"]) == ("media", "movie", "Дюна")
    assert movie["source"] == "dictation"


def test_overrides_correct_kind_title_and_route(service, monkeypatch):
    connect(service, monkeypatch)
    note = service.apply("сегодня с 15 до 16 вынести мусор", external_id="o-1",
                         overrides={"route": "note", "title": "Мусор"})["note"]
    assert (note["title"], note["due"]) == ("Мусор (15:00–16:00)", "2026-09-24")
    idea = service.apply("сделать тёмную тему", external_id="o-2", overrides={"kind": "idea"})["note"]
    assert idea["kind"] == "idea" and idea["due"] is None
    with pytest.raises(ValidationError, match="Неизвестные поля"):
        service.apply("купить хлеб", external_id="o-3", overrides={"status": "done"})
    with pytest.raises(ValidationError, match="нужно время"):
        service.apply("купить хлеб", external_id="o-4", overrides={"route": "calendar"})
    renamed = service.apply("завтра в 9 созвон", external_id="o-5", overrides={"title": "Созвон с командой"})
    assert renamed["event"]["title"] == "Созвон с командой"


def test_preview_reports_where_the_capture_would_go(service, monkeypatch):
    offline = service.preview("завтра в 9 созвон")
    assert offline["route"] == "calendar" and offline["effective_route"] == "note"
    assert offline["calendar"] == {"available": False, "calendar_id": None, "name": None}
    connect(service, monkeypatch)
    online = service.preview("завтра в 9 созвон")
    assert online["effective_route"] == "calendar" and online["calendar"]["name"] == "Личный"
    assert service.preview("купить хлеб")["effective_route"] == "note"


def test_a_chosen_date_moves_the_spoken_time_to_that_day(service, monkeypatch):
    calls = connect(service, monkeypatch)
    result = service.apply("завтра в 9 созвон", external_id="d-1", overrides={"due": "2026-09-28"})
    body = calls[-1][3]
    assert result["route"] == "calendar"
    assert body["start"] == {"dateTime": "2026-09-28T09:00:00+03:00", "timeZone": "Europe/Moscow"}
    assert body["end"] == {"dateTime": "2026-09-28T10:00:00+03:00", "timeZone": "Europe/Moscow"}
    # A date chosen for a repeat makes one event on that day at the spoken time.
    service.apply("каждый понедельник в 9 планёрка", external_id="d-2", overrides={"due": "2026-09-30"})
    body = calls[-1][3]
    assert body["start"]["dateTime"] == "2026-09-30T09:00:00+03:00" and "recurrence" not in body
    service.apply("каждую субботу уборка", external_id="d-3", overrides={"due": "2026-10-01"})
    assert calls[-1][3]["start"] == {"date": "2026-10-01"} and calls[-1][3]["end"] == {"date": "2026-10-02"}


def test_no_date_a_basket_or_another_kind_drops_the_spoken_time(service, monkeypatch):
    connect(service, monkeypatch)
    undated = service.apply("завтра в 9 созвон", external_id="n-1",
                            overrides={"due": None, "planning_horizon": None})["note"]
    assert (undated["kind"], undated["title"], undated["due"], undated["planning_horizon"]) == (
        "task", "Созвон", None, None)
    week = service.apply("завтра в 10 созвон", external_id="n-2", overrides={"planning_horizon": "week"})["note"]
    assert (week["due"], week["planning_horizon"]) == (None, "week")
    thought = service.apply("завтра в 11 созвон", external_id="n-3", overrides={"kind": "thought"})["note"]
    assert (thought["kind"], thought["due"], thought["title"]) == ("thought", None, "Созвон")
    # A date or a basket makes an untyped phrase a task.
    milk = service.apply("Молоко", external_id="n-4", overrides={"due": "2026-09-25"})["note"]
    assert (milk["kind"], milk["due"]) == ("task", "2026-09-25")
    with pytest.raises(ValidationError, match="YYYY-MM-DD"):
        service.apply("Молоко", external_id="n-5", overrides={"due": "25.09.2026"})


def test_preview_plan_is_what_apply_saves(service, monkeypatch):
    preview = service.preview("завтра в 9 созвон", overrides={"due": "2026-09-28"})
    plan = preview["plan"]
    assert preview["effective_route"] == "note" and plan["reason"] == "write_access"
    assert (plan["kind"], plan["title"], plan["due"], plan["event"]) == ("task", "Созвон (09:00–10:00)", "2026-09-28", None)
    assert plan["warnings"] == []
    saved = service.apply("завтра в 9 созвон", external_id="p-plan", overrides={"due": "2026-09-28"})
    assert (saved["note"]["title"], saved["note"]["due"]) == (plan["title"], plan["due"])
    assert saved["warnings"] == ["Запись в Google Календарь не подключена: сохранено как дело"]
    connect(service, monkeypatch)
    online = service.preview("каждый понедельник в 9 планёрка")["plan"]
    assert online["route"] == "calendar" and online["reason"] is None
    assert online["event"]["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=MO"]
    manual = service.preview("каждый понедельник в 9 планёрка", overrides={"route": "note"})
    assert manual["plan"]["reason"] == "manual" and manual["calendar"]["name"] == "Личный"
    assert manual["plan"]["warnings"] == ["Повтор не сохранён: повторяются только события Google Календаря"]
    project = service.store.capture("Проект", kind="project", title="Мегамозг")
    chosen = service.preview("написать тесты", overrides={"context_id": project["id"]})["plan"]
    assert (chosen["context_id"], chosen["context_title"], chosen["context_kind"]) == (project["id"], "Мегамозг", "project")


def test_attachments_keep_a_timed_phrase_in_an_mmm_task(service, monkeypatch):
    calls = connect(service, monkeypatch)
    picture = service.store.upload_attachment("ticket.png", base64.b64encode(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR" + b"\x00" * 12).decode("ascii"))
    preview = service.preview("в субботу в 19 концерт", with_attachments=True)
    assert preview["plan"]["reason"] == "attachments" and preview["effective_route"] == "note"
    result = service.apply("в субботу в 19 концерт", external_id="a-1", attachments=[picture])
    note = result["note"]
    assert result["route"] == "note" and not any(call[0] == "POST" for call in calls)
    assert (note["title"], note["due"]) == ("Концерт (19:00–20:00)", "2026-09-26")
    assert [item["id"] for item in note["attachments"]] == [picture["id"]]
    assert "С вложениями сохранено как дело: событие в календаре не создано" in result["warnings"]
    with pytest.raises(ValidationError, match="списком"):
        service.apply("купить хлеб", external_id="a-2", attachments="ticket.png")


def test_captured_at_keeps_the_day_the_user_meant(service):
    queued = service.apply("завтра купить хлеб", external_id="q-1", captured_at="2026-09-20T23:30:00+03:00")
    assert queued["note"]["due"] == "2026-09-21"
    # Chrome sends UTC with milliseconds; 21:30Z is already the next day in Moscow.
    late = service.apply("завтра купить сыр", external_id="q-2", captured_at="2026-09-20T21:30:00.123Z")
    assert late["note"]["due"] == "2026-09-22"
    future = service.apply("завтра купить масло", external_id="q-3", captured_at="2027-01-01T00:00:00+03:00")
    assert future["note"]["due"] == "2026-09-25"
    for bad in ("вчера", "2026-09-20T10:00:00", 5):
        with pytest.raises(ValidationError, match="Время записи"):
            service.apply("купить хлеб", external_id="q-bad", captured_at=bad)


def test_topic_and_undated_flag_go_to_the_note(service):
    thought = service.apply("подумал, что память работает иначе", external_id="t-topic",
                            topic="  Память ", show_in_unscheduled=True)["note"]
    assert (thought["kind"], thought["topic"], thought["show_in_unscheduled"]) == ("thought", "Память", True)
    task = service.apply("купить хлеб", external_id="t-flag", topic=" ", show_in_unscheduled=True)["note"]
    assert (task["topic"], task["show_in_unscheduled"]) == (None, False)
    with pytest.raises(ValidationError, match="несрочных"):
        service.apply("купить хлеб", external_id="t-bad", show_in_unscheduled="yes")


@pytest.mark.parametrize("bad", [None, "", "x" * 2001, 5])
def test_invalid_dictation_is_a_validation_error(service, bad):
    with pytest.raises(ValidationError):
        service.apply(bad, external_id="bad")


def test_external_id_and_source_are_validated(service):
    for invalid in ("", " ", "has space", False, 0, [], {}):
        with pytest.raises(ValidationError, match="ID события"):
            service.apply("купить хлеб", external_id=invalid)
    assert service.store.list_notes() == []
    generated = service.apply("купить хлеб", external_id=None)["note"]
    assert generated["id"] and len(service.store.list_notes()) == 1
    with pytest.raises(ValidationError, match="источник"):
        service.apply("купить хлеб", external_id="ok", source="bad source")


def test_http_routes_require_token_and_reject_unknown_fields(tmp_path):
    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765")
    auth = {"Authorization": "Bearer " + Store(tmp_path).token()}
    assert client.post("/api/dictation/parse", json={"text": "купить хлеб"}).status_code == 401
    parsed = client.post("/api/dictation/parse", json={"text": "завтра купить хлеб"}, headers=auth).json()
    assert parsed["kind"] == "purchase" and parsed["effective_route"] == "note"
    assert client.post("/api/dictation", json={"text": "x", "mood": 1}, headers=auth).status_code == 422
    saved = client.post("/api/dictation", json={"text": "завтра купить хлеб", "external_id": "http-1"},
                        headers=auth).json()
    assert saved["route"] == "note" and saved["note"]["kind"] == "purchase"
    assert client.post("/api/dictation", json={"text": "  "}, headers=auth).status_code == 422
    corrected = client.post("/api/dictation/parse", headers=auth, json={
        "text": "завтра в 9 созвон", "overrides": {"due": None}, "with_attachments": True}).json()
    assert corrected["plan"]["event"] is None and corrected["plan"]["title"] == "Созвон"
    assert client.post("/api/dictation/parse", headers=auth,
                       json={"text": "созвон", "with_attachments": "yes"}).status_code == 422
    queued = client.post("/api/dictation", headers=auth, json={
        "text": "завтра купить сыр", "external_id": "http-3", "captured_at": "2026-09-20T12:00:00+03:00",
        "topic": "Дом", "show_in_unscheduled": False, "attachments": []}).json()
    assert queued["note"]["due"] == "2026-09-21" and queued["note"]["topic"] == "Дом"


def test_http_route_creates_calendar_events_through_the_configured_feed(tmp_path, monkeypatch):
    authorize(tmp_path)
    calls = []
    monkeypatch.setattr(GoogleCalendarApi, "request", lambda self, method, path, *, query=None, body=None: (
        calls.append((method, path, body)), body)[1])
    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765")
    auth = {"Authorization": "Bearer " + Store(tmp_path).token()}
    client.post("/api/calendar/config", json={"ical_url": URL, "name": "Личный"}, headers=auth)
    result = client.post("/api/dictation", json={"text": "в 23:30 проверить бэкап", "external_id": "http-2"},
                         headers=auth).json()
    assert result["route"] == "calendar" and calls[-1][0] == "POST"
    assert calls[-1][2]["start"]["dateTime"].endswith("T23:30:00+03:00")


def test_cli_parse_prints_the_plan_without_saving(tmp_path, capsys):
    code = cli.main(["--root", str(tmp_path), "parse", "--text", "в пятницу в 18:00 тренировка"])
    output = json.loads(capsys.readouterr().out)
    assert code == 0 and output["route"] == "calendar" and output["title"] == "Тренировка"
    assert Store(tmp_path).list_notes() == []


def test_cli_dictate_saves_a_note(tmp_path, capsys):
    code = cli.main(["--root", str(tmp_path), "dictate", "--text", "купить молоко", "--external-id", "cli-1"])
    output = json.loads(capsys.readouterr().out)
    assert code == 0 and output["route"] == "note" and output["note"]["kind"] == "purchase"
    assert output["note"]["source"] == "cli"


def test_event_ids_are_stable_google_base32hex():
    first = event_id_for("voice-1")
    assert first == event_id_for("voice-1") != event_id_for("voice-2")
    assert len(first) == 52 and set(first) <= set("0123456789abcdefghijklmnopqrstuv")


def test_insert_event_validates_its_input(tmp_path, monkeypatch):
    google = GoogleCalendarApi(tmp_path)
    monkeypatch.setattr(google, "request", lambda *args, **kwargs: {"id": "abcde"})
    good = {"title": "Созвон", "start": "2026-09-24T15:00:00+03:00", "end": "2026-09-24T16:00:00+03:00",
            "all_day": False, "recurrence": None, "time_zone": "Europe/Moscow"}
    assert google.insert_event("user@gmail.com", good, event_id="abcde")["id"] == "abcde"
    with pytest.raises(ValidationError, match="ID события"):
        google.insert_event("user@gmail.com", good, event_id="UPPER")
    with pytest.raises(ValidationError, match="позже начала"):
        google.insert_event("user@gmail.com", {**good, "end": good["start"]}, event_id="abcde")
    with pytest.raises(ValidationError, match="часовой пояс"):
        google.insert_event("user@gmail.com", {**good, "start": "2026-09-24T15:00:00"}, event_id="abcde")
    with pytest.raises(ValidationError, match="повтора"):
        google.insert_event("user@gmail.com", {**good, "recurrence": ["EXDATE:bad value"]}, event_id="abcde")
    with pytest.raises(ValidationError, match="названия"):
        google.insert_event("user@gmail.com", {**good, "title": " "}, event_id="abcde")


def test_write_target_defaults_to_the_first_calendar(tmp_path):
    feed = CalendarFeed(tmp_path)
    assert feed.write_target() is None
    feed.configure(URL, "Личный")
    feed.configure(URL_TWO, "Работа")
    first = feed.write_target()
    assert (first["name"], first["google_calendar_id"]) == ("Личный", "user@gmail.com")
    assert "ical_url" not in first
    with pytest.raises(ValidationError):
        feed.write_target("0" * 32)
