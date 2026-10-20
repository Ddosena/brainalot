"""Desktop geometry in physical pixels, independent of WebView's logical API.

Windows' virtual desktop includes negative origins. Never scale an absolute
desktop coordinate by the DPI of a different monitor.
"""
from __future__ import annotations

from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import os
from contextlib import contextmanager

_USER32 = None


class _MonitorInfo(ctypes.Structure):
    # A stable type is essential: multiple renderer/bridge threads enumerate
    # monitors concurrently and share our private function's argtypes.
    _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                ("work", wintypes.RECT), ("flags", wintypes.DWORD),
                ("device", wintypes.WCHAR * 32)]


def _user32():
    """Private user32 handle. Its argtypes must not leak into the shared
    ctypes.windll.user32: pywebview's own move() passes None for sizes."""
    global _USER32
    if _USER32 is None:
        _USER32 = ctypes.WinDLL("user32")
    return _USER32


def enable_thread_dpi():
    """Per-monitor v2 for this app's UI thread, never a machine-wide setting."""
    if os.name != "nt":
        return None
    try:
        setter = _user32().SetThreadDpiAwarenessContext
        setter.argtypes = [ctypes.c_void_p]
        setter.restype = ctypes.c_void_p
        return setter(ctypes.c_void_p(-4))
    except AttributeError:
        return None


def enable_process_dpi():
    """Set awareness for this process before WinForms creates its UI thread."""
    if os.name == "nt":
        try:
            setter = _user32().SetProcessDpiAwarenessContext
            setter.argtypes = [ctypes.c_void_p]
            setter(ctypes.c_void_p(-4))
        except AttributeError:
            pass


@contextmanager
def physical_context():
    previous = enable_thread_dpi()
    try:
        yield
    finally:
        if previous:
            _user32().SetThreadDpiAwarenessContext(ctypes.c_void_p(previous))


@dataclass(frozen=True)
class WorkArea:
    x: int
    y: int
    width: int
    height: int
    name: str = ""
    dpi: int = 96


def work_areas(fallback=()):
    if os.name == "nt":
        user = _user32()
        old = None
        try:
            # Local thread context only; no global Windows setting is changed.
            user.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
            user.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
            old = user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        except AttributeError:
            pass
        result = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE,
                                           wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
        def collect(monitor, *_):
            info = _MonitorInfo()
            info.size = ctypes.sizeof(info)
            if user.GetMonitorInfoW(monitor, ctypes.byref(info)):
                dpi = 96
                try:
                    # GetDpiForMonitor depends on PROCESS awareness, which an
                    # embedded WinForms host may have fixed to system-aware.
                    # ScaleFactorForMonitor gives the selected display scale.
                    scale = ctypes.c_int(100)
                    get_scale = ctypes.windll.shcore.GetScaleFactorForMonitor
                    get_scale.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_int)]
                    if get_scale(monitor, ctypes.byref(scale)) == 0:
                        dpi = round(96 * scale.value / 100)
                except (AttributeError, OSError):
                    pass
                r = info.work
                result.append(WorkArea(r.left, r.top, r.right-r.left, r.bottom-r.top,
                                       info.device, dpi))
            return True
        try:
            user.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MonitorInfo)]
            user.EnumDisplayMonitors(None, None, callback_type(collect), 0)
        finally:
            if old:
                user.SetThreadDpiAwarenessContext(ctypes.c_void_p(old))
        if result:
            return result
    result = []
    for screen in fallback:
        area = getattr(screen, "work_area", None)
        if area is not None:
            result.append(WorkArea(int(area.X), int(area.Y), int(area.Width), int(area.Height)))
        else:
            result.append(WorkArea(int(screen.x), int(screen.y), int(screen.width), int(screen.height),
                                   getattr(screen, "name", ""), getattr(screen, "dpi", 96)))
    return result


def select_area(rect, areas):
    """Choose overlap first; a disconnected screen falls back to the nearest."""
    if not areas:
        return WorkArea(0, 0, 1920, 1080)
    x, y = rect.get("x"), rect.get("y")
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return next((a for a in areas if a.name and a.name == rect.get("screen")), areas[0])
    width, height = rect.get("width", 1), rect.get("height", 1)
    def rank(a):
        overlap = max(0, min(x+width, a.x+a.width)-max(x, a.x)) * max(0, min(y+height, a.y+a.height)-max(y, a.y))
        distance = max(a.x-x, 0, x-(a.x+a.width))**2 + max(a.y-y, 0, y-(a.y+a.height))**2
        return (-overlap, distance)
    return min(areas, key=rank)


def fit_geometry(rect, areas, default, minimum=(1, 1), side=None):
    area = select_area(rect, areas)
    width = min(area.width, max(minimum[0], int(rect.get("width", default[0]))))
    height = min(area.height, max(minimum[1], int(rect.get("height", default[1]))))
    x = int(rect.get("x", area.x+(area.width-width)//2))
    y = int(rect.get("y", area.y+(area.height-height)//2))
    if side == "left":
        x = area.x
    elif side == "right":
        x = area.x+area.width-width
    elif side == "top":
        y = area.y
    elif side == "bottom":
        y = area.y+area.height-height
    return {"x": max(area.x, min(x, area.x+area.width-width)),
            "y": max(area.y, min(y, area.y+area.height-height)),
            "width": width, "height": height}


def nearest_edge(rect, area):
    """Distance to the four work-area edges, preserving stable tie order."""
    distances = {"left": max(0, rect["x"]-area.x),
                "right": max(0, area.x+area.width-rect["x"]-rect["width"]),
                "top": max(0, rect["y"]-area.y),
                "bottom": max(0, area.y+area.height-rect["y"]-rect["height"])}
    return min(distances, key=distances.get)


def popup_geometry(base, area, height, side):
    """Grow vertically inward, keeping the original card at its screen position."""
    height = min(area.height, max(base["height"], int(height)))
    below = area.y+area.height-base["y"]
    above = base["y"]+base["height"]-area.y
    popup_above = side == "bottom" or (side != "top" and height > below and above > below)
    available = above if popup_above else below
    height = min(height, max(base["height"], available))
    return {**base, "height": height,
            "y": base["y"]+base["height"]-height if popup_above else base["y"]}, popup_above


def double_click_settings(dpi=96):
    """Windows preferences, in CSS/logical pixels for the current WebView."""
    if os.name != "nt":
        return {"interval_ms": 500, "width": 4, "height": 4}
    user = _user32()
    with physical_context():
        try:
            metric = lambda code: user.GetSystemMetricsForDpi(code, 96)
            width, height = metric(36), metric(37)
        except AttributeError:
            width, height = (round(user.GetSystemMetrics(code)*96/max(96, dpi)) for code in (36, 37))
        return {"interval_ms": int(user.GetDoubleClickTime()), "width": max(2, width), "height": max(2, height)}


def native_rect(window):
    native = getattr(window, "native", None)
    if native is not None and hasattr(native, "Handle"):
        rect = wintypes.RECT()
        user = _user32()
        user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        with physical_context():
            if user.GetWindowRect(int(native.Handle.ToInt64()), ctypes.byref(rect)):
                return {"x": rect.left, "y": rect.top, "width": rect.right-rect.left, "height": rect.bottom-rect.top}
    return {"x": int(window.x), "y": int(window.y), "width": int(window.width), "height": int(window.height)}


def set_native_rect(window, rect, minimum=None):
    native = getattr(window, "native", None)
    if native is not None and hasattr(native, "Invoke"):
        from System import Action
        from System.Drawing import Size
        user = _user32()
        user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, wintypes.UINT]
        def update():
            if minimum:
                native.MinimumSize = Size(*minimum)
            # HWND_TOPMOST + NOACTIVATE. Updating geometry never steals focus.
            user.SetWindowPos(int(native.Handle.ToInt64()), -1, rect["x"], rect["y"],
                              rect["width"], rect["height"], 0x0010)
        native.Invoke(Action(update))
    else:
        window.resize(rect["width"], rect["height"])
        window.move(rect["x"], rect["y"])


def cursor_position():
    point = wintypes.POINT()
    with physical_context():
        _user32().GetCursorPos(ctypes.byref(point))
    return point.x, point.y
