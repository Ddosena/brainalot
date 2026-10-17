import json
import base64
import hashlib

import pytest
from fastapi.testclient import TestClient

from megamozg.api import create_app
from megamozg.core import Store, ValidationError
from megamozg.time_tracking import TimeStore
from megamozg.work_store import WorkStore


def test_paused_work_routes_and_tasks_preserve_data(tmp_path):
    store = Store(tmp_path)
    note = store.capture("Library task", kind="task")
    work = WorkStore(tmp_path)
    document = {"version": 1, "projects": [{"id": "p", "name": "Saved project"}],
                "items": [{"id": "w", "title": "Hidden Work task"}], "boards": []}
    raw = json.dumps(document).encode()
    blob = hashlib.sha256(raw).hexdigest()
    work.upload_chunk(blob, {"index": 0, "total": 1, "content_base64": base64.b64encode(raw).decode()})
    work.commit({"request_id": "seed", "expected_revision": 0, "state_blob": blob, "images": []})
    before = work.read()
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765") as client:
        assert client.get("/api/work/state").status_code == 401
        client.headers.update({"Authorization": "Bearer " + store.token()})
        for method, route in [("GET", "/api/work/state"), ("POST", "/api/work/commit"),
                              ("POST", "/api/work/snapshot"), ("POST", "/api/chats/sync"),
                              ("POST", "/api/chats/web")]:
            response = client.request(method, route, json={})
            assert response.status_code == 503
            assert response.json()["code"] == "work_disabled"
        assert client.get("/api/time/tasks").json()["tasks"] == [
            {"source": "library", "id": note["id"], "title": note["title"]}]
        assert client.get("/api/time/state").status_code == 200
        assert client.get("/api/recall").status_code == 200
        assert client.post("/api/browser/poll", json={"client_id": "chrome"}).status_code == 200
        command = {"request_id": "capture", "action": "active-tab", "payload": {}}
        assert client.post("/api/browser/commands", json=command).status_code == 200
        commands = client.post("/api/browser/poll", json={"client_id": "chrome"}).json()["commands"]
        assert [c["action"] for c in commands] == ["active-tab"]
        assert client.post("/api/browser/commands", json={**command, "request_id": "work", "action": "open-workspace"}).status_code == 422
    assert work.read() == before


def test_new_install_does_not_initialize_work_or_chats(tmp_path):
    create_app(tmp_path)
    assert not (tmp_path / "state" / "chats.sqlite3").exists()
    assert not (tmp_path / "state" / "work").exists()


def test_pending_work_browser_actions_are_not_delivered_to_old_clients(tmp_path, monkeypatch):
    from megamozg.browser_bridge import BrowserBridge
    bridge = BrowserBridge(tmp_path)
    bridge.poll("chrome")
    monkeypatch.setattr("megamozg.features.WORK_ENABLED", True)
    bridge.submit({"request_id": "old", "action": "open-workspace", "payload": {}})
    monkeypatch.setattr("megamozg.features.WORK_ENABLED", False)
    assert bridge.poll("chrome")["commands"] == []
    assert bridge.get("old")["status"] == "error"


def test_paused_work_cli_does_not_initialize_work(tmp_path, capsys):
    from megamozg.cli import main
    for command in (["work", "snapshot"], ["chats", "sync"]):
        assert main(["--root", str(tmp_path), *command]) == 2
        assert "Work" in json.loads(capsys.readouterr().err)["error"]["message"]
    assert not (tmp_path / "state" / "chats.sqlite3").exists()


def test_paused_work_rejects_new_time_binding_but_library_runs(tmp_path):
    timer = TimeStore(tmp_path, clock=lambda: 1000, monotonic=lambda: 1000)
    command = dict(request_id="work", expected_revision=0, action="start",
                   task={"source": "work", "id": "retained", "title": "Retained task"})
    with pytest.raises(ValidationError, match="Work"):
        timer.command(command)
    assert timer.state()["revision"] == 0
    assert timer.command({**command, "request_id": "library", "task": {
        "source": "library", "id": "task", "title": "Task"}})["status"] == "running"
