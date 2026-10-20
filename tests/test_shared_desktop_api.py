"""Real stores and HTTP contract, with isolated data (no Chrome/native claims)."""
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import sqlite3
import zipfile

import pytest
from fastapi.testclient import TestClient

from megamozg.api import create_app
from megamozg.browser_bridge import BrowserBridge
from megamozg.core import ConflictError, Store, ValidationError
from megamozg.shared_state import SharedState
from megamozg.work_store import CHUNK_SIZE, WorkStore
from megamozg.work_inbox import WorkInbox


def state(title="Task"):
    return {"version": 1, "projects": [{"id": "p", "name": "Project", "custom": {"retained": True}}],
            "items": [{"id": "t", "project": "p", "title": title, "notes": "Full notes", "groups": [], "custom": [1, 2]}],
            "boards": [{"id": "general", "items": [{"id": "i", "type": "image", "imageId": "picture"}], "view": {"z": 2}}],
            "view": {"board": "general", "days": 14}, "future_field": "preserved"}


def upload(work, raw):
    digest = hashlib.sha256(raw).hexdigest()
    chunks = [raw[i:i+CHUNK_SIZE] for i in range(0, len(raw), CHUNK_SIZE)]
    for index, chunk in enumerate(chunks):
        result = work.upload_chunk(digest, {"index": index, "total": len(chunks), "content_base64": base64.b64encode(chunk).decode()})
    assert result["complete"]
    return digest


def commit(work, document=None, revision=0, request_id="migration"):
    image = upload(work, b"image data" * 20000)
    blob = upload(work, json.dumps(document or state()).encode())
    command = dict(request_id=request_id, expected_revision=revision, state_blob=blob,
                   images=[{"id": "picture", "blob": image, "mime": "image/png"}])
    return work.commit(command), command


def test_full_work_retry_conflict_restore_and_agent_snapshot(tmp_path):
    work = WorkStore(tmp_path)
    migrated, command = commit(work)
    assert migrated["state"] == state()
    assert work.commit(command) == migrated
    assert work.image("picture")[0] == b"image data" * 20000
    with pytest.raises(ConflictError):
        work.commit({**command, "expected_revision": 1})
    with pytest.raises(ConflictError):
        work.commit({**command, "request_id": "other"})
    commit(work, state("Renamed on desktop"), 1, "edit")
    inbox = WorkInbox(tmp_path)
    assert inbox.snapshot_for_agent()["items"][0]["title"] == "Renamed on desktop"
    with pytest.raises(ConflictError):
        inbox.save_snapshot({"projects": [], "items": []})
    restored = work.restore({"request_id": "restore", "expected_revision": 2, "revision": 1})
    assert restored["revision"] == 3 and restored["state"] == state()
    assert len(work.revisions()["revisions"]) == 3


def test_interrupted_upload_never_switches_work_source(tmp_path):
    work = WorkStore(tmp_path)
    raw = b"x" * (CHUNK_SIZE + 20)
    digest = hashlib.sha256(raw).hexdigest()
    first = dict(index=0, total=2, content_base64=base64.b64encode(raw[:CHUNK_SIZE]).decode())
    assert not work.upload_chunk(digest, first)["complete"]
    assert work.upload_chunk(digest, first)["received"] == 1
    blob = upload(work, json.dumps(state()).encode())
    command = dict(request_id="m", expected_revision=0, state_blob=blob,
                   images=[dict(id="picture", blob=digest, mime="image/png")])
    with pytest.raises(ValidationError):
        work.commit(command)
    assert work.read()["initialized"] is False
    # A new process resumes the same upload, then atomically activates everything.
    work = WorkStore(tmp_path)
    work.upload_chunk(digest, dict(index=1, total=2, content_base64=base64.b64encode(raw[CHUNK_SIZE:]).decode()))
    assert work.commit(command)["initialized"]
    assert work.image("picture")[0] == raw


def test_missing_image_bad_hash_and_duplicate_ids_do_not_commit(tmp_path):
    work = WorkStore(tmp_path)
    with pytest.raises(ValidationError):
        work.upload_chunk("0" * 64, dict(index=0, total=1, content_base64="eA=="))
    blob = upload(work, json.dumps(state()).encode())
    with pytest.raises(ValidationError):
        work.commit(dict(request_id="bad", expected_revision=0, state_blob=blob, images=[]))
    document = state()
    document["items"].append(document["items"][0])
    with pytest.raises(ValidationError):
        commit(work, document)
    assert work.read()["revision"] == 0


def test_two_work_clients_cannot_overwrite_each_other(tmp_path):
    work = WorkStore(tmp_path)
    _, command = commit(work)
    def edit(key):
        try:
            return WorkStore(tmp_path).commit({**command, "request_id": key, "expected_revision": 1})["revision"]
        except ConflictError:
            return "conflict"
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(edit, ["one", "two"]))
    assert sorted(map(str, results)) == ["2", "conflict"]


def test_recall_filters_pause_missing_and_reset(tmp_path):
    store, prefs = Store(tmp_path), SharedState(tmp_path)
    one = store.capture("First idea", kind="idea")
    two = store.capture("Second thought", kind="thought")
    prefs.set_recall(dict(request_id="pause", expected_revision=0,
                         patch={"paused": True, "selected_id": one["id"], "kinds": ["idea"]}))
    dashboard = Store(tmp_path).dashboard()
    assert dashboard["resurface"]["id"] == one["id"]
    assert [n["id"] for n in dashboard["resurface_candidates"]] == [one["id"]]
    prefs.set_recall(dict(request_id="filter", expected_revision=1, patch={"kinds": ["thought"]}))
    dashboard = store.dashboard()
    assert dashboard["resurface"] is None and dashboard["recall"]["paused"]
    assert dashboard["recall"]["selection_missing"]
    prefs.set_recall(dict(request_id="manual", expected_revision=2, patch={"selected_id": two["id"]}))
    assert store.dashboard()["resurface"]["id"] == two["id"]
    store.delete(two["id"], two["version"])
    assert store.dashboard()["resurface"] is None
    prefs.set_recall(dict(request_id="reset", expected_revision=3, reset=True))
    assert store.dashboard()["resurface"]["id"] == one["id"]
    assert not prefs.recall()["paused"]


def test_browser_at_most_once_and_uncertain_result(tmp_path, monkeypatch):
    monkeypatch.setattr("megamozg.features.WORK_ENABLED", True)
    now = [100000]
    bridge = BrowserBridge(tmp_path, clock=lambda: now[0])
    command = dict(request_id="open", action="open-workspace", payload={"id": "t"})
    with pytest.raises(ConflictError):
        bridge.submit(command)
    bridge.poll("browser-a")
    assert bridge.submit(command)["status"] == "pending"
    assert len(bridge.poll("browser-a")["commands"]) == 1
    assert bridge.poll("browser-b")["commands"] == []
    now[0] += 31000
    assert bridge.get("open")["status"] == "uncertain"
    assert bridge.poll("browser-b")["commands"] == []
    with pytest.raises(ConflictError):
        bridge.complete("open", {"client_id": "browser-b", "result": True})
    assert bridge.complete("open", {"client_id": "browser-a", "result": True})["status"] == "done"
    assert bridge.submit(command)["status"] == "done"


def test_api_auth_static_cas_chunks_and_real_client_sharing(tmp_path):
    app = create_app(tmp_path)
    token = Store(tmp_path).token()
    with TestClient(app, base_url="http://127.0.0.1:8765") as desktop:
        assert desktop.get("/api/time/state").status_code == 401
        assert desktop.get("/desktop/panel.html").status_code == 200
        assert desktop.get("/desktop/tests/work-tabs.test.mjs").status_code == 404
        assert desktop.get("/desktop/manifest.json").status_code == 404
        assert desktop.get("/desktop/../state/token").status_code in (401, 404)
        headers = {"Authorization": "Bearer " + token}
        browser = TestClient(app, base_url="http://127.0.0.1:8765", headers={**headers, "Origin": "chrome-extension://"+"a"*32})
        desktop.headers.update(headers)
        before = browser.get("/api/time/state").json()
        start = dict(request_id="start", expected_revision=before["revision"], action="start")
        running = browser.post("/api/time/command", json=start).json()
        assert desktop.get("/api/time/state").json()["revision"] == running["revision"]
        total = desktop.get("/api/time/totals")
        assert total.status_code == 200
        assert total.json()["state"]["revision"] == running["revision"]
        assert len(total.json()["rows"]) == 1
        assert total.json()["rows"][0]["total_ms"] >= 0
        assert desktop.post("/api/time/command", json={"request_id": "pause", "expected_revision": running["revision"], "action": "pause"}).status_code == 200
        assert browser.get("/api/time/state").json()["status"] == "paused"
        assert browser.post("/api/time/command", json=start).json() == running
        assert browser.post("/api/time/command", json={**start, "request_id": "stale"}).status_code == 409
        patch = dict(request_id="recall", expected_revision=0, patch={"paused": True})
        assert browser.post("/api/recall", json=patch).status_code == 200
        assert desktop.get("/api/recall").json()["paused"]
        assert browser.get("/api/time/summary?day=2026-10-18&timezone=Bad/Zone").status_code == 422


def test_backup_contains_recoverable_new_stores_and_no_secrets(tmp_path):
    root = tmp_path / "source"
    store = Store(root)
    commit(WorkStore(root))
    SharedState(root).set_recall(dict(request_id="pause", expected_revision=0, patch={"paused": True}))
    from megamozg.time_tracking import TimeStore
    TimeStore(root)
    backup = store.backup(tmp_path / "backup.zip")
    with zipfile.ZipFile(backup) as archive:
        assert {"state/shared.sqlite3", "state/time.sqlite3", "state/journal.sqlite3"} <= set(archive.namelist())
        assert not any("token" in name for name in archive.namelist())
        archive.extractall(tmp_path / "restored")
    assert WorkStore(tmp_path / "restored").read()["state"] == state()
    assert SharedState(tmp_path / "restored").recall()["paused"]
    with sqlite3.connect(tmp_path / "restored/state/shared.sqlite3") as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_empty_untyped_legacy_blob_preserved(tmp_path):
    work = WorkStore(tmp_path)
    digest = hashlib.sha256(b"").hexdigest()
    assert work.upload_chunk(digest, dict(index=0, total=1, content_base64=""))["complete"]
    state_blob = upload(work, json.dumps(state()).encode())
    work.commit(dict(request_id="empty", expected_revision=0, state_blob=state_blob,
                     images=[dict(id="picture", blob=digest, mime="application/octet-stream")]))
    assert work.image("picture") == (b"", "application/octet-stream")


def test_malformed_input_and_windows_case_variants_are_rejected(tmp_path):
    store = Store(tmp_path)
    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765", raise_server_exceptions=False)
    headers = {"Authorization": "Bearer " + store.token(), "Content-Type": "application/json"}
    assert client.post("/api/recall", content='{"request_id":"nan","expected_revision":0,"patch":{"paused":NaN}}', headers=headers).status_code == 422
    assert client.get("/desktop/Tests/work-sync.test.mjs").status_code == 404
    work = WorkStore(tmp_path)
    malformed = state()
    malformed["boards"][0]["items"][0]["imageId"] = []
    with pytest.raises(ValidationError):
        commit(work, malformed)


def test_large_canonical_work_agent_snapshot_is_available(tmp_path):
    document = state()
    document["items"] = [{"id": str(i), "title": "x"*300, "notes": "n"*5000} for i in range(600)]
    commit(WorkStore(tmp_path), document)
    assert len(WorkInbox(tmp_path).snapshot_for_agent()["items"]) == 600


def test_restore_cli_boundary_rejects_overwrite_and_paths(tmp_path):
    from megamozg.backup_restore import restore_backup
    root = tmp_path / "data"
    store = Store(root)
    commit(WorkStore(root))
    archive = store.backup(tmp_path / "backup.zip")
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    assert WorkStore(restored).read()["state"] == state()
    with pytest.raises(ValidationError):
        restore_backup(archive, restored)
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as out:
        out.writestr("../escape", "no")
    with pytest.raises(ValidationError):
        restore_backup(bad, tmp_path / "bad-restore")
    assert not (tmp_path / "escape").exists()


def test_actual_browser_exporter_against_actual_api(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    project = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "tests/work_export_contract.mjs", sys.executable, str(tmp_path)],
                            cwd=project, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr + result.stdout
    report = json.loads(result.stdout.strip())
    assert report["verified"] and report["images"] == 3 and report["stateBytes"] > 262144


def test_cli_time_recall_and_restore_use_same_stores(tmp_path, capsys):
    from megamozg.cli import main
    root = str(tmp_path / "data")
    assert main(["--root", root, "time", "state"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "idle"
    assert main(["--root", root, "recall", "get"]) == 0
    assert json.loads(capsys.readouterr().out)["revision"] == 0

def test_shared_timer_preset_validation_cas_and_replay(tmp_path):
    shared = SharedState(tmp_path)
    command = {"request_id": "preset-40-10", "expected_revision": 0,
               "patch": {"time:preset": {"work_ms": 2_400_000, "break_ms": 600_000}}}
    result = shared.set_preferences(command)
    assert result["values"]["time:preset"] == {"work_ms": 2_400_000, "break_ms": 600_000}
    assert SharedState(tmp_path).set_preferences(command) == result
    with pytest.raises(ConflictError):
        shared.set_preferences({**command, "request_id": "other-client"})
    for preset in ({"work_ms": True, "break_ms": 600_000}, {"work_ms": 1, "break_ms": 600_000},
                   {"work_ms": 2_400_000}, {"work_ms": 2_400_000, "break_ms": 600_000, "other": 1}):
        with pytest.raises(ValidationError):
            shared.set_preferences({"request_id": "invalid", "expected_revision": 1, "patch": {"time:preset": preset}})
    assert shared.preferences() == result


def test_shared_timer_color_preserves_palette_and_rejects_invalid_values(tmp_path):
    shared = SharedState(tmp_path)
    for revision, color in enumerate(("fire", "pink", "violet", "cyan", "lime", "amber")):
        command = {"request_id": f"color-{color}", "expected_revision": revision,
                   "patch": {"panel:focus-overlay-color": color}}
        result = shared.set_preferences(command)
        assert result["values"]["panel:focus-overlay-color"] == color
        assert SharedState(tmp_path).set_preferences(command) == result
    for value in (None, [], {}, 2, True, "#ffffff", "other"):
        with pytest.raises(ValidationError):
            shared.set_preferences({"request_id": "invalid", "expected_revision": 6,
                                    "patch": {"panel:focus-overlay-color": value}})
    assert shared.preferences()["revision"] == 6


def test_spotify_api_auth_validation_and_lifecycle_without_playback(tmp_path):
    class FakeSpotify:
        def __init__(self): self.calls, self.closed = [], False
        def snapshot(self): return {"connected": True, "title": "Synthetic track", "controls": {"next": True}}
        def command(self, action, value=None):
            self.calls.append((action, value))
            return {"ok": True, "state": self.snapshot()}
        def close(self): self.closed = True
    spotify = FakeSpotify()
    app = create_app(tmp_path, spotify_controller=spotify)
    auth = {"Authorization": "Bearer " + Store(tmp_path).token()}
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert client.get("/api/media/spotify").status_code == 401
        assert client.post("/api/media/spotify/command", json={"action": "next"}).status_code == 401
        assert client.get("/api/media/spotify", headers=auth).json()["title"] == "Synthetic track"
        for body in ({"action": []}, {"action": "unknown"}, {"action": "next", "value": 1},
                     {"action": "shuffle", "value": "true"}, {"action": "repeat", "value": []},
                     {"action": "volume"}, {"action": "volume", "value": True},
                     {"action": "volume", "value": "0.5"}, {"action": "volume", "value": []},
                     {"action": "volume", "value": -0.1}, {"action": "volume", "value": 1.1},
                     {"action": "volume", "value": 10**400}):
            assert client.post("/api/media/spotify/command", json=body, headers=auth).status_code == 422
        for nonfinite in ("NaN", "Infinity", "-Infinity"):
            assert client.post("/api/media/spotify/command",
                               content='{"action":"volume","value":' + nonfinite + '}',
                               headers={**auth, "Content-Type": "application/json"}).status_code == 422
        assert spotify.calls == []
        for body in ({"action": "next"}, {"action": "shuffle", "value": False},
                     {"action": "repeat", "value": "track"}, {"action": "volume", "value": 0},
                     {"action": "volume", "value": 0.64}, {"action": "volume", "value": 1}):
            assert client.post("/api/media/spotify/command", json=body, headers=auth).json()["ok"]
        assert spotify.calls == [("next", None), ("shuffle", False), ("repeat", "track"),
                                 ("volume", 0), ("volume", 0.64), ("volume", 1)]
    assert spotify.closed


def test_time_tasks_supplies_server_day_for_quick_capture(tmp_path):
    from datetime import date
    store = Store(tmp_path)
    auth = {"Authorization": "Bearer " + store.token()}
    app = create_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        catalog = client.get("/api/time/tasks", headers=auth).json()
        assert catalog["today"] == date.today().isoformat()
        response = client.post("/api/captures", headers=auth, json={
            "text": "Synthetic quick task", "kind": "task", "due": catalog["today"],
            "source": "desktop-widget", "external_id": "test-quick-task",
        })
        assert response.status_code == 200
        note = response.json()
        assert note["due"] == catalog["today"]
        assert note["id"] in {n["id"] for n in store.dashboard()["today"]}


def test_spotify_delivery_receipt_survives_duplicate_and_service_restart(tmp_path):
    class FakeSpotify:
        def __init__(self): self.calls = []
        def command(self, action, value=None):
            self.calls.append((action, value))
            return {"ok": True, "state": {"title": "Next synthetic track"}}
        def close(self): pass
    auth = {"Authorization": "Bearer " + Store(tmp_path).token()}
    command = {"request_id": "media-lost-response", "action": "next"}
    first = FakeSpotify()
    with TestClient(create_app(tmp_path, spotify_controller=first), base_url="http://127.0.0.1:8765") as client:
        result = client.post("/api/media/spotify/command", json=command, headers=auth).json()
        assert result["ok"]
        assert client.post("/api/media/spotify/command", json=command, headers=auth).json() == result
        assert client.post("/api/media/spotify/command", json={**command, "action": "previous"},
                           headers=auth).status_code == 409
        assert first.calls == [("next", None)]
    restarted = FakeSpotify()
    with TestClient(create_app(tmp_path, spotify_controller=restarted), base_url="http://127.0.0.1:8765") as client:
        assert client.post("/api/media/spotify/command", json=command, headers=auth).json() == result
        assert restarted.calls == []


def test_external_receipt_keeps_unknown_outcome_after_dispatch_failure(tmp_path):
    shared = SharedState(tmp_path)
    calls = []
    command = {"request_id": "media-interrupted", "action": "next"}
    def interrupted():
        calls.append("next")
        raise RuntimeError("response lost after side effect")
    with pytest.raises(RuntimeError):
        shared.external_command("spotify", command, interrupted)
    recovered = SharedState(tmp_path).external_command("spotify", command, interrupted)
    assert recovered["ok"] is False and recovered["pending"] is True
    assert calls == ["next"]


def test_external_receipt_concurrent_redelivery_does_not_repeat_side_effect(tmp_path):
    from threading import Event
    started, finish = Event(), Event()
    calls = []
    command = {"request_id": "media-overlap", "action": "next"}
    def delayed():
        calls.append("next")
        started.set()
        assert finish.wait(5)
        return {"ok": True}
    shared = SharedState(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(shared.external_command, "spotify", command, delayed)
        assert started.wait(5)
        try:
            duplicate = pool.submit(SharedState(tmp_path).external_command, "spotify", command, delayed)
            assert duplicate.result(timeout=2)["pending"] is True
        finally:
            finish.set()
        assert first.result(timeout=2) == {"ok": True}
    assert calls == ["next"]
