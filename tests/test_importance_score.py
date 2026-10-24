"""Numeric importance at the real durable boundaries, using synthetic roots only."""
import json
from copy import deepcopy
from datetime import datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from megamozg import cli
from megamozg.api import create_app
from megamozg.calendar import CalendarFeed
from megamozg.core import ConflictError, Store, ValidationError, decode_note, encode_note
from megamozg.dictation_service import DictationService
from megamozg.google_calendar_api import GoogleCalendarApi
from test_work_planning import op, planning, seed, upload_state


@pytest.mark.parametrize("score,legacy", [(0, "low"), (1, "low"), (2, "normal"),
                                         (3, "high"), (4, "high"), (5, "critical")])
def test_capture_update_replay_undo_and_custom_content(tmp_path, score, legacy):
    store = Store(tmp_path)
    note = store.capture("Original", kind="task", importance="normal", importance_score=score,
                         external_id="capture")
    assert (note["importance_score"], note["importance"]) == (score, legacy)
    assert store.capture("Original", kind="task", importance_score=score,
                         external_id="capture")["id"] == note["id"]
    note = store.update(note["id"], {"body": "# Custom heading\n\nKeep exactly."}, note["version"])
    updated = store.update(note["id"], {"importance_score": 5-score}, note["version"])
    assert updated["body"] == note["body"]
    with pytest.raises(ConflictError):
        store.update(note["id"], {"importance_score": score}, note["version"])
    restored = store.undo(store.history()[0]["id"])
    assert (restored["importance_score"], restored["body"]) == (score, note["body"])


@pytest.mark.parametrize("value", [True, False, -1, 6, 1.0, "3", [], {}])
def test_strict_numeric_validation(tmp_path, value):
    store = Store(tmp_path)
    with pytest.raises(ValidationError):
        store.capture("Bad", kind="task", importance_score=value)
    note = store.capture("Good", kind="task")
    with pytest.raises(ValidationError):
        store.update(note["id"], {"importance_score": value}, note["version"])
    assert store.list_notes()[0]["version"] == note["version"]


@pytest.mark.parametrize("legacy,score", [("low", 1), ("normal", 2), ("high", 4), ("critical", 5)])
def test_old_markdown_and_old_capture_receipt_are_compatible(tmp_path, legacy, score):
    store = Store(tmp_path)
    note = store.capture("Legacy", kind="task", importance=legacy, external_id="legacy")
    path = store.vault / note["path"]
    meta, body = decode_note(path.read_text(encoding="utf-8"))
    meta.pop("importance_score")
    path.write_text(encode_note(meta, body), encoding="utf-8")
    before = path.read_bytes()
    with store._db() as db:
        row = db.execute("SELECT payload FROM captures WHERE external_id='legacy'").fetchone()
        payload = json.loads(row[0])
        payload.pop("importance_score")
        db.execute("UPDATE captures SET payload=? WHERE external_id='legacy'", (json.dumps(payload),))
    retry = store.capture("Legacy", kind="task", importance=legacy, external_id="legacy")
    assert retry["importance_score"] == score
    assert path.read_bytes() == before
    updated = store.update(retry["id"], {"importance": "high"}, retry["version"])
    assert updated["importance_score"] == 4
    assert store.dashboard()["warnings"] == []
    with pytest.raises(ConflictError):
        store.capture("Legacy", kind="task", importance_score=0, external_id="legacy")


def test_score_five_survives_kind_change_and_legacy_is_legal(tmp_path):
    store = Store(tmp_path)
    note = store.capture("Idea", kind="idea", importance_score=5)
    assert (note["importance_score"], note["importance"]) == (5, "high")
    note = store.update(note["id"], {"kind": "task", "importance_score": 5}, note["version"])
    assert (note["importance_score"], note["importance"]) == (5, "critical")


def test_importance_update_recovers_after_journaled_crash(tmp_path, monkeypatch):
    store = Store(tmp_path)
    note = store.capture("Recover", kind="task", importance_score=0)
    def crash(*args):
        raise OSError("simulated interruption")
    monkeypatch.setattr(store, "_apply", crash)
    with pytest.raises(OSError):
        store.update(note["id"], {"importance_score": 5}, note["version"])
    recovered = Store(tmp_path)
    note = recovered.list_notes()[0]
    assert (note["importance_score"], note["importance"]) == (5, "critical")
    assert recovered.history()[0]["status"] == "applied"


def test_cli_capture_score(tmp_path, capsys):
    assert cli.main(["--root", str(tmp_path), "capture", "--text", "Task", "--kind", "task",
                     "--importance", "high", "--importance-score", "0"]) == 0
    note = json.loads(capsys.readouterr().out)
    assert (note["importance_score"], note["importance"]) == (0, "low")


def api_client(root):
    client = TestClient(create_app(root), base_url="http://127.0.0.1:8765")
    return client, {"Authorization": "Bearer " + Store(root).token()}


def test_capture_api_roundtrip_and_rejects_invalid_score(tmp_path):
    client, headers = api_client(tmp_path)
    note = client.post("/api/captures", headers=headers,
                       json={"text": "API task", "kind": "task", "importance_score": 0}).json()
    assert note["importance_score"] == 0
    response = client.patch(f"/api/notes/{note['id']}", headers=headers,
                            json={"expected_version": note["version"], "patch": {"importance_score": 5}})
    assert response.status_code == 200 and response.json()["importance"] == "critical"
    for invalid in [True, "2", 6, 1.0]:
        assert client.post("/api/captures", headers=headers,
                           json={"text": "Invalid", "kind": "task", "importance_score": invalid}).status_code == 422
    assert client.post("/api/captures", headers=headers,
                       json={"text": "Invalid kind", "kind": [], "importance_score": 5}).status_code == 422


def test_dictation_zero_preview_apply_replay_and_invalid(tmp_path):
    store = Store(tmp_path)
    service = DictationService(store, CalendarFeed(tmp_path), GoogleCalendarApi(tmp_path))
    overrides = {"kind": "task", "importance_score": 0}
    preview = service.preview("Разобрать документы", overrides=overrides)
    assert preview["plan"]["importance_score"] == 0
    assert "importance_score" not in service.preview("Разобрать документы")["plan"]
    result = service.apply("Разобрать документы", external_id="dictation", overrides=overrides)
    assert result["note"]["importance_score"] == 0
    assert service.apply("Разобрать документы", external_id="dictation", overrides=overrides) == result
    with pytest.raises(ConflictError):
        service.apply("Разобрать документы", external_id="dictation", overrides={**overrides, "importance_score": 5})
    for invalid in [True, -1, 6, "0", None, 1.0]:
        with pytest.raises(ValidationError):
            service.preview("Разобрать документы", overrides={**overrides, "importance_score": invalid})
    for invalid in [False, 0.0]:
        with pytest.raises(ValidationError):
            service.apply("Разобрать документы", external_id="dictation",
                          overrides={**overrides, "importance_score": invalid})


def strip_importance(document):
    document = deepcopy(document)
    for row in document["tasks"] + document["result"]["tasks"] + document["result"]["task_pool"]:
        row.pop("importance_score", None)
    return document


def test_work_typed_importance_preserves_completed_plan_and_cas(tmp_path):
    work = seed(tmp_path)
    first = op(work, "import", "import", {"planning": planning()})
    assert all(task["importance_score"] == 0 for task in first["state"]["planning"]["tasks"])
    cube = first["state"]["planning"]["result"]["cubes"][0]
    done = op(work, "done", "cube_progress", {"cube_id": cube["id"], "percent": 100, "actual_minutes": 77})
    before = done["state"]["planning"]
    for score in range(6):
        command = {"request_id": f"importance-{score}", "expected_revision": work.read()["revision"],
                   "action": "task_importance", "data": {"task_id": cube["task_id"], "importance_score": score}}
        result = work.planning_operation(command)
        assert work.planning_operation(command) == result
        after = result["state"]["planning"]
        task = next(task for task in after["tasks"] if task["id"] == cube["task_id"])
        assert task["importance_score"] == score
        assert strip_importance(after) == strip_importance(before)
        with pytest.raises(ConflictError):
            work.planning_operation({**command, "request_id": f"stale-{score}"})
    for i, invalid in enumerate([True, False, -1, 6, "0", None, 1.0]):
        with pytest.raises(ValidationError):
            op(work, f"invalid-{i}", "task_importance", {"task_id": "a", "importance_score": invalid})
    with pytest.raises(ValidationError):
        op(work, "unknown", "task_importance", {"task_id": "missing", "importance_score": 2})
    # A generic client may round-trip projected defaults without gaining permission to edit planning.
    state = work.read()["state"]
    digest = upload_state(work, state)
    work.commit({"request_id": "roundtrip", "expected_revision": work.read()["revision"],
                 "state_blob": digest, "images": []})
    imported = op(work, "reimport", "import", {"planning": planning()})
    assert next(task for task in imported["state"]["planning"]["tasks"]
                if task["id"] == cube["task_id"])["importance_score"] == 5


def test_work_import_strict_score_and_api_typed_command(tmp_path):
    work = seed(tmp_path)
    for i, invalid in enumerate([True, 1.0, "2", -1, 6]):
        document = planning()
        document["tasks"][0]["importance_score"] = invalid
        with pytest.raises(ValidationError):
            op(work, f"invalid-import-{i}", "import", {"planning": document})
    snapshot = op(work, "import", "import", {"planning": planning()})
    client, headers = api_client(tmp_path)
    command = {"request_id": "api-importance", "expected_revision": snapshot["revision"],
               "action": "task_importance", "data": {"task_id": "a", "importance_score": 5}}
    result = client.post("/api/work/planning", headers=headers, json=command)
    assert result.status_code == 200
    assert result.json()["state"]["planning"]["tasks"][0]["importance_score"] == 5
    assert client.post("/api/work/planning", headers=headers, json=command).json() == result.json()


def test_monthly_approval_is_explicit_metadata_only_replay_and_cas(tmp_path):
    work = seed(tmp_path)
    initial = op(work, "import", "import", {"planning": planning()})
    assert "monthly_plan" not in initial["state"]["planning"]
    cube = initial["state"]["planning"]["result"]["cubes"][0]
    snapshot = op(work, "completed", "cube_progress", {"cube_id": cube["id"], "percent": 100,
                                                       "actual_minutes": 91})
    before = snapshot["state"]["planning"]
    command = {"request_id": "approve-month", "expected_revision": snapshot["revision"],
               "action": "monthly_plan_approve", "data": {"month": "2027-01", "task_ids": ["a", "b"]}}
    client, headers = api_client(tmp_path)
    response = client.post("/api/work/planning", headers=headers, json=command)
    assert response.status_code == 200
    result = response.json()
    assert client.post("/api/work/planning", headers=headers, json=command).json() == result
    after = deepcopy(result["state"]["planning"])
    record = after.pop("monthly_plan")
    assert after == before
    assert set(record) == {"id", "month", "status", "approved_at", "task_ids"}
    assert str(UUID(record["id"])) == record["id"]
    assert (record["month"], record["task_ids"], record["status"]) == ("2027-01", ["a", "b"], "approved")
    assert datetime.fromisoformat(record["approved_at"]).utcoffset() == timedelta(0)
    assert client.post("/api/work/planning", headers=headers,
                       json={**command, "request_id": "stale-month"}).status_code == 409
    assert client.post("/api/work/planning", headers=headers,
                       json={**command, "data": {"month": "2027-02", "task_ids": ["a"]}}).status_code == 409
    empty = op(work, "explicit-empty", "monthly_plan_approve", {"month": "2027-01", "task_ids": []})
    assert empty["state"]["planning"]["monthly_plan"]["task_ids"] == []
    assert empty["state"]["planning"]["monthly_plan"]["status"] == "approved"
    assert empty["state"]["planning"]["monthly_plan"]["id"] == record["id"]
    next_month = op(work, "next-month", "monthly_plan_approve", {"month": "2027-02", "task_ids": ["c"]})
    assert next_month["state"]["planning"]["monthly_plan"]["id"] != record["id"]


@pytest.mark.parametrize("data", [
    {"month": "2027-00", "task_ids": []}, {"month": "2027-13", "task_ids": []},
    {"month": "0000-01", "task_ids": []}, {"month": "2027-1", "task_ids": []},
    {"month": "2027-01-01", "task_ids": []}, {"month": True, "task_ids": []},
    {"month": "2027-01", "task_ids": ["a", "a"]}, {"month": "2027-01", "task_ids": ["unknown"]},
    {"month": "2027-01", "task_ids": [True]}, {"month": "2027-01", "task_ids": [[]]},
    {"month": "2027-01", "task_ids": "a"}, {"month": "2027-01", "task_ids": None},
    {"month": "2027-01", "task_ids": [], "synthetic_only": True},
    {"month": "2027-01", "task_ids": [], "approved_at": "2027-01-01T00:00:00Z"},
    {"month": "2027-01", "task_ids": [], "status": "approved"}, {"month": "2027-01"},
])
def test_monthly_approval_rejects_invalid_without_commit(tmp_path, data):
    work = seed(tmp_path)
    before = op(work, "import", "import", {"planning": planning()})
    with pytest.raises(ValidationError):
        op(work, "invalid", "monthly_plan_approve", data)
    assert work.read() == before


def test_monthly_approval_reimport_preserves_and_invalidates_removed_ids(tmp_path):
    work = seed(tmp_path)
    op(work, "import", "import", {"planning": planning()})
    approved = op(work, "approve", "monthly_plan_approve", {"month": "2027-01", "task_ids": ["a", "c"]})
    monthly = approved["state"]["planning"]["monthly_plan"]
    same = op(work, "reimport", "import", {"planning": planning()})
    assert same["state"]["planning"]["monthly_plan"] == monthly
    reduced = planning()
    reduced["tasks"] = [task for task in reduced["tasks"] if task["id"] != "c"]
    result = op(work, "remove-c", "import", {"planning": reduced})
    record = result["state"]["planning"]["monthly_plan"]
    assert record == {**monthly, "task_ids": ["a"], "status": "draft", "approved_at": None}
    reapplied = op(work, "reapprove", "monthly_plan_approve", {"month": "2027-01", "task_ids": ["a"]})
    assert reapplied["state"]["planning"]["monthly_plan"]["status"] == "approved"
    assert reapplied["state"]["planning"]["monthly_plan"]["id"] == monthly["id"]


def test_monthly_approval_cannot_be_faked_by_plan_import_or_commit(tmp_path):
    work = seed(tmp_path)
    document = planning()
    document["monthly_plan"] = {"id": "synthetic", "month": "2027-01", "status": "approved",
                                "approved_at": "2027-01-01T00:00:00Z", "task_ids": ["a"],
                                "synthetic_only": True}
    with pytest.raises(ValidationError):
        op(work, "fake-import", "import", {"planning": document})
    op(work, "import", "import", {"planning": planning()})
    snapshot = op(work, "approve", "monthly_plan_approve", {"month": "2027-01", "task_ids": ["a"]})
    state = deepcopy(snapshot["state"])
    state["planning"]["monthly_plan"]["task_ids"] = ["a", "b"]
    digest = upload_state(work, state)
    with pytest.raises(ValidationError):
        work.commit({"request_id": "fake-commit", "expected_revision": snapshot["revision"],
                     "state_blob": digest, "images": []})
    with pytest.raises(ValidationError):
        op(work, "fake-changed-import", "import", {"planning": state["planning"]})
    assert work.read() == snapshot


@pytest.mark.parametrize("patch", [
    {"task_ids": ["missing"]}, {"task_ids": ["a", "a"]}, {"approved_at": "yesterday"},
    {"approved_at": "2027-01-01T00:00:00"}, {"approved_at": "2027-01-01T00:00:00+03:00"},
    {"id": "fake"}, {"month": "2027-13"}, {"status": "approved_synthetic"}, {"synthetic_only": True},
])
def test_monthly_metadata_validation_rejects_fake_restore_shape(tmp_path, patch):
    work = seed(tmp_path)
    op(work, "import", "import", {"planning": planning()})
    snapshot = op(work, "approve", "monthly_plan_approve", {"month": "2027-01", "task_ids": ["a"]})
    state = deepcopy(snapshot["state"])
    state["planning"]["monthly_plan"].update(patch)
    digest = upload_state(work, state)
    with pytest.raises(ValidationError):
        work.commit({"request_id": "invalid-restore", "expected_revision": snapshot["revision"],
                     "state_blob": digest, "images": [], "restore_import": True})
    assert work.read() == snapshot
