from datetime import date, timedelta

import pytest

from megamozg.calendar import CalendarFeed
from megamozg.calendar_bridge import CalendarBridge
from megamozg.core import ValidationError
from megamozg.work_period import color_rules

ICAL = "https://calendar.google.com/calendar/ical/owner%40example.com/private-test-abc/basic.ics"
URL = "https://script.google.com/macros/s/AKfycbx0123456789abcdefghijklmnop/exec"


class FakeBridge:
    def __init__(self, events):
        self.events, self.calls = events, []

    def list_events(self, calendar_id, time_min, time_max):
        self.calls.append((calendar_id, time_min, time_max))
        return self.events


def api_event(uid, day, **extra):
    return {"iCalUID": uid, "summary": uid, "start": {"dateTime": f"{day}T12:00:00+03:00"},
            "end": {"dateTime": f"{day}T13:00:00+03:00"}, **extra}


def test_connect_checks_url_and_protocol_and_keeps_the_key(tmp_path, monkeypatch):
    bridge = CalendarBridge(tmp_path)
    with pytest.raises(ValidationError):
        bridge.connect("https://example.com/exec")
    key = bridge.prepare()["key"]
    sent = []
    monkeypatch.setattr(bridge, "_post", lambda url, body: sent.append(body) or {"ok": True, "protocol": 1})
    status = bridge.connect(URL)
    assert status["configured"] and sent == [{"action": "ping", "key": key}]
    assert bridge.prepare()["key"] == key
    assert not bridge.disconnect()["configured"]


def test_bridge_reads_colours_once_a_minute_without_ical(tmp_path, monkeypatch):
    feed = CalendarFeed(tmp_path)
    feed.configure(ICAL, "Личный")
    monkeypatch.setattr(feed, "_download", lambda url: pytest.fail("iCal must not be read"))
    today = date.today()
    bridge = FakeBridge([api_event("banana", today.isoformat(), colorId="5"),
                         api_event("mango", today.isoformat(), eventLabelId="l1",
                                   labelColor="#F09300", labelName="Скульптинг"),
                         api_event("plain", (today + timedelta(days=1)).isoformat())])
    week = feed.events(today, today + timedelta(days=6), bridge=bridge)
    colours = {event["title"]: (event.get("color"), event.get("color_name")) for event in week["events"]}
    assert colours == {"banana": ("#f6bf26", None), "mango": ("#f09300", "Скульптинг"), "plain": (None, None)}
    assert not week["stale"] and bridge.calls[0][0] == "owner@example.com"
    # Work's wider horizon is served by the same window, not by another script call.
    feed.events(today - timedelta(days=20), today + timedelta(days=40), bridge=bridge)
    assert len(bridge.calls) == 1


def test_failed_bridge_falls_back_to_ical(tmp_path, monkeypatch):
    feed = CalendarFeed(tmp_path)
    feed.configure(ICAL)
    class Broken:
        def list_events(self, *args):
            raise ValidationError("Нет связи с мостом Google")
    monkeypatch.setattr(feed, "_download", lambda url: b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n")
    result = feed.events(date.today(), date.today(), bridge=Broken())
    assert result["events"] == [] and not result["stale"]


def test_assigned_colour_becomes_ruler_points_and_can_move_between_projects():
    projects = [{"id": "print", "title": "3D"}, {"id": "sculpt", "title": "Скульптинг"}]
    events = [{"id": "e1", "kind": "calendar", "title": "Вебинар", "date": "2026-10-31", "start": "12:00",
               "end": "13:00", "color": "#f6bf26"}, {"id": "e2", "kind": "calendar", "title": "Встреча",
               "date": "2026-11-01", "color": "#7986cb"}]
    state = {}
    model = {"projects": projects}
    color_rules.apply(state, "calendar_color_assign", {"color": "#F6BF26", "project_id": "print"}, model)
    enriched = color_rules.enrich(state, [dict(p) for p in projects], events, events[:1])
    points = {p["id"]: p["calendar_points"] for p in enriched}
    assert [point["title"] for point in points["print"]] == ["Вебинар"] and points["sculpt"] == []
    assert points["print"][0]["can_open"] and points["print"][0]["color_name"] == "Банан"
    banana = next(row for row in color_rules.palette(state, events, projects) if row["color"] == "#f6bf26")
    assert banana == {"color": "#f6bf26", "name": "Банан", "standard": True, "count": 1, "project_id": "print"}
    color_rules.apply(state, "calendar_color_assign", {"color": "#f6bf26", "project_id": "sculpt"}, model)
    assert state["period_planner"]["calendar_colors"] == {"#f6bf26": "sculpt"}
    color_rules.apply(state, "calendar_color_assign", {"color": "#f6bf26", "project_id": None}, model)
    assert state["period_planner"]["calendar_colors"] == {}
    with pytest.raises(ValidationError):
        color_rules.apply(state, "calendar_color_assign", {"color": "#f6bf26", "project_id": "nope"}, model)
