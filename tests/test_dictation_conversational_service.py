"""Task speech is checked through the same HTTP/Store path as both clients."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from megamozg import api as api_module
from megamozg.core import Store


NOW = datetime.fromisoformat("2026-10-25T23:58:00+03:00")
TITLE = "Подготовить урок для Никиты"
HOME = {"kind": "task", "route": "note", "keep_context_in_title": True}


@pytest.fixture
def speech_api(tmp_path, monkeypatch):
    store = Store(tmp_path, clock=lambda: NOW)
    monkeypatch.setattr(api_module, "Store", lambda root: store)
    client = TestClient(api_module.create_app(tmp_path), base_url="http://127.0.0.1:43984")
    return client, store, {"Authorization": "Bearer " + store.token()}


@pytest.mark.parametrize("overrides", [{}, HOME], ids=["capture-auto", "home-microphone"])
@pytest.mark.parametrize("text,due,horizon,bucket", [
    ("Добавь на неделю задание подготовить урок для Никиты", None, "week", "backlog_week"),
    ("На сегодня мне поставить задачу подготовить урок для Никиты", "2026-10-25", None, "today"),
    ("Давай на сегодня поставим задачу подготовить урок для Никиты", "2026-10-25", None, "today"),
    ("Не сегодня, а на неделе подготовить урок для Никиты", None, "week", "backlog_week"),
    ("Сегодня, нет, лучше завтра подготовить урок для Никиты", "2026-10-26", None, "week"),
    ("Подготовить урок для Никиты", None, None, "unscheduled"),
])
def test_speech_preview_saved_title_and_dashboard_agree(speech_api, overrides, text, due, horizon, bucket):
    client, store, auth = speech_api
    preview = client.post("/api/dictation/parse", headers=auth, json={"text": text, "overrides": overrides})
    assert preview.status_code == 200, preview.text
    plan = preview.json()["plan"]
    assert (plan["title"], plan["due"], plan["planning_horizon"]) == (TITLE, due, horizon)
    payload = {"text": text, "source": "voice", "external_id": "speech-1", "overrides": overrides,
               "captured_at": NOW.isoformat(), "time_zone": "Europe/Moscow"}
    response = client.post("/api/dictation", headers=auth, json=payload)
    assert response.status_code == 200, response.text
    note = response.json()["note"]
    assert (note["title"], note["due"], note["planning_horizon"]) == (TITLE, due, horizon)
    assert text in note["body"]
    assert note["source"] == "voice"
    assert client.post("/api/dictation", headers=auth, json=payload).json() == response.json()
    assert len(store.list_notes()) == 1
    dashboard = client.get("/api/dashboard", headers=auth).json()
    assert note["id"] in {entry["id"] for entry in dashboard[bucket]}


def test_manual_schedule_still_overrides_spoken_today(speech_api):
    client, _, auth = speech_api
    for schedule in [{"due": None, "planning_horizon": "week"},
                     {"due": None, "planning_horizon": None},
                     {"due": "2026-10-27", "planning_horizon": None}]:
        preview = client.post("/api/dictation/parse", headers=auth, json={
            "text": "На сегодня мне поставить задачу подготовить урок для Никиты",
            "overrides": {**HOME, **schedule}}).json()["plan"]
        assert preview["title"] == TITLE
        assert {key: preview[key] for key in schedule} == schedule


def test_queued_today_retains_capture_day_and_delivery_receipt(speech_api):
    client, store, auth = speech_api
    # Speech was recorded before Moscow midnight, but delivered after it.
    store.clock = lambda: datetime.fromisoformat("2026-10-26T00:05:00+03:00")
    payload = {"text": "На сегодня мне поставить задачу подготовить урок для Никиты",
               "source": "voice", "external_id": "offline-speech", "overrides": HOME,
               "captured_at": "2026-10-25T20:58:00Z", "time_zone": "Europe/Moscow"}
    first = client.post("/api/dictation", headers=auth, json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["note"]["due"] == "2026-10-25"
    store.clock = lambda: datetime.fromisoformat("2026-10-27T11:00:00+03:00")
    assert client.post("/api/dictation", headers=auth, json=payload).json() == first.json()
    assert len(store.list_notes()) == 1
    assert client.post("/api/dictation", headers=auth,
                       json={**payload, "text": payload["text"] + " завтра"}).status_code == 409


def test_home_title_keeps_meaningful_course_context(speech_api):
    client, store, auth = speech_api
    project = store.capture("QA", kind="project", title="Курс скульптинга")
    result = client.post("/api/dictation", headers=auth, json={
        "text": "Добавь на сегодня задачу проработать урок по проекту Курс скульптинга",
        "external_id": "course-speech", "source": "voice", "overrides": HOME}).json()
    assert result["note"]["title"] == "Проработать урок по проекту Курс скульптинга"
    assert result["note"]["context_id"] == project["id"]
    assert result["note"]["due"] == "2026-10-25"
