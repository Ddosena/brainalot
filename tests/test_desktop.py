"""Desktop host contracts without launching a GUI or touching the live service."""
from __future__ import annotations

import json
import queue
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


def test_storage_skips_disk_write_for_unchanged_values(tmp_path, monkeypatch):
    state = desktop.DesktopState(tmp_path)
    assert state.set({"panel:focus-timer": {"status": "idle", "elapsedMs": 0}, "flag": True})
    writes = []
    original = state.save
    monkeypatch.setattr(state, "save", lambda: (writes.append(1), original()))
    # The background re-sets the same snapshot every few seconds.
    assert state.set({"panel:focus-timer": {"elapsedMs": 0, "status": "idle"}})
    assert state.remove("missing")
    assert writes == []
    assert state.set({"flag": 1})  # JSON-distinct value is still persisted
    assert state.set({"panel:focus-timer": {"status": "running", "elapsedMs": 5}})
    assert state.remove("flag")
    assert writes == [1, 1, 1]
    assert desktop.DesktopState(tmp_path).get() == {"panel:focus-timer": {"status": "running", "elapsedMs": 5}}


def test_geometry_argtypes_do_not_leak_into_shared_user32():
    import ctypes
    from megamozg import desktop_geometry
    if not hasattr(ctypes, "WinDLL"):
        pytest.skip("Windows only")
    # pywebview's move() calls SetWindowPos(hwnd, None, x, y, None, None, flags):
    # c_int argtypes on the shared handle would make it raise ArgumentError.
    shared = ctypes.windll.user32.SetThreadDpiAwarenessContext
    before = shared.argtypes
    previous = desktop_geometry.enable_thread_dpi()
    if previous:
        desktop_geometry._user32().SetThreadDpiAwarenessContext(ctypes.c_void_p(previous))
    assert shared.argtypes == before
    assert desktop_geometry._user32().SetWindowPos is not ctypes.windll.user32.SetWindowPos


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

    def is_set(self): return True

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class Window:
    def __init__(self, **kwargs):
        self.options = kwargs
        self.x, self.y = kwargs["x"], kwargs["y"]
        self.width, self.height = kwargs["width"], kwargs["height"]
        self.on_top = kwargs.get("on_top", False)
        self.events = SimpleNamespace(closing=Event(), moved=Event(), resized=Event(), before_show=Event(), loaded=Event())
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


def test_window_modes_are_visible_topmost_and_native_close_exits(tmp_path, monkeypatch):
    quit_event = threading.Event()
    host = desktop.DesktopHost(tmp_path, 8780, "test-token", lambda: quit_event.set())
    monkeypatch.setattr(host, "_areas", lambda: [desktop.WorkArea(0, 0, 1920, 1040)])
    host.create_windows(Webview())
    assert host.full.on_top and host.widget.on_top
    assert host.widget.options["shadow"] is False  # DWM's default square fringe defeats rounded client regions.
    assert host.full.options.get("shadow", True) is True
    host.window_action("bar")
    assert host.mode == "bar" and host.widget.width == 84 and host.widget.height == 48
    assert host.widget.x == 1920-84
    host.window_action("card")
    assert host.mode == "card" and (host.widget.width, host.widget.height) == (360, 116)
    assert host.widget.x == 1920-360
    host.window_action("topmost", False)
    assert host.widget.on_top is True and host.full.on_top is True
    host.open_page("work.html")
    assert host.mode == "full"
    assert any(call[0] == "evaluate_js" and "brainalot-view" in call[1] for call in host.full.calls if isinstance(call, tuple))
    assert host._close_window(host.full, "full") is False
    assert quit_event.wait(2)
    host.window_action("hide")
    assert host.mode == "bar"
    host.destroy()


def test_old_saved_card_size_becomes_one_row_bar_at_same_place(tmp_path, monkeypatch):
    state = desktop.DesktopState(tmp_path)
    state.data["geometry"]["card"] = {"x": 100, "y": 200, "width": 388, "height": 154, "units": "physical", "dpi": 96}
    state.save()
    host = desktop.DesktopHost(tmp_path, 8780, "test", lambda: True)
    monkeypatch.setattr(host, "_areas", lambda: [desktop.WorkArea(0, 0, 1920, 1040)])
    rect, minimum = host._rect("card")
    assert rect == {"x": 100, "y": 200, "width": 360, "height": 116} and minimum == (360, 116)


def test_geometry_is_independent_debounced_and_flushed(tmp_path, monkeypatch):
    host = desktop.DesktopHost(tmp_path, 8780, "test", lambda: True)
    monkeypatch.setattr(host, "_areas", lambda: [desktop.WorkArea(-1600, 0, 1600, 860, "left")])
    host.create_windows(Webview())
    host.window_action("full")
    host.full.move(-1300, 55)
    host.full.resize(900, 600)
    host.window_action("card")
    host.widget.move(-1200, 100)
    host.window_action("bar")
    host.widget.move(-82, 200)
    host._remember_geometry(host.widget, "widget")
    host.window_action("full")
    assert (host.full.x, host.full.y, host.full.width, host.full.height) == (-1300, 55, 900, 600)
    host.destroy()
    saved = desktop.DesktopState(tmp_path).data
    assert set(saved["geometry"]) == {"full", "card", "bar"}
    assert saved["geometry"]["card"]["x"] == -1200
    assert all(value["units"] == "physical" for value in saved["geometry"].values())


def test_physical_workarea_dpi_and_topology_healing():
    from megamozg.desktop_geometry import WorkArea, fit_geometry
    areas = [WorkArea(-2560, -200, 2560, 1400, "left", 144), WorkArea(0, 0, 1920, 1040, "main", 96)]
    assert fit_geometry({"x":-2600,"y":1500,"width":123,"height":93},areas,(82,62),side="left") == {"x":-2560,"y":1107,"width":123,"height":93}
    healed = fit_geometry({"x":-2500,"y":-100,"width":1800,"height":1000},areas[1:],(1120,760))
    assert healed == {"x":0,"y":0,"width":1800,"height":1000}


def test_drag_threshold_and_side_snap_do_not_command_time(tmp_path, monkeypatch):
    host = desktop.DesktopHost(tmp_path,8780,"test",lambda: True)
    monkeypatch.setattr(host,"_areas",lambda:[desktop.WorkArea(-1600,0,1600,860,"left")])
    host.create_windows(Webview()); host.window_action("bar")
    cursor = [(-40,100)]
    monkeypatch.setattr(desktop,"cursor_position",lambda:cursor[0])
    host.window_action("gesture_begin")
    cursor[0]=(-42,101)
    assert host.window_action("gesture_end")["dragged"] is False
    host.window_action("gesture_begin")
    cursor[0]=(-1500,150)
    assert host.window_action("gesture_end")["dragged"] is True
    assert host.widget.x == -1600 and host.side == "left"
    host.destroy()


@pytest.mark.parametrize("side,position", [("left", (0, 300)), ("right", (916, 300)),
                                         ("top", (420, 0)), ("bottom", (420, 752))])
def test_four_edge_snap_and_inward_card_preserve_position(tmp_path, monkeypatch, side, position):
    host = desktop.DesktopHost(tmp_path, 8780, "test", lambda: True)
    monkeypatch.setattr(host, "_areas", lambda: [desktop.WorkArea(0, 0, 1000, 800)])
    host.create_windows(Webview()); host.window_action("bar")
    host.widget.move(*position)
    cursor = [(100, 100)]
    monkeypatch.setattr(desktop, "cursor_position", lambda: cursor[0])
    host.window_action("gesture_begin")
    cursor[0] = (107, 100)
    host.window_action("gesture_end")
    assert host.side == side
    anchor = desktop.native_rect(host.widget)
    host.window_action("card")
    card = desktop.native_rect(host.widget)
    assert 0 <= card["x"] <= 1000-card["width"] and 0 <= card["y"] <= 800-card["height"]
    if side in {"top", "bottom"}: assert card["x"] == anchor["x"]
    if side in {"left", "right"}: assert card["y"] == anchor["y"]
    if side == "bottom": assert card["y"]+card["height"] == 800
    if side == "right": assert card["x"]+card["width"] == 1000
    host.destroy()
    assert desktop.DesktopHost(tmp_path, 8780, "test", lambda: True).side == side


@pytest.mark.parametrize("side,y,above", [("top", 0, False), ("bottom", 736, True),
                                       ("right", 650, True), ("left", 100, False)])
def test_popup_geometry_does_not_persist_temporary_height(tmp_path, monkeypatch, side, y, above):
    host = desktop.DesktopHost(tmp_path, 8780, "test", lambda: True)
    host.media_visible = False
    monkeypatch.setattr(host, "_areas", lambda: [desktop.WorkArea(0, 0, 1000, 800)])
    host.create_windows(Webview()); host.window_action("card")
    host.side = side
    host.widget.move(200, y)
    base = desktop.native_rect(host.widget)
    opened = host.window_action("widget_popup", {"open": True, "height": 360})
    assert opened == {"ok": True, "height": 360, "popup_above": above}
    host._remember_geometry(host.widget, "widget")
    assert host.state.data["geometry"]["card"]["height"] == 64
    assert host.state.data["geometry"]["card"]["y"] == y
    rect = desktop.native_rect(host.widget)
    assert rect["y"]+rect["height"] == y+64 if above else rect["y"] == y
    host.window_action("widget_popup", {"open": False})
    assert desktop.native_rect(host.widget) == base
    host.destroy()


def test_popup_limits_to_workarea_and_mode_switch_restores_base(tmp_path, monkeypatch):
    host = desktop.DesktopHost(tmp_path, 8780, "test", lambda: True)
    host.media_visible = False
    monkeypatch.setattr(host, "_areas", lambda: [desktop.WorkArea(-1400, -200, 1400, 900, dpi=144)])
    host.create_windows(Webview()); host.window_action("card")
    host.side = "bottom"; host.widget.move(-700, 604)
    result = host.window_action("widget_popup", {"open": True, "height": 5000})
    assert result == {"ok": True, "popup_above": True, "height": 600}
    assert desktop.native_rect(host.widget)["y"] == -200
    host.window_action("bar")
    assert host._popup_base is None
    assert host.state.data["geometry"]["card"]["height"] == 96
    assert host.state.data["geometry"]["card"]["y"] == 604
    assert (host.widget.width, host.widget.height) == (126, 72)
    host.destroy()


@pytest.mark.parametrize("dpi", [96,144])
@pytest.mark.parametrize("side", ["left","right","top","bottom"])
def test_widget_media_height_preserves_base_anchor_and_popup(tmp_path,monkeypatch,dpi,side):
    host=desktop.DesktopHost(tmp_path,8780,"test",lambda:True)
    area=desktop.WorkArea(-1000,-200,1000,900,dpi=dpi)
    monkeypatch.setattr(host,"_areas",lambda:[area])
    host.create_windows(Webview());host.window_action("card");host.side=side
    host.window_action("widget_media",{"visible":False})
    scale=dpi/96
    base={"x":-900,"y":-200 if side=="top" else 700-round(64*scale) if side=="bottom" else 0,
          "width":round(360*scale),"height":round(64*scale)}
    desktop.set_native_rect(host.widget,base)
    expanded=host.window_action("widget_media",{"visible":True})
    card=desktop.native_rect(host.widget)
    assert expanded["card_height"]==116 and card["height"]==round(116*scale)
    assert card["y"]+card["height"]==base["y"]+base["height"] if side=="bottom" else card["y"]==base["y"]
    host.window_action("widget_popup",{"open":True,"height":400})
    host.window_action("widget_media",{"visible":False})
    assert host._popup_base["height"]==round(64*scale)
    assert host.state.data["geometry"]["card"]["height"]==round(64*scale)
    host.window_action("widget_popup",{"open":False})
    assert desktop.native_rect(host.widget)==base
    host.window_action("bar")
    bar=desktop.native_rect(host.widget)
    host.window_action("widget_media",{"visible":True})
    assert desktop.native_rect(host.widget)==bar
    host.destroy()
    assert desktop.DesktopHost(tmp_path,8780,"test",lambda:True).media_visible is True


def test_widget_media_requires_boolean_and_bootstrap_returns_saved_visibility(tmp_path,monkeypatch):
    state=desktop.DesktopState(tmp_path);state.set({"desktop:spotify-visible":False})
    host=desktop.DesktopHost(tmp_path,8780,"test",lambda:True)
    monkeypatch.setattr(host,"_areas",lambda:[desktop.WorkArea(0,0,1920,1040)])
    host.create_windows(Webview())
    assert desktop.DesktopBridge(host).bootstrap()["media_visible"] is False
    assert host._widget_size("card")== (360,64)
    with pytest.raises(ValueError):host.window_action("widget_media",{"visible":"true"})
    host.destroy()


def test_full_window_minimum_and_bootstrap_system_double_click(tmp_path, monkeypatch):
    host = desktop.DesktopHost(tmp_path, 8780, "test", lambda: True)
    monkeypatch.setattr(host, "_areas", lambda: [desktop.WorkArea(0, 0, 1920, 1040)])
    host.create_windows(Webview())
    host.state.data["geometry"]["full"] = {"width": 1, "height": 1}
    assert host._rect("full")[0]["width"] == 300
    assert host._rect("full")[0]["height"] == 360
    settings = desktop.DesktopBridge(host).bootstrap()["double_click"]
    assert settings["interval_ms"] > 0 and settings["width"] > 0 and settings["height"] > 0
    host.destroy()


def test_compact_cpu_surface_routes_buttons_keyboard_and_passive_double_clicks(monkeypatch):
    """Exercise application routing without creating or synthesizing OS input."""
    surface = desktop.CompactSurface.__new__(desktop.CompactSurface)
    calls, scripts = [], []
    surface.host = SimpleNamespace(mode="card", _areas=lambda:[desktop.WorkArea(0,0,1920,1080,dpi=144)])
    def action(name):
        calls.append(name)
        if name in {"card","bar","full"}: surface.host.mode = name
        return {"ok":True,"dragged":False}
    surface.host.window_action = action
    surface.window = SimpleNamespace(x=10,y=10,width=540,height=96)
    surface.last_click = None; surface.focus_id = None; surface.closed = False
    surface._js = lambda script: scripts.append(script)
    surface._schedule = lambda *_args: None
    surface.boxes = [{"id":control,"x":index*30,"y":0,"w":28,"h":28}
                     for index,control in enumerate(("widget-task","widget-run","widget-more"))]
    surface._input = queue.Queue()
    def up(at, **fields):
        surface._input.put(("up",{"button":0,"id":None,"up_id":None,"moved":False,"x":10,"y":10,"at":at,**fields}))
    up(0); up(.2, x=12)  # Windows 4px double-click width at 150% DPI permits 3px.
    up(1,button=2); up(1.2,button=2)
    up(2,id="widget-run",up_id="widget-run")
    up(3,id="widget-more",up_id="widget-more",moved=True)
    surface._input.put(("key",{"key":"Tab","shift":False}))
    surface._input.put(("key",{"key":"Tab","shift":False}))
    surface._input.put(("key",{"key":"Return","shift":False}))
    surface._input.put(("close",None))
    monkeypatch.setattr(desktop,"double_click_settings",lambda:{"interval_ms":500,"width":4,"height":4})
    surface._inputs()
    assert calls.count("bar") == 1 and calls.count("full") == 1
    assert len([script for script in scripts if '"widget-run")?.click()' in script]) == 2
    assert not any('"widget-more")?.click()' in script for script in scripts)
    assert surface.focus_id == "widget-run"
    surface.boxes = [{"id":"widget-tab","x":0,"y":0,"w":84,"h":48}]
    assert surface._hit(10,10) is None  # A tab is passive for the mouse: single click must not expand.


def test_compact_surface_media_button_hitboxes_are_controls_and_keyboard_targets():
    surface=desktop.CompactSurface.__new__(desktop.CompactSurface)
    scripts=[]
    surface.boxes=[{"id":"widget-spotify-play_pause","x":12,"y":10,"w":28,"h":28}]
    surface.focus_id=None;surface.closed=False;surface._input=queue.Queue()
    surface._js=lambda script:scripts.append(script);surface._schedule=lambda *_:None
    assert surface._hit(20,20)=="widget-spotify-play_pause"
    surface._input.put(("key",{"key":"Tab","shift":False}))
    surface._input.put(("key",{"key":"Return","shift":False}))
    surface._input.put(("close",None));surface._inputs()
    assert any('"widget-spotify-play_pause")?.click()' in script for script in scripts)


def test_disabled_media_controls_block_drag_but_are_skipped_by_keyboard():
    surface=desktop.CompactSurface.__new__(desktop.CompactSurface)
    scripts=[]
    surface.boxes=[{"id":"widget-spotify-next","x":0,"y":0,"w":28,"h":28,"disabled":True},
                   {"id":"widget-run","x":30,"y":0,"w":28,"h":28}]
    surface.focus_id=None;surface.closed=False;surface._input=queue.Queue()
    surface._js=lambda script:scripts.append(script);surface._schedule=lambda *_:None
    assert surface._hit(10,10)=="widget-spotify-next"
    surface._activate_control('widget-spotify-next')
    assert scripts==[]
    surface._input.put(("key",{"key":"Tab","shift":False}))
    surface._input.put(("key",{"key":"Return","shift":False}))
    surface._input.put(("close",None));surface._inputs()
    assert surface.focus_id=='widget-run'
    assert any('"widget-run")?.click()' in script for script in scripts)


def test_quit_failure_keeps_current_visible_state(tmp_path, monkeypatch):
    host = desktop.DesktopHost(tmp_path,8780,"test",lambda: False)
    monkeypatch.setattr(host,"_areas",lambda:[desktop.WorkArea(0,0,1920,1040)])
    host.create_windows(Webview()); host.window_action("card")
    assert host.window_action("quit")["ok"] is False
    assert host.mode == "card" and not host._quitting
    host.destroy()


def test_update_pauses_and_saves_before_stopping_owned_service(tmp_path, monkeypatch):
    calls = []
    application = app.App(tmp_path)
    application.desktop = SimpleNamespace(port=8780, token="test", destroy=lambda:calls.append("save-and-close"))
    application.service = SimpleNamespace(stop=lambda:calls.append("stop-service"))
    application.update = (SimpleNamespace(apply_updates_and_restart=lambda info:calls.append("update")), "info")
    monkeypatch.setattr(desktop,"pause_on_quit",lambda *args:calls.append("pause"))
    application.apply_update()
    assert calls == ["pause","save-and-close","stop-service","update"]


def test_update_pause_failure_keeps_app_and_service_running(tmp_path, monkeypatch):
    calls = []
    application = app.App(tmp_path)
    application.desktop = SimpleNamespace(port=8780, token="test", destroy=lambda:calls.append("close"))
    application.service = SimpleNamespace(stop=lambda:calls.append("stop"))
    application.update = (SimpleNamespace(apply_updates_and_restart=lambda info:calls.append("update")), "info")
    def fail(*args): raise RuntimeError("offline")
    monkeypatch.setattr(desktop,"pause_on_quit",fail)
    application.apply_update()
    assert calls == []



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


def test_invalid_selected_data_root_is_reported_before_start(monkeypatch):
    from types import SimpleNamespace

    messages = []
    monkeypatch.setattr(app, "_velopack_hooks", lambda: None)
    def invalid_root():
        raise RuntimeError("Selected data folder is unavailable")
    monkeypatch.setattr(app.paths, "default_root", invalid_root)
    monkeypatch.setattr(app.ctypes, "windll", SimpleNamespace(user32=SimpleNamespace(
        MessageBoxW=lambda *args: messages.append(args))), raising=False)
    assert app.main([]) == 1
    assert len(messages) == 1
    assert "Selected data folder is unavailable" in messages[0][1]
