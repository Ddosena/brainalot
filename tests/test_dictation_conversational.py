"""Conversational Russian captures with a fixed local clock."""

from datetime import datetime

import pytest

from megamozg.dictation import parse


NOW = datetime.fromisoformat("2026-09-24T20:50:00+00:00")  # 23:50 in Moscow


@pytest.mark.parametrize("phrase,title", [
    ("на неделю подготовить урок для Никиты", "Подготовить урок для Никиты"),
    ("в план на неделю подготовить урок для Никиты", "Подготовить урок для Никиты"),
    ("мне надо запланировать на неделю подготовить урок для Никиты", "Подготовить урок для Никиты"),
    ("запланировать на неделю отпуск", "Запланировать отпуск"),
    ("Добавь на неделю отпуск", "Отпуск"),
    ("Добавь на неделю задание подготовить урок для Никиты", "Подготовить урок для Никиты"),
    ("Добавь задачу на неделю подготовить урок для Никиты", "Подготовить урок для Никиты"),
])
def test_week_plan_uses_conversational_forms(phrase, title):
    result = parse(phrase, now=NOW)
    assert (result["kind"], result["title"], result["due"], result["planning_horizon"]) == (
        "task", title, None, "week")
    assert result["route"] == "note" and result["event"] is None
    assert any(span["field"] == "horizon" and "на неделю" in span["text"] for span in result["spans"])


@pytest.mark.parametrize("phrase,title,due", [
    ("на сегодня мне поставить задачу проработать урок для Никиты", "Проработать урок для Никиты", "2026-09-24"),
    ("давай на сегодня поставим задачу подготовить урок для Никиты", "Подготовить урок для Никиты", "2026-09-24"),
    ("я хочу, чтобы ты добавил мне задачу подготовить урок для Никиты", "Подготовить урок для Никиты", None),
    ("Я хочу сегодня подготовить урок для Никиты", "Подготовить урок для Никиты", "2026-09-24"),
    ("Мне бы на сегодня поставить задачу подготовить урок для Никиты", "Подготовить урок для Никиты", "2026-09-24"),
    ("Добавь мне на сегодня задачу, чтобы подготовить урок для Никиты", "Подготовить урок для Никиты", "2026-09-24"),
    ("поставить задачу не забыть подготовить урок", "Подготовить урок", None),
])
def test_explicit_task_request_has_a_short_action_title(phrase, title, due):
    result = parse(phrase, now=NOW)
    assert (result["kind"], result["title"], result["due"]) == ("task", title, due)
    assert result["text"] == phrase


@pytest.mark.parametrize("phrase,title,due,horizon", [
    ("не сегодня, а на неделе подготовить урок", "Подготовить урок", None, "week"),
    ("не сегодня, а на этой неделе подготовить урок", "Подготовить урок", None, "week"),
    ("не сегодня, а на эту неделю подготовить урок", "Подготовить урок", None, "week"),
    ("не сегодня, а в этом месяце подготовить урок", "Подготовить урок", None, "month"),
    ("сегодня, нет, лучше завтра подготовить урок", "Подготовить урок", "2026-09-25", None),
    ("на сегодня, нет, завтра подготовить урок", "Подготовить урок", "2026-09-25", None),
    ("на неделе, нет, сегодня подготовить урок", "Подготовить урок", "2026-09-24", None),
    ("на этой неделе, нет, лучше завтра подготовить урок", "Подготовить урок", "2026-09-25", None),
    ("в этом месяце, нет, лучше завтра подготовить урок", "Подготовить урок", "2026-09-25", None),
])
def test_explicit_date_correction_wins(phrase, title, due, horizon):
    result = parse(phrase, now=NOW)
    assert (result["kind"], result["title"], result["due"], result["planning_horizon"]) == (
        "task", title, due, horizon)
    assert not any("несколько дат" in warning for warning in result["warnings"])
    for span in result["spans"]:
        assert result["text"][span["start"]:span["end"]] == span["text"]


def test_uncued_multiple_dates_keep_existing_warning():
    result = parse("завтра в понедельник позвонить", now=NOW)
    assert result["due"] == "2026-09-25"
    assert "В диктовке несколько дат; использована первая" in result["warnings"]


@pytest.mark.parametrize("phrase,title", [
    ("добавить соль", "Добавить соль"),
    ("поставить чайник", "Поставить чайник"),
    ("записать ребёнка к врачу", "Записать ребёнка к врачу"),
    ("запланировать отпуск", "Запланировать отпуск"),
    ("подготовить урок и отправить письмо", "Подготовить урок и отправить письмо"),
])
def test_real_actions_are_preserved(phrase, title):
    result = parse(phrase, now=NOW)
    assert result["title"] == title


def test_wish_without_action_is_not_turned_into_a_task():
    result = parse("я хочу новую идею для урока", now=NOW)
    assert result["kind"] != "task"
    assert result["title"] == "Я хочу новую идею для урока"


def test_desire_about_oneself_remains_in_the_action_title():
    result = parse("я хочу научиться отдыхать", now=NOW)
    assert result["title"] == "Я хочу научиться отдыхать"


@pytest.mark.parametrize("phrase,title", [
    ("уехать на неделю к маме", "Уехать на неделю к маме"),
    ("уехать к маме на неделю", "Уехать к маме на неделю"),
    ("взять отпуск на неделю", "Взять отпуск на неделю"),
    ("Запланировать отпуск на неделю", "Запланировать отпуск на неделю"),
    ("Добавь отпуск на неделю", "Отпуск на неделю"),
    ("арендовать квартиру на неделю", "Арендовать квартиру на неделю"),
    ("отложить встречу на неделю", "Отложить встречу на неделю"),
    ("Добавь задачу уехать к маме на неделю", "Уехать к маме на неделю"),
    ("Добавь задачу подготовить урок на неделю", "Подготовить урок на неделю"),
])
def test_week_long_action_is_duration_not_planning_horizon(phrase, title):
    result = parse(phrase, now=NOW)
    assert result["title"] == title
    assert result["planning_horizon"] is None


def test_week_horizon_starts_a_new_clause():
    result = parse("проверить почту, на неделю подготовить урок", now=NOW)
    assert result["planning_horizon"] == "week"


def test_unresolved_pronoun_does_not_invent_previous_task():
    result = parse("поставь её на сегодня", now=NOW)
    assert result["title"] == "Её" and result["due"] == "2026-09-24"


def test_noisy_asr_phrase_only_recognizes_unambiguous_week():
    phrase = "Бафь на неделю, здание подготовить отчёт для команды."
    result = parse(phrase, now=NOW)
    assert result["text"] == phrase
    assert result["planning_horizon"] == "week"
    assert result["due"] is None
    assert "подготовить отчёт" in result["title"].lower()
