"""A native, click-through focus bar above the foreground Chrome window.

The extension owns the Pomodoro state in Chrome storage.  This module only
renders a supplied state and deliberately stores neither a token nor timer
data.  On non-Windows systems it is a no-op so the HTTP service and its tests
remain portable.
"""
from __future__ import annotations

import logging
import math
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


# Fallback only.  Normally the bar follows the page's own content window
# (``CONTENT_WIDGET_CLASS``).  Without it, the page of a maximized Chrome (tab
# strip + toolbar, no bookmarks bar) starts about 87 logical pixels below the
# visible frame.  In F11 the toolbar is absent, so the bar belongs at the outer
# edge itself.
CONTENT_TOP_OFFSET_DIP = 87
# «Неон», chosen by the user on 2026-09-24: a light core inside a saturated
# tube, a thin dark outline that holds on white pages and a soft halo that
# shines on dark ones.  The halo breathes through the window's constant alpha.
BAR_HEIGHT_DIP = 6
NEON_CORE_DIP = 2
GLOW_MARGIN_DIP = 12
NEON_HALO_ALPHA = 0.85
NEON_HEAD_RADIUS_DIP = 12
BREATH_PERIOD_S = 3.5
BREATH_MIN = 0.78
# id -> (tube, core); the extension offers the same ids in the timer menu.
NEON_COLORS: dict[str, tuple[tuple[int, int, int], tuple[int, int, int]]] = {
    "fire": ((255, 90, 31), (255, 243, 214)),
    "pink": ((255, 45, 149), (255, 230, 243)),
    "violet": ((155, 92, 255), (240, 232, 255)),
    "cyan": ((25, 198, 255), (230, 251, 255)),
    "lime": ((62, 230, 107), (234, 255, 239)),
    "amber": ((255, 179, 26), (255, 246, 220)),
}
DEFAULT_NEON = "fire"
PANEL_MEASUREMENT_MAX_AGE_MS = 7_000
MAX_PANEL_WIDTH_DIP = 1_200
MIN_PAGE_WIDTH_DIP = 160
# Chrome keeps one such child window per visible web contents: the page, the
# Side Panel, docked DevTools.  Its rectangle is the exact page area.
CONTENT_WIDGET_CLASS = "Chrome_RenderWidgetHostHWND"
MIN_CONTENT_SIZE_DIP = 120
# Tabs + toolbar take ~87 DIP; a popup window has a ~31 DIP title strip.
BROWSER_TOOLBAR_MIN_DIP = 56
_POLL_MS = 100
_RENDER_ACK_TIMEOUT_SECONDS = 0.65
_FOCUS_OVERLAY_CLASS_PREFIX = "MegaMozgFocusOverlay_"
_SWP_NOSIZE_VALUE = 0x0001
_SWP_NOMOVE_VALUE = 0x0002
_SWP_NOREDRAW_VALUE = 0x0008
_SWP_NOACTIVATE_VALUE = 0x0010


_log = logging.getLogger(__name__)
Rect = tuple[int, int, int, int]


class OverlayUnavailableError(RuntimeError):
    """The native focus overlay could not be created on this Windows session."""


@dataclass(frozen=True)
class BarGeometry:
    """Physical-pixel rectangle for one native bar window."""

    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True)
class ChromeTarget:
    """The foreground Chrome window, in physical pixels of its monitor."""

    hwnd: int
    outer: Rect
    frame: Rect | None  # visible frame without the invisible resize borders
    dpi: int
    fullscreen: bool
    content: Rect | None  # the web page itself, when Chrome exposes it


def timer_remaining_fraction(state: Mapping[str, Any] | None, now_ms: float | None = None) -> float | None:
    """Return the remaining timer fraction, or ``None`` when it must be hidden."""
    if not isinstance(state, Mapping) or state.get("status") not in {"running", "paused"}:
        return None
    duration = state.get("durationMs")
    elapsed = state.get("elapsedMs", 0)
    if not _finite_number(duration) or duration <= 0 or not _finite_number(elapsed):
        return None
    elapsed = min(max(float(elapsed), 0.0), float(duration))
    if state.get("status") == "paused" and elapsed == 0:
        return None
    if state.get("status") == "running":
        started_at = state.get("startedAt")
        if not _finite_number(started_at):
            return None
        now = time.time() * 1000 if now_ms is None else now_ms
        if not _finite_number(now):
            return None
        elapsed = min(float(duration), elapsed + max(0.0, float(now) - float(started_at)))
    if elapsed >= float(duration):
        return None
    return (float(duration) - elapsed) / float(duration)


def timer_progress_fraction(state: Mapping[str, Any] | None, now_ms: float | None = None) -> float | None:
    """Elapsed share of the timer: the bar fills from left to right like a scale."""
    remaining = timer_remaining_fraction(state, now_ms)
    return None if remaining is None else 1.0 - remaining


def shows_browser_page(target: ChromeTarget) -> bool:
    """Only tabbed browser windows get the bar.

    Chrome popups (Brainalot «Записать», the library window, installed apps) have just
    a title strip above their content instead of tabs and the address bar.
    """
    if target.fullscreen or target.content is None or target.frame is None:
        return True
    scale = max(int(target.dpi), 96) / 96
    return target.content[1] - target.frame[1] >= round(BROWSER_TOOLBAR_MIN_DIP * scale)


def select_overlay_target(
    foreground: ChromeTarget | None, remembered: ChromeTarget | None,
) -> ChromeTarget | None:
    """Prefer an active tabbed Chrome window, otherwise retain the last one.

    Clicking a different application must not stop the visual timer while its
    Chrome page stays uncovered.  Chrome popups are deliberately not targets:
    the prior tabbed browser remains the candidate and the native Z-order check
    decides whether a popup covers its bar.
    """
    return foreground if foreground is not None and shows_browser_page(foreground) else remembered


def is_focus_overlay_window_class(class_name: object) -> bool:
    """Whether a top-level window is another instance of this native overlay."""
    return isinstance(class_name, str) and class_name.startswith(_FOCUS_OVERLAY_CLASS_PREFIX)


def bar_geometry(
    window_rect: tuple[int, int, int, int], dpi: int, *, fullscreen: bool, right_inset_px: int = 0,
) -> BarGeometry:
    """Translate a Chrome outer rectangle into the intended content-edge bar."""
    left, top, right, _bottom = window_rect
    scale = max(int(dpi), 96) / 96
    offset = 0 if fullscreen else round(CONTENT_TOP_OFFSET_DIP * scale)
    return BarGeometry(
        left=left,
        top=top + offset,
        width=max(0, right - left - max(0, int(right_inset_px))),
        height=max(1, round(BAR_HEIGHT_DIP * scale)),
    )


def layered_geometry(bar: BarGeometry, dpi: int) -> BarGeometry:
    """Expand the bright line with transparent pixels reserved for its soft glow."""
    margin = max(2, round(GLOW_MARGIN_DIP * max(int(dpi), 96) / 96))
    return BarGeometry(bar.left, bar.top - margin, bar.width, bar.height + 2 * margin)


def page_content_rect(candidates: Iterable[Rect], dpi: int) -> Rect | None:
    """Pick the web page among the visible content windows of one Chrome window.

    The page starts highest: the Side Panel has its own header below the
    toolbar, docked DevTools sits beside or below the page.  Among equally high
    windows the widest one is the page.  Tiny placeholders are ignored.
    """
    scale = max(int(dpi), 96) / 96
    minimum = round(MIN_CONTENT_SIZE_DIP * scale)
    usable = [rect for rect in candidates if rect[2] - rect[0] >= minimum and rect[3] - rect[1] >= minimum]
    if not usable:
        return None
    top = min(rect[1] for rect in usable)
    tolerance = max(2, round(2 * scale))
    return max((rect for rect in usable if rect[1] - top <= tolerance), key=lambda rect: rect[2] - rect[0])


def target_bar_geometry(target: ChromeTarget, state: Mapping[str, Any] | None, now_ms: float | None = None) -> BarGeometry:
    """Bright-line rectangle along the top edge of the page in ``target``."""
    scale = max(int(target.dpi), 96) / 96
    if target.content is not None:
        left, top, right, _bottom = target.content
        return BarGeometry(left, top, max(0, right - left), max(1, round(BAR_HEIGHT_DIP * scale)))
    frame = target.outer if target.fullscreen or target.frame is None else target.frame
    inset = fresh_panel_width_px(state, target.dpi, max(0, frame[2] - frame[0]), now_ms)
    return bar_geometry(frame, target.dpi, fullscreen=target.fullscreen, right_inset_px=inset)


def rects_intersect(first: Rect, second: Rect) -> bool:
    return first[0] < second[2] and second[0] < first[2] and first[1] < second[3] and second[1] < first[3]


def normalize_neon(value: object) -> str:
    return value if isinstance(value, str) and value in NEON_COLORS else DEFAULT_NEON


def breath_alpha(seconds: float, *, animate: bool = True) -> int:
    """Window-wide alpha of the breathing halo: BREATH_MIN..1 over BREATH_PERIOD_S."""
    if not animate:
        return 255
    wave = 0.5 + 0.5 * math.sin(2 * math.pi * seconds / BREATH_PERIOD_S)
    return round(255 * (BREATH_MIN + (1 - BREATH_MIN) * wave))


def _mix(first: tuple[int, int, int], second: tuple[int, int, int], share: float) -> tuple[float, float, float]:
    return tuple(a + (b - a) * share for a, b in zip(first, second))


def _over(top: tuple[float, float, float, float], bottom: tuple[float, float, float, float]) -> tuple[float, ...]:
    alpha = top[3] + bottom[3] * (1 - top[3])
    if alpha <= 0:
        return (0.0, 0.0, 0.0, 0.0)
    return (*((t * top[3] + b * bottom[3] * (1 - top[3])) / alpha for t, b in zip(top[:3], bottom[:3])), alpha)


def _bgra(color: tuple[float, ...]) -> bytes:
    """Straight RGBA (alpha 0..1) -> premultiplied BGRA for UpdateLayeredWindow."""
    red, green, blue, alpha = color
    return bytes((round(blue * alpha), round(green * alpha), round(red * alpha), round(alpha * 255)))


def neon_pixels(width: int, height: int, fill: int, tube: int, dpi: int, color: str = DEFAULT_NEON) -> bytes:
    """Premultiplied BGRA rows of the neon bar, ``fill`` pixels of tube from the left.

    Rows are uniform along the tube, so each one is built once and repeated;
    only the glowing head is computed per pixel.
    """
    base, core = NEON_COLORS[normalize_neon(color)]
    scale = max(int(dpi), 96) / 96
    fill = max(0, min(width, fill))
    margin = max(0, (height - tube) // 2)
    core_half = max(1, round(NEON_CORE_DIP * scale)) / 2
    outline = max(1, round(scale))
    sigma = 4.5 * scale
    dark = (*_mix(base, (0, 0, 0), 0.8), 0.45)
    rows = []
    for y in range(height):
        if margin <= y < margin + tube:
            offset = abs(y - margin - (tube - 1) / 2)
            pixel = _bgra((*(core if offset < core_half else base), 1.0))
        else:
            distance = margin - y if y < margin else y - (margin + tube - 1)
            glow = (*base, NEON_HALO_ALPHA * math.exp(-distance * distance / (2 * sigma * sigma)))
            pixel = _bgra(_over(dark, glow) if distance <= outline else glow)
        rows.append(pixel * fill + bytes(4 * (width - fill)))
    data = bytearray(b"".join(rows))
    radius = max(3, round(NEON_HEAD_RADIUS_DIP * scale))
    light = _mix(base, (255, 255, 255), 0.45)
    center_y = margin + (tube - 1) / 2
    for y in range(max(0, round(center_y - radius)), min(height, round(center_y + radius) + 1)):
        for x in range(max(0, fill - radius), min(width, fill + radius + 1)):
            distance = math.hypot(x - fill, y - center_y)
            if distance >= radius:
                continue
            alpha = 0.7 * (1 - distance / radius) ** 2
            index = (y * width + x) * 4
            keep = 1 - alpha
            halo = (light[2] * alpha, light[1] * alpha, light[0] * alpha, 255 * alpha)
            data[index:index + 4] = bytes(min(255, round(h + d * keep)) for h, d in zip(halo, data[index:index + 4]))
    return bytes(data)


def fresh_panel_width_px(
    state: Mapping[str, Any] | None, dpi: int, chrome_width_px: int, now_ms: float | None = None,
) -> int:
    """Return a fresh, safely clamped Side Panel width in physical pixels.

    Chrome reports the width in DIP and its timestamp in epoch milliseconds.
    It is optional: a missing, invalid, or stale measurement means that the
    overlay covers the whole Chrome window again.
    """
    if not isinstance(state, Mapping):
        return 0
    panel_dip, measured_at = state.get("panelWidthDip"), state.get("panelMeasuredAt")
    if not _finite_number(panel_dip) or not _finite_number(measured_at) or panel_dip < 0:
        return 0
    now = time.time() * 1000 if now_ms is None else now_ms
    if not _finite_number(now) or not 0 <= float(now) - float(measured_at) <= PANEL_MEASUREMENT_MAX_AGE_MS:
        return 0
    scale = max(int(dpi), 96) / 96
    max_panel = min(round(MAX_PANEL_WIDTH_DIP * scale), max(0, chrome_width_px - round(MIN_PAGE_WIDTH_DIP * scale)))
    return min(round(float(panel_dip) * scale), max_panel)


def is_fullscreen_rect(
    window_rect: tuple[int, int, int, int], monitor_rect: tuple[int, int, int, int], *, tolerance: int = 2,
) -> bool:
    """Whether a window fills its monitor closely enough to be Chrome F11 fullscreen."""
    return all(abs(actual - expected) <= tolerance for actual, expected in zip(window_rect, monitor_rect))


def available() -> bool:
    """Whether this host can create the Windows overlay without starting it."""
    if os.name != "nt":
        return False
    try:
        _windows_api()
    except (AttributeError, OSError):
        return False
    return True


_controller_lock = threading.Lock()
_controller: "_FocusOverlay | None" = None


def update(state: dict[str, Any], visible: bool, color: str = DEFAULT_NEON) -> None:
    """Update the singleton native overlay.

    ``state`` is the normalized timer object from the extension, ``visible``
    is the user's persistent show/hide choice and ``color`` a ``NEON_COLORS``
    id.  The call is safe from FastAPI's worker thread.  Non-Windows
    installations intentionally do nothing.
    """
    if os.name != "nt":
        return
    if not available():
        raise OverlayUnavailableError("Нативный оверлей Windows недоступен в этой сессии.")
    global _controller
    with _controller_lock:
        if _controller is None:
            _controller = _FocusOverlay()
        controller = _controller
    controller.update(state, visible, normalize_neon(color))


def _finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _require_render_acknowledgement(
    acknowledged: bool, *, thread_alive: bool, render_error: BaseException | None,
) -> None:
    """Turn a failed GUI acknowledgement into the API-visible error contract."""
    if render_error is not None:
        raise OverlayUnavailableError("Нативный оверлей Windows не смог отрисовать полосу.") from render_error
    if not thread_alive:
        raise OverlayUnavailableError("Поток нативного оверлея Windows остановился.")
    if not acknowledged:
        raise OverlayUnavailableError("Нативный оверлей Windows не подтвердил отрисовку вовремя.")


def topmost_position_flags(*, resize: bool) -> int:
    """SetWindowPos flags that keep a layered popup above Chrome without focus or repaint flicker."""
    flags = _SWP_NOACTIVATE_VALUE | _SWP_NOREDRAW_VALUE
    if not resize:
        flags |= _SWP_NOMOVE_VALUE | _SWP_NOSIZE_VALUE
    return flags


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

    _LONG_PTR = ctypes.c_ssize_t
    _LRESULT = ctypes.c_ssize_t
    _UINT_PTR = ctypes.c_size_t
    # Python 3.12's ``ctypes.wintypes`` is missing several named Win32 handles
    # on some Windows builds (notably HCURSOR).  All of them are pointer-sized
    # handles, so define local aliases rather than making import availability
    # depend on a CPython-version-specific convenience name.
    _HANDLE = ctypes.c_void_p
    _HWND = ctypes.c_void_p
    _HINSTANCE = ctypes.c_void_p
    _HICON = ctypes.c_void_p
    _HCURSOR = ctypes.c_void_p
    _HBRUSH = ctypes.c_void_p
    _HDC = ctypes.c_void_p
    _HMENU = ctypes.c_void_p
    _HBITMAP = ctypes.c_void_p
    _HGDIOBJ = ctypes.c_void_p
    _WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, _HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    _ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, _HWND, wintypes.LPARAM)
    _WINEVENTPROC = ctypes.WINFUNCTYPE(
        None, _HANDLE, wintypes.DWORD, _HWND, wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD,
    )
    _dwmapi = ctypes.WinDLL("dwmapi")

    class _WNDCLASSEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", _WNDPROC),
            ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", _HINSTANCE),
            ("hIcon", _HICON), ("hCursor", _HCURSOR), ("hbrBackground", _HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", _HICON),
        ]

    class _RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class _MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", _RECT), ("rcWork", _RECT), ("dwFlags", wintypes.DWORD)]

    class _PAINTSTRUCT(ctypes.Structure):
        _fields_ = [
            ("hdc", _HDC), ("fErase", wintypes.BOOL), ("rcPaint", _RECT),
            ("fRestore", wintypes.BOOL), ("fIncUpdate", wintypes.BOOL), ("rgbReserved", wintypes.BYTE * 32),
        ]

    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class _SIZE(ctypes.Structure):
        _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]

    class _BLENDFUNCTION(ctypes.Structure):
        _fields_ = [
            ("BlendOp", wintypes.BYTE), ("BlendFlags", wintypes.BYTE),
            ("SourceConstantAlpha", wintypes.BYTE), ("AlphaFormat", wintypes.BYTE),
        ]

    class _BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
            ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
            ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
        ]

    class _BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 1)]

    class _MSG(ctypes.Structure):
        _fields_ = [
            ("hwnd", _HWND), ("message", wintypes.UINT), ("wParam", wintypes.WPARAM),
            ("lParam", wintypes.LPARAM), ("time", wintypes.DWORD), ("pt", _POINT), ("lPrivate", wintypes.DWORD),
        ]

    _user32.DefWindowProcW.argtypes = (_HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    _user32.DefWindowProcW.restype = _LRESULT
    _user32.RegisterClassExW.argtypes = (ctypes.POINTER(_WNDCLASSEXW),)
    _user32.RegisterClassExW.restype = wintypes.WORD
    _user32.CreateWindowExW.argtypes = (
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        _HWND, _HMENU, _HINSTANCE, ctypes.c_void_p,
    )
    _user32.CreateWindowExW.restype = _HWND
    _user32.GetForegroundWindow.restype = _HWND
    _user32.PostMessageW.argtypes = (_HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    _user32.PostMessageW.restype = wintypes.BOOL
    _user32.DestroyWindow.argtypes = (_HWND,)
    _user32.ShowWindow.argtypes = (_HWND, ctypes.c_int)
    _user32.IsWindowVisible.argtypes = (_HWND,)
    _user32.IsIconic.argtypes = (_HWND,)
    _user32.SetWindowPos.argtypes = (_HWND, _HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)
    _user32.SetWindowPos.restype = wintypes.BOOL
    _user32.SetTimer.argtypes = (_HWND, _UINT_PTR, wintypes.UINT, ctypes.c_void_p)
    _user32.SetTimer.restype = _UINT_PTR
    _user32.KillTimer.argtypes = (_HWND, _UINT_PTR)
    _user32.KillTimer.restype = wintypes.BOOL
    _user32.SystemParametersInfoW.argtypes = (wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT)
    _user32.SystemParametersInfoW.restype = wintypes.BOOL
    _user32.GetWindowRect.argtypes = (_HWND, ctypes.POINTER(_RECT))
    _user32.GetWindowRect.restype = wintypes.BOOL
    _user32.GetClientRect.argtypes = (_HWND, ctypes.POINTER(_RECT))
    _user32.GetClientRect.restype = wintypes.BOOL
    _user32.GetDpiForWindow.argtypes = (_HWND,)
    _user32.GetDpiForWindow.restype = wintypes.UINT
    _user32.GetWindowThreadProcessId.argtypes = (_HWND, ctypes.POINTER(wintypes.DWORD))
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.MonitorFromWindow.argtypes = (_HWND, wintypes.DWORD)
    _user32.MonitorFromWindow.restype = _HANDLE
    _user32.GetMonitorInfoW.argtypes = (_HANDLE, ctypes.POINTER(_MONITORINFO))
    _user32.GetMonitorInfoW.restype = wintypes.BOOL
    _user32.GetClassNameW.argtypes = (_HWND, wintypes.LPWSTR, ctypes.c_int)
    _user32.EnumChildWindows.argtypes = (_HWND, _ENUMPROC, wintypes.LPARAM)
    _user32.EnumThreadWindows.argtypes = (wintypes.DWORD, _ENUMPROC, wintypes.LPARAM)
    _user32.GetWindow.argtypes = (_HWND, wintypes.UINT)
    _user32.GetWindow.restype = _HWND
    _user32.GetAncestor.argtypes = (_HWND, wintypes.UINT)
    _user32.GetAncestor.restype = _HWND
    _user32.GetWindowLongPtrW.argtypes = (_HWND, ctypes.c_int)
    _user32.GetWindowLongPtrW.restype = _LONG_PTR
    _user32.SetWinEventHook.argtypes = (
        wintypes.DWORD, wintypes.DWORD, _HANDLE, _WINEVENTPROC, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
    )
    _user32.SetWinEventHook.restype = _HANDLE
    _user32.UnhookWinEvent.argtypes = (_HANDLE,)
    _user32.UnhookWinEvent.restype = wintypes.BOOL
    _dwmapi.DwmGetWindowAttribute.argtypes = (_HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
    _dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
    _user32.BeginPaint.argtypes = (_HWND, ctypes.POINTER(_PAINTSTRUCT))
    _user32.BeginPaint.restype = _HDC
    _user32.EndPaint.argtypes = (_HWND, ctypes.POINTER(_PAINTSTRUCT))
    _user32.UpdateLayeredWindow.argtypes = (
        _HWND, _HDC, ctypes.POINTER(_POINT), ctypes.POINTER(_SIZE), _HDC,
        ctypes.POINTER(_POINT), wintypes.DWORD, ctypes.POINTER(_BLENDFUNCTION), wintypes.DWORD,
    )
    _user32.UpdateLayeredWindow.restype = wintypes.BOOL
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.OpenProcess.restype = _HANDLE
    _kernel32.GetModuleHandleW.restype = _HINSTANCE
    _kernel32.QueryFullProcessImageNameW.argtypes = (_HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
    _gdi32.CreateCompatibleDC.argtypes = (_HDC,)
    _gdi32.CreateCompatibleDC.restype = _HDC
    _gdi32.CreateDIBSection.argtypes = (
        _HDC, ctypes.POINTER(_BITMAPINFO), wintypes.UINT, ctypes.POINTER(ctypes.c_void_p),
        _HANDLE, wintypes.DWORD,
    )
    _gdi32.CreateDIBSection.restype = _HBITMAP
    _gdi32.SelectObject.argtypes = (_HDC, _HGDIOBJ)
    _gdi32.SelectObject.restype = _HGDIOBJ
    _gdi32.DeleteObject.argtypes = (_HGDIOBJ,)

    _WM_CLOSE = 0x0010
    _WM_DESTROY = 0x0002
    _WM_PAINT = 0x000F
    _WM_TIMER = 0x0113
    _WM_NCHITTEST = 0x0084
    _WM_MOUSEACTIVATE = 0x0021
    _WM_APP_STATE = 0x8001
    _HTTRANSPARENT = -1
    _MA_NOACTIVATE = 3
    _WS_POPUP = 0x80000000
    _WS_EX_TRANSPARENT = 0x00000020
    _WS_EX_TOOLWINDOW = 0x00000080
    _WS_EX_LAYERED = 0x00080000
    _WS_EX_NOACTIVATE = 0x08000000
    _WS_EX_TOPMOST = 0x00000008
    _GWL_EXSTYLE = -20
    _GW_HWNDPREV = 3
    _GW_OWNER = 4
    _GA_ROOTOWNER = 3
    _DWMWA_EXTENDED_FRAME_BOUNDS = 9
    _HWND_TOPMOST = _HWND(-1)
    _SWP_NOSIZE = _SWP_NOSIZE_VALUE
    _SWP_NOMOVE = _SWP_NOMOVE_VALUE
    _SWP_NOACTIVATE = _SWP_NOACTIVATE_VALUE
    _SWP_NOREDRAW = _SWP_NOREDRAW_VALUE
    _SWP_SHOWWINDOW = 0x0040
    _SW_HIDE = 0
    _SW_SHOWNOACTIVATE = 4
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _MONITOR_DEFAULTTONEAREST = 2
    _ERROR_CLASS_ALREADY_EXISTS = 1410
    _DIB_RGB_COLORS = 0
    _BI_RGB = 0
    _ULW_ALPHA = 0x00000002
    _POLL_TIMER = 1
    _BREATH_TIMER = 2
    _BREATH_MS = 50
    _SPI_GETCLIENTAREAANIMATION = 0x1042
    _AC_SRC_OVER = 0
    _AC_SRC_ALPHA = 1
    _EVENT_SYSTEM_FOREGROUND = 0x0003
    _EVENT_OBJECT_LOCATIONCHANGE = 0x800B
    _WINEVENT_OUTOFCONTEXT = 0x0000
    _WINEVENT_SKIPOWNPROCESS = 0x0002
    _OBJID_WINDOW = 0
    _CHILDID_SELF = 0

    def _windows_api() -> None:
        # Evaluating these attributes catches stripped-down or incompatible Win32 hosts.
        _ = (_user32.CreateWindowExW, _user32.SetWindowPos, _user32.GetForegroundWindow, _kernel32.OpenProcess)

    class _FocusOverlay:
        def __init__(self) -> None:
            self._state_lock = threading.Lock()
            self._command_lock = threading.Lock()
            self._health_lock = threading.Lock()
            self._state: Mapping[str, Any] | None = None
            self._visible = False
            self._color = DEFAULT_NEON
            self._breath = 255
            self._breathing = False
            self._ready = threading.Event()
            self._thread = threading.Thread(target=self._run, name="MegaMozg focus overlay", daemon=True)
            self._hwnd: int | None = None
            self._shown = False
            self._last_rect: BarGeometry | None = None
            self._last_pixels: tuple[int, int, int, int, str] | None = None
            self._mem_dc: int | None = None
            self._dib: int | None = None
            self._old_bitmap: int | None = None
            self._bits: int | None = None
            self._surface_size: tuple[int, int] | None = None
            self._start_error: BaseException | None = None
            self._render_error: BaseException | None = None
            self._pending_ack: threading.Event | None = None
            self._restacks = 0
            self._recreated_at = -math.inf
            self._instance = None
            # WinEvent hooks move the bar together with Chrome instead of on the next poll.
            self._win_event_proc = None
            self._foreground_hook: int | None = None
            self._location_hook: int | None = None
            self._hooked_pid: int | None = None
            self._target_hwnd: int | None = None
            self._refreshing = False
            self._thread.start()
            if not self._ready.wait(2):
                raise OverlayUnavailableError("Нативный оверлей Windows не запустился за 2 секунды.")
            if self._start_error is not None:
                raise OverlayUnavailableError("Не удалось создать нативный оверлей Windows.") from self._start_error

        def update(self, state: Mapping[str, Any] | None, visible: bool, color: str = DEFAULT_NEON) -> None:
            # The loopback endpoint must not acknowledge a timer change until the
            # GUI thread has either hidden the bar or completed UpdateLayeredWindow.
            # It remains comfortably below the extension's 1.5-second timeout.
            with self._command_lock:
                self._assert_healthy()
                with self._state_lock:
                    self._state = dict(state) if isinstance(state, Mapping) else None
                    self._visible = bool(visible)
                    self._color = normalize_neon(color)
                acknowledgement = threading.Event()
                with self._health_lock:
                    self._pending_ack = acknowledgement
                hwnd = self._hwnd
                if not hwnd or not _user32.PostMessageW(hwnd, _WM_APP_STATE, 0, 0):
                    with self._health_lock:
                        if self._pending_ack is acknowledgement:
                            self._pending_ack = None
                    self._assert_healthy()
                    raise OverlayUnavailableError("Не удалось передать состояние нативному оверлею Windows.")
                acknowledged = acknowledgement.wait(_RENDER_ACK_TIMEOUT_SECONDS)
                with self._health_lock:
                    if self._pending_ack is acknowledgement:
                        self._pending_ack = None
                    render_error = self._render_error
                _require_render_acknowledgement(
                    acknowledged, thread_alive=self._thread.is_alive(), render_error=render_error,
                )

        def _assert_healthy(self) -> None:
            with self._health_lock:
                render_error = self._render_error
            _require_render_acknowledgement(True, thread_alive=self._thread.is_alive(), render_error=render_error)

        def _acknowledge_render(self) -> None:
            with self._health_lock:
                acknowledgement, self._pending_ack = self._pending_ack, None
            if acknowledgement is not None:
                acknowledgement.set()

        def _record_render_failure(self, error: BaseException) -> None:
            with self._health_lock:
                if self._render_error is None:
                    self._render_error = error
                acknowledgement, self._pending_ack = self._pending_ack, None
            if acknowledgement is not None:
                acknowledgement.set()

        def _run(self) -> None:
            try:
                # Keep Win32 coordinates physical on every monitor without changing the
                # DPI behavior of the FastAPI thread.
                set_thread_dpi = getattr(_user32, "SetThreadDpiAwarenessContext", None)
                if set_thread_dpi:
                    set_thread_dpi(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
                self._create_window()
                self._ready.set()
                _user32.SetTimer(self._hwnd, _POLL_TIMER, _POLL_MS, None)
                msg = _MSG()
                while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                    _user32.TranslateMessage(ctypes.byref(msg))
                    _user32.DispatchMessageW(ctypes.byref(msg))
            except BaseException as exc:  # Startup must surface a useful API error.
                self._start_error = exc
                self._record_render_failure(exc)
                self._ready.set()

        def _create_window(self) -> None:
            self._wndproc = _WNDPROC(self._window_proc)
            self._class_name = f"{_FOCUS_OVERLAY_CLASS_PREFIX}{id(self):x}"
            wc = _WNDCLASSEXW(
                cbSize=ctypes.sizeof(_WNDCLASSEXW), style=0, lpfnWndProc=self._wndproc,
                cbClsExtra=0, cbWndExtra=0, hInstance=_kernel32.GetModuleHandleW(None),
                hIcon=None, hCursor=None, hbrBackground=None, lpszMenuName=None,
                lpszClassName=self._class_name, hIconSm=None,
            )
            atom = _user32.RegisterClassExW(ctypes.byref(wc))
            if not atom and ctypes.get_last_error() != _ERROR_CLASS_ALREADY_EXISTS:
                raise ctypes.WinError(ctypes.get_last_error())
            self._instance = wc.hInstance
            self._hwnd = self._new_bar_window()
            # Out-of-context hooks call back on this thread from its message loop.
            # The poll stays as the fallback when a hook cannot be installed.
            self._win_event_proc = _WINEVENTPROC(self._on_win_event)
            self._foreground_hook = _user32.SetWinEventHook(
                _EVENT_SYSTEM_FOREGROUND, _EVENT_SYSTEM_FOREGROUND, None, self._win_event_proc, 0, 0,
                _WINEVENT_OUTOFCONTEXT | _WINEVENT_SKIPOWNPROCESS,
            )
            if not self._foreground_hook:
                _log.warning("Brainalot focus bar: no foreground hook (%d); polling only", ctypes.get_last_error())

        def _new_bar_window(self) -> int:
            """A hidden bar window that is topmost from its creation.

            Observed on 2026-09-24: Windows silently ignores SetWindowPos(HWND_TOPMOST)
            from the background service process (the call succeeds, the window stays
            under Chrome), while WS_EX_TOPMOST given to CreateWindowExW always holds.
            """
            hwnd = _user32.CreateWindowExW(
                _WS_EX_TOPMOST | _WS_EX_LAYERED | _WS_EX_TRANSPARENT | _WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE,
                self._class_name, "", _WS_POPUP, 0, 0, 1, 1, None, None, self._instance, None,
            )
            if not hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            # Also put it on top of the topmost band; harmless where Windows ignores it.
            _user32.SetWindowPos(hwnd, _HWND_TOPMOST, 0, 0, 0, 0, topmost_position_flags(resize=False))
            return hwnd

        def _is_topmost(self) -> bool:
            return bool(self._hwnd and _user32.GetWindowLongPtrW(self._hwnd, _GWL_EXSTYLE) & _WS_EX_TOPMOST)

        def _recreate_window(self) -> None:
            """Replace a bar that lost its topmost state with a fresh, topmost one."""
            old = self._hwnd
            self._hwnd = self._new_bar_window()
            _user32.SetTimer(self._hwnd, _POLL_TIMER, _POLL_MS, None)
            self._shown = False
            self._breathing = False
            self._last_rect = None
            self._last_pixels = None
            if old:
                # Its WM_DESTROY must not end the message loop: see _window_proc.
                _user32.DestroyWindow(old)

        def _on_win_event(self, _hook, event, hwnd, id_object, id_child, _thread, _time) -> None:
            if event == _EVENT_OBJECT_LOCATIONCHANGE and (
                    id_object != _OBJID_WINDOW or id_child != _CHILDID_SELF or not hwnd or hwnd != self._target_hwnd):
                return
            if self._refreshing:
                return
            try:
                self._refresh()
            except BaseException as exc:
                # The poll reports persistent failures; a missed follow frame is not fatal.
                _log.warning("Brainalot focus bar could not follow Chrome: %s", exc)

        def _follow(self, chrome: int | None) -> None:
            """Listen to location changes of the Chrome process that owns the target window."""
            self._target_hwnd = chrome
            if not chrome or self._win_event_proc is None:
                return
            pid = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(chrome, ctypes.byref(pid))
            if not pid.value or pid.value == self._hooked_pid:
                return
            if self._location_hook:
                _user32.UnhookWinEvent(self._location_hook)
            self._location_hook = _user32.SetWinEventHook(
                _EVENT_OBJECT_LOCATIONCHANGE, _EVENT_OBJECT_LOCATIONCHANGE, None, self._win_event_proc,
                pid.value, 0, _WINEVENT_OUTOFCONTEXT,
            )
            # Remember the process even on failure: the poll keeps working and the
            # hook is not retried on every tick.
            self._hooked_pid = pid.value
            if not self._location_hook:
                _log.warning("Brainalot focus bar: no location hook for Chrome (%d); polling only", ctypes.get_last_error())

        def _unhook(self) -> None:
            for hook in (self._location_hook, self._foreground_hook):
                if hook:
                    _user32.UnhookWinEvent(hook)
            self._location_hook = self._foreground_hook = None
            self._hooked_pid = None

        def _window_proc(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
            if message == _WM_NCHITTEST:
                return _HTTRANSPARENT
            if message == _WM_MOUSEACTIVATE:
                return _MA_NOACTIVATE
            if message == _WM_TIMER and wparam == _BREATH_TIMER:
                # Only the window's constant alpha changes; a failed frame is not
                # worth killing the bar (and must not acknowledge a state update).
                try:
                    self._breathe()
                except BaseException as exc:
                    _log.warning("Brainalot focus bar breathing frame failed: %s", exc)
                return 0
            if message in {_WM_TIMER, _WM_APP_STATE}:
                try:
                    self._refresh()
                except BaseException as exc:
                    # ctypes callbacks swallow exceptions.  Record it explicitly so
                    # the HTTP caller receives an error instead of a false 200.
                    self._record_render_failure(exc)
                    _user32.DestroyWindow(hwnd)
                    return 0
                self._acknowledge_render()
                return 0
            if message == _WM_PAINT:
                paint = _PAINTSTRUCT()
                _user32.BeginPaint(hwnd, ctypes.byref(paint))
                _user32.EndPaint(hwnd, ctypes.byref(paint))
                return 0
            if message == _WM_CLOSE:
                _user32.DestroyWindow(hwnd)
                return 0
            if message == _WM_DESTROY:
                if hwnd != self._hwnd:
                    return 0  # a replaced bar window; the current one keeps the loop alive
                self._unhook()
                _user32.PostQuitMessage(0)
                return 0
            return _user32.DefWindowProcW(hwnd, message, wparam, lparam)

        def _refresh(self) -> None:
            self._refreshing = True
            try:
                self._render()
            finally:
                self._refreshing = False

        def _render(self) -> None:
            with self._state_lock:
                state, visible, color = self._state, self._visible, self._color
            fraction = timer_progress_fraction(state)
            hwnd = self._hwnd
            foreground = self._foreground_chrome() if visible and fraction is not None else None
            remembered = self._remembered_chrome() if visible and fraction is not None else None
            target = select_overlay_target(foreground, remembered)
            if foreground is not None and shows_browser_page(foreground):
                # A later activation of another full browser window intentionally
                # moves the bar to it; a popup never steals the remembered target.
                self._follow(foreground.hwnd)
            if not hwnd or target is None or not shows_browser_page(target):
                self._hide()
                return
            bright_bar = target_bar_geometry(target, state)
            rect = layered_geometry(bright_bar, target.dpi)
            # A topmost overlay would otherwise cross menus, unrelated Chrome
            # popups, or another application placed over the remembered page.
            if bright_bar.width <= 0 or self._window_above_target_covers(target.hwnd, rect):
                self._hide()
                return
            # A short lit stub marks a timer that has just started.
            stub = min(bright_bar.width, round(4 * max(target.dpi, 96) / 96))
            width = max(stub, round(bright_bar.width * fraction))
            if rect != self._last_rect:
                self._set_topmost(rect, resize=True)
                self._last_rect = rect
            if not self._shown:
                self._start_breathing()
                _user32.ShowWindow(hwnd, _SW_SHOWNOACTIVATE)
            # A pure move (dragging Chrome) only repositions the window above;
            # the pixels are redrawn for a new size, progress or colour.
            pixels_key = (rect.width, rect.height, width, bright_bar.height, color)
            if pixels_key != self._last_pixels:
                self._draw_layer(rect, width, bright_bar.height, target.dpi, color)
                self._last_pixels = pixels_key
            if self._keep_above(target.hwnd):
                # A fresh topmost window replaced the bar: show and draw it right away.
                self._render()
                return
            self._shown = True

        def _start_breathing(self) -> None:
            enabled = wintypes.BOOL(True)
            _user32.SystemParametersInfoW(_SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0)
            # Windows "Show animations" off: a steady bar, no timer.
            self._breathing = bool(enabled.value)
            self._breath = breath_alpha(time.monotonic(), animate=self._breathing)
            if self._breathing:
                _user32.SetTimer(self._hwnd, _BREATH_TIMER, _BREATH_MS, None)

        def _breathe(self) -> None:
            if not self._shown or not self._breathing:
                return
            alpha = breath_alpha(time.monotonic())
            if alpha == self._breath:
                return
            self._breath = alpha
            blend = _BLENDFUNCTION(_AC_SRC_OVER, 0, alpha, _AC_SRC_ALPHA)
            # No source DC: keep the drawn pixels, change only the constant alpha.
            _user32.UpdateLayeredWindow(self._hwnd, None, None, None, None, None, 0, ctypes.byref(blend), _ULW_ALPHA)

        def _keep_above(self, chrome: int) -> bool:
            """Restore the Z-order whenever something has put the bar under Chrome.

            The service's window was observed without its topmost state while
            Chrome stayed in front, so the state is checked on every tick.  An
            unconditional reassert would also cover Chrome's newer menus.  When
            Windows refuses to make the existing window topmost again, the bar is
            replaced by a window created topmost (at most once in 5 seconds).
            Returns True when the window was replaced.
            """
            if not self._is_below(chrome):
                return False
            self._restacks += 1
            if self._restacks == 1 or self._restacks % 100 == 0:
                _log.warning("Brainalot focus bar was below Chrome (%d times); restoring topmost", self._restacks)
            if not _user32.SetWindowPos(self._hwnd, _HWND_TOPMOST, 0, 0, 0, 0, topmost_position_flags(resize=False)):
                _log.warning("SetWindowPos(HWND_TOPMOST) failed: %d", ctypes.get_last_error())
            if self._is_topmost() or time.monotonic() - self._recreated_at < 5:
                return False
            self._recreated_at = time.monotonic()
            _log.warning("Brainalot focus bar: Windows kept the bar below Chrome; recreating it topmost")
            self._recreate_window()
            return True

        def _is_below(self, chrome: int) -> bool:
            if not _user32.GetWindowLongPtrW(self._hwnd, _GWL_EXSTYLE) & _WS_EX_TOPMOST:
                return True
            # Chrome itself may be topmost (an "always on top" utility); then it
            # can still rise above the bar.
            above = _user32.GetWindow(self._hwnd, _GW_HWNDPREV)
            for _ in range(1024):
                if not above:
                    return False
                if above == chrome:
                    return True
                above = _user32.GetWindow(above, _GW_HWNDPREV)
            return False

        def _window_above_target_covers(self, chrome: int, rect: BarGeometry) -> bool:
            """Whether any visible top-level window above ``chrome`` covers the bar.

            This also catches Chrome popups that belong to another browser
            window.  The overlay is skipped because it is intentionally in the
            topmost band and is not an occluder of the browser page.
            """
            bar = (rect.left, rect.top, rect.left + rect.width, rect.top + rect.height)
            above = _user32.GetWindow(chrome, _GW_HWNDPREV)
            for _ in range(1024):
                if not above:
                    return False
                if (above != self._hwnd and not self._is_focus_overlay_window(above)
                        and _user32.IsWindowVisible(above) and not _user32.IsIconic(above)):
                    box = _RECT()
                    if _user32.GetWindowRect(above, ctypes.byref(box)) and rects_intersect(
                            (box.left, box.top, box.right, box.bottom), bar):
                        return True
                above = _user32.GetWindow(above, _GW_HWNDPREV)
            return False

        @staticmethod
        def _is_focus_overlay_window(hwnd: int) -> bool:
            name = ctypes.create_unicode_buffer(64)
            _user32.GetClassNameW(hwnd, name, len(name))
            return is_focus_overlay_window_class(name.value)

        def _remembered_chrome(self) -> ChromeTarget | None:
            target = self._chrome_target(self._target_hwnd)
            if target is None:
                # Minimized or closed Chrome must not leave a stale HWND around.
                self._follow(None)
            return target

        def _chrome_target(self, hwnd: int | None) -> ChromeTarget | None:
            if hwnd:
                # An active Chrome bubble or menu belongs to its browser window.
                hwnd = _user32.GetAncestor(hwnd, _GA_ROOTOWNER) or hwnd
            if not hwnd or not _user32.IsWindowVisible(hwnd) or _user32.IsIconic(hwnd) or not self._is_chrome(hwnd):
                return None
            rect = _RECT()
            if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return None
            outer = (rect.left, rect.top, rect.right, rect.bottom)
            monitor = _user32.MonitorFromWindow(hwnd, _MONITOR_DEFAULTTONEAREST)
            info = _MONITORINFO(cbSize=ctypes.sizeof(_MONITORINFO))
            if not monitor or not _user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                return None
            monitor_rect = (info.rcMonitor.left, info.rcMonitor.top, info.rcMonitor.right, info.rcMonitor.bottom)
            dpi = int(_user32.GetDpiForWindow(hwnd) or 96)
            frame = _RECT()
            has_frame = _dwmapi.DwmGetWindowAttribute(
                hwnd, _DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(frame), ctypes.sizeof(frame)) == 0
            return ChromeTarget(
                hwnd=hwnd, outer=outer, dpi=dpi, fullscreen=is_fullscreen_rect(outer, monitor_rect),
                frame=(frame.left, frame.top, frame.right, frame.bottom) if has_frame else None,
                content=page_content_rect(self._content_rects(hwnd), dpi),
            )

        def _set_topmost(self, rect: BarGeometry | None, *, resize: bool) -> None:
            if not self._hwnd:
                raise OverlayUnavailableError("У нативного оверлея Windows нет окна.")
            if rect is None:
                left = top = width = height = 0
            else:
                left, top, width, height = rect.left, rect.top, rect.width, rect.height
            if not _user32.SetWindowPos(
                self._hwnd, _HWND_TOPMOST, left, top, width, height, topmost_position_flags(resize=resize),
            ):
                raise ctypes.WinError(ctypes.get_last_error())

        def _hide(self) -> None:
            if self._shown and self._hwnd:
                _user32.KillTimer(self._hwnd, _BREATH_TIMER)
                _user32.ShowWindow(self._hwnd, _SW_HIDE)
            self._shown = False
            self._breathing = False
            self._last_rect = None
            self._last_pixels = None

        def _draw_layer(self, rect: BarGeometry, fill: int, tube: int, dpi: int, color: str) -> None:
            self._ensure_surface(rect.width, rect.height)
            if not self._mem_dc or not self._bits:
                raise OverlayUnavailableError("Не удалось создать прозрачную поверхность оверлея.")
            data = neon_pixels(rect.width, rect.height, fill, tube, dpi, color)
            ctypes.memmove(self._bits, data, len(data))
            destination = _POINT(rect.left, rect.top)
            size = _SIZE(rect.width, rect.height)
            source = _POINT(0, 0)
            # Redraws keep the current breathing alpha, so the halo never jumps.
            blend = _BLENDFUNCTION(_AC_SRC_OVER, 0, self._breath, _AC_SRC_ALPHA)
            if not _user32.UpdateLayeredWindow(self._hwnd, None, ctypes.byref(destination), ctypes.byref(size), self._mem_dc,
                                                ctypes.byref(source), 0, ctypes.byref(blend), _ULW_ALPHA):
                raise ctypes.WinError(ctypes.get_last_error())

        def _ensure_surface(self, width: int, height: int) -> None:
            if self._surface_size == (width, height):
                return
            if self._mem_dc is None:
                self._mem_dc = _gdi32.CreateCompatibleDC(None)
                if not self._mem_dc:
                    raise ctypes.WinError(ctypes.get_last_error())
            if self._dib:
                _gdi32.SelectObject(self._mem_dc, self._old_bitmap)
                _gdi32.DeleteObject(self._dib)
            info = _BITMAPINFO()
            info.bmiHeader = _BITMAPINFOHEADER(
                biSize=ctypes.sizeof(_BITMAPINFOHEADER), biWidth=width, biHeight=-height,
                biPlanes=1, biBitCount=32, biCompression=_BI_RGB, biSizeImage=width * height * 4,
                biXPelsPerMeter=0, biYPelsPerMeter=0, biClrUsed=0, biClrImportant=0,
            )
            bits = ctypes.c_void_p()
            dib = _gdi32.CreateDIBSection(self._mem_dc, ctypes.byref(info), _DIB_RGB_COLORS, ctypes.byref(bits), None, 0)
            if not dib or not bits.value:
                raise ctypes.WinError(ctypes.get_last_error())
            self._old_bitmap = _gdi32.SelectObject(self._mem_dc, dib)
            self._dib, self._bits, self._surface_size = dib, bits.value, (width, height)

        def _foreground_chrome(self) -> ChromeTarget | None:
            return self._chrome_target(_user32.GetForegroundWindow())

        @staticmethod
        def _content_rects(chrome: int) -> list[Rect]:
            found: list[Rect] = []
            name = ctypes.create_unicode_buffer(64)

            @_ENUMPROC
            def visit(child: int, _: int) -> bool:
                _user32.GetClassNameW(child, name, len(name))
                if name.value == CONTENT_WIDGET_CLASS and _user32.IsWindowVisible(child):
                    box = _RECT()
                    if _user32.GetWindowRect(child, ctypes.byref(box)):
                        found.append((box.left, box.top, box.right, box.bottom))
                return True

            _user32.EnumChildWindows(chrome, visit, 0)
            return found

        @staticmethod
        def _is_chrome(hwnd: int) -> bool:
            name = ctypes.create_unicode_buffer(260)
            _user32.GetClassNameW(hwnd, name, len(name))
            if name.value != "Chrome_WidgetWin_1":
                return False
            pid = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            process = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
            if not process:
                return False
            try:
                length = wintypes.DWORD(len(name))
                if not _kernel32.QueryFullProcessImageNameW(process, 0, name, ctypes.byref(length)):
                    return False
                return Path(name.value).name.lower() == "chrome.exe"
            finally:
                _kernel32.CloseHandle(process)

else:
    def _windows_api() -> None:
        raise OSError("Windows only")
