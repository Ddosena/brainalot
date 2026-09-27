from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import base64
import json
import sqlite3
import subprocess
import sys
import zipfile
import yaml

import pytest
from fastapi.testclient import TestClient

from megamozg.api import create_app
from megamozg.core import ConflictError, Store, ValidationError, decode_note, encode_note


def test_capture_update_undo_and_external_edit(tmp_path):
    store = Store(tmp_path / "data")
    first = store.capture("Привет", external_id="voice-1")
    assert store.capture("Привет", external_id="voice-1")["id"] == first["id"]
    with pytest.raises(ConflictError): store.capture("Иное", external_id="voice-1")
    child = store.capture("Купить хлеб", kind="task", due="2026-09-18", source_capture_id=first["id"])
    assert child["source_capture_id"] == first["id"]
    with pytest.raises(ValidationError): store.capture("Без источника", source_capture_id="missing")
    updated = store.update(child["id"], {"due":"2026-09-21", "status":"done"}, child["version"])
    assert updated["completed_at"]
    assert store.dashboard()["stats"]["done"] == 1
    op = store.history()[0]["id"]
    restored = store.undo(op)
    assert restored["due"] == "2026-09-18" and restored["status"] == "active"
    assert store.dashboard()["stats"]["done"] == 0
    assert store.dashboard()["stats"]["deferred"] == 0
    path = store.vault / restored["path"]
    path.write_text(path.read_text(encoding="utf-8") + "Внешняя правка\n", encoding="utf-8")
    with pytest.raises(ConflictError): store.update(restored["id"], {"status":"done"}, restored["version"])
    with pytest.raises(ConflictError): store.undo(store.history()[1]["id"])


def test_replay_and_backup(tmp_path, monkeypatch):
    root = tmp_path / "data"
    store = Store(root)
    original_apply = store._apply
    monkeypatch.setattr(store, "_apply", lambda *_: (_ for _ in ()).throw(OSError("crash")))
    with pytest.raises(OSError): store.capture("after crash", external_id="crash")
    monkeypatch.setattr(store, "_apply", original_apply)
    recovered = Store(root)
    note = recovered.capture("after crash", external_id="crash")
    assert note["body"].find("after crash") >= 0
    archive = recovered.backup(tmp_path / "backup.zip")
    restored_root = tmp_path / "restored"
    with zipfile.ZipFile(archive) as zf: zf.extractall(restored_root)
    restored = Store(restored_root)
    assert restored.capture("after crash", external_id="crash")["id"] == note["id"]


def test_overdue_timezone_and_stats(tmp_path):
    current = datetime(2026, 9, 19, 0, 5, tzinfo=timezone(timedelta(hours=3)))
    store = Store(tmp_path, clock=lambda: current)
    a = store.capture("Вчера", kind="task", due="2026-09-18")
    b = store.capture("Позавчера", kind="task", due="2026-09-17")
    dash = store.dashboard()
    assert dash["today"] == dash["overdue_yesterday"] == dash["overdue_older"] == []
    assert {x["id"] for x in dash["unscheduled"]} == {a["id"], b["id"]}
    assert {x["due"] for x in dash["unscheduled"]} == {None}
    assert [op["kind"] for op in store.history()[:2]] == ["auto_rollover", "auto_rollover"]
    assert dash["stats"]["deferred"] == 0
    rollover_ids = [op["id"] for op in store.history() if op["kind"] == "auto_rollover"]
    store.dashboard()
    assert [op["id"] for op in store.history() if op["kind"] == "auto_rollover"] == rollover_ids

    rolled = next(x for x in dash["unscheduled"] if x["id"] == a["id"])
    moved = store.update(a["id"], {"kind":"purchase", "due":"2026-09-22"}, rolled["version"])
    assert moved["due"] == "2026-09-22"
    assert store.dashboard()["stats"]["deferred"] == 0
    store.undo(store.history()[0]["id"])
    assert store.dashboard()["stats"]["deferred"] == 0


def test_undo_stats_without_prior_due(tmp_path):
    store = Store(tmp_path)
    note = store.capture("Дело", kind="task")
    store.update(note["id"], {"due":"2026-09-22"}, note["version"])
    assert store.dashboard()["stats"]["deferred"] == 0
    store.undo(store.history()[0]["id"])
    assert store.dashboard()["stats"]["deferred"] == 0


def test_api_security_and_validation(tmp_path):
    store = Store(tmp_path)
    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765")
    auth = {"Authorization": "Bearer " + store.token(), "Origin":"chrome-extension://" + "a"*32}
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/dashboard").status_code == 401
    assert client.get("/api/dashboard", headers=auth).status_code == 200
    assert client.get("/api/dashboard", headers={**auth,"Host":"evil.example"}).status_code == 403
    assert client.get("/api/dashboard", headers={**auth,"Origin":"https://evil.example"}).status_code == 403
    assert client.options("/api/captures", headers={"Origin":auth["Origin"]}).status_code == 200
    for bad in ({"text":"x","kind":[]},{"text":"x","status":"done"},{"text":"x","tags":"tag"}):
        assert client.post("/api/captures", headers=auth, json=bad).status_code == 422
    assert client.post("/api/captures", headers=auth, json={"text":"x","url":"http://[foo]"}).status_code == 422
    assert client.post("/api/captures", headers=auth, content=b"x"*262145).status_code == 413
    good = client.post("/api/captures", headers=auth, json={"text":"hello"})
    assert good.status_code == 200
    assert client.patch("/api/notes/"+good.json()["id"], headers=auth, json={"expected_version":good.json()["version"],"patch":{"path":"../bad"}}).status_code == 422


def test_cli_two_agents(tmp_path):
    root = tmp_path / "data"
    def call(*args, input=None):
        result = subprocess.run([sys.executable,"-m","megamozg","--root",str(root),*args],input=input.encode("utf-8") if input else None,capture_output=True,check=False)
        return result.returncode, json.loads((result.stdout or result.stderr).decode("utf-8"))
    code, first = call("capture","--source","codex","--external-id","codex-1","--kind","task",input="Сделать дело")
    assert code == 0
    code, second = call("capture","--source","claude","--external-id","claude-1","--kind","idea",input="Новая идея")
    assert code == 0 and second["id"] != first["id"]
    code, notes = call("list")
    assert code == 0 and len(notes["notes"]) == 2


def test_partial_file_write_replays_and_shared_external_id(tmp_path, monkeypatch):
    root = tmp_path / "data"
    store = Store(root)
    original = store._atomic_file
    writes = 0

    def fail_second(path, content):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("interrupted between original and note")
        return original(path, content)

    monkeypatch.setattr(store, "_atomic_file", fail_second)
    with pytest.raises(OSError):
        store.capture("replay", external_id="same")
    assert len(list((root / "vault" / "9 Система" / "Оригиналы").glob("*.md"))) == 1
    recovered = Store(root)
    assert recovered.capture("replay", external_id="same")["body"].find("replay") >= 0
    stores = [Store(root), Store(root)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda s: s.capture("concurrent", external_id="shared")["id"], stores))
    assert ids[0] == ids[1]
    assert len([n for n in recovered.list_notes() if "concurrent" in n["body"]]) == 1


def test_dashboard_all_active_and_weighted_card(tmp_path):
    now = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    store = Store(tmp_path, clock=lambda: now)
    person = store.capture("Ученик", kind="person", importance="high")
    project = store.capture("Игра", kind="project")
    task = store.capture("Когда-нибудь", kind="task", context_id=project["id"], topic="Механика")
    future = store.capture("Через месяц", kind="purchase", due="2026-10-19", context_id=person["id"])
    dash = store.dashboard()
    assert {x["id"] for x in dash["unscheduled"]} == {task["id"]}
    assert {x["id"] for x in dash["upcoming"]} == {future["id"]}
    assert dash["resurface"]["id"] in {person["id"], task["id"], future["id"]}
    assert "важность" in dash["resurface"]["reason"]
    base = yaml.safe_load((store.vault / "9 Система" / "Дела.base").read_text(encoding="utf-8"))
    assert base["properties"]["note.title"]["displayName"] == "Название"
    assert base["properties"]["file.name"]["displayName"] == "Открыть"
    assert all(key in base["properties"] for key in base["views"][0]["order"])
    assert "file.name" in base["views"][0]["order"]
    assert "context_id" not in base["views"][0]["order"]
    inbox_base = yaml.safe_load((store.vault / "9 Система" / "Входящие.base").read_text(encoding="utf-8"))
    assert inbox_base["views"][0]["order"][0] == "note.title"
    assert inbox_base["properties"]["note.created"]["displayName"] == "Добавлено"
    assert store.update(task["id"], {"importance":"low", "topic":"Другая тема"}, task["version"])["importance"] == "low"
    with pytest.raises(ValidationError):
        store.capture("bad", kind="media", media_type=[])
    with pytest.raises(ValidationError):
        store.capture("bad", importance=[])
    with pytest.raises(ValidationError):
        store.capture("bad", context_id=task["id"])


def test_external_body_edit_survives_fresh_update_and_old_undo_stats(tmp_path):
    now = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    store = Store(tmp_path, clock=lambda: now)
    note = store.capture("First", kind="task", due="2026-09-20")
    path = store.vault / note["path"]
    path.write_text(path.read_text(encoding="utf-8") + "External body\n", encoding="utf-8")
    fresh = next(n for n in store.list_notes() if n["id"] == note["id"])
    changed = store.update(note["id"], {"kind":"purchase", "due":"2026-09-21"}, fresh["version"])
    assert changed["kind"] == "purchase" and "External body" in changed["body"]
    assert store.dashboard()["stats"]["deferred"] == 1
    store.undo(store.history()[0]["id"])
    assert store.dashboard()["stats"]["deferred"] == 0
    old = store.capture("Old", kind="task")
    done = store.update(old["id"], {"status":"done"}, old["version"])
    op = store.history()[0]["id"]
    later = Store(tmp_path, clock=lambda: now + timedelta(days=8))
    assert later.dashboard()["stats"]["done"] == 0
    later.undo(op)
    assert later.dashboard()["stats"]["done"] == 0
    manual = store.capture("Manual", kind="task")
    finished = store.update(manual["id"], {"status":"done"}, manual["version"])
    from megamozg.core import decode_note, encode_note
    file = store.vault / finished["path"]
    meta, body = decode_note(file.read_text(encoding="utf-8"))
    meta["status"] = "active"
    file.write_text(encode_note(meta, body), encoding="utf-8")
    assert store.dashboard()["stats"]["completed_recent"] == 0


def test_update_can_edit_title_body_and_move_any_record_between_sections(tmp_path):
    store = Store(tmp_path)
    note = store.capture("Старый текст", kind="idea", title="Старое название")

    edited = store.update(note["id"], {
        "title": "Новое название",
        "body": "\n# Новое название\n\nНовый текст\n",
        "kind": "task",
        "due": "2026-09-21",
        "planning_horizon": None,
    }, note["version"])

    assert edited["title"] == "Новое название"
    assert edited["kind"] == "task" and edited["due"] == "2026-09-21"
    assert "Новый текст" in edited["body"] and "Старый текст" not in edited["body"]
    assert edited["path"].startswith("2 Дела/Задачи/")
    moved = store.update(edited["id"], {"due": None, "planning_horizon": "week"}, edited["version"])
    assert moved["due"] is None and moved["planning_horizon"] == "week"


def test_update_body_validation_and_automatic_generated_heading_rename(tmp_path):
    store = Store(tmp_path)
    note = store.capture("Текст", kind="thought", title="До")
    renamed = store.update(note["id"], {"title": "После"}, note["version"])
    assert renamed["body"].startswith("\n# После\n")
    with pytest.raises(ValidationError):
        store.update(renamed["id"], {"body": None}, renamed["version"])
    with pytest.raises(ValidationError):
        store.update(renamed["id"], {"body": "x" * 50001}, renamed["version"])


def test_api_bad_unicode_auth_and_bounded_stream(tmp_path):
    store = Store(tmp_path)
    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765")
    auth = {"Authorization": "Bearer " + store.token()}
    assert client.get("/api/dashboard", headers={"Authorization":b"Bearer \xd1\x82\xd0\xbe\xd0\xba\xd0\xb5\xd0\xbd"}).status_code == 401
    assert client.post("/api/captures", headers=auth, json={"text":"x", "kind":"media", "media_type":[]}).status_code == 422
    assert client.post("/api/captures", headers=auth, json={"text":"x", "importance":[]}).status_code == 422
    assert client.post("/api/captures", headers=auth, content=(b"x" * 65536 for _ in range(5))).status_code == 413
    good = client.post("/api/captures", headers=auth, json={"text":"x", "kind":"project"})
    assert good.status_code == 200 and good.json()["importance"] == "normal"
    assert client.patch("/api/notes/" + good.json()["id"], headers=auth, json={"expected_version":good.json()["version"], "patch":{"topic":"Test"}}).status_code == 200


def test_cli_context_update_and_source_capture(tmp_path):
    root = tmp_path / "data"

    def call(*args, input=None):
        result = subprocess.run([sys.executable, "-m", "megamozg", "--root", str(root), *args],
                                input=input.encode("utf-8") if input is not None else None,
                                capture_output=True, check=False)
        return result.returncode, json.loads((result.stdout or result.stderr).decode("utf-8"))

    code, source = call("capture", "--source", "codex", "--external-id", "dictation", input="Полная диктовка")
    assert code == 0
    code, hub = call("capture", "--kind", "project", "--title", "Космическая игра", "--external-id", "hub", input="Проект")
    assert code == 0
    code, child = call("capture", "--kind", "task", "--context-id", hub["id"], "--topic", "Корабли",
                       "--importance", "critical", "--source-capture-id", source["id"], input="Сделать прототип")
    assert (code == 0 and child["source_capture_id"] == source["id"]
            and child["context_id"] == hub["id"] and child["importance"] == "critical")
    code, updated = call("update", child["id"], "--version", child["version"], "--patch", '{"topic":"Экономика"}')
    assert code == 0 and updated["topic"] == "Экономика"


def test_legacy_capture_retry_accepts_new_default_fields(tmp_path):
    store = Store(tmp_path)
    note = store.capture("До новых полей", external_id="legacy")
    with sqlite3.connect(store.db_path) as db:
        payload = json.loads(db.execute("SELECT payload FROM captures WHERE external_id='legacy'").fetchone()[0])
        for key in ("context_id", "topic", "importance", "show_in_unscheduled"):
            del payload[key]
        db.execute("UPDATE captures SET payload=? WHERE external_id='legacy'", (json.dumps(payload, ensure_ascii=False, sort_keys=True),))
    assert store.capture("До новых полей", external_id="legacy")["id"] == note["id"]


def test_carousel_weight_and_explicit_feedback_only(tmp_path):
    current = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    store = Store(tmp_path, clock=lambda: current)
    high = store.capture("High", kind="thought", importance="high")
    low = store.capture("Low", kind="idea", importance="low")
    counts = {high["id"]: 0, low["id"]: 0}
    for slot in range(4):
        current = datetime(2026, 9, 19, 12, tzinfo=timezone.utc) + timedelta(minutes=slot)
        counts[store.dashboard()["resurface"]["id"]] += 1
    assert counts == {high["id"]: 3, low["id"]: 1}
    assert all(n["status"] == "active" for n in store.list_notes())
    store.feedback(high["id"], "later", high["version"])
    assert store.dashboard()["resurface"]["id"] == low["id"]
    assert next(n for n in store.list_notes() if n["id"] == high["id"])["status"] == "active"


def test_critical_importance_is_task_only_and_survives_history(tmp_path):
    current = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    store = Store(tmp_path, clock=lambda: current)
    critical = store.capture("Срочно завершить", kind="task", importance="critical")
    stored_meta, _ = decode_note((store.vault / critical["path"]).read_text(encoding="utf-8"))
    assert stored_meta["importance"] == "critical"
    assert store.dashboard()["resurface"]["reason"] == "Вспомнить: дело; важность — очень высокая. Показ не меняет статус."

    high = store.capture("Важно", kind="purchase", importance="high")
    counts = {critical["id"]: 0, high["id"]: 0}
    for slot in range(7):
        current = datetime(2026, 9, 19, 12, tzinfo=timezone.utc) + timedelta(minutes=slot)
        counts[store.dashboard()["resurface"]["id"]] += 1
    assert counts == {critical["id"]: 4, high["id"]: 3}

    updated = store.update(high["id"], {"importance": "critical"}, high["version"])
    assert updated["importance"] == "critical"
    restored = store.undo(store.history()[0]["id"])
    assert restored["importance"] == "high"

    with pytest.raises(ValidationError, match="только для дел и покупок"):
        store.capture("Не горит", kind="thought", importance="critical")
    thought = store.capture("Обычная мысль", kind="thought")
    with pytest.raises(ValidationError, match="только для дел и покупок"):
        store.update(thought["id"], {"importance": "critical"}, thought["version"])
    with pytest.raises(ValidationError, match="только для дел и покупок"):
        store.update(critical["id"], {"kind": "thought"}, critical["version"])


def test_manual_carousel_candidates_share_auto_eligibility_without_repeated_weights(tmp_path, monkeypatch):
    current = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)
    store = Store(tmp_path, clock=lambda: current)
    high = store.capture("Important thought", kind="thought", importance="high")
    low = store.capture("Another idea", kind="idea", importance="low")
    task = store.capture("Unscheduled task", kind="task")
    project = store.capture("Project context", kind="project")
    future = store.capture("Dated task", kind="task", due="2026-10-01")
    store.capture("Today's task", kind="task", due="2026-09-23")
    store.capture("Today's purchase", kind="purchase", due="2026-09-23")
    store.capture("Unsorted inbox", kind="inbox")
    hidden = store.capture("Hidden thought", kind="thought")
    store.update(hidden["id"], {"show_in_carousel": False}, hidden["version"])
    for status in ("done", "cancelled", "archived"):
        item = store.capture(status, kind="thought")
        store.update(item["id"], {"status": status}, item["version"])
    deferred = store.capture("Read later", kind="thought")
    store.feedback(deferred["id"], "later", deferred["version"])
    before_history = store.history()
    before_notes = store.list_notes()

    expected = {high["id"], low["id"], task["id"], future["id"]}
    for slot in range(6):
        current = datetime(2026, 9, 23, 12, tzinfo=timezone.utc) + timedelta(minutes=slot)
        dashboard = store.dashboard()
        ids = [item["id"] for item in dashboard["resurface_candidates"]]
        assert ids == sorted(expected)
        assert dashboard["resurface"]["id"] in expected
    assert store.history() == before_history
    assert store.list_notes() == before_notes

    monkeypatch.setattr("megamozg.api.Store", lambda root: store)
    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765")
    response = client.get("/api/dashboard", headers={"Authorization": "Bearer " + store.token()})
    assert response.status_code == 200
    assert {item["id"] for item in response.json()["resurface_candidates"]} == expected

    store.feedback(high["id"], "archive", high["version"])
    assert {item["id"] for item in store.dashboard()["resurface_candidates"]} == expected - {high["id"]}
    current += timedelta(days=3)
    assert deferred["id"] in {item["id"] for item in store.dashboard()["resurface_candidates"]}


def test_only_today_tasks_do_not_resurface_and_overdue_roll_to_unscheduled(tmp_path):
    current = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    store = Store(tmp_path, clock=lambda: current)
    today_task = store.capture("Дело на сегодня", kind="task", due="2026-09-19", importance="high")
    overdue_purchase = store.capture("Просроченная покупка", kind="purchase", due="2026-09-18", importance="high")
    future_task = store.capture("Дело на завтра", kind="task", due="2026-09-20")
    idea = store.capture("Мысль для возвращения", kind="idea")

    for slot in range(8):
        current = datetime(2026, 9, 19, 12, tzinfo=timezone.utc) + timedelta(minutes=slot)
        dashboard = store.dashboard()
        assert {note["id"] for note in dashboard["today"]} == {today_task["id"]}
        assert overdue_purchase["id"] in {note["id"] for note in dashboard["unscheduled"]}
        expected = {overdue_purchase["id"], future_task["id"], idea["id"]}
        assert {note["id"] for note in dashboard["resurface_candidates"]} == expected
        assert dashboard["resurface"]["id"] in expected

    current = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
    dashboard = store.dashboard()
    assert {note["id"] for note in dashboard["unscheduled"]} >= {overdue_purchase["id"], today_task["id"]}
    assert future_task["id"] in {note["id"] for note in dashboard["today"]}
    assert future_task["id"] not in {note["id"] for note in dashboard["resurface_candidates"]}
    assert dashboard["resurface"]["id"] in {overdue_purchase["id"], today_task["id"], idea["id"]}


def test_rollover_preserves_note_and_undo_waits_until_next_local_day(tmp_path):
    current = datetime(2026, 9, 19, 23, 59, tzinfo=timezone(timedelta(hours=3)))
    store = Store(tmp_path, clock=lambda: current)
    note = store.capture("Продолжить курс", kind="task", due="2026-09-19")
    original_path = store.vault / note["path"]
    original_content = original_path.read_text(encoding="utf-8")
    assert {item["id"] for item in store.dashboard()["today"]} == {note["id"]}
    assert not [op for op in store.history() if op["kind"] == "auto_rollover"]

    current += timedelta(minutes=2)
    first = store.dashboard()
    rolled = next(item for item in first["unscheduled"] if item["id"] == note["id"])
    assert rolled["due"] is None and rolled["path"] == note["path"]
    assert rolled["body"] == note["body"] and original_path.exists()
    assert not first["today"] and not first["overdue_yesterday"] and not first["overdue_older"]
    operation = next(op for op in store.history() if op["kind"] == "auto_rollover")
    assert operation["status"] == "applied" and first["stats"]["deferred"] == 0
    assert store.dashboard()["unscheduled"][0]["id"] == note["id"]
    assert len([op for op in store.history() if op["kind"] == "auto_rollover"]) == 1
    with pytest.raises(ConflictError):
        store.update(note["id"], {"due": "2026-09-22"}, note["version"])

    restored = store.undo(operation["id"])
    assert restored["due"] == note["due"] and restored["version"] == note["version"]
    assert original_path.read_text(encoding="utf-8") == original_content
    assert {item["id"] for item in store.dashboard()["overdue_yesterday"]} == {note["id"]}
    assert len([op for op in store.history() if op["kind"] == "auto_rollover"]) == 1

    reassigned = store.update(note["id"], {"due": "2026-09-22"}, restored["version"])
    assert reassigned["due"] == "2026-09-22"
    assert note["id"] in {item["id"] for item in store.dashboard()["week"]}
    current += timedelta(days=1)
    assert note["id"] in {item["id"] for item in store.dashboard()["week"]}
    assert len([op for op in store.history() if op["kind"] == "auto_rollover"]) == 1
    chosen_past = store.update(note["id"], {"due": "2026-09-18"}, reassigned["version"])
    assert chosen_past["due"] == "2026-09-18"
    assert note["id"] in {item["id"] for item in store.dashboard()["unscheduled"]}
    assert len([op for op in store.history() if op["kind"] == "auto_rollover"]) == 2


def test_rollover_crash_recovery_and_inactive_notes(tmp_path, monkeypatch):
    current = datetime(2026, 9, 20, 0, 1, tzinfo=timezone.utc)
    store = Store(tmp_path, clock=lambda: current)
    active = store.capture("Активно", kind="purchase", due="2026-09-19")
    done = store.capture("Сделано", kind="task", due="2026-09-19")
    done = store.update(done["id"], {"status": "done"}, done["version"])
    original_apply = store._apply

    def fail_rollover(db, op):
        if op["kind"] == "auto_rollover":
            raise OSError("simulated crash after journal commit")
        return original_apply(db, op)

    monkeypatch.setattr(store, "_apply", fail_rollover)
    with pytest.raises(OSError):
        store.dashboard()
    pending = [op for op in store.history() if op["kind"] == "auto_rollover"]
    assert len(pending) == 1 and pending[0]["status"] == "pending"

    recovered = Store(tmp_path, clock=lambda: current)
    dashboard = recovered.dashboard()
    assert {item["id"] for item in dashboard["unscheduled"]} == {active["id"]}
    assert recovered.list_notes()[0]["id"] in {active["id"], done["id"]}
    assert next(item for item in recovered.list_notes() if item["id"] == done["id"])["due"] == "2026-09-19"
    assert len([op for op in recovered.history() if op["kind"] == "auto_rollover"]) == 1
    assert recovered.history()[0]["status"] == "applied"


def test_rollover_replays_after_file_write_and_respects_version_conflict(tmp_path, monkeypatch):
    current = datetime(2026, 9, 20, 0, 1, tzinfo=timezone.utc)
    store = Store(tmp_path, clock=lambda: current)
    note = store.capture("Версия", kind="task", due="2026-09-19")
    path = store.vault / note["path"]
    original_update = store._update
    raced = False

    def edit_before_rollover(db, current_note, patch, version, **kwargs):
        nonlocal raced
        if kwargs.get("operation_kind") == "auto_rollover" and not raced:
            raced = True
            path.write_text(path.read_text(encoding="utf-8") + "Внешнее уточнение\n", encoding="utf-8")
        return original_update(db, current_note, patch, version, **kwargs)

    monkeypatch.setattr(store, "_update", edit_before_rollover)
    first = store.dashboard()
    assert any("Автоперенос" in warning for warning in first["warnings"])
    assert not [op for op in store.history() if op["kind"] == "auto_rollover"]
    monkeypatch.setattr(store, "_update", original_update)

    original_apply = store._apply
    crashed = False

    def crash_after_file_write(db, op):
        nonlocal crashed
        if op["kind"] == "auto_rollover" and not crashed:
            crashed = True
            for change in json.loads(op["changes"]):
                store._atomic_file(store._path(change["path"]), change["after"])
            raise OSError("simulated crash after file replacement")
        return original_apply(db, op)

    monkeypatch.setattr(store, "_apply", crash_after_file_write)
    with pytest.raises(OSError):
        store.dashboard()
    assert decode_note(path.read_text(encoding="utf-8"))[0]["due"] is None
    recovered = Store(tmp_path, clock=lambda: current)
    dashboard = recovered.dashboard()
    assert {item["id"] for item in dashboard["unscheduled"]} == {note["id"]}
    assert "Внешнее уточнение" in dashboard["unscheduled"][0]["body"]
    assert len([op for op in recovered.history() if op["kind"] == "auto_rollover"]) == 1


def test_dashboard_scan_warning_is_not_duplicated_by_rollover(tmp_path):
    store = Store(tmp_path, clock=lambda: datetime(2026, 9, 20, tzinfo=timezone.utc))
    store.capture("Вчерашнее", kind="task", due="2026-09-19")
    broken = store.vault / "2 Дела" / "Задачи" / "broken.md"
    broken.write_text("not a MegaMozg note", encoding="utf-8")

    dashboard = store.dashboard()
    assert len([warning for warning in dashboard["warnings"] if "broken.md" in warning]) == 1
    assert len(dashboard["unscheduled"]) == 1


def test_project_records_resurface_but_project_itself_stays_only_in_library(tmp_path):
    current = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    store = Store(tmp_path, clock=lambda: current)
    project = store.capture("Пример проекта", kind="project", importance="high")
    assert store.dashboard()["resurface"] is None
    assert store.dashboard()["resurface_candidates"] == []
    idea = store.capture("Отдельная идея", kind="idea", context_id=project["id"])
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR" + b"\x00" * 12).decode("ascii")
    attachment = store.upload_attachment("project-image.png", png)
    picture = store.capture("Картинка проекта", kind="thought", context_id=project["id"], attachments=[attachment])
    before_history = store.history()
    for slot in range(5):
        current = datetime(2026, 9, 19, 12, tzinfo=timezone.utc) + timedelta(minutes=slot)
        dashboard = store.dashboard()
        assert dashboard["resurface"]["id"] in {idea["id"], picture["id"]}
        assert {note["id"] for note in dashboard["resurface_candidates"]} == {idea["id"], picture["id"]}
        assert idea["context_id"] == project["id"]
        assert picture["context_id"] == project["id"]
        assert project["id"] in {note["id"] for note in dashboard["library"]}
        assert project["id"] in {context["id"] for context in dashboard["contexts"]}
    assert store.history() == before_history


def test_carousel_visibility_preserves_library_and_contexts(tmp_path):
    store = Store(tmp_path)
    person = store.capture("Ученик", kind="person")
    project = store.capture("Игра", kind="project")
    hidden = store.update(person["id"], {"show_in_carousel": False}, person["version"])
    hidden_project = store.update(project["id"], {"show_in_carousel": False}, project["version"])

    dashboard = store.dashboard()
    assert dashboard["resurface"] is None
    assert hidden["show_in_carousel"] is False
    assert {hidden["id"], hidden_project["id"]} <= {note["id"] for note in dashboard["library"]}
    assert {hidden["id"], hidden_project["id"]} <= {context["id"] for context in dashboard["contexts"]}
    with pytest.raises(ConflictError):
        store.update(hidden["id"], {"show_in_carousel": True}, person["version"])

    restored = store.update(hidden["id"], {"show_in_carousel": True}, hidden["version"])
    assert restored["show_in_carousel"] is True
    assert store.dashboard()["resurface"]["id"] == restored["id"]
    with pytest.raises(ValidationError):
        store.update(restored["id"], {"show_in_carousel": "false"}, restored["version"])


def test_existing_markdown_defaults_to_carousel_visibility(tmp_path):
    store = Store(tmp_path)
    captured = store.capture("Старая заметка", kind="idea")
    path = store.vault / captured["path"]
    meta, body = decode_note(path.read_text(encoding="utf-8"))
    del meta["show_in_carousel"]
    path.write_text(encode_note(meta, body), encoding="utf-8")

    legacy = next(item for item in store.list_notes() if item["id"] == captured["id"])
    assert legacy["show_in_carousel"] is True
    assert store.dashboard()["resurface"]["id"] == legacy["id"]


def test_unscheduled_thought_is_preserved_and_reversible(tmp_path):
    current = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    store = Store(tmp_path, clock=lambda: current)
    thought = store.capture("Посмотреть образцы узоров", kind="thought",
                            show_in_unscheduled=True, external_id="sample-thought")
    task = store.capture("Обычное бессрочное дело", kind="task")
    purchase = store.capture("Обычная бессрочная покупка", kind="purchase")
    dated = store.capture("Мысль со сроком", kind="thought", due="2026-09-20",
                          show_in_unscheduled=True)

    dashboard = store.dashboard()
    assert thought["kind"] == "thought" and thought["show_in_unscheduled"] is True
    assert thought["path"].startswith("4 Идеи и мысли/Мысли/")
    assert {note["id"] for note in dashboard["unscheduled"]} == {thought["id"], task["id"], purchase["id"]}
    assert thought["id"] in {note["id"] for note in dashboard["library"]}
    assert thought["id"] not in {note["id"] for note in dashboard["today"] + dashboard["week"] + dashboard["upcoming"]}
    assert dated["id"] not in {note["id"] for note in dashboard["unscheduled"]}
    assert store.capture("Посмотреть образцы узоров", kind="thought",
                         show_in_unscheduled=True, external_id="sample-thought")["id"] == thought["id"]
    assert len([note for note in store.list_notes() if note["id"] == thought["id"]]) == 1

    completed = store.update(thought["id"], {"status": "done"}, thought["version"])
    assert thought["id"] not in {note["id"] for note in store.dashboard()["unscheduled"]}
    assert thought["id"] in {note["id"] for note in store.dashboard()["completed_tasks"]}
    restored = store.undo(store.history()[0]["id"])
    assert restored["id"] == thought["id"] and restored["status"] == "active"
    assert thought["id"] in {note["id"] for note in store.dashboard()["unscheduled"]}


def test_unscheduled_thought_defaults_and_validation(tmp_path):
    store = Store(tmp_path)
    captured = store.capture("Старая мысль", kind="thought")
    path = store.vault / captured["path"]
    meta, body = decode_note(path.read_text(encoding="utf-8"))
    del meta["show_in_unscheduled"]
    path.write_text(encode_note(meta, body), encoding="utf-8")

    legacy = next(note for note in store.list_notes() if note["id"] == captured["id"])
    assert legacy["show_in_unscheduled"] is False
    enabled = store.update(legacy["id"], {"show_in_unscheduled": True}, legacy["version"])
    assert enabled["kind"] == "thought" and enabled["show_in_unscheduled"] is True
    with pytest.raises(ValidationError, match="только мысли"):
        store.capture("Нельзя превратить дело", kind="task", show_in_unscheduled=True)
    with pytest.raises(ValidationError, match="true или false"):
        store.update(enabled["id"], {"show_in_unscheduled": "true"}, enabled["version"])
    with pytest.raises(ValidationError, match="только мысли"):
        store.update(enabled["id"], {"kind": "idea"}, enabled["version"])


def test_unscheduled_thought_cli_and_api_capture(tmp_path):
    root = tmp_path / "data"
    result = subprocess.run([sys.executable, "-m", "megamozg", "--root", str(root), "capture",
                             "--kind", "thought", "--show-in-unscheduled"], input="CLI мысль".encode("utf-8"),
                            capture_output=True, check=False)
    assert result.returncode == 0
    cli_note = json.loads(result.stdout.decode("utf-8"))
    assert cli_note["kind"] == "thought" and cli_note["show_in_unscheduled"] is True

    store = Store(root)
    client = TestClient(create_app(root), base_url="http://127.0.0.1:8765")
    auth = {"Authorization": "Bearer " + store.token()}
    response = client.post("/api/captures", headers=auth,
                           json={"text": "API мысль", "kind": "thought", "show_in_unscheduled": True})
    assert response.status_code == 200 and response.json()["show_in_unscheduled"] is True
    rejected = client.post("/api/captures", headers=auth,
                           json={"text": "API дело", "kind": "task", "show_in_unscheduled": True})
    assert rejected.status_code == 422


def test_carousel_visibility_update_recovers_from_interrupted_write(tmp_path, monkeypatch):
    store = Store(tmp_path)
    note = store.capture("Восстановить", kind="idea")
    original_apply = store._apply
    monkeypatch.setattr(store, "_apply", lambda *_: (_ for _ in ()).throw(OSError("interrupted")))
    with pytest.raises(OSError):
        store.update(note["id"], {"show_in_carousel": False}, note["version"])
    monkeypatch.setattr(store, "_apply", original_apply)

    recovered = Store(tmp_path)
    restored = next(item for item in recovered.list_notes() if item["id"] == note["id"])
    assert restored["show_in_carousel"] is False


def test_completed_tasks_include_unscheduled_and_sort_by_completion_time(tmp_path):
    current = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    store = Store(tmp_path, clock=lambda: current)
    earlier = store.capture("Ранее завершённое", kind="task", due="2026-09-01")
    store.update(earlier["id"], {"status": "done"}, earlier["version"])
    current = datetime(2026, 9, 19, 13, tzinfo=timezone.utc)
    later = store.capture("Завершённое без срока", kind="purchase")
    store.update(later["id"], {"status": "done"}, later["version"])

    completed = store.dashboard()["completed_tasks"]
    assert [note["id"] for note in completed] == [later["id"], earlier["id"]]
    assert completed[0]["due"] is None
    assert all(note["completed_at"] for note in completed)


def test_stats_seven_calendar_day_boundary(tmp_path):
    current = datetime(2026, 9, 12, 12, tzinfo=timezone.utc)
    store = Store(tmp_path, clock=lambda: current)
    old = store.capture("Outside", kind="task", due="2026-09-12")
    store.update(old["id"], {"status":"done"}, old["version"])
    current = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
    inside = store.capture("Inside", kind="task", due="2026-09-13")
    store.update(inside["id"], {"status":"done"}, inside["version"])
    current = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    dashboard = store.dashboard()
    stats = dashboard["stats"]
    assert stats["completed_recent"] == 1
    assert stats["done"] == 1
    assert {note["id"] for note in dashboard["completed_tasks"]} == {old["id"], inside["id"]}
    assert {note["due"] for note in dashboard["completed_tasks"]} == {"2026-09-12", "2026-09-13"}


def test_delete_removes_vault_files_dashboard_and_retries(tmp_path):
    store = Store(tmp_path)
    note = store.capture("Удалить меня", kind="idea", external_id="delete-event")
    original = store.vault / "9 Система" / "Оригиналы" / f"{note['id']}.md"
    main = store.vault / note["path"]

    deleted = store.delete(note["id"], note["version"])
    assert deleted["id"] == note["id"] and deleted["deleted"] is True
    assert not main.exists() and not original.exists()
    assert note["id"] not in {item["id"] for item in store.dashboard()["library"]}
    assert store.history()[0]["id"] == deleted["operation_id"]
    with pytest.raises(ConflictError, match="удалена"):
        store.capture("Удалить меня", kind="idea", external_id="delete-event")

    restored = store.undo(deleted["operation_id"])
    assert restored["id"] == note["id"]
    assert main.exists() and original.exists()


def test_delete_guards_links_and_version_and_recovers(tmp_path, monkeypatch):
    root = tmp_path / "data"
    store = Store(root)
    source = store.capture("Диктовка", external_id="source")
    child = store.capture("Связанная заметка", kind="task", source_capture_id=source["id"])
    with pytest.raises(ConflictError, match="ссылаются"):
        store.delete(source["id"], source["version"])
    with pytest.raises(ConflictError):
        store.delete(child["id"], "stale")

    original_apply = store._apply
    monkeypatch.setattr(store, "_apply", lambda *_: (_ for _ in ()).throw(OSError("interrupted")))
    with pytest.raises(OSError):
        store.delete(child["id"], child["version"])
    monkeypatch.setattr(store, "_apply", original_apply)
    recovered = Store(root)
    assert child["id"] not in {item["id"] for item in recovered.list_notes()}
    delete_op = recovered.history()[0]
    assert delete_op["kind"] == "delete" and delete_op["status"] == "applied"
    assert recovered.undo(delete_op["id"])["id"] == child["id"]


def test_delete_cli_and_api_auth_response(tmp_path):
    root = tmp_path / "data"
    store = Store(root)
    note = store.capture("API delete", kind="idea")
    client = TestClient(create_app(root), base_url="http://127.0.0.1:8765")
    path = "/api/notes/" + note["id"]
    assert client.request("DELETE", path, json={"expected_version": note["version"]}).status_code == 401
    auth = {"Authorization": "Bearer " + store.token(), "Origin": "chrome-extension://" + "a" * 32}
    response = client.request("DELETE", path, headers=auth, json={"expected_version": note["version"]})
    assert response.status_code == 200
    assert response.json()["id"] == note["id"] and response.json()["deleted"] is True
    assert client.request("DELETE", path, headers=auth, json={"expected_version": note["version"]}).status_code == 404

    live = Store(root).capture("CLI delete", kind="idea")
    result = subprocess.run([sys.executable, "-m", "megamozg", "--root", str(root), "delete", live["id"], "--version", live["version"]],
                            capture_output=True, check=False)
    assert result.returncode == 0
    value = json.loads(result.stdout.decode("utf-8"))
    assert value["id"] == live["id"] and value["deleted"] is True


def test_reorder_persists_sorts_undoes_and_supports_legacy_notes(tmp_path):
    current = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
    store = Store(tmp_path, clock=lambda: current)
    first = store.capture("Первое", kind="task", due="2026-09-20")
    second = store.capture("Второе", kind="purchase", due="2026-09-20")
    future = store.capture("Будущее", kind="task", due="2026-09-22")

    initial_order = [note["id"] for note in store.dashboard()["today"]]
    requested_order = list(reversed(initial_order))
    result = store.reorder("2026-09-20", requested_order,
                           {first["id"]: first["version"], second["id"]: second["version"]})
    assert [note["id"] for note in result["notes"]] == requested_order
    assert [note["manual_order"] for note in result["notes"]] == [0, 1]
    assert [note["id"] for note in Store(tmp_path, clock=lambda: current).dashboard()["today"]] == requested_order

    legacy_path = store.vault / future["path"]
    legacy_meta, legacy_body = decode_note(legacy_path.read_text(encoding="utf-8"))
    del legacy_meta["manual_order"]
    legacy_path.write_text(encode_note(legacy_meta, legacy_body), encoding="utf-8")
    assert next(note for note in store.list_notes() if note["id"] == future["id"])["manual_order"] is None

    store.undo(result["operation_id"])
    restored = {note["id"]: note for note in store.list_notes()}
    assert restored[first["id"]]["manual_order"] is None
    assert restored[second["id"]]["manual_order"] is None
    assert [note["id"] for note in store.dashboard()["today"]] == initial_order

    moved = store.update(first["id"], {"due": "2026-09-22"}, restored[first["id"]]["version"])
    assert moved["manual_order"] is None


def test_reorder_validates_full_group_and_versions_without_partial_write(tmp_path):
    current = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
    store = Store(tmp_path, clock=lambda: current)
    first = store.capture("Первое", kind="task", due="2026-09-20")
    second = store.capture("Второе", kind="purchase", due="2026-09-20")
    done = store.capture("Готово", kind="task", due="2026-09-20")
    done = store.update(done["id"], {"status": "done"}, done["version"])
    versions = {first["id"]: first["version"], second["id"]: second["version"]}

    with pytest.raises(ValidationError, match="повторяющиеся"):
        store.reorder("2026-09-20", [first["id"], first["id"]], {first["id"]: first["version"]})
    with pytest.raises(ConflictError, match="полный"):
        store.reorder("2026-09-20", [first["id"]], {first["id"]: first["version"]})
    with pytest.raises(ConflictError, match="полный"):
        store.reorder("2026-09-20", [first["id"], done["id"]],
                      {first["id"]: first["version"], done["id"]: done["version"]})
    with pytest.raises(ConflictError, match="изменено"):
        store.reorder("2026-09-20", [second["id"], first["id"]],
                      {**versions, second["id"]: "0" * 64})
    assert all(note["manual_order"] is None for note in store.dashboard()["today"])
    assert not [op for op in store.history() if op["kind"] == "reorder"]


def test_reorder_recovers_partial_application_and_api_guards(tmp_path, monkeypatch):
    root = tmp_path / "data"
    current = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
    store = Store(root, clock=lambda: current)
    first = store.capture("Первое", kind="task", due="2026-09-20")
    second = store.capture("Второе", kind="task", due="2026-09-20")
    versions = {first["id"]: first["version"], second["id"]: second["version"]}
    original = store._atomic_file
    writes = 0

    def fail_second(path, content):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("interrupted reorder")
        return original(path, content)

    monkeypatch.setattr(store, "_atomic_file", fail_second)
    with pytest.raises(OSError):
        store.reorder("2026-09-20", [second["id"], first["id"]], versions)
    recovered = Store(root, clock=lambda: current)
    assert [note["id"] for note in recovered.dashboard()["today"]] == [second["id"], first["id"]]
    operation = next(op for op in recovered.history() if op["kind"] == "reorder")
    assert operation["status"] == "applied"

    client = TestClient(create_app(root), base_url="http://127.0.0.1:8765")
    auth = {"Authorization": "Bearer " + recovered.token(), "Origin": "chrome-extension://" + "a" * 32}
    notes = recovered.dashboard()["today"]
    payload = {"due": "2026-09-20", "ordered_ids": [notes[1]["id"], notes[0]["id"]],
               "expected_versions": {note["id"]: note["version"] for note in notes}}
    assert client.post("/api/tasks/reorder", json=payload).status_code == 401
    response = client.post("/api/tasks/reorder", headers=auth, json=payload)
    assert response.status_code == 200
    assert [note["id"] for note in response.json()["notes"]] == payload["ordered_ids"]


def test_planning_horizons_keep_markdown_and_patches_consistent(tmp_path):
    current = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
    store = Store(tmp_path / "data", clock=lambda: current)

    legacy_source = store.capture("Старая задача", kind="task")
    legacy_path = store.vault / legacy_source["path"]
    meta, body = decode_note(legacy_path.read_text(encoding="utf-8"))
    del meta["planning_horizon"]
    legacy_path.write_text(encode_note(meta, body), encoding="utf-8")
    legacy = next(note for note in store.list_notes() if note["id"] == legacy_source["id"])
    assert legacy["planning_horizon"] is None

    week = store.capture("Сделать на неделе", kind="task", planning_horizon="week")
    month = store.capture("Купить за месяц", kind="purchase", planning_horizon="month")
    dated = store.capture("Уже назначено", kind="task", due="2026-09-23", planning_horizon="week")
    assert week["planning_horizon"] == "week"
    assert month["planning_horizon"] == "month"
    assert dated["planning_horizon"] is None

    with pytest.raises(ValidationError, match="дел и покупок"):
        store.capture("Не дело", kind="thought", planning_horizon="week")
    with pytest.raises(ValidationError, match="week, month или null"):
        store.capture("Неверный горизонт", kind="task", planning_horizon="later")
    with pytest.raises(ValidationError, match="week, month или null"):
        store.capture("Не строка", kind="task", planning_horizon=[])
    with pytest.raises(ValidationError, match="только без срока"):
        store.update(dated["id"], {"planning_horizon": "week"}, dated["version"])

    moved = store.update(dated["id"], {"due": None, "planning_horizon": "week"}, dated["version"])
    assert moved["due"] is None and moved["planning_horizon"] == "week"
    by_due = store.update(moved["id"], {"due": "2026-09-23", "planning_horizon": "month"}, moved["version"])
    assert by_due["due"] == "2026-09-23" and by_due["planning_horizon"] is None

    changed_kind = store.update(week["id"], {"kind": "idea", "planning_horizon": "month"}, week["version"])
    assert changed_kind["kind"] == "idea" and changed_kind["planning_horizon"] is None

    dashboard = store.dashboard()
    assert {note["id"] for note in dashboard["unscheduled"]} == {legacy["id"]}
    assert {note["id"] for note in dashboard["backlog_week"]} == set()
    assert {note["id"] for note in dashboard["backlog_month"]} == {month["id"]}
    assert by_due["id"] in {note["id"] for note in dashboard["week"]}


def test_planning_horizons_reset_order_undo_and_rollover(tmp_path):
    current = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
    store = Store(tmp_path / "data", clock=lambda: current)
    first = store.capture("Первое", kind="task")
    second = store.capture("Второе", kind="purchase")
    versions = {first["id"]: first["version"], second["id"]: second["version"]}
    ordered = store.reorder(None, [second["id"], first["id"]], versions)
    reordered_first = next(note for note in ordered["notes"] if note["id"] == first["id"])

    planned = store.update(first["id"], {"planning_horizon": "week"}, reordered_first["version"])
    assert planned["manual_order"] is None
    assert first["id"] in {note["id"] for note in store.dashboard()["backlog_week"]}

    operation_id = store.history()[0]["id"]
    restored = store.undo(operation_id)
    assert restored["planning_horizon"] is None and restored["manual_order"] == 1
    assert first["id"] in {note["id"] for note in store.dashboard()["unscheduled"]}

    overdue = store.capture("Вчерашнее", kind="task", due="2026-09-19", planning_horizon="month")
    dashboard = store.dashboard()
    rolled = next(note for note in dashboard["unscheduled"] if note["id"] == overdue["id"])
    assert rolled["due"] is None and rolled["planning_horizon"] is None
    rollover = next(op for op in store.history() if op["kind"] == "auto_rollover")
    undone = store.undo(rollover["id"])
    assert undone["due"] == "2026-09-19" and undone["planning_horizon"] is None


def test_planning_horizon_api_capture_and_atomic_patch(tmp_path):
    store = Store(tmp_path)
    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765")
    auth = {"Authorization": "Bearer " + store.token()}
    created = client.post("/api/captures", headers=auth,
                          json={"text": "Через неделю", "kind": "task", "planning_horizon": "week"})
    assert created.status_code == 200 and created.json()["planning_horizon"] == "week"
    note = created.json()
    response = client.patch("/api/notes/" + note["id"], headers=auth,
                            json={"expected_version": note["version"],
                                  "patch": {"due": "2026-09-27", "planning_horizon": "month"}})
    assert response.status_code == 200
    assert response.json()["due"] == "2026-09-27"
    assert response.json()["planning_horizon"] is None
