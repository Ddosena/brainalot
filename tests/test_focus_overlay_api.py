from fastapi.testclient import TestClient

from megamozg.api import create_app
from megamozg.core import Store


def test_focus_overlay_requires_auth_and_passes_valid_state(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("megamozg.api.focus_overlay.update",
                        lambda state, visible, color: calls.append((state, visible, color)))
    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765")
    state = {"durationMs": 1_500_000, "elapsedMs": 250, "startedAt": 1_000_000,
             "status": "running"}
    assert client.post("/api/focus/overlay", json={"state": state, "visible": True}).status_code == 401
    auth = {"Authorization": "Bearer " + Store(tmp_path).token()}
    result = client.post("/api/focus/overlay", json={"state": state, "visible": True}, headers=auth)
    assert result.status_code == 200 and result.json() == {"ok": True, "rendererVersion": 2}
    assert calls == [(state, True, "fire")]
    measured = {**state, "panelWidthDip": 480, "panelMeasuredAt": 1_000_100}
    assert client.post("/api/focus/overlay", json={"state": measured, "visible": False}, headers=auth).status_code == 200
    assert calls[-1] == (measured, False, "fire")


def test_focus_overlay_passes_the_chosen_neon_color(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("megamozg.api.focus_overlay.update",
                        lambda state, visible, color: calls.append(color))
    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765")
    auth = {"Authorization": "Bearer " + Store(tmp_path).token()}
    state = {"durationMs": 1_500_000, "elapsedMs": 0, "startedAt": 1_000_000, "status": "running"}
    for color in ["cyan", "pink", "violet", "lime", "amber", "fire"]:
        body = {"state": state, "visible": True, "color": color}
        assert client.post("/api/focus/overlay", json=body, headers=auth).status_code == 200
    assert calls == ["cyan", "pink", "violet", "lime", "amber", "fire"]


def test_focus_overlay_rejects_invalid_state_without_drawing(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("megamozg.api.focus_overlay.update",
                        lambda state, visible, color: calls.append((state, visible, color)))
    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765")
    auth = {"Authorization": "Bearer " + Store(tmp_path).token()}
    state = {"durationMs": 1_500_000, "elapsedMs": 0, "startedAt": 1_000_000,
             "status": "running"}
    for bad in [
        {"state": state, "visible": "yes"},
        {"state": {**state, "durationMs": -1}, "visible": True},
        {"state": {**state, "status": []}, "visible": True},
        {"state": {**state, "startedAt": None}, "visible": True},
        {"state": {**state, "panelWidthDip": 480}, "visible": True},
        {"state": {**state, "panelWidthDip": 9000, "panelMeasuredAt": 1_000_100}, "visible": True},
        {"state": state, "visible": True, "color": "rainbow"},
        {"state": state, "visible": True, "color": 7},
    ]:
        assert client.post("/api/focus/overlay", json=bad, headers=auth).status_code == 422
    assert calls == []
