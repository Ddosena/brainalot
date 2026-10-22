"""Native WebView2 host for Brainalot's same-origin desktop pages."""
from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import os
from pathlib import Path
import queue
import threading
import time
from urllib.error import HTTPError
from .desktop_geometry import (WorkArea, work_areas, fit_geometry, select_area, native_rect,
                               set_native_rect, cursor_position, enable_thread_dpi, enable_process_dpi,
                               nearest_edge, popup_geometry, double_click_settings, clear_native_frame, round_native_window)
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen
import uuid
import webbrowser

_log = logging.getLogger("brainalot.desktop")
WIDGET_SIZES = {"bar": (84, 48), "card": (360, 76)}
MEDIA_ROW_HEIGHT = 52
FULL_SIZE = (484, 900)
FULL_GEOMETRY_LAYOUT = "unified-484x900-v1"
MINIMUM_SIZES = {"full": (300, 360), **WIDGET_SIZES}
STORAGE_FILE = "desktop-ui.json"


def clamp_geometry(rect: dict, screens: list, default: tuple[int, int]) -> dict:
    """Compatibility wrapper; geometry is explicitly in physical work-area pixels."""
    return fit_geometry(rect, [WorkArea(s.x, s.y, s.width, s.height) for s in screens],
                        default, minimum=(min(300, default[0]), min(80, default[1])))


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
            storage = self.data["storage"]
            # Background sync re-sets unchanged snapshots every few seconds;
            # like chrome.storage, an identical value is not a disk write.
            same = lambda key, value: json.dumps(storage[key], sort_keys=True) == json.dumps(value, sort_keys=True)
            if all(key in storage and same(key, value) for key, value in items.items()):
                return True
            storage.update(items)
            self.save()
        return True

    def remove(self, keys):
        if isinstance(keys, str):
            keys = [keys]
        if not isinstance(keys, list) or any(not isinstance(key, str) for key in keys):
            raise ValueError("Invalid storage keys")
        with self.lock:
            if not any(key in self.data["storage"] for key in keys):
                return True
            for key in keys:
                self.data["storage"].pop(key, None)
            self.save()
        return True


def input_idle_ms():
    """Milliseconds since the user's last keyboard or mouse input in this
    Windows session. Only a duration is read; no input is observed or stored."""
    if os.name != "nt":
        return 0

    class LastInput(ctypes.Structure):
        _fields_ = [("size", ctypes.c_uint), ("time", ctypes.c_uint)]

    info = LastInput()
    info.size = ctypes.sizeof(info)
    user, kernel = ctypes.WinDLL("user32"), ctypes.WinDLL("kernel32")
    kernel.GetTickCount.restype = ctypes.c_uint32
    if not user.GetLastInputInfo(ctypes.byref(info)):
        return 0
    return (kernel.GetTickCount() - info.time) & 0xFFFFFFFF  # 32-bit tick wrap


class DesktopBridge:
    def __init__(self, host):
        # pywebview recursively walks every public non-callable attribute of
        # js_api. Exposing the host would recurse into WinForms/WebView2 objects.
        self._host = host

    def bootstrap(self):
        window = self._host.full if self._host.mode == "full" else self._host.widget
        area = select_area(native_rect(window), self._host._areas()) if window else WorkArea(0, 0, 1920, 1080)
        return {"port": self._host.port, "token": self._host.token, "desktop": True,
                "mode": self._host.mode, "widget_mode": self._host.widget_mode,
                "topmost": self._host.topmost, "side": self._host.side,
                "media_visible": self._host.media_visible,
                "work_expanded": self._host._expanded_full_geometry is not None,
                "double_click": double_click_settings(area.dpi)}

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

    def input_idle_ms(self):
        return input_idle_ms()

    def remove_storage(self, keys):
        return self._host.state.remove(keys)


class CompactSurface:
    """CPU-painted closed widget; the live WebView stays outside the form.

    Windows 10's DirectComposition child ignores the form's rounded region.
    Merely hiding WebView also suspends CapturePreview. Keep its controller
    visible at an offscreen CHILD position, and present only a GDI bitmap in the
    form. Popup/fields use the regular WebView. All input below is real input
    received by our own control, routed to application actions, never OS input.
    """
    def __init__(self, host):
        self.host, self.window = host, host.widget
        self.active = False
        self.ready = False
        self.closed = False
        self.generation = 0
        self.timer = None
        self.canvas = None
        self.image = None
        self._image_digest = None
        self.boxes = []
        self.snapshot = None
        self.focus_id = None
        self.press = None
        self.hover_id = None
        self.pointer_id = None
        self.pressed_id = None
        self._local_pending = {}
        self.feedback_latencies_ms = []
        self.last_click = None
        self._signature = None
        self._capture_lock = threading.Lock()
        self._input = queue.Queue()
        self._move_pending = False
        self.frames = 0
        self.skipped_frames = 0
        self.capture_seconds = 0.0
        self._worker = threading.Thread(target=self._inputs, name="brainalot-compact-input", daemon=True)
        self._worker.start()

    def _invoke(self, callback):
        from System import Action
        self.window.native.Invoke(Action(callback))

    def _task(self, callback):
        from System import Func, Object
        task = self.window.native.Invoke(Func[Object](callback))
        deadline = time.monotonic()+4
        while not task.IsCompleted:
            if self.closed or time.monotonic() > deadline:
                raise TimeoutError("Compact renderer did not respond")
            time.sleep(.01)
        return task.GetAwaiter().GetResult()

    def _js(self, script):
        return json.loads(str(self._task(lambda: self.window.native.webview.CoreWebView2.ExecuteScriptAsync(script))))

    @staticmethod
    def _path(width, height, radius):
        from System.Drawing.Drawing2D import GraphicsPath
        path = GraphicsPath()
        diameter = min(width, height, radius*2)
        for x, y, angle in ((0, 0, 180), (width-diameter, 0, 270),
                            (width-diameter, height-diameter, 0), (0, height-diameter, 90)):
            path.AddArc(x, y, diameter, diameter, angle, 90)
        path.CloseFigure()
        return path

    def _hit(self, x, y):
        return next((box["id"] for box in self.boxes if box["id"] != "widget-tab" and box["x"] <= x < box["x"]+box["w"]
                     and box["y"] <= y < box["y"]+box["h"]), None)

    def _pending_ids(self):
        return {box["id"] for box in self.boxes if box.get("pending")} | set(self._local_pending)

    def _invalidate_feedback(self):
        """Own UI-thread feedback: no renderer round trip and no background erase."""
        if self.canvas is None or not self.active: return
        requested = time.monotonic()
        user = ctypes.WinDLL("user32")
        user.InvalidateRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
        user.InvalidateRect(int(self.canvas.Handle.ToInt64()), None, False)
        self.canvas.Update()
        # Update returns after buffered WM_PAINT has completed, not merely
        # after drawing into its temporary buffer.
        self.feedback_latencies_ms.append((time.monotonic()-requested)*1000)
        self.feedback_latencies_ms = self.feedback_latencies_ms[-32:]

    def _pointer_feedback(self, x=None, y=None, pressed=None, release=False):
        hit = self._hit(x,y) if x is not None and y is not None else None
        self.pointer_id = hit
        box = next((box for box in self.boxes if box["id"] == hit), None)
        hover = hit if box and not box.get("disabled") else None
        new_press = None if release else pressed if pressed is not None else self.pressed_id
        changed = (self.hover_id,self.pressed_id) != (hover,new_press)
        self.hover_id,self.pressed_id = hover,new_press
        self._update_cursor()
        if changed: self._invalidate_feedback()

    def _update_cursor(self):
        from System.Windows.Forms import Cursors
        box = next((box for box in self.boxes if box["id"] == self.pointer_id), None)
        self.canvas.Cursor = (Cursors.AppStarting if self.pointer_id in self._pending_ids() else
                              Cursors.No if box and box.get("disabled") else Cursors.Hand if box else Cursors.SizeAll)

    def _paint_feedback(self, graphics):
        from System.Drawing import Color, SolidBrush, Pen
        dark = bool((self.snapshot or {}).get("dark"))
        pending = self._pending_ids()
        for box in self.boxes:
            control = box["id"]
            alpha = 42 if control == self.pressed_id else 26 if control in pending else 18 if control == self.hover_id else 0
            if not alpha: continue
            path = self._path(max(1,round(box["w"]-2)),max(1,round(box["h"]-2)),min(8,box["h"]/2))
            from System.Drawing.Drawing2D import Matrix
            transform = Matrix(); transform.Translate(float(box["x"]+1),float(box["y"]+1));path.Transform(transform);transform.Dispose()
            color = 255 if dark else 0
            brush = SolidBrush(Color.FromArgb(alpha,color,color,color)); graphics.FillPath(brush,path);brush.Dispose()
            if control in pending:
                pen = Pen(Color.FromArgb(145,color,color,color),1.4);graphics.DrawPath(pen,path);pen.Dispose()
            path.Dispose()

    def _create_canvas(self):
        from System import Array, Object
        from System.Reflection import BindingFlags
        from System.Windows.Forms import UserControl, DockStyle, MouseButtons, Keys, ControlStyles
        canvas = UserControl()
        # BackgroundImage assignment invalidates with a separate background
        # erase. Paint our bitmap in one buffered WM_PAINT instead.
        setter = canvas.GetType().GetMethod("SetStyle", BindingFlags.Instance | BindingFlags.NonPublic)
        setter.Invoke(canvas, Array[Object]([ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint |
                      ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, True]))
        canvas.Dock = DockStyle.Fill
        canvas.TabStop = True
        def paint(_sender, event):
            if self.image is not None: event.Graphics.DrawImageUnscaled(self.image, 0, 0)
            self._paint_feedback(event.Graphics)
        def down(_sender, event):
            if not self.active: return
            button = 0 if event.Button == MouseButtons.Left else 2 if event.Button == MouseButtons.Right else -1
            if button < 0: return
            canvas.Focus(); canvas.Capture = True
            item = {"button":button, "id":self._hit(event.X, event.Y), "x":event.X, "y":event.Y,
                    "point":cursor_position(), "at":time.monotonic(), "moved":False}
            self.press = item
            self._pointer_feedback(event.X,event.Y,pressed=item["id"] if button == 0 else None)
            if button == 0 and not item["id"]: self._input.put(("begin", item))
        def move(_sender, event):
            self._pointer_feedback(event.X,event.Y)
            item = self.press
            if not item: return
            point = cursor_position()
            if abs(point[0]-item["point"][0])+abs(point[1]-item["point"][1]) >= 5: item["moved"] = True
            if item["moved"] and item["button"] == 0 and not item["id"] and not self._move_pending:
                self._move_pending = True; self._input.put(("move", item))
        def up(_sender, event):
            item, self.press = self.press, None
            canvas.Capture = False
            self._pointer_feedback(event.X,event.Y,release=True)
            if item:
                point = cursor_position()
                item["moved"] |= abs(point[0]-item["point"][0])+abs(point[1]-item["point"][1]) >= 5
                item["up_id"] = self._hit(event.X, event.Y)
                self._input.put(("up", item))
        def preview(_sender, event):
            if event.KeyCode in (Keys.Tab, Keys.Enter, Keys.Space, Keys.Escape): event.IsInputKey = True
        def key(_sender, event):
            if event.KeyCode not in (Keys.Tab, Keys.Enter, Keys.Space, Keys.Escape): return
            event.Handled = True; event.SuppressKeyPress = True
            self._input.put(("key", {"key":str(event.KeyCode), "shift":bool(event.Shift)}))
        canvas.MouseDown += down; canvas.MouseMove += move; canvas.MouseUp += up
        canvas.PreviewKeyDown += preview; canvas.KeyDown += key
        canvas.Paint += paint
        def leave(_sender,_event): self._pointer_feedback()
        canvas.MouseLeave += leave
        self._handlers = (down, move, up, preview, key, paint, leave)
        self.window.native.Controls.Add(canvas)
        self.canvas = canvas

    def activate(self):
        if self.closed: return
        rect = native_rect(self.window)
        signature = (self.host.mode, rect["width"], rect["height"])
        if self.active and signature == self._signature: return
        self.active = True; self.generation += 1; self._signature = signature
        self.ready = False
        self._image_digest = None
        self.boxes = []; self.focus_id = None; self.last_click = None
        self.hover_id = self.pressed_id = self.pointer_id = None
        self._local_pending.clear()
        def apply():
            from System.Drawing import Rectangle, Region
            from System.Windows.Forms import DockStyle
            if self.canvas is None: self._create_canvas()
            was_focused = bool(self.window.native.ContainsFocus)
            # Keep the renderer logically visible so it can rasterize, but do
            # not expose stale/pre-CSS pixels during the first compact frame.
            self.window.native.Opacity = 0
            control = self.window.native.webview
            control.Dock = getattr(DockStyle, "None")
            control.Bounds = Rectangle(-16384, -16384, rect["width"], rect["height"])
            control.Show()
            radius = round((24 if self.host.mode == "bar" else 30) *
                           select_area(rect, self.host._areas()).dpi/96)
            path = self._path(rect["width"], rect["height"], radius)
            old = self.window.native.Region
            self.window.native.Region = Region(path)
            if old: old.Dispose()
            path.Dispose()
            clear_native_frame(self.window)
            self.canvas.Show(); self.canvas.BringToFront()
            if was_focused: self.canvas.Focus()
        self._invoke(apply)
        self._schedule(0)

    def deactivate(self):
        if not self.active: return
        self.active = False; self.generation += 1; self._signature = None
        self.ready = False
        if self.timer: self.timer.cancel()
        def apply():
            from System.Windows.Forms import DockStyle
            if self.canvas: self.canvas.Hide()
            old = self.window.native.Region
            self.window.native.Region = None
            if old: old.Dispose()
            self.window.native.webview.Dock = DockStyle.Fill
            self.window.native.webview.Show(); self.window.native.webview.BringToFront()
            self.window.native.Opacity = 1
            if self.host._popup_base is not None and self.window.native.ContainsFocus:
                self.window.native.webview.Focus()
        self._invoke(apply)

    def _schedule(self, delay=.25):
        if self.active and not self.closed:
            if self.timer: self.timer.cancel()
            self.timer = threading.Timer(delay, self._refresh)
            self.timer.daemon = True; self.timer.start()

    def _refresh(self):
        if not self.active or self.closed: return
        if not self._capture_lock.acquire(False): self._schedule(); return
        generation = self.generation
        signature = self._signature
        started = time.monotonic()
        try:
            from System.Drawing import Bitmap, Graphics, Image, Rectangle
            from System.Drawing.Drawing2D import SmoothingMode
            from System.Drawing.Imaging import ImageLockMode, PixelFormat
            from System.IO import MemoryStream
            from Microsoft.Web.WebView2.Core import CoreWebView2CapturePreviewImageFormat
            snapshot = self._js("""(() => ({width:innerWidth,height:innerHeight,ready:document.body.dataset.widgetReady==='true',
              mode:document.body.dataset.widgetMode, title:document.getElementById('widget-burning')?.textContent,
              dark:document.documentElement.dataset.theme==='dark',boxes:
              [...document.querySelectorAll('#widget-spotify button[id], .widget-surface button[id], .widget-surface input[type=checkbox][id]'),
               ...['widget-task','widget-ask-choice','widget-run','widget-more','widget-tab'].map(id=>document.getElementById(id))]
              .filter((e,index,all)=>all.indexOf(e)===index)
              .filter(e=>e && !e.hidden && e.getBoundingClientRect().width)
              .map(e=>{const r=e.getBoundingClientRect();return {id:e.id,x:r.x,y:r.y,w:r.width,h:r.height,disabled:Boolean(e.disabled),pending:e.dataset.pending==='true'}})}))()""")
            if (not snapshot.get("ready") or snapshot.get("mode") != signature[0] or
                not snapshot["boxes"] or (signature[0] == "card" and not snapshot.get("title")) or
                any(box["x"] < 0 or box["y"] < 0 or box["x"]+box["w"] > snapshot["width"]+.5 or
                    box["y"]+box["h"] > snapshot["height"]+.5 for box in snapshot["boxes"])):
                return
            stream = MemoryStream()
            try:
                self._task(lambda: self.window.native.webview.CoreWebView2.CapturePreviewAsync(
                    CoreWebView2CapturePreviewImageFormat.Png, stream))
                source = Image.FromStream(stream)
                try:
                    if generation != self.generation or not self.active: return
                    width,height = signature[1:]
                    image = Bitmap(width, height)
                    graphics = Graphics.FromImage(image)
                    path = self._path(width,height,round((24 if signature[0] == "bar" else 30)*width/
                                                        WIDGET_SIZES[signature[0]][0]))
                    graphics.SmoothingMode = SmoothingMode.AntiAlias
                    graphics.SetClip(path)
                    graphics.DrawImage(source, Rectangle(0,0,width,height))
                    graphics.Dispose(); path.Dispose()
                    pixels = image.LockBits(Rectangle(0,0,width,height), ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb)
                    try:
                        digest = hashlib.sha256(ctypes.string_at(int(pixels.Scan0.ToInt64()), abs(pixels.Stride)*height)).digest()
                    finally: image.UnlockBits(pixels)
                finally: source.Dispose()
            finally: stream.Dispose()
            def present():
                if generation != self.generation or not self.active:
                    image.Dispose(); return
                before_pending = self._pending_ids()
                self.snapshot = snapshot
                self.boxes = [{**box, "x":box["x"]*width/snapshot["width"], "y":box["y"]*height/snapshot["height"],
                               "w":box["w"]*width/snapshot["width"], "h":box["h"]*height/snapshot["height"]}
                              for box in snapshot["boxes"]]
                now = time.monotonic()
                for control,issued in list(self._local_pending.items()):
                    native_pending = any(box["id"] == control and box.get("pending") for box in self.boxes)
                    if native_pending: self._local_pending[control] = None
                    elif issued is None or now-issued > .75: self._local_pending.pop(control,None)
                self._update_cursor()
                if before_pending != self._pending_ids(): self._invalidate_feedback()
                if digest == self._image_digest:
                    self.skipped_frames += 1
                    image.Dispose()
                    return
                old, self.image = self.image, image
                self._image_digest = digest
                self.frames += 1
                first_frame = not self.ready
                self.ready = True
                user = ctypes.WinDLL("user32")
                user.InvalidateRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
                user.InvalidateRect(int(self.canvas.Handle.ToInt64()), None, False)
                if old: old.Dispose()
                if not self.window.native.Visible: self.window.native.Show()
                if first_frame: self.window.native.Opacity = 1
            self._invoke(present)
        except Exception:
            if not self.closed: _log.debug("Compact bitmap refresh failed", exc_info=True)
        finally:
            self.capture_seconds += time.monotonic()-started
            self._capture_lock.release(); self._schedule()

    def _activate_control(self, control):
        if any(box["id"] == control and box.get("disabled") for box in self.boxes): return
        if control == "widget-tab": self.host.window_action("card")
        elif control:
            feedback = control.startswith("widget-spotify-") and getattr(self,"canvas",None) is not None
            def pending(value):
                if value: self._local_pending[control] = time.monotonic()
                else: self._local_pending.pop(control,None)
                self._update_cursor()
                self._invalidate_feedback()
            if feedback: self._invoke(lambda:pending(True))
            result = self._js("document.getElementById("+json.dumps(control)+")?.click(); "
                              "document.getElementById("+json.dumps(control)+")?.dataset.pending === 'true'")
            if feedback and result is not True: self._invoke(lambda:pending(False))

    def _inputs(self):
        while True:
            action, item = self._input.get()
            if action == "close": return
            if self.closed: continue
            try:
                if action == "begin": self.host.window_action("gesture_begin")
                elif action == "move":
                    try: self.host.window_action("gesture_move")
                    finally: self._move_pending = False
                elif action == "up":
                    if item["id"]:
                        self.last_click = None
                        # Small pointer drift is still a click when released on the same control.
                        if item["button"] == 0 and item["up_id"] == item["id"]: self._activate_control(item["id"])
                    else:
                        result = self.host.window_action("gesture_end") if item["button"] == 0 else {}
                        if item["moved"] or result.get("dragged"):
                            self.last_click = None; continue
                        settings = double_click_settings()
                        rect = native_rect(self.window)
                        scale = select_area(rect,self.host._areas()).dpi/96
                        last = self.last_click
                        paired = (last and last["button"] == item["button"] and
                                  item["at"]-last["at"] <= settings["interval_ms"]/1000 and
                                  abs(item["x"]-last["x"]) <= settings["width"]*scale/2 and
                                  abs(item["y"]-last["y"]) <= settings["height"]*scale/2)
                        self.last_click = None if paired else item
                        if paired: self.host.window_action("full" if item["button"] == 2 else
                                                           "card" if self.host.mode == "bar" else "bar")
                elif action == "key":
                    if item["key"] == "Tab":
                        ids = [box["id"] for box in self.boxes if not box.get("disabled")]
                        if not ids: continue
                        index = ids.index(self.focus_id) if self.focus_id in ids else (0 if item["shift"] else -1)
                        self.focus_id = ids[(index+(-1 if item["shift"] else 1))%len(ids)]
                        self._js("document.querySelectorAll('.native-keyboard-focus').forEach(e=>e.classList.remove('native-keyboard-focus'));"
                                 "document.getElementById("+json.dumps(self.focus_id)+")?.classList.add('native-keyboard-focus'); true")
                    elif item["key"] in {"Return", "Enter", "Space"}: self._activate_control(self.focus_id)
                self._schedule(0)
            except Exception:
                if not self.closed: _log.warning("Compact input failed",exc_info=True)

    def close(self):
        self.closed = True; self.active = False; self.generation += 1
        if self.timer: self.timer.cancel()
        self._input.put(("close",None))
        def dispose():
            if self.canvas:
                image, self.image = self.image, None
                if image: image.Dispose()
                self.canvas.Dispose()
            region = self.window.native.Region
            self.window.native.Region = None
            if region: region.Dispose()
        self._invoke(dispose)


class DesktopHost:
    def __init__(self, root: Path, port: int, token: str, on_quit):
        self.root, self.port, self.token = Path(root), int(port), token
        self.on_quit = on_quit
        self.state = DesktopState(root)
        self.media_visible = self.state.get({"desktop:spotify-visible": True})["desktop:spotify-visible"] is not False
        self.mode = "full"
        self.widget_mode = self.state.data.get("widget_mode", "bar")
        if self.widget_mode not in WIDGET_SIZES:
            self.widget_mode = "bar"
        self.topmost = True  # old saved false/hide states cannot disable visibility
        self.side = self.state.data.get("side", "right")
        if self.side not in {"left", "right", "top", "bottom"}:
            self.side = "right"
        self.full = self.widget = None
        self._quitting = False
        self._switching = False
        self._lock = threading.RLock()
        self._eval_lock = threading.RLock()
        self._navigation_guards = []
        self._full_resize_handlers = []
        self._geometry_timer = None
        self._topology_timer = None
        self._drag = None
        self._topology = None
        self._popup_base = None
        self._popup_above = False
        self._compact_surface = None
        self._expanded_full_geometry = None

    def _widget_size(self, mode):
        width, height = WIDGET_SIZES[mode]
        return width, height+(MEDIA_ROW_HEIGHT if mode == "card" and self.media_visible else 0)

    def _sync_compact_surface(self):
        if not self.widget or not hasattr(getattr(self.widget, "native", None), "Invoke"):
            return
        if self.mode in WIDGET_SIZES and self._popup_base is None:
            if self._compact_surface is None: self._compact_surface = CompactSurface(self)
            self._compact_surface.activate()
        elif self._compact_surface:
            self._compact_surface.deactivate()

    def _url(self, page: str):
        return f"http://127.0.0.1:{self.port}/desktop/{page}"

    def _areas(self):
        return work_areas(list(self.webview.screens))

    def _rect(self, mode, anchor=None):
        areas = self._areas()
        default = FULL_SIZE if mode == "full" else self._widget_size(mode)
        saved = dict(self.state.data["geometry"].get(mode, {}))
        # Migrate old logical widget geometry once. Discard ambiguous oversized
        # tab dimensions; its native bounds must equal the visible small surface.
        if not saved and mode == "card":
            saved = dict(self.state.data["geometry"].get("widget", {}))
        area = select_area(anchor or saved, areas)
        scale = area.dpi / 96
        size = tuple(round(v * scale) for v in default)
        minimum = tuple(round(v * scale) for v in (MINIMUM_SIZES["full"] if mode == "full" else self._widget_size(mode)))
        migrate_full = mode == "full" and self.state.data.get("full_geometry_layout") != FULL_GEOMETRY_LAYOUT
        if saved.get("units") == "physical" and saved.get("dpi"):
            ratio = area.dpi / saved["dpi"]
            saved["width"] = round(saved["width"] * ratio)
            saved["height"] = round(saved["height"] * ratio)
        elif saved:
            # Legacy pywebview values were logical; sizes scale, absolute origins
            # are only clamped (multiplying an absolute origin breaks mixed DPI).
            saved["width"] = round(saved.get("width", default[0]) * scale)
            saved["height"] = round(saved.get("height", default[1]) * scale)
        if mode in WIDGET_SIZES:
            # Compact views have a fixed size; only their position is remembered.
            saved.update(width=size[0], height=size[1])
        elif migrate_full:
            saved.update(width=size[0], height=size[1])
        if anchor:
            saved.update(x=anchor["x"], y=anchor["y"])
            width, height = saved.get("width", size[0]), saved.get("height", size[1])
            if self.side == "right": saved["x"] = anchor["x"]+anchor["width"]-width
            if self.side == "bottom": saved["y"] = anchor["y"]+anchor["height"]-height
        rect = fit_geometry(saved, areas, size, minimum, self.side if mode == "bar" else None)
        if migrate_full:
            with self.state.lock:
                self.state.data["geometry"]["full"] = {**rect,"units":"physical","screen":area.name,"dpi":area.dpi}
                self.state.data["full_geometry_layout"] = FULL_GEOMETRY_LAYOUT
                self.state.save()
        return rect, minimum

    def create_windows(self, webview):
        enable_process_dpi()
        enable_thread_dpi()
        self.webview = webview
        bridge = DesktopBridge(self)
        self.full = webview.create_window("Brainalot", self._url("desktop.html"), js_api=bridge,
                                         hidden=True, frameless=True, easy_drag=False,
                                         on_top=True, shadow=False, min_size=MINIMUM_SIZES["full"],
                                         x=80, y=80,
                                         width=FULL_SIZE[0], height=FULL_SIZE[1])
        self.widget = webview.create_window("Brainalot", self._url(f"widget.html?mode={self.widget_mode}"),
                                           js_api=bridge, hidden=True, frameless=True, resizable=False,
                                           easy_drag=False, on_top=True, shadow=False, min_size=MINIMUM_SIZES["bar"],
                                           x=80, y=80,
                                           width=self._widget_size(self.widget_mode)[0],
                                           height=self._widget_size(self.widget_mode)[1])
        for name, window in (("full", self.full), ("widget", self.widget)):
            original_evaluate_js = getattr(window, "evaluate_js", None)
            if callable(original_evaluate_js):
                def evaluate_while_alive(*args, _original=original_evaluate_js, **kwargs):
                    with self._eval_lock:
                        if self._quitting:
                            return None
                        # A response can outlive the document that requested it.
                        # Guard only pywebview's generated reply, not app errors.
                        if args and isinstance(args[0], str) and args[0].startswith('window.pywebview._returnValuesCallbacks['):
                            expression = args[0]
                            callback = expression[:expression.index('](')+1]
                            args = (f'if (typeof {callback} === "function") {{ {expression}; }}', *args[1:])
                        return _original(*args, **kwargs)
                window.evaluate_js = evaluate_while_alive
            window.events.closing += lambda *_, w=window, n=name: self._close_window(w, n)
            window.events.moved += lambda *_, w=window, n=name: self._remember_geometry(w, n)
            window.events.resized += lambda *_, w=window, n=name: self._remember_geometry(w, n)
            window.events.before_show += lambda *_, w=window: self._guard_navigation(w)

    def _guard_navigation(self, window):
        if getattr(window, "_brainalot_guarded", False):
            return
        if window is self.full:
            clear_native_frame(window)
            round_native_window(window)
            def resized(*_): round_native_window(window)
            window.native.Resize += resized
            if hasattr(window.native,"DpiChanged"): window.native.DpiChanged += resized
            self._full_resize_handlers.append(resized)
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
        if self._quitting or self._switching or (name == "full" and self._expanded_full_geometry is not None):
            return
        mode = "full" if name == "full" else self.widget_mode
        if self.mode != mode:
            return
        geometry = dict(self._popup_base) if name == "widget" and self._popup_base else native_rect(window)
        if geometry["width"] <= 0 or geometry["height"] <= 0:
            return
        area = select_area(geometry, self._areas())
        geometry.update(units="physical", screen=area.name, dpi=area.dpi)
        with self.state.lock:
            self.state.data["geometry"][mode] = geometry
            if self._geometry_timer:
                self._geometry_timer.cancel()
            self._geometry_timer = threading.Timer(.25, self.state.save)
            self._geometry_timer.daemon = True
            self._geometry_timer.start()

    def _close_window(self, window, name):
        if self._quitting:
            return True
        # pywebview calls closing subscribers synchronously: destroying a window
        # from this callback would reenter FormClosing/Invoke. Quit on a worker.
        threading.Thread(target=lambda: self.window_action("quit"),
                         name="brainalot-close", daemon=True).start()
        return False

    def _show(self, window):
        if window:
            clear_native_frame(window)
            if window is self.full: round_native_window(window)
            window.show()
            window.restore()

    def _notify_full_visibility(self, visible):
        if not self.full or not self.full.events.loaded.is_set():
            return
        # WinForms.Hide does not update Page Visibility in WebView2. Notify our
        # own documents synchronously before hiding so microphones are released.
        self.full.evaluate_js("""(() => {
          const visit = doc => {
            doc.dispatchEvent(new doc.defaultView.CustomEvent('brainalot:visibility',
              {detail:{visible:VISIBLE}}));
            for (const frame of doc.querySelectorAll('iframe')) {
              try { const child=frame.contentDocument;
                if (child && child.location.origin===location.origin) visit(child);
              } catch { /* Foreign documents do not receive app lifecycle. */ }
            }
          };
          visit(document); return true;
        })()""".replace("VISIBLE", "true" if visible else "false"))

    def _restore_geometry(self):
        self._switching = True
        try:
            for mode, window in (("full", self.full), (self.widget_mode, self.widget)):
                rect, minimum = self._rect(mode)
                set_native_rect(window, rect, minimum)
        finally:
            self._switching = False

    def _watch_topology(self):
        with self._lock:
            if self._quitting:
                return
            areas = self._areas()
            topology = tuple((a.name, a.x, a.y, a.width, a.height, a.dpi) for a in areas)
            if self._topology is not None and topology != self._topology:
                self._widget_popup({"open": False})
                window = self.full if self.mode == "full" else self.widget
                rect, minimum = self._rect(self.mode)
                set_native_rect(window, rect, minimum)
                self._sync_compact_surface()
                self._remember_geometry(window, "full" if self.mode == "full" else "widget")
            self._topology = topology
            self._topology_timer = threading.Timer(2, self._watch_topology)
            self._topology_timer.daemon = True
            self._topology_timer.start()

    def _gesture(self, action, value):
        window = self.full if self.mode == "full" else self.widget
        if action == "gesture_begin":
            self._widget_popup({"open": False})
            self._drag = {"origin": cursor_position(), "rect": native_rect(window),
                          "edge": (value or {}).get("edge", ""), "moved": False,
                          "mode": self.mode,
                          "dpi": select_area(native_rect(window), self._areas()).dpi}
        elif self._drag:
            drag = self._drag
            x, y = cursor_position()
            dx, dy = x-drag["origin"][0], y-drag["origin"][1]
            if abs(dx)+abs(dy) >= 6:
                drag["moved"] = True
            if drag["moved"] and drag["mode"] == self.mode:
                rect = dict(drag["rect"])
                edge = drag["edge"]
                if edge and self.mode == "full":
                    if "e" in edge: rect["width"] += dx
                    if "s" in edge: rect["height"] += dy
                    if "w" in edge: rect["x"] += dx; rect["width"] -= dx
                    if "n" in edge: rect["y"] += dy; rect["height"] -= dy
                    area = select_area(rect, self._areas())
                    minimum = tuple(round(v*area.dpi/96) for v in MINIMUM_SIZES["full"])
                    if rect["width"] < minimum[0]:
                        if "w" in edge: rect["x"] -= minimum[0]-rect["width"]
                        rect["width"] = minimum[0]
                    if rect["height"] < minimum[1]:
                        if "n" in edge: rect["y"] -= minimum[1]-rect["height"]
                        rect["height"] = minimum[1]
                else:
                    rect["x"] += dx; rect["y"] += dy
                if action == "gesture_end":
                    area = select_area(rect, self._areas())
                    ratio = area.dpi / drag["dpi"]
                    if ratio != 1 and not edge:
                        rect["width"] = round(rect["width"] * ratio)
                        rect["height"] = round(rect["height"] * ratio)
                    if self.mode == "bar":
                        rect["width"], rect["height"] = (round(v*area.dpi/96) for v in WIDGET_SIZES["bar"])
                        self.side = nearest_edge(rect, area)
                        self.state.data["side"] = self.side
                    rect = fit_geometry(rect, self._areas(), self._widget_size(self.mode) if self.mode in WIDGET_SIZES else FULL_SIZE,
                                        side=self.side if self.mode == "bar" else None)
                set_native_rect(window, rect)
            if action == "gesture_end":
                self._drag = None
                self._sync_compact_surface()
                self._remember_geometry(window, "full" if self.mode == "full" else "widget")
            return {"ok": True, "dragged": drag["moved"], "side": self.side}
        return {"ok": True, "dragged": False, "side": self.side}

    def _widget_popup(self, value):
        value = value or {}
        if not value.get("open"):
            if self._popup_base:
                base, self._popup_base = self._popup_base, None
                self._switching = True
                try:
                    set_native_rect(self.widget, base)
                finally:
                    self._switching = False
            self._popup_above = False
            self._sync_compact_surface()
            return {"ok": True, "popup_above": False, "height": self._widget_size(self.widget_mode)[1]}
        if self.mode != "card":
            return {"ok": False, "popup_above": False, "height": self._widget_size(self.widget_mode)[1]}
        requested = value.get("height", self._widget_size("card")[1])
        if isinstance(requested, bool) or not isinstance(requested, (int, float)) or not 0 < requested < 100000:
            raise ValueError("Popup height must be a positive logical size")
        if self._popup_base is None:
            self._popup_base = native_rect(self.widget)
        self._sync_compact_surface()
        area = select_area(self._popup_base, self._areas())
        rect, self._popup_above = popup_geometry(self._popup_base, area, round(requested*area.dpi/96), self.side)
        self._switching = True
        try:
            set_native_rect(self.widget, rect)
        finally:
            self._switching = False
        return {"ok": True, "popup_above": self._popup_above, "height": round(rect["height"]*96/area.dpi)}

    def _widget_media(self, value):
        if not isinstance(value, dict) or not isinstance(value.get("visible"), bool):
            raise ValueError("Media visibility must be a boolean")
        visible = value["visible"]
        changed = self.media_visible != visible
        self.media_visible = visible
        self.state.set({"desktop:spotify-visible": visible})
        result = {"ok": True, "media_visible": visible, "card_height": self._widget_size("card")[1],
                  "height": self._widget_size(self.widget_mode)[1], "popup_above": self._popup_above}
        if self.mode != "card" or not changed:
            if self.mode == "card":
                current = native_rect(self.widget)
                area = select_area(current, self._areas())
                result["height"] = round(current["height"]*96/area.dpi)
            return result
        before = native_rect(self.widget)
        base = dict(self._popup_base or before)
        area = select_area(base, self._areas())
        height = round(self._widget_size("card")[1]*area.dpi/96)
        extra = max(0, before["height"]-base["height"])
        if self.side == "bottom": base["y"] += base["height"]-height
        base["height"] = height
        base = fit_geometry(base, self._areas(), self._widget_size("card"))
        rect = base
        if self._popup_base is not None:
            self._popup_base = base
            rect, self._popup_above = popup_geometry(base, area, base["height"]+extra, self.side)
        self._switching = True
        try:
            set_native_rect(self.widget, rect, tuple(round(v*area.dpi/96) for v in self._widget_size("card")))
            self._sync_compact_surface()
        finally:
            self._switching = False
        self._remember_geometry(self.widget, "widget")
        result.update(height=round(rect["height"]*96/area.dpi), popup_above=self._popup_above)
        return result

    def window_action(self, action, value=None):
        with self._lock:
            if self._quitting:
                return {"ok": False, "mode": self.mode, "topmost": True}
            if action.startswith("gesture_"):
                return self._gesture(action, value)
            if action == "widget_popup":
                return self._widget_popup(value)
            if action == "widget_media":
                return self._widget_media(value)
            if action == "work_expand":
                if type(value) is not bool:
                    raise ValueError("work_expand requires a boolean")
                if self.mode != "full":
                    self.window_action("full")
                expanded = self._expanded_full_geometry is not None
                if value != expanded:
                    if value:
                        self._remember_geometry(self.full, "full")
                        self._expanded_full_geometry = dict(native_rect(self.full))
                        area = select_area(self._expanded_full_geometry, self._areas())
                        rect = {"x": area.x, "y": area.y, "width": area.width, "height": area.height}
                    else:
                        rect = fit_geometry(self._expanded_full_geometry, self._areas(), FULL_SIZE, MINIMUM_SIZES["full"])
                    self._switching = True
                    try:
                        set_native_rect(self.full, rect)
                    finally:
                        self._switching = False
                    if not value:
                        self._expanded_full_geometry = None
                        self._remember_geometry(self.full, "full")
                return {"ok": True, "expanded": value}
            if action == "topmost":
                self.topmost = True
                # pywebview's on_top setter writes WinForms.TopMost off the UI
                # thread; native property events can deadlock with Python's GIL.
                for window in (self.full, self.widget):
                    set_native_rect(window, native_rect(window))
            elif action in {"full", "card", "bar", "hide"}:
                # Compatibility for old callers: hide is now a visible tab.
                if action == "hide": action = "bar"
                self._widget_popup({"open": False})
                previous = self.mode
                if action != "full":
                    self._notify_full_visibility(False)
                current = self.full if previous == "full" else self.widget
                self._remember_geometry(current, "full" if previous == "full" else "widget")
                anchor = native_rect(self.widget) if previous in WIDGET_SIZES and action in WIDGET_SIZES else None
                if action == "bar" and previous == "card":
                    source = anchor or native_rect(current)
                    self.side = nearest_edge(source, select_area(source, self._areas()))
                self._switching = True
                try:
                    target = self.full if action == "full" else self.widget
                    if action != "full":
                        changed = self.widget_mode != action
                        self.widget_mode = action
                        self.state.data["widget_mode"] = action
                        if changed and self.widget.events.loaded.is_set():
                            # Reuse the loaded document. Reloading during rapid
                            # view changes races pywebview's in-flight callbacks.
                            self.widget.evaluate_js("window.dispatchEvent(new CustomEvent('brainalot-widget-mode',"
                                                    + "{detail:" + json.dumps(action) + "}))")
                    self.mode = action
                    rect, minimum = self._rect(action, anchor)
                    if action == "full" and self._expanded_full_geometry is not None:
                        area = select_area(self._expanded_full_geometry, self._areas())
                        rect = {"x": area.x, "y": area.y, "width": area.width, "height": area.height}
                    set_native_rect(target, rect, minimum)
                    self._sync_compact_surface()
                    self._show(target)  # show before hiding the old surface
                    if action == "full":
                        self._notify_full_visibility(True)
                    (self.widget if action == "full" else self.full).hide()
                    self.state.data.update(last_mode=action, topmost=True, side=self.side)
                    self.state.save()
                finally:
                    self._switching = False
                self._remember_geometry(target, "full" if action == "full" else "widget")
            elif action == "quit":
                if self.on_quit() is False:
                    return {"ok": False, "mode": self.mode, "topmost": True}
            else:
                raise ValueError("Unknown window action")
            return {"ok": True, "mode": self.mode, "topmost": True, "side": self.side}

    def open_page(self, path):
        view = internal_view(path)
        self.window_action("full")
        self.full.evaluate_js("window.dispatchEvent(new CustomEvent('brainalot-view',{detail:"
                              + json.dumps(view) + "}))")

    def destroy(self):
        with self._lock, self._eval_lock:
            if self._quitting:
                return
            # Flush the current physical bounds even if the debounce has not fired.
            for name, window in (("full", self.full), ("widget", self.widget)):
                if window: self._remember_geometry(window, name)
            self._quitting = True
            if self._compact_surface: self._compact_surface.close()
            for timer in (self._geometry_timer, self._topology_timer):
                if timer: timer.cancel()
            self.state.save()
            if self.widget: self.widget.destroy()
            if self.full: self.full.destroy()

    def run(self, background=False):
        try:
            import webview
        except ImportError as exc:
            raise RuntimeError("Desktop requires the 'desktop' extra (pywebview and WebView2)") from exc
        self.create_windows(webview)
        webview.settings["ALLOW_DOWNLOADS"] = True
        def ready():
            self._restore_geometry()
            mode = self.state.data.get("last_mode", "bar" if background else "full")
            self.window_action(mode if mode in {"full", "card", "bar"} else "bar")
            self._watch_topology()
        webview.start(ready, gui="edgechromium", private_mode=False,
                      storage_path=str(self.root / "state" / "desktop-webview"))
