import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from megamozg.api import create_app
from megamozg.core import ValidationError
from megamozg.core import Store
from megamozg.google_calendar_api import GoogleCalendarApi
from megamozg.google_calendar_api import REDIRECT_URI


CLIENT = {
    "installed": {
        "client_id": "test.apps.googleusercontent.com",
        "client_secret": "not-a-secret-for-desktop-apps",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}


def test_oauth_client_start_callback_and_disconnect(tmp_path, monkeypatch):
    client = GoogleCalendarApi(tmp_path)
    assert client.status() == {"client_configured": False, "authorized": False}
    assert client.configure_client(CLIENT) == {"client_configured": True, "authorized": False}
    started = client.start()
    assert started["authorization_url"].startswith("https://accounts.google.com/")
    assert "calendar.events" in started["authorization_url"]
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A8765" in started["authorization_url"]
    assert REDIRECT_URI == "http://127.0.0.1:8765"
    pending = json.loads(client.pending_path.read_text(encoding="utf-8"))
    monkeypatch.setattr(client, "_form", lambda *_: {
        "access_token": "access", "refresh_token": "refresh", "expires_in": 3600,
    })
    client.callback(pending["state"], "authorization-code")
    assert client.status()["authorized"] is True
    assert "access" not in str(client.status())
    assert client.disconnect() == {"client_configured": True, "authorized": False}


def test_oauth_rejects_web_client_and_bad_callback_state(tmp_path):
    client = GoogleCalendarApi(tmp_path)
    with pytest.raises(ValidationError, match="Desktop"):
        client.configure_client({"web": CLIENT["installed"]})
    client.configure_client(CLIENT)
    client.start()
    with pytest.raises(ValidationError, match="устарела"):
        client.callback("wrong-state", "code")


def test_move_and_delete_target_only_selected_recurring_instance(tmp_path, monkeypatch):
    client = GoogleCalendarApi(tmp_path)
    calls = []
    instances = {"items": [
        {"id": "series_20260921", "start": {"dateTime": "2026-09-21T10:00:00+03:00"},
         "end": {"dateTime": "2026-09-21T11:00:00+03:00"}},
        {"id": "series_20260922", "start": {"dateTime": "2026-09-22T10:00:00+03:00"},
         "end": {"dateTime": "2026-09-22T11:00:00+03:00"}},
    ]}

    def request(method, path, *, query=None, body=None):
        calls.append((method, path, query, body))
        return instances if method == "GET" else None

    monkeypatch.setattr(client, "request", request)
    reference = {"google_calendar_id": "user@example.com", "ical_uid": "series@example.com",
                 "start": "2026-09-22T10:00:00+03:00", "end": "2026-09-22T11:00:00+03:00",
                 "all_day": False}
    client.move_event(reference, "2026-09-25")
    assert calls[-1][0] == "PATCH" and calls[-1][1].endswith("/series_20260922")
    assert calls[-1][3]["start"]["dateTime"].startswith("2026-09-25T10:00:00")
    client.delete_event(reference)
    assert calls[-1][0] == "DELETE" and calls[-1][1].endswith("/series_20260922")


def test_move_all_day_preserves_duration(tmp_path, monkeypatch):
    client = GoogleCalendarApi(tmp_path)
    calls = []

    def request(method, path, *, query=None, body=None):
        calls.append((method, path, body))
        if method == "GET":
            return {"items": [{"id": "all-day", "start": {"date": "2026-09-24"},
                                "end": {"date": "2026-09-26"}}]}
        return None

    monkeypatch.setattr(client, "request", request)
    reference = {"google_calendar_id": "user@example.com", "ical_uid": "all-day@example.com",
                 "start": "2026-09-24T00:00:00+03:00", "end": "2026-09-26T00:00:00+03:00",
                 "all_day": True}
    client.move_event(reference, "2026-10-01")
    assert calls[-1][2] == {"start": {"date": "2026-10-01"}, "end": {"date": "2026-10-03"}}


def test_move_timed_event_recomputes_iana_offset_and_rejects_dst_gaps(tmp_path, monkeypatch):
    client = GoogleCalendarApi(tmp_path)
    original = {"id": "timed", "start": {"dateTime": "2026-07-01T10:00:00-04:00",
                                            "timeZone": "America/New_York"},
                "end": {"dateTime": "2026-07-01T11:00:00-04:00",
                        "timeZone": "America/New_York"}}
    monkeypatch.setattr(client, "_event", lambda _reference: ("calendar", original))
    calls = []
    monkeypatch.setattr(client, "request", lambda *args, **kwargs: calls.append((args, kwargs)))
    client.move_event({"all_day": False}, "2026-12-01")
    body = calls[-1][1]["body"]
    assert body["start"]["dateTime"] == "2026-12-01T10:00:00-05:00"
    assert body["end"]["dateTime"] == "2026-12-01T11:00:00-05:00"

    original["start"]["dateTime"] = "2026-03-08T01:30:00-05:00"
    original["end"]["dateTime"] = "2026-03-08T03:30:00-04:00"
    client.move_event({"all_day": False}, "2026-12-01")
    body = calls[-1][1]["body"]
    assert body["start"]["dateTime"] == "2026-12-01T01:30:00-05:00"
    assert body["end"]["dateTime"] == "2026-12-01T02:30:00-05:00"  # one elapsed hour

    original["start"]["dateTime"] = "2026-07-01T02:30:00-04:00"
    original["end"]["dateTime"] = "2026-07-01T03:30:00-04:00"
    with pytest.raises(ValidationError, match="такого местного времени нет"):
        client.move_event({"all_day": False}, "2026-03-08")
    original["start"]["dateTime"] = "2026-07-01T01:30:00-04:00"
    with pytest.raises(ValidationError, match="местное время повторяется"):
        client.move_event({"all_day": False}, "2026-11-01")
    assert len(calls) == 2  # Invalid wall times never reach Google.


def test_list_events_paginates_expanded_time_range(tmp_path, monkeypatch):
    client = GoogleCalendarApi(tmp_path)
    calls = []

    def request(method, path, *, query=None, body=None):
        calls.append((method, path, query))
        return ({"items": [{"iCalUID": "first"}], "nextPageToken": "second"}
                if query.get("pageToken") is None else {"items": [{"iCalUID": "next"}]})

    monkeypatch.setattr(client, "request", request)
    events = client.list_events("calendar@example.com",
                                datetime(2026, 9, 21, tzinfo=timezone.utc),
                                datetime(2026, 9, 28, tzinfo=timezone.utc))

    assert [event["iCalUID"] for event in events] == ["first", "next"]
    assert len(calls) == 2 and calls[0][2]["singleEvents"] == "true"
    assert calls[1][2]["pageToken"] == "second"


def test_list_events_accepts_google_empty_response_without_items(tmp_path, monkeypatch):
    client = GoogleCalendarApi(tmp_path)
    monkeypatch.setattr(client, "request", lambda *_, **__: {})

    assert client.list_events("calendar@example.com",
                              datetime(2026, 9, 21, tzinfo=timezone.utc),
                              datetime(2026, 9, 28, tzinfo=timezone.utc)) == []


def test_oauth_api_callback_is_state_guarded_but_does_not_require_mmm_token(tmp_path, monkeypatch):
    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765")
    auth = {"Authorization": "Bearer " + Store(tmp_path).token()}
    assert client.post("/api/calendar/oauth/client", json={"credentials": CLIENT}).status_code == 401
    configured = client.post("/api/calendar/oauth/client", headers=auth, json={"credentials": CLIENT})
    assert configured.status_code == 200 and configured.json()["client_configured"]
    started = client.post("/api/calendar/oauth/start", headers=auth)
    assert started.status_code == 200
    pending = json.loads((tmp_path / "state" / "google-calendar-oauth-pending.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(GoogleCalendarApi, "_form", lambda *_: {
        "access_token": "access", "refresh_token": "refresh", "expires_in": 3600,
    })
    callback = client.get("/", params={"state": pending["state"], "code": "code"})
    assert callback.status_code == 200 and "можно закрыть" in callback.text
    assert client.get("/api/calendar/oauth/status", headers=auth).json()["authorized"] is True


def test_desktop_oauth_client_secret_is_optional(tmp_path):
    credentials = json.loads(json.dumps(CLIENT))
    credentials["installed"].pop("client_secret")
    client = GoogleCalendarApi(tmp_path)
    assert client.configure_client(credentials)["client_configured"] is True
