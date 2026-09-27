"""Bounded English grammar and its explicit ambiguity boundaries."""
import json
from datetime import datetime, timedelta

import pytest

from megamozg.dictation import parse


NOW = datetime.fromisoformat("2026-09-24T10:00:00+03:00")  # Thursday


@pytest.mark.parametrize("phrase,kind,title,start,rrule", [
    ("call the dentist tomorrow at 10", "task", "Call the dentist", "2026-09-25T10:00:00+03:00", None),
    ("team call tomorrow at 10", "task", "Team call", "2026-09-25T10:00:00+03:00", None),
    ("send report today at 14:00", "task", "Send report", "2026-09-24T14:00:00+03:00", None),
    ("meet Alex next Monday at 2:30pm", "task", "Meet Alex", "2026-09-28T14:30:00+03:00", None),
    ("call dentist on 2026-10-01 at 10am", "task", "Call dentist", "2026-10-01T10:00:00+03:00", None),
    ("email Alice in 15 minutes", "task", "Email Alice", "2026-09-24T10:15:00+03:00", None),
    ("call Alice in 2 hours", "task", "Call Alice", "2026-09-24T12:00:00+03:00", None),
    ("call Mom every Tuesday at 9am", "task", "Call Mom", "2026-09-29T09:00:00+03:00", "RRULE:FREQ=WEEKLY;BYDAY=TU"),
    ("call Mom daily at 9am", "task", "Call Mom", "2026-09-25T09:00:00+03:00", "RRULE:FREQ=DAILY"),
    ("task: review budget every week at 2pm", "task", "Review budget", "2026-09-24T14:00:00+03:00", "RRULE:FREQ=WEEKLY"),
])
def test_english_calendar(phrase, kind, title, start, rrule):
    result = parse(phrase, now=NOW)
    assert result["text"] == phrase
    assert result["kind"] == kind and result["title"] == title
    assert result["confident"] is True and result["route"] == "calendar"
    assert result["event"]["start"] == start
    assert result["event"]["end"] == (datetime.fromisoformat(start) + timedelta(hours=1)).isoformat()
    assert result["event"]["recurrence"] == ([rrule] if rrule else None)
    assert result["due"] == (None if rrule else start[:10])
    assert json.loads(json.dumps(result)) == result


@pytest.mark.parametrize("phrase,kind,title,due,media", [
    ("send the invoice", "task", "Send the invoice", None, None),
    ("buy milk", "purchase", "Buy milk", None, None),
    ("task: prepare slides tomorrow", "task", "Prepare slides", "2026-09-25", None),
    ("remind me to renew the domain in 3 days", "task", "Renew the domain", "2026-09-27", None),
    ("idea: a quieter dashboard", "idea", "A quieter dashboard", None, None),
    ("note: the display is bright", "thought", "The display is bright", None, None),
    ("movie: Arrival", "media", "Arrival", None, "movie"),
    ("book: The Hobbit", "media", "The Hobbit", None, "book"),
    ("the weather is lovely", "inbox", "The weather is lovely", None, None),
    ("the weather tomorrow may change", "inbox", "The weather may change", None, None),
])
def test_english_note_or_due(phrase, kind, title, due, media):
    result = parse(phrase, now=NOW)
    assert (result["kind"], result["title"], result["due"], result["media_type"]) == (kind, title, due, media)
    assert result["route"] == "note" and result["event"] is None


def test_protected_fragments_are_not_dates_and_context_is_supplied():
    contexts = [{"id": "a" * 32, "title": "Mom", "kind": "person"},
                {"id": "b" * 32, "title": "Old", "kind": "project", "status": "archived"}]
    result = parse('call Mom `tomorrow at 10` and review "2026-10-01"', now=NOW, contexts=contexts)
    assert result["kind"] == "task" and result["route"] == "note" and result["due"] is None
    assert result["context_id"] == "a" * 32 and result["context_title"] == "Mom"
    assert result["spans"] == []
    link = parse("read https://example.com/tomorrow/2026-10-01 in 2 days", now=NOW)
    assert link["due"] == "2026-09-26"
    assert link["url"] == "https://example.com/tomorrow/2026-10-01"
    assert [(span["field"], span["text"]) for span in link["spans"]] == [("date", "in 2 days")]


def test_recurrence_without_time_and_ambiguous_date():
    daily = parse("task: stretch every day", now=NOW)
    assert daily["event"] == {"title": "Stretch", "start": "2026-09-24", "end": "2026-09-25",
                              "all_day": True, "recurrence": ["RRULE:FREQ=DAILY"], "time_zone": "Europe/Moscow"}
    conflicting = parse("call dentist tomorrow next Monday at 10", now=NOW)
    assert conflicting["route"] == "note" and conflicting["due"] is None
    assert "Multiple dates; clarify the intended date" in conflicting["warnings"]
    assert "Please clarify the date or time before saving" in conflicting["warnings"]


def test_english_command_can_match_a_russian_named_context():
    contexts = [{"id": "a" * 32, "title": "Мама", "kind": "person"}]
    result = parse("call Мама tomorrow at 10", now=NOW, contexts=contexts)
    assert result["event"]["start"] == "2026-09-25T10:00:00+03:00"
    assert result["context_id"] == "a" * 32


def test_requested_zone_and_nonexistent_dst_time():
    local = parse("call dentist tomorrow at 10am", now=NOW, time_zone="Asia/Yekaterinburg", event_minutes=30)
    assert local["event"]["start"] == "2026-09-25T10:00:00+05:00"
    assert local["event"]["end"] == "2026-09-25T10:30:00+05:00"
    gap = parse("task: call dentist on 2026-03-29 at 2:30am",
                now=datetime.fromisoformat("2026-03-28T10:00:00+01:00"), time_zone="Europe/Berlin")
    assert gap["event"] is None
    assert any("nonexistent local time" in warning for warning in gap["warnings"])


def test_invalid_iso_date_does_not_turn_clock_into_a_different_day():
    result = parse("call dentist on 2026-02-30 at 10am", now=NOW)
    assert result["route"] == "note" and result["due"] is None
    assert "Invalid ISO date: 2026-02-30" in result["warnings"]


def test_relative_hours_are_elapsed_time_across_dst():
    result = parse("call dentist in 2 hours", now=datetime.fromisoformat("2026-03-29T01:30:00+01:00"),
                   time_zone="Europe/Berlin")
    assert result["event"]["start"] == "2026-03-29T04:30:00+02:00"
