"""Production HTTP/CLI entrypoints, only synthetic local data."""
import base64
import hashlib
import json
from copy import deepcopy

from fastapi.testclient import TestClient

from megamozg.api import create_app
from megamozg.core import Store
from megamozg.cli import main
from megamozg import features
from test_work_period import synthetic_state, synthetic_store


def client(tmp_path, monkeypatch, initialized=True):
    monkeypatch.setenv("BRAINALOT_DISABLE_MEDIA", "1")
    monkeypatch.setattr(features, "WORK_ENABLED", True)
    if initialized:
        synthetic_store(tmp_path, synthetic_state())
    api = TestClient(create_app(tmp_path, port=43919), base_url="http://127.0.0.1:43919")
    return api, {"Authorization": "Bearer " + Store(tmp_path).token()}


def test_period_auth_context_cas_and_shared_state(tmp_path, monkeypatch):
    api, auth = client(tmp_path, monkeypatch)
    assert api.get("/api/work/period/model").status_code == 401
    assert api.get("/api/work/period/model", headers={**auth, "Origin": "https://invalid.example"}).status_code == 403
    before = api.get("/api/work/state", headers=auth).json()
    reply = api.post("/api/work/period/context", headers=auth, json={"request_id": "ctx", "expected_revision": before["revision"], "patch": {"cube_id": "geometry"}})
    assert reply.status_code == 200
    assert reply.json()["context"]["cube_id"] == "geometry"
    renamed = api.post("/api/work/period/command", headers=auth, json={"request_id": "rename", "expected_revision": reply.json()["revision"], "action": "cube_rename", "data": {"cube_id": "geometry", "title": "HTTP shared cube"}})
    assert renamed.status_code == 200
    assert next(c for c in renamed.json()["cubes"] if c["id"] == "geometry")["title"] == "HTTP shared cube"
    stale = api.post("/api/work/period/context", headers=auth, json={"request_id": "old", "expected_revision": before["revision"], "patch": {"cube_id": None}})
    assert stale.status_code == 409
    after = api.get("/api/work/period/snapshot", headers=auth).json()
    a, b = deepcopy(before["state"]), deepcopy(after["state"]);a.pop("period_planner", None);b.pop("period_planner", None)
    assert a == b
    api.close()


def test_empty_period_no_fake_data_and_work_feature_guard(tmp_path, monkeypatch):
    api, auth = client(tmp_path, monkeypatch, False)
    model = api.get("/api/work/period/model", headers=auth).json()
    assert model["initialized"] is False and model["projects"] == [] and model["cubes"] == []
    assert model["provenance"]["training_copy"] is False
    api.close()
    monkeypatch.setattr(features, "WORK_ENABLED", False)
    disabled = TestClient(create_app(tmp_path, port=43919), base_url="http://127.0.0.1:43919")
    assert disabled.get("/api/work/period/model", headers=auth).status_code == 503
    disabled.close()


def test_uploaded_adoption_and_installed_cli_use_same_store(tmp_path, monkeypatch, capsys):
    api, auth = client(tmp_path, monkeypatch)
    before = api.get("/api/work/period/snapshot", headers=auth).json()
    packet = {"schema": "work-period-adopt-v1", "namespace": {"periods": {"a": {"start": "2027-01-05", "end": "2027-01-09"}}}, "source_revision": 7, "source_hash": "1" * 64}
    raw = json.dumps(packet).encode();digest = hashlib.sha256(raw).hexdigest()
    assert api.post(f"/api/work/blobs/{digest}/chunks", headers=auth, json={"index": 0, "total": 1, "content_base64": base64.b64encode(raw).decode()}).status_code == 200
    body = {"request_id": "adopt-http", "expected_revision": before["revision"], "source_blob": digest}
    first = api.post("/api/work/period/adopt", headers=auth, json=body)
    assert first.status_code == 200, first.text
    assert api.post("/api/work/period/adopt", headers=auth, json=body).json() == first.json()
    assert main(["--root", str(tmp_path), "work", "period", "context"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["revision"] == first.json()["revision"] and result["provenance"]["training_copy"] is False
    api.close()


def test_existing_day_endpoint_shares_period_schedule_and_keeps_raw_snapshot(tmp_path, monkeypatch):
    api, auth = client(tmp_path, monkeypatch)
    before = api.get('/api/work/state', headers=auth).json()
    assert api.get('/api/work/period/day').status_code == 401
    chosen = api.post('/api/work/period/command', headers=auth, json={
        'request_id': 'plan-select', 'expected_revision': before['revision'],
        'action': 'cube_select', 'data': {'cube_id': 'geometry', 'date': '2027-01-06', 'start': '15:00'}})
    assert chosen.status_code == 200, chosen.text
    projected = api.get('/api/work/period/day', headers=auth).json()
    cube = next(c for c in projected['planning']['result']['constructor']['cubes'] if c['id'] == 'geometry')
    assert cube['selected_date'] == '2027-01-06' and cube['start'] == '15:00'
    returned = api.post('/api/work/planning', headers=auth, json={
        'request_id': 'day-return', 'expected_revision': chosen.json()['revision'],
        'action': 'constructor_return', 'data': {'cube_id': 'geometry'}})
    assert returned.status_code == 200, returned.text
    assert returned.json()['state']['planning'] == before['state']['planning']
    fresh = api.get('/api/work/period/model', headers=auth).json()
    assert next(c for c in fresh['cubes'] if c['id'] == 'geometry')['selected_date'] is None
    api.close()
