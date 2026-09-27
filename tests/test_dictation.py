import json
from datetime import date, datetime
from pathlib import Path

import pytest
import yaml

from megamozg.dictation import parse


FIXTURE = yaml.safe_load((Path(__file__).parent / "fixtures" / "dictation_ru.yaml").read_text(encoding="utf-8"))
NOW = datetime.fromisoformat(FIXTURE["now"])
CONTEXTS = FIXTURE["contexts"]


def _iso(value):
    return value.isoformat() if isinstance(value, date) else value


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=[case["text"] for case in FIXTURE["cases"]])
def test_golden_phrase(case):
    result = parse(case["text"], now=NOW, contexts=CONTEXTS)
    expected = {
        "kind": case["kind"], "title": case["title"], "due": _iso(case.get("due")),
        "planning_horizon": case.get("horizon"), "importance": case.get("importance", "normal"),
        "context_title": case.get("context"), "media_type": case.get("media"),
        "deadline": case.get("deadline"), "tags": case.get("tags", []), "url": case.get("url"),
    }
    actual = {key: result[key] for key in expected}
    assert actual == expected
    if "confident" in case:
        assert result["confident"] is case["confident"]
    event = result["event"]
    if "start" not in case:
        assert event is None and result["route"] == "note"
        return
    assert result["route"] == "calendar"
    assert event["start"].startswith(_iso(case["start"]))
    assert event["all_day"] is bool(case.get("all_day", False))
    if "end" in case:
        assert event["end"].startswith(case["end"])
    assert event["recurrence"] == ([case["rrule"]] if "rrule" in case else None)
    assert event["title"] == case["title"]
    assert event["time_zone"] == "Europe/Moscow"


def test_fixture_is_large_and_unique():
    texts = [case["text"] for case in FIXTURE["cases"]]
    assert len(texts) >= 250
    assert len(set(texts)) == len(texts)


def test_result_is_json_and_reports_recognised_spans():
    result = parse("поставь мне на сегодня вынести мусор с 15 до 16", now=NOW, contexts=CONTEXTS)
    assert json.loads(json.dumps(result, ensure_ascii=False)) == result
    assert [(span["field"], span["text"]) for span in result["spans"]] == [
        ("date", "на сегодня"), ("time", "с 15 до 16")]
    assert result["text"][result["spans"][1]["start"]:result["spans"][1]["end"]] == "с 15 до 16"
    assert result["event"] == {
        "title": "Вынести мусор", "start": "2026-09-24T15:00:00+03:00", "end": "2026-09-24T16:00:00+03:00",
        "all_day": False, "recurrence": None, "time_zone": "Europe/Moscow"}


def test_context_ids_come_from_the_supplied_list_and_skip_archived_projects():
    result = parse("по проекту Мегамозг написать тесты", now=NOW, contexts=CONTEXTS)
    assert result["context_id"] == "11111111111111111111111111111111"
    archived = parse("по проекту старый проект закрыть долги", now=NOW, contexts=CONTEXTS)
    assert archived["context_id"] is None


def test_a_created_project_never_points_at_itself():
    result = parse("новый проект Мегамозг", now=NOW, contexts=CONTEXTS)
    assert result["kind"] == "project" and result["context_id"] is None


def test_time_zone_and_default_event_length_are_configurable():
    result = parse("завтра в 9 утра врач", now=NOW, time_zone="Asia/Yekaterinburg", event_minutes=30)
    assert result["event"]["start"] == "2026-09-25T09:00:00+05:00"
    assert result["event"]["end"] == "2026-09-25T09:30:00+05:00"
    assert result["event"]["time_zone"] == "Asia/Yekaterinburg"


def test_naive_now_is_read_in_the_requested_zone():
    result = parse("в 15 созвон", now=datetime(2026, 9, 24, 10, 0))
    assert result["event"]["start"] == "2026-09-24T15:00:00+03:00"


def test_warnings_explain_dropped_or_conflicting_facts():
    assert "Такой даты нет: 31 ноября" in parse("31 ноября сдать отчёт", now=NOW)["warnings"]
    assert "Время уже прошло" in parse("сегодня в 9 утра планёрка", now=NOW)["warnings"]
    assert "Дата сохраняется только у дел и покупок" in parse("мысль на завтра: проверить гипотезу", now=NOW)["warnings"]
    assert "В диктовке несколько дат; использована первая" in parse("завтра в понедельник позвонить", now=NOW)["warnings"]


@pytest.mark.parametrize("bad", ["", "   ", "x" * 2001])
def test_empty_or_huge_dictation_is_rejected(bad):
    with pytest.raises(ValueError):
        parse(bad, now=NOW)


def test_unknown_time_zone_is_rejected():
    with pytest.raises(ValueError, match="часовой пояс"):
        parse("завтра позвонить", now=NOW, time_zone="Mars/Olympus")


def test_titles_are_single_line_and_bounded():
    result = parse("позвонить " + "очень " * 60 + "далеко\nи надолго", now=NOW)
    assert "\n" not in result["title"] and 0 < len(result["title"]) <= 200
