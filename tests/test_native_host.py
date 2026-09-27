import io
import json
import struct

from megamozg import native_host


NETSTAT = """
  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       1004
  TCP    127.0.0.1:8765         0.0.0.0:0              LISTENING       25940
  TCP    127.0.0.1:8765         127.0.0.1:50000        ESTABLISHED     25940
  TCP    127.0.0.1:87650        0.0.0.0:0              LISTENING       7
"""


def frame(payload: dict) -> io.BytesIO:
    data = json.dumps(payload).encode()
    return io.BytesIO(struct.pack("<I", len(data)) + data)


def test_finds_only_the_listener_of_the_service_port():
    assert native_host.listener_pids(NETSTAT) == [25940]


def test_message_framing_round_trip():
    assert native_host.read_message(frame({"action": "status"})) == {"action": "status"}
    out = io.BytesIO()
    native_host.write_message(out, {"ok": True})
    assert struct.unpack("<I", out.getvalue()[:4])[0] == len(out.getvalue()) - 4


def test_oversized_or_truncated_messages_are_refused():
    assert native_host.read_message(io.BytesIO(struct.pack("<I", 10**6) + b"x")) is None
    assert native_host.read_message(io.BytesIO(b"\x01")) is None


def test_unknown_actions_are_refused_without_touching_processes(monkeypatch, tmp_path):
    monkeypatch.setattr(native_host, "_run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    assert native_host.handle({"action": "format-disk"}, tmp_path)["ok"] is False
    assert native_host.handle("start", tmp_path)["ok"] is False


def test_start_does_nothing_when_the_service_already_answers(monkeypatch, tmp_path):
    monkeypatch.setattr(native_host, "running", lambda root: {"port": 8770})
    monkeypatch.setattr(native_host, "stop_service", lambda root: (_ for _ in ()).throw(AssertionError("must not stop")))
    assert native_host.handle({"action": "start"}, tmp_path)["running"] is True


def test_hello_hands_out_the_real_port_and_the_token(monkeypatch, tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "api-token").write_text("secret\n", encoding="utf-8")
    monkeypatch.setattr(native_host, "running", lambda root: {"port": 8771, "version": "9.9"})
    answer = native_host.handle({"action": "hello"}, tmp_path)
    assert (answer["ok"], answer["port"], answer["token"], answer["version"]) == (True, 8771, "secret", "9.9")
    # Other actions never carry the token.
    assert "token" not in native_host.handle({"action": "status"}, tmp_path)


def test_hello_starts_a_stopped_service(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(native_host, "running", lambda root: None)
    monkeypatch.setattr(native_host, "stop_service", lambda root: calls.append("stop"))
    monkeypatch.setattr(native_host, "start_service", lambda root: calls.append("start") or {"port": 8765})
    answer = native_host.handle({"action": "hello"}, tmp_path)
    assert answer["ok"] and answer["port"] == 8765 and calls == ["stop", "start"]


def test_restart_stops_then_starts_and_reports_failures(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(native_host, "running", lambda root: {"port": 8765})
    monkeypatch.setattr(native_host, "stop_service", lambda root: calls.append("stop"))
    monkeypatch.setattr(native_host, "start_service", lambda root: calls.append("start") or {"port": 8765})
    assert native_host.handle({"action": "restart"}, tmp_path)["ok"] is True
    assert calls == ["stop", "start"]

    def broken(root):
        raise RuntimeError("no python")
    monkeypatch.setattr(native_host, "start_service", broken)
    result = native_host.handle({"action": "restart"}, tmp_path)
    assert result["ok"] is False and "no python" in result["error"]
