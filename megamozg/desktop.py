"""Native WebView2 host for Brainalot's same-origin desktop pages."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import threading
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen
import uuid
import webbrowser

_log = logging.getLogger("brainalot.desktop")
WIDGET_SIZES = {"bar": (370, 90), "card": (370, 230)}
FULL_SIZE = (1120, 760)
STORAGE_FILE = "desktop-ui.json"


def clamp_geometry(rect: dict, screens: list, default: tuple[int, int]) -> dict:
    """Keep the entire window inside one display's logical work area."""
    width = max(300, int(rect.get("width", default[0])))
    height = max(80, int(rect.get("height", default[1])))
    displays = [s for s in screens if int(s.width) > 0 and int(s.height) > 0]
    if not displays:
        return {"x": 80, "y": 80, "width": width, "height": height}
    saved_x, saved_y = rect.get("x"), rect.get("y")
    def distance(s):
        if not isinstance(saved_x, int) or not isinstance(saved_y, int):
            return 0
        return max(s.x - saved_x, 0, saved_x - (s.x + s.width)) ** 2 + \
            max(s.y - saved_y, 0, saved_y - (s.y + s.height)) ** 2
    screen = min(displays, key=distance)
    width, height = min(width, int(screen.width)), min(height, int(screen.height))
    x = int(saved_x) if isinstance(saved_x, int) else int(screen.x + (screen.width - width) / 2)
    y = int(saved_y) if isinstance(saved_y, int) else int(screen.y + (screen.height - height) / 2)
    x = max(int(screen.x), min(x, int(screen.x + screen.width - width)))
    y = max(int(screen.y), min(y, int(screen.y + screen.height - height)))
    return {"x": x, "y": y, "width": width, "height": height}


def internal_view(path: str) -> str:
    """Convert a page request to a view on the trusted desktop shell."""
    if not isinstance(path, str):
        raise ValueError("Internal page must be a string")
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.path.startswith("//"):
        raise ValueError("Only internal pages may open in the desktop window")
    page = parsed.path.removeprefix("/").removeprefix("desktop/")
    if page == "desktop.html":
        view = parse_qs(parsed.query).get("view", ["home"])[0]
        if view not in {"home", "library", "capture", "work"}:
            raise ValueError("Unknown desktop view")
        return view
    if page in ("", "panel.html"):
        return "library" if parse_qs(parsed.query).get("library-window") == ["1"] else "home"
    if page == "capture.html":
        return "capture"
    if page == "work.html":
        return "work"
    if page in {"library", "capture", "work", "home"}:
        return page
    raise ValueError("Page is not part of Brainalot desktop")


def pause_on_quit(port: int, token: str) -> None:
    """Pause the shared clock before shutting down the desktop process.

    A 409 is a real concurrent edit. It is surfaced to the user rather than
    silently overwriting the other window's command.
    """
    base = f"http://127.0.0.1:{port}"
    headers = {"Authorization": "Bearer " + token}
    with urlopen(Request(base + "/api/time/state", headers=headers), timeout=3) as response:
        state = json.load(response)
    if state.get("status") != "running":
        return
    payload = json.dumps({"request_id": str(uuid.uuid4()), "expected_revision": state["revision"],
                          "action": "pause"}).encode("utf-8")
    request = Request(base + "/api/time/command", payload,
                      {**headers, "Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=3) as response:
            result = json.load(response)
    except HTTPError as exc:
        if exc.code == 409:
            raise RuntimeError("Таймер изменился в другом окне; проверьте его и повторите выход") from exc
        raise
    if result.get("status") == "running":
        raise RuntimeError("Сервис не подтвердил паузу таймера")


class DesktopState:
    def __init__(self, root: Path):
        self.path = Path(root) / "state" / STORAGE_FILE
        self.lock = threading.RLock()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.data = data if isinstance(data, dict) else {}
        if not isinstance(self.data.get("storage"), dict):
            self.data["storage"] = {}
        if not isinstance(self.data.get("geometry"), dict):
            self.data["geometry"] = {}

    def save(self):
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
            temp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, self.path)

    def get(self, keys=None):
        with self.lock:
            values = dict(self.data["storage"])
        if keys is None:
            return values
        if isinstance(keys, str):
            return {keys: values[keys]} if keys in values else {}
        if isinstance(keys, list):
            return {key: values[key] for key in keys if isinstance(key, str) and key in values}
        if isinstance(keys, dict):
            return {key: values.get(key, default) for key, default in keys.items() if isinstance(key, str)}
        raise ValueError("Invalid storage keys")

    def set(self, items: dict):
        if not isinstance(items, dict) or any(not isinstance(key, str) for key in items):
            raise ValueError("Storage items must be an object with string keys")
        with self.lock:
            self.data["storage"].update(items)
            self.save()
        return True

    def remove(self, keys):
        if isinstance(keys, str):
            keys = [keys]
        if not isinstance(keys, list) or any(not isinstance(key, str) for key in keys):
            raise ValueError("Invalid storage keys")
        with self.lock:
            for key in keys:
                self.data["storage"].pop(key, None)
            self.save()
        return True


class DesktopBridge:
    def __init__(self, host):
        # pywebview recursively walks every public non-callable attribute of
        # js_api. Exposing the host would recurse into WinForms/WebView2 objects.
        self._host = host

    def bootstrap(self):
        return {"port": self._host.port, "token": self._host.token, "desktop": True,
                "mode": self._host.mode, "topmost": self._host.topmost}

    def window_action(self, action, value=None):
        return self._host.window_action(action, value)

    def open_page(self, path):
        self._host.open_page(path)
        return True

    def external_url(self, url):
        parsed = urlsplit(url) if isinstance(url, str) else None
        if not parsed or parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Only HTTP(S) URLs may open externally")
        return bool(webbrowser.open(url))

    def get_storage(self, keys=None):
        return self._host.state.get(keys)

    def set_storage(self, items):
        return self._host.state.set(items)

    def remove_storage(self, keys):
        return self._host.state.remove(keys)


class DesktopHost:
    def __init__(self, root: Path, port: int, token: str, on_quit):
        self.root, self.port, self.token = Path(root), int(port), token
        self.on_quit = on_quit
        self.state = DesktopState(root)
        self.saved_geometry = dict(self.state.data["geometry"])
        self.mode = "hide"
        self.widget_mode = self.state.data.get("widget_mode", "bar")
        if self.widget_mode not in WIDGET_SIZES:
            self.widget_mode = "bar"
        self.topmost = bool(self.state.data.get("topmost", True))
        self.full = self.widget = None
        self._quitting = False
        self._lock = threading.RLock()
        self._eval_lock = threading.RLock()
        self._navigation_guards = []

    def _url(self, page: str):
        return f"http://127.0.0.1:{self.port}/desktop/{page}"

    def create_windows(self, webview):
        self.webview = webview
        bridge = DesktopBridge(self)
        screens = list(webview.screens)
        full = clamp_geometry(self.state.data["geometry"].get("full", {}), screens, FULL_SIZE)
        widget = clamp_geometry(self.state.data["geometry"].get("widget", {}), screens, WIDGET_SIZES[self.widget_mode])
        self.full = webview.create_window("Brainalot", self._url("desktop.html"), js_api=bridge,
                                          hidden=True, min_size=(700, 480), **full)
        self.widget = webview.create_window("Brainalot", self._url(f"widget.html?mode={self.widget_mode}"),
                                            js_api=bridge, hidden=True, frameless=True,
                                            easy_drag=False, on_top=self.topmost,
                                            min_size=(300, 80), **widget)
        for name, window in (("full", self.full), ("widget", self.widget)):
            original_evaluate_js = getattr(window, "evaluate_js", None)
            if callable(original_evaluate_js):
                def evaluate_while_alive(*args, _original=original_evaluate_js, **kwargs):
                    with self._eval_lock:
                        if self._quitting:
                            return None
                        return _original(*args, **kwargs)
                window.evaluate_js = evaluate_while_alive
            window.events.closing += lambda *_, w=window, n=name: self._close_window(w, n)
            window.events.moved += lambda *_, w=window, n=name: self._remember_geometry(w, n)
            window.events.resized += lambda *_, w=window, n=name: self._remember_geometry(w, n)
            window.events.before_show += lambda *_, w=window: self._guard_navigation(w)

    def _guard_navigation(self, window):
        """Keep the privileged JS bridge on trusted local desktop documents."""
        if getattr(window, "_brainalot_guarded", False):
            return
        control = window.native.webview
        def check(_sender, args):
            parsed = urlsplit(str(args.Uri))
            trusted = (parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
                       and parsed.port == self.port and parsed.path.startswith("/desktop/"))
            if not trusted:
                args.Cancel = True
                if parsed.scheme in {"http", "https"}:
                    webbrowser.open(str(args.Uri))
        control.NavigationStarting += check
        self._navigation_guards.append(check)
        window._brainalot_guarded = True

    def _remember_geometry(self, window, name):
        if self._quitting:
            return
        geometry = {"x": int(window.x), "y": int(window.y),
                    "width": int(window.width), "height": int(window.height)}
        with self.state.lock:
            self.state.data["geometry"][name] = geometry
            self.state.save()

    def _close_window(self, window, name):
        if self._quitting:
            return True
        self._remember_geometry(window, name)
        window.hide()
        with self._lock:
            if self.mode == name or (name == "widget" and self.mode in WIDGET_SIZES):
                self.mode = "hide"
        return False  # cancel native close; tray stays alive

    def _show(self, window):
        if window:
            window.show()
            window.restore()

    def _restore_geometry(self):
        screens = list(self.webview.screens)
        for name, window, size in (("full", self.full, FULL_SIZE),
                                    ("widget", self.widget, WIDGET_SIZES[self.widget_mode])):
            rect = clamp_geometry(self.saved_geometry.get(name, {}), screens, size)
            window.resize(rect["width"], rect["height"])
            window.move(rect["x"], rect["y"])

    def window_action(self, action, value=None):
        with self._lock:
            if action == "topmost":
                self.topmost = not self.topmost if value is None else bool(value)
                self.widget.on_top = self.topmost
                self.state.data["topmost"] = self.topmost
                self.state.save()
            elif action == "full":
                self.widget.hide()
                self._show(self.full)
                self.mode = "full"
            elif action in WIDGET_SIZES:
                self._remember_geometry(self.widget, "widget")
                previous_mode = self.widget_mode
                self.widget_mode = action
                self.state.data["widget_mode"] = action
                self.state.save()
                width, height = WIDGET_SIZES[action]
                self._show(self.widget)
                self.widget.resize(width, height)
                if action != previous_mode:
                    self.widget.load_url(self._url(f"widget.html?mode={action}"))
                self.full.hide()
                self.mode = action
            elif action == "hide":
                self.full.hide()
                self.widget.hide()
                self.mode = "hide"
            elif action == "quit":
                if self.on_quit() is False:
                    return {"ok": False, "mode": self.mode, "topmost": self.topmost}
            else:
                raise ValueError("Unknown window action")
            return {"ok": True, "mode": self.mode, "topmost": self.topmost}

    def open_page(self, path):
        view = internal_view(path)
        self.window_action("full")
        self.full.load_url(self._url("desktop.html") + "?" + urlencode({"view": view}))

    def destroy(self):
        with self._eval_lock:
            self._quitting = True
            if self.widget:
                self.widget.destroy()
            if self.full:
                self.full.destroy()

    def run(self, background=False):
        try:
            import webview
        except ImportError as exc:
            raise RuntimeError("Desktop requires the 'desktop' extra (pywebview and WebView2)") from exc
        self.create_windows(webview)
        webview.settings["ALLOW_DOWNLOADS"] = True
        def ready():
            self._restore_geometry()
            if not background:
                self.window_action("full")
        webview.start(ready, gui="edgechromium", private_mode=False,
                      storage_path=str(self.root / "state" / "desktop-webview"))
