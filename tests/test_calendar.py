from datetime import date, datetime, timedelta

from fastapi.testclient import TestClient
import pytest

from megamozg.api import create_app
from megamozg.calendar import CalendarFeed, ICAL_REFRESH_INTERVAL
from megamozg.core import Store
from megamozg.google_calendar_api import GoogleCalendarApi


FEED = b"""BEGIN:VCALENDAR\r
VERSION:2.0\r
PRODID:-//MegaMozg test//EN\r
BEGIN:VEVENT\r
UID:lesson-1\r
DTSTAMP:20260919T100000Z\r
DTSTART;TZID=Europe/Moscow:20260921T100000\r
DTEND;TZID=Europe/Moscow:20260921T110000\r
RRULE:FREQ=DAILY;COUNT=3\r
EXDATE;TZID=Europe/Moscow:20260922T100000\r
SUMMARY:Student lesson\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:lesson-1\r
DTSTAMP:20260919T100000Z\r
RECURRENCE-ID;TZID=Europe/Moscow:20260923T100000\r
DTSTART;TZID=Europe/Moscow:20260923T140000\r
DTEND;TZID=Europe/Moscow:20260923T150000\r
SUMMARY:Moved lesson\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:allday-1\r
DTSTAMP:20260919T100000Z\r
DTSTART;VALUE=DATE:20260924\r
DTEND;VALUE=DATE:20260926\r
SUMMARY:Conference\r
END:VEVENT\r
END:VCALENDAR\r
"""
URL = "https://calendar.google.com/calendar/ical/user%40gmail.com/private-test/basic.ics"
URL_TWO = "https://calendar.google.com/calendar/ical/other%40gmail.com/private-test/basic.ics"


def test_occurrence_identity_survives_moves_without_merging_series_instances(tmp_path):
    feed = CalendarFeed(tmp_path)
    item = {"id": "test-calendar", "name": "Test"}
    first, last = date(2026, 9, 21), date(2026, 9, 30)
    before = feed._parse_events(FEED, item, first, last)
    moved_feed = (FEED.replace(b"20260923T140000", b"20260924T140000")
                 .replace(b"20260923T150000", b"20260924T150000")
                 .replace(b"DTSTART;VALUE=DATE:20260924", b"DTSTART;VALUE=DATE:20260926")
                 .replace(b"DTEND;VALUE=DATE:20260926", b"DTEND;VALUE=DATE:20260928"))
    after = feed._parse_events(moved_feed, item, first, last)
    assert len({event["occurrence_id"] for event in before}) == len(before)
    for title in ("Moved lesson", "Conference"):
        old = next(event for event in before if event["title"] == title)
        new = next(event for event in after if event["title"] == title)
        assert old["id"] != new["id"]
        assert old["occurrence_id"] == new["occurrence_id"]
    other_calendar = feed._parse_events(FEED, {**item, "id": "another-calendar"}, first, last)
    assert not {event["occurrence_id"] for event in before}.intersection(event["occurrence_id"] for event in other_calendar)


def test_recurring_occurrence_id_matches_api_when_original_start_uses_utc(tmp_path):
    feed = CalendarFeed(tmp_path)
    item = {"id": "test-calendar", "name": "Test"}
    first, last = date(2026, 9, 21), date(2026, 9, 30)
    ical_events = feed._parse_events(FEED, item, first, last)
    api_events, _ = feed._parse_api_events([
        {"iCalUID": "lesson-1", "recurringEventId": "series",
         "originalStartTime": {"dateTime": "2026-09-21T07:00:00Z"},
         "summary": "Student lesson", "start": {"dateTime": "2026-09-21T10:00:00+03:00"},
         "end": {"dateTime": "2026-09-21T11:00:00+03:00"}},
        {"iCalUID": "lesson-1", "recurringEventId": "series",
         "originalStartTime": {"dateTime": "2026-09-23T07:00:00Z"},
         "summary": "Moved lesson", "start": {"dateTime": "2026-09-23T14:00:00+03:00"},
         "end": {"dateTime": "2026-09-23T15:00:00+03:00"}},
    ], item, first, last)

    for title in ("Student lesson", "Moved lesson"):
        ical = next(event for event in ical_events if event["title"] == title)
        api = next(event for event in api_events if event["title"] == title)
        assert ical["occurrence_id"] == api["occurrence_id"]


def test_calendar_recurring_exclusion_override_and_multiday(tmp_path, monkeypatch):
    feed = CalendarFeed(tmp_path)
    feed.configure(URL)
    calls = []
    monkeypatch.setattr(feed, "_download", lambda url: (calls.append(url), FEED)[1])
    result = feed.events(date(2026, 9, 21), date(2026, 9, 27))
    assert result["configured"] and not result["stale"] and result["error"] is None
    assert calls == [URL]
    assert [(item["title"], item["dates"]) for item in result["events"]] == [
        ("Student lesson", ["2026-09-21"]),
        ("Moved lesson", ["2026-09-23"]),
        ("Conference", ["2026-09-24", "2026-09-25"]),
    ]
    assert feed.events(date(2026, 9, 21), date(2026, 9, 27))["events"] == result["events"]
    assert calls == [URL]
    # A multi-day event must remain visible after its starting day has passed.
    overlap = feed.events(date(2026, 9, 25), date(2026, 9, 27))
    assert [(item["title"], item["dates"]) for item in overlap["events"]] == [
        ("Conference", ["2026-09-25"]),
    ]
    selected = next(item for item in result["events"] if item["title"] == "Moved lesson")
    reference = feed.event_reference(selected["calendar_id"], selected["id"], selected["start"])
    assert reference["google_calendar_id"] == "user@gmail.com"
    assert reference["ical_uid"] == "lesson-1" and reference["all_day"] is False


def test_calendar_keeps_last_good_feed_on_refresh_error(tmp_path, monkeypatch):
    feed = CalendarFeed(tmp_path)
    feed.configure(URL)
    monkeypatch.setattr(feed, "_download", lambda _: FEED)
    first = feed.events(date(2026, 9, 21), date(2026, 9, 27))
    calendar_id = feed.status()["calendars"][0]["id"]
    feed._meta_path(calendar_id).write_text('{"last_attempt":"2020-01-01T00:00:00+00:00","last_updated":"2026-09-19T00:00:00+03:00"}', encoding="utf-8")
    monkeypatch.setattr(feed, "_download", lambda _: (_ for _ in ()).throw(OSError(URL)))
    second = feed.events(date(2026, 9, 21), date(2026, 9, 27))
    assert second["stale"] and second["events"] == first["events"]
    assert URL not in str(second)


def test_malformed_event_cannot_replace_last_good_feed(tmp_path, monkeypatch):
    feed = CalendarFeed(tmp_path)
    feed.configure(URL)
    monkeypatch.setattr(feed, "_download", lambda _: FEED)
    first = feed.events(date(2026, 9, 21), date(2026, 9, 27), force=True)
    calendar_id = feed.status()["calendars"][0]["id"]
    saved = feed._cache_path(calendar_id).read_bytes()
    malformed = (b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
                 b"UID:incomplete\r\nSUMMARY:Missing start\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    monkeypatch.setattr(feed, "_download", lambda _: malformed)
    after = feed.events(date(2026, 9, 21), date(2026, 9, 27), force=True)
    assert after["stale"] and after["events"] == first["events"]
    assert feed._cache_path(calendar_id).read_bytes() == saved


def test_manual_calendar_refresh_bypasses_cache_interval_through_api(tmp_path, monkeypatch):
    CalendarFeed(tmp_path).configure(URL)
    versions = [FEED, FEED.replace(b"SUMMARY:Moved lesson", b"SUMMARY:New lesson")]
    calls = []

    def download(self, url):
        calls.append(url)
        return versions.pop(0)

    monkeypatch.setattr(CalendarFeed, "_download", download)
    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765")
    auth = {"Authorization": "Bearer " + Store(tmp_path).token()}
    path = "/api/calendar/events?start=2026-09-21&end=2026-09-27"

    first = client.get(path, headers=auth).json()
    cached = client.get(path, headers=auth).json()
    forced = client.get(path + "&force=1", headers=auth).json()

    assert calls == [URL, URL]
    assert first["events"] == cached["events"]
    assert any(event["title"] == "New lesson" for event in forced["events"])
    assert not forced["stale"] and forced["error"] is None


def test_delete_route_passes_only_the_selected_recurring_instance_to_google(tmp_path, monkeypatch):
    feed = CalendarFeed(tmp_path)
    feed.configure(URL)
    monkeypatch.setattr(CalendarFeed, "_download", lambda self, url: FEED)
    events = feed.events(date(2026, 9, 21), date(2026, 9, 27))["events"]
    selected = next(event for event in events if event["title"] == "Moved lesson")
    calls = []
    monkeypatch.setattr(GoogleCalendarApi, "delete_event", lambda self, ref: calls.append(("delete", ref)))
    monkeypatch.setattr(CalendarFeed, "invalidate", lambda self, calendar_id: calls.append(("invalidate", calendar_id)))
    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765")
    auth = {"Authorization": "Bearer " + Store(tmp_path).token()}
    path = "/api/calendar/events/" + selected["id"]
    body = {"calendar_id": selected["calendar_id"], "start": selected["start"]}

    deleted = client.request("DELETE", path, headers=auth, json=body)
    assert deleted.status_code == 200 and deleted.json() == {"id": selected["id"], "deleted": True}
    assert [name for name, _ in calls] == ["delete", "invalidate"]
    assert calls[0][1]["ical_uid"] == "lesson-1"
    assert calls[0][1]["start"] == selected["start"]
    assert calls[1][1] == selected["calendar_id"]
    wrong_day = client.request("DELETE", path, headers=auth, json={**body, "start": "2026-09-22T10:00:00+03:00"})
    assert wrong_day.status_code == 422 and len(calls) == 2


def test_calendar_refreshes_after_moderate_ical_interval(tmp_path, monkeypatch):
    feed = CalendarFeed(tmp_path)
    feed.configure(URL)
    updated = FEED.replace(b"SUMMARY:Moved lesson", b"SUMMARY:New lesson")
    downloads = [FEED, updated]
    calls = []

    def download(url):
        calls.append(url)
        return downloads.pop(0)

    monkeypatch.setattr(feed, "_download", download)
    first = feed.events(date(2026, 9, 21), date(2026, 9, 27))
    calendar_id = feed.status()["calendars"][0]["id"]
    previous = datetime.now().astimezone() - ICAL_REFRESH_INTERVAL - timedelta(seconds=1)
    feed._meta_path(calendar_id).write_text(
        '{"last_attempt": "' + previous.isoformat() + '", "last_updated": "'
        + previous.isoformat() + '"}', encoding="utf-8")

    refreshed = feed.events(date(2026, 9, 21), date(2026, 9, 27))

    assert calls == [URL, URL]
    assert first["events"] != refreshed["events"]
    assert any(event["title"] == "New lesson" for event in refreshed["events"])


def test_ical_failures_back_off_and_keep_the_cached_events(tmp_path, monkeypatch):
    feed = CalendarFeed(tmp_path)
    feed.configure(URL)
    calendar_id = feed.status()["calendars"][0]["id"]
    feed._cache_path(calendar_id).write_bytes(FEED)
    now = datetime.now().astimezone()
    feed._meta_path(calendar_id).write_text(
        '{"last_attempt": "' + now.isoformat() + '", "last_updated": "'
        + now.isoformat() + '", "refresh_failed": true, "failure_count": 2}', encoding="utf-8")
    calls = []
    monkeypatch.setattr(feed, "_download", lambda url: calls.append(url))

    result = feed.events(date(2026, 9, 21), date(2026, 9, 27))

    assert calls == []
    assert result["stale"] and result["events"]


def test_authorized_api_reads_paginated_events_and_resolves_new_event_reference(tmp_path):
    class DirectApi:
        def __init__(self):
            self.calls = []

        def list_events(self, calendar_id, time_min, time_max):
            self.calls.append((calendar_id, time_min, time_max))
            return [
                {"iCalUID": "timed@example.com", "summary": "Занятие",
                 "start": {"dateTime": "2026-09-21T10:00:00+03:00"},
                 "end": {"dateTime": "2026-09-21T11:00:00+03:00"}},
                {"iCalUID": "series@example.com", "recurringEventId": "series",
                 "originalStartTime": {"date": "2026-09-22"}, "summary": "Весь день",
                 "start": {"date": "2026-09-22"}, "end": {"date": "2026-09-24"}},
            ]

    feed, direct = CalendarFeed(tmp_path), DirectApi()
    feed.configure(URL)
    result = feed.events(date(2026, 9, 21), date(2026, 9, 27), google_calendar=direct)

    assert len(direct.calls) == 1
    assert [event["title"] for event in result["events"]] == ["Занятие", "Весь день"]
    all_day = result["events"][1]
    assert all_day["all_day"] and all_day["dates"] == ["2026-09-22", "2026-09-23"]
    reference = feed.event_reference(all_day["calendar_id"], all_day["id"], all_day["start"])
    assert reference["ical_uid"] == "series@example.com" and reference["all_day"] is True
    assert not feed._cache_path(all_day["calendar_id"]).exists()


def test_api_refresh_failure_keeps_its_last_good_copy(tmp_path):
    class DirectApi:
        def __init__(self):
            self.available = True

        def list_events(self, *_):
            if not self.available:
                raise OSError("temporary")
            return [{"iCalUID": "event@example.com", "summary": "Занятие",
                     "start": {"dateTime": "2026-09-21T10:00:00+03:00"},
                     "end": {"dateTime": "2026-09-21T11:00:00+03:00"}}]

    feed, direct = CalendarFeed(tmp_path), DirectApi()
    feed.configure(URL)
    first = feed.events(date(2026, 9, 21), date(2026, 9, 27), google_calendar=direct)
    calendar_id = first["events"][0]["calendar_id"]
    previous = datetime.now().astimezone() - timedelta(minutes=3)
    feed._api_meta_path(calendar_id).write_text(
        '{"last_attempt": "' + previous.isoformat() + '", "last_updated": "'
        + previous.isoformat() + '"}', encoding="utf-8")
    direct.available = False

    second = feed.events(date(2026, 9, 21), date(2026, 9, 27), google_calendar=direct)

    assert second["stale"] and second["events"] == first["events"]


def test_api_failure_without_a_cache_uses_fallback_without_repeating_requests(tmp_path, monkeypatch):
    class DirectApi:
        def __init__(self):
            self.calls = 0

        def list_events(self, *_):
            self.calls += 1
            raise OSError("temporary")

    feed, direct = CalendarFeed(tmp_path), DirectApi()
    feed.configure(URL)
    calls = []
    monkeypatch.setattr(feed, "_download", lambda url: (calls.append(url), FEED)[1])

    first = feed.events(date(2026, 9, 21), date(2026, 9, 27), google_calendar=direct)
    second = feed.events(date(2026, 9, 21), date(2026, 9, 27), google_calendar=direct)

    assert direct.calls == 1
    assert calls == [URL]
    assert first["events"] == second["events"]


def test_calendar_api_auth_validation_and_no_secret_echo(tmp_path, monkeypatch):
    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765")
    auth = {"Authorization": "Bearer " + Store(tmp_path).token()}
    assert client.get("/api/calendar/config").status_code == 401
    assert client.post("/api/calendar/config", headers=auth, json={"ical_url": "http://localhost/private.ics"}).status_code == 422
    assert client.post("/api/calendar/config", headers=auth, json={"ical_url": "https://calendar.google.com:bad/calendar/ical/x/fixture/basic.ics"}).status_code == 422
    configured = client.post("/api/calendar/config", headers=auth, json={"ical_url": URL})
    assert configured.status_code == 200 and configured.json()["calendar_count"] == 1
    assert URL not in configured.text and URL not in client.get("/api/calendar/config", headers=auth).text
    assert client.get("/api/calendar/events?start=2026-09-21&end=2026-11-30", headers=auth).status_code == 422
    assert client.get("/api/calendar/events?start=bad&end=2026-09-21", headers=auth).status_code == 422
    cleared = client.post("/api/calendar/config", headers=auth, json={"ical_url": None})
    assert cleared.json()["configured"] is False


def test_legacy_config_and_cache_migrate_without_losing_connection(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    (state / "calendar.json").write_text('{"ical_url": ' + repr(URL).replace("'", '"') + "}", encoding="utf-8")
    (state / "calendar-cache.ics").write_bytes(FEED)
    (state / "calendar-meta.json").write_text('{"last_updated":"2026-09-19T00:00:00+03:00"}', encoding="utf-8")
    feed = CalendarFeed(tmp_path)
    monkeypatch.setattr(feed, "_download", lambda _: (_ for _ in ()).throw(OSError("offline")))
    status = feed.status()
    assert status["configured"] and status["calendar_count"] == 1
    assert feed.events(date(2026, 9, 21), date(2026, 9, 21))["events"]
    assert not (state / "calendar-cache.ics").exists()


def test_legacy_migration_keeps_sources_when_config_write_fails_then_retries(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    (state / "calendar.json").write_text('{"ical_url": ' + repr(URL).replace("'", '"') + "}", encoding="utf-8")
    (state / "calendar-cache.ics").write_bytes(FEED)
    (state / "calendar-meta.json").write_text('{"last_updated":"2026-09-19T00:00:00+03:00"}', encoding="utf-8")
    feed = CalendarFeed(tmp_path)
    from megamozg import calendar as calendar_module

    original_write = calendar_module._atomic_write

    def fail_config_write(path, data):
        if path == feed.config_path:
            raise OSError("simulated config write failure")
        original_write(path, data)

    monkeypatch.setattr(calendar_module, "_atomic_write", fail_config_write)
    try:
        feed.status()
    except OSError:
        pass
    else:
        raise AssertionError("migration must surface the failed config write")
    assert (state / "calendar.json").read_text(encoding="utf-8").startswith('{"ical_url"')
    assert (state / "calendar-cache.ics").read_bytes() == FEED
    assert (state / "calendar-meta.json").exists()
    monkeypatch.setattr(calendar_module, "_atomic_write", original_write)
    status = feed.status()
    assert status["configured"] and status["calendar_count"] == 1
    assert not (state / "calendar-cache.ics").exists()
    assert not (state / "calendar-meta.json").exists()
    assert feed.events(date(2026, 9, 21), date(2026, 9, 21))["events"]


def test_multiple_calendars_are_idempotent_removable_and_never_echo_urls(tmp_path):
    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765")
    auth = {"Authorization": "Bearer " + Store(tmp_path).token()}
    first = client.post("/api/calendar/config", headers=auth, json={"ical_url": URL, "name": "Ученики"}).json()
    duplicate = client.post("/api/calendar/config", headers=auth, json={"ical_url": URL, "name": "Уроки"}).json()
    second = client.post("/api/calendar/config", headers=auth, json={"ical_url": URL_TWO, "name": "Личное"}).json()
    assert first["calendar_count"] == duplicate["calendar_count"] == 1
    assert duplicate["calendars"][0]["name"] == "Уроки"
    assert second["calendar_count"] == 2
    assert URL not in str(second) and URL_TWO not in str(second)
    removed = client.delete("/api/calendar/config/" + first["calendars"][0]["id"], headers=auth)
    assert removed.status_code == 200 and removed.json()["calendar_count"] == 1
    assert client.post("/api/calendar/config", headers=auth, json={"ical_url": None}).json()["calendar_count"] == 0


def test_remove_keeps_config_and_cache_when_config_write_fails(tmp_path, monkeypatch):
    feed = CalendarFeed(tmp_path)
    feed.configure(URL, "Первый")
    configured = feed.configure(URL_TWO, "Второй")
    removed_id = configured["calendars"][0]["id"]
    cache_path = feed._cache_path(removed_id)
    cache_path.write_bytes(FEED)
    from megamozg import calendar as calendar_module

    original_write = calendar_module._atomic_write

    def fail_config_write(path, data):
        if path == feed.config_path:
            raise OSError("simulated config write failure")
        original_write(path, data)

    monkeypatch.setattr(calendar_module, "_atomic_write", fail_config_write)
    with pytest.raises(OSError, match="simulated config write failure"):
        feed.remove(removed_id)
    assert feed.status()["calendar_count"] == 2
    assert cache_path.read_bytes() == FEED


def test_events_merge_distinguish_ids_and_keep_good_feed_on_partial_failure(tmp_path, monkeypatch):
    feed = CalendarFeed(tmp_path)
    first, second = feed.configure(URL, "Первый"), feed.configure(URL_TWO, "Второй")
    feeds = {URL: FEED, URL_TWO: FEED.replace(b"Student lesson", b"Second lesson")}
    monkeypatch.setattr(feed, "_download", lambda url: feeds[url])
    result = feed.events(date(2026, 9, 21), date(2026, 9, 21))
    assert [event["calendar_name"] for event in result["events"]] == ["Второй", "Первый"]
    assert len({event["id"] for event in result["events"]}) == 2
    ids = [calendar["id"] for calendar in second["calendars"]]
    feed._meta_path(ids[1]).write_text('{"last_attempt":"2020-01-01T00:00:00+00:00"}', encoding="utf-8")
    monkeypatch.setattr(feed, "_download", lambda url: feeds[url] if url == URL else (_ for _ in ()).throw(OSError("offline")))
    partial = feed.events(date(2026, 9, 21), date(2026, 9, 21))
    assert partial["stale"] and partial["error"] and len(partial["events"]) == 2
    assert URL_TWO not in str(partial) and URL not in str(partial)
