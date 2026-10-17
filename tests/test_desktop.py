"""Desktop host contracts without launching a GUI or touching the live service."""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
from types import SimpleNamespace
from urllib.error import HTTPError
import uuid

import pytest

from megamozg import app, desktop, desktop_smoke


def test_geometry_recovers_from_removed_and_scaled_display():
    screens = [SimpleNamespace(x=0, y=0, width=1920, height=1080),
               SimpleNamespace(x=-1600, y=0, width=1600, height=900)]
    assert desktop.clamp_geometry({"x": -9000, "y": 600, "width": 370, "height": 230},
                                  screens, (370, 230)) == \
        {"x": -1600, "y": 600, "width": 370, "height": 230}
    assert desktop.clamp_geometry({"x": 9000, "y": 9000, "width": 3000, "height": 2000},
                                  screens, (370, 90)) == \
        {"x": 0, "y": 0, "width": 1920, "height": 1080}


@pytest.mark.parametrize("path,view", [("panel.html", "home"),
                                       ("panel.html?library-window=1", "library"),
                                       ("/desktop/work.html", "work"),
                                       ("capture.html", "capture")])
def test_internal_view(path, view):
    assert desktop.internal_view(path) == view


@pytest.mark.parametrize("path", ["https://example.com", "//example.com/work.html",
                                        "../../private", "api-token", "file:///secret"])
def test_internal_view_rejects_other_navigation(path):
    with pytest.raises(ValueError):
        desktop.internal_view(path)


def test_storage_roundtrip(tmp_path):
    state = desktop.DesktopState(tmp_path)
    assert state.set({"panel:theme": "dark", "panel:locale": "ru"})
    assert state.get("panel:theme") == {"panel:theme": "dark"}
    assert state.get({"unknown": 1}) == {"unknown": 1}
    assert desktop.DesktopState(tmp_path).get(["panel:locale"]) == {"panel:locale": "ru"}
    assert state.remove("panel:theme")
    assert "panel:theme" not in state.get()


def test_bridge_does_not_expose_host_object(tmp_path):
    bridge = desktop.DesktopBridge(desktop.DesktopHost(tmp_path, 8780, "test", lambda: True))
    assert all(callable(getattr(bridge, name)) for name in dir(bridge) if not name.startswith("_"))


def test_host_uses_persistent_webview_profile(tmp_path, monkeypatch):
    arguments = []
    fake = SimpleNamespace(settings={}, start=lambda callback, **kwargs: arguments.append(kwargs))
    monkeypatch.setitem(sys.modules, "webview", fake)
    host = desktop.DesktopHost(tmp_path, 8780, "test", lambda: True)
    monkeypatch.setattr(host, "create_windows", lambda _webview: None)
    host.run(background=True)
    assert arguments == [{"gui": "edgechromium", "private_mode": False,
                          "storage_path": str(tmp_path / "state" / "desktop-webview")}]
    assert fake.settings["ALLOW_DOWNLOADS"] is True


def test_product_smoke_uses_same_persistent_profile(tmp_path, monkeypatch):
    arguments = []
    class Service:
        def __init__(self, root, port): self.root = root
        def start(self):
            (self.root / "state").mkdir(exist_ok=True)
            (self.root / "state" / "api-token").write_text("test", encoding="utf-8")
        def stop(self): pass
    class Host:
        def __init__(self, *_args): pass
        def create_windows(self, _webview): pass
    fake = SimpleNamespace(settings={}, start=lambda callback, **kwargs: arguments.append(kwargs))
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr(desktop_smoke, "port_free", lambda port: True)
    monkeypatch.setattr(desktop_smoke, "Service", Service)
    monkeypatch.setattr(desktop_smoke, "DesktopHost", Host)
    assert desktop_smoke.run(tmp_path) == 1  # fake renderer never invokes inspect
    assert arguments == [{"gui": "edgechromium", "private_mode": False,
                          "storage_path": str(tmp_path / "state" / "desktop-webview")}]


def test_app_restarts_on_the_first_pinned_port(tmp_path, monkeypatch):
    started = []
    class Service:
        def __init__(self, _root, port):
            self.port = port
        def start(self): started.append(self.port)
        def stop(self): pass
    monkeypatch.setattr(app, "Service", Service)
    monkeypatch.setattr(app, "pick_port", lambda: 8877)
    monkeypatch.setattr(app, "port_free", lambda port: port == 8877)
    application = app.App(tmp_path)
    application.start_service()
    assert json.loads((tmp_path / "state" / "desktop-port.json").read_text())["port"] == 8877
    monkeypatch.setattr(app, "pick_port", lambda: pytest.fail("must reuse saved origin"))
    application.restart_service()
    assert started == [8877, 8877]


def test_app_refuses_occupied_pinned_port(tmp_path, monkeypatch):
    app._remember_desktop_port(tmp_path, 8877)
    monkeypatch.setattr(app, "port_free", lambda _port: False)
    monkeypatch.setattr(app, "pick_port", lambda: pytest.fail("must not change origin"))
    with pytest.raises(RuntimeError, match="Порт Brainalot 8877 занят"):
        app.App(tmp_path).start_service()


def test_app_refuses_external_service_on_different_port(tmp_path, monkeypatch):
    app._remember_desktop_port(tmp_path, 8877)
    (tmp_path / "state" / "api-token").write_text("test-token", encoding="utf-8")
    dialogs = []
    monkeypatch.setattr(app.ctypes, "windll", SimpleNamespace(
        user32=SimpleNamespace(MessageBoxW=lambda *args: dialogs.append(args))), raising=False)
    monkeypatch.setattr(app, "_velopack_hooks", lambda: None)
    monkeypatch.setattr(app.paths, "default_root", lambda: tmp_path)
    monkeypatch.setattr(app.paths, "prepare_installed_root", lambda root: root)
    monkeypatch.setattr(app, "setup_logging", lambda root: None)
    monkeypatch.setattr(app, "_single_instance", lambda: True)
    monkeypatch.setattr(app, "read_runtime", lambda root: {"port": 8878})
    monkeypatch.setattr(app, "serves_root", lambda port, token: True)
    monkeypatch.setattr(app, "_desktop_service_ready", lambda port, token: pytest.fail("wrong port"))
    assert app.main([]) == 1
    assert "прежний порт 8877" in dialogs[0][1]


class Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class Window:
    def __init__(self, **kwargs):
        self.x, self.y = kwargs["x"], kwargs["y"]
        self.width, self.height = kwargs["width"], kwargs["height"]
        self.on_top = kwargs.get("on_top", False)
        self.events = SimpleNamespace(closing=Event(), moved=Event(), resized=Event(), before_show=Event())
        self.calls = []

    def hide(self): self.calls.append("hide")
    def show(self): self.calls.append("show")
    def restore(self): self.calls.append("restore")
    def destroy(self): self.calls.append("destroy")
    def evaluate_js(self, script):
        self.calls.append(("evaluate_js", script))
        return "evaluated"
    def load_url(self, value): self.calls.append(("url", value))
    def resize(self, width, height):
        self.width, self.height = width, height
        self.calls.append(("resize", width, height))
    def move(self, x, y):
        self.x, self.y = x, y


class Webview:
    screens = [SimpleNamespace(x=0, y=0, width=1920, height=1080)]
    def __init__(self): self.windows = []
    def create_window(self, _title, _url, **kwargs):
        window = Window(**kwargs)
        self.windows.append(window)
        return window


def test_window_modes_and_close_to_tray(tmp_path):
    host = desktop.DesktopHost(tmp_path, 8780, "test-token", lambda: True)
    host.create_windows(Webview())
    host.window_action("bar")
    assert host.mode == "bar" and host.widget.width == 370 and host.widget.height == 90
    host.window_action("card")
    assert host.mode == "card" and host.widget.height == 230
    host.window_action("topmost", False)
    assert host.widget.on_top is False
    host.open_page("work.html")
    assert host.mode == "full"
    assert ("url", "http://127.0.0.1:8780/desktop/desktop.html?view=work") in host.full.calls
    assert host._close_window(host.full, "full") is False
    assert host.mode == "hide" and "destroy" not in host.full.calls
    assert json.loads((tmp_path / "state" / "desktop-ui.json").read_text())["widget_mode"] == "card"


def test_late_bridge_callback_does_not_evaluate_after_destroy(tmp_path):
    host = desktop.DesktopHost(tmp_path, 8780, "test", lambda: True)
    host.create_windows(Webview())
    assert host.full.evaluate_js("before") == "evaluated"
    host.destroy()
    assert host.full.evaluate_js("late reply") is None
    assert host.widget.evaluate_js("late reply") is None
    assert ("evaluate_js", "late reply") not in host.full.calls
    assert ("evaluate_js", "late reply") not in host.widget.calls


def test_pause_on_quit_uses_revision_and_bearer(monkeypatch):
    requests = []
    class Response:
        def __init__(self, payload): self.payload = payload
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self, *_): return json.dumps(self.payload).encode()
    def request(req, timeout):
        requests.append(req)
        if len(requests) == 1:
            return Response({"revision": 7, "status": "running"})
        return Response({"revision": 8, "status": "paused"})
    monkeypatch.setattr(desktop, "urlopen", request)
    desktop.pause_on_quit(8780, "test-token")
    assert len(requests) == 2
    assert requests[0].get_header("Authorization") == "Bearer test-token"
    payload = json.loads(requests[1].data)
    uuid.UUID(payload.pop("request_id"))
    assert payload == {"expected_revision": 7, "action": "pause"}


def test_pause_conflict_is_not_retried(monkeypatch):
    calls = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self, *_): return b'{"revision":7,"status":"running"}'
    def request(req, timeout):
        calls.append(req)
        if len(calls) > 1:
            raise HTTPError(req.full_url, 409, "Conflict", {}, None)
        return Response()
    monkeypatch.setattr(desktop, "urlopen", request)
    with pytest.raises(RuntimeError, match="Таймер изменился"):
        desktop.pause_on_quit(8780, "test-token")
    assert len(calls) == 2


def test_old_supervised_service_is_not_attached():
    class OldService(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(404 if self.path.startswith("/api/time") else 200)
            self.end_headers()
            self.wfile.write(b'{}')
        def log_message(self, *_): pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), OldService)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert not app._desktop_service_ready(server.server_port, "test-token")
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
