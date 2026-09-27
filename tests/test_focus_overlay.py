import pytest

from megamozg.focus_overlay import (
    BAR_HEIGHT_DIP,
    BREATH_PERIOD_S,
    CONTENT_TOP_OFFSET_DIP,
    NEON_COLORS,
    ChromeTarget,
    breath_alpha,
    neon_pixels,
    normalize_neon,
    available,
    bar_geometry,
    fresh_panel_width_px,
    is_fullscreen_rect,
    is_focus_overlay_window_class,
    page_content_rect,
    rects_intersect,
    select_overlay_target,
    shows_browser_page,
    target_bar_geometry,
    timer_progress_fraction,
    timer_remaining_fraction,
    topmost_position_flags,
    update,
    _require_render_acknowledgement,
)

# Measured 2026-09-24 on the owner's Chrome, maximized on a 96-DPI monitor at
# x=2560..4480 with the Brainalot Side Panel open: page, Side Panel, 1x1 placeholder.
LIVE_CONTENT_WINDOWS = [(2568, 213, 3970, 1158), (3979, 252, 4471, 1157), (3402, 211, 3403, 212)]
LIVE_CHROME = ChromeTarget(hwnd=1, outer=(2552, 118, 4488, 1174), frame=(2560, 126, 4480, 1166), dpi=96,
                           fullscreen=False, content=(2568, 213, 3970, 1158))


def test_running_timer_uses_epoch_start_time_for_remaining_fraction():
    state = {"status": "running", "durationMs": 100_000, "elapsedMs": 10_000, "startedAt": 1_000_000}
    assert timer_remaining_fraction(state, 1_030_000) == 0.6


def test_paused_timer_uses_accumulated_elapsed_time_only():
    state = {"status": "paused", "durationMs": 100_000, "elapsedMs": 25_000, "startedAt": None}
    assert timer_remaining_fraction(state, 9_999_999) == 0.75


def test_freshly_reset_paused_timer_has_no_chrome_bar():
    assert timer_remaining_fraction({"status": "paused", "durationMs": 1_500_000,
                                     "elapsedMs": 0, "startedAt": None}) is None


def test_idle_finished_and_invalid_timer_states_hide_the_overlay():
    assert timer_remaining_fraction({"status": "idle", "durationMs": 100, "elapsedMs": 0}) is None
    assert timer_remaining_fraction({"status": "finished", "durationMs": 100, "elapsedMs": 100}) is None
    assert timer_remaining_fraction({"status": "running", "durationMs": 100, "elapsedMs": 0}) is None
    assert timer_remaining_fraction({"status": "running", "durationMs": 100, "elapsedMs": 100, "startedAt": 0}, 0) is None


def _pixel(data, width, x, y):
    index = (y * width + x) * 4
    return tuple(data[index:index + 4])  # premultiplied B, G, R, A


def test_neon_tube_has_light_core_saturated_edges_dark_outline_and_fading_halo():
    width, height, fill, tube = 60, 30, 20, 6  # 96 DPI: 12 px of halo above and below
    data = neon_pixels(width, height, fill, tube, 96, "fire")
    assert len(data) == width * height * 4
    (red, green, blue), (core_red, core_green, core_blue) = NEON_COLORS["fire"]
    assert _pixel(data, width, 3, 14) == (core_blue, core_green, core_red, 255)  # core rows 14-15
    assert _pixel(data, width, 3, 12) == (blue, green, red, 255)  # tube edge rows 12 and 17
    outline = _pixel(data, width, 3, 18)  # first row under the tube
    assert outline[3] > 200 and outline[2] < red * outline[3] / 255 * 0.7
    assert 0 < _pixel(data, width, 3, 0)[3] < 12  # the halo fades out at its edge
    assert _pixel(data, width, 3, 22)[3] > _pixel(data, width, 3, 26)[3]


def test_neon_head_glows_past_the_fill_and_the_rest_stays_clear():
    width, height, fill = 60, 30, 20
    data = neon_pixels(width, height, fill, 6, 96, "cyan")
    assert _pixel(data, width, fill + 4, 14)[3] > 0  # soft head beyond the fill
    assert all(_pixel(data, width, fill + 14, y) == (0, 0, 0, 0) for y in range(height))
    assert all(_pixel(data, width, width - 1, y) == (0, 0, 0, 0) for y in range(height))


def test_every_neon_color_is_distinct_and_unknown_ids_fall_back():
    tubes = {_pixel(neon_pixels(10, 30, 10, 6, 96, name), 10, 1, 12) for name in NEON_COLORS}
    assert len(tubes) == len(NEON_COLORS) == 6
    assert normalize_neon("rainbow") == normalize_neon(None) == "fire"
    assert normalize_neon("violet") == "violet"


def test_halo_breathes_between_most_and_full_brightness():
    assert breath_alpha(BREATH_PERIOD_S / 4) == 255
    assert breath_alpha(BREATH_PERIOD_S * 3 / 4) == round(255 * 0.78)
    assert breath_alpha(BREATH_PERIOD_S * 3 / 4, animate=False) == 255


def test_bar_fills_from_left_to_right_with_elapsed_time():
    state = {"status": "running", "durationMs": 100_000, "elapsedMs": 10_000, "startedAt": 1_000_000}
    assert timer_progress_fraction(state, 1_030_000) == pytest.approx(0.4)
    assert timer_progress_fraction({"status": "idle", "durationMs": 100, "elapsedMs": 0}) is None


def test_bar_is_only_for_tabbed_browser_windows_not_chrome_popups():
    assert shows_browser_page(LIVE_CHROME)
    # Brainalot «Записать» measured live: a 31 px title strip above the content.
    capture = ChromeTarget(hwnd=2, outer=(3270, 309, 3830, 959), frame=(3277, 309, 3823, 952), dpi=96,
                           fullscreen=False, content=(3278, 340, 3822, 951))
    assert not shows_browser_page(capture)
    f11 = ChromeTarget(hwnd=3, outer=(0, 0, 1920, 1080), frame=(0, 0, 1920, 1080), dpi=96,
                       fullscreen=True, content=(0, 0, 1920, 1080))
    assert shows_browser_page(f11)


def test_keeps_last_tabbed_chrome_target_when_focus_moves_to_an_app_or_popup():
    # A different app has no Chrome target.  Brainalot's short-title popup is a
    # Chrome window but cannot steal the bar from the last tabbed browser.
    popup = ChromeTarget(hwnd=2, outer=(3270, 309, 3830, 959), frame=(3277, 309, 3823, 952), dpi=96,
                         fullscreen=False, content=(3278, 340, 3822, 951))
    other_browser = ChromeTarget(hwnd=3, outer=(0, 0, 1920, 1080), frame=(0, 0, 1920, 1080), dpi=96,
                                 fullscreen=False, content=(0, 90, 1920, 1080))
    assert select_overlay_target(None, LIVE_CHROME) is LIVE_CHROME
    assert select_overlay_target(popup, LIVE_CHROME) is LIVE_CHROME
    assert select_overlay_target(other_browser, LIVE_CHROME) is other_browser


def test_only_native_focus_overlay_windows_are_ignored_as_occluders():
    assert is_focus_overlay_window_class("MegaMozgFocusOverlay_deadbeef")
    assert not is_focus_overlay_window_class("Chrome_WidgetWin_1")
    assert not is_focus_overlay_window_class("Codex")


def test_page_is_chosen_over_side_panel_and_placeholders():
    assert page_content_rect(LIVE_CONTENT_WINDOWS, 96) == (2568, 213, 3970, 1158)


def test_page_is_the_widest_of_equally_high_content_windows():
    page, devtools = (0, 100, 900, 800), (905, 100, 1400, 800)
    assert page_content_rect([devtools, page], 96) == page
    assert page_content_rect([(0, 0, 1, 1), (5, 5, 90, 90)], 96) is None
    assert page_content_rect([], 144) is None


def test_bar_sits_on_the_top_edge_of_the_page_under_the_address_bar():
    bar = target_bar_geometry(LIVE_CHROME, {"panelWidthDip": 480, "panelMeasuredAt": 1}, now_ms=2)
    assert (bar.left, bar.top, bar.width, bar.height) == (2568, 213, 1402, BAR_HEIGHT_DIP)
    hidpi = ChromeTarget(hwnd=1, outer=(0, 0, 3000, 2000), frame=None, dpi=144, fullscreen=False,
                         content=(10, 180, 2010, 1990))
    assert target_bar_geometry(hidpi, None).height == round(BAR_HEIGHT_DIP * 1.5)


def test_without_page_window_bar_uses_visible_frame_and_side_panel_width():
    target = ChromeTarget(hwnd=1, outer=(2552, 118, 4488, 1174), frame=(2560, 126, 4480, 1166), dpi=96,
                          fullscreen=False, content=None)
    bar = target_bar_geometry(target, {"panelWidthDip": 492, "panelMeasuredAt": 1_000_000}, now_ms=1_000_500)
    assert (bar.left, bar.top, bar.width) == (2560, 126 + CONTENT_TOP_OFFSET_DIP, 1920 - 492)
    fullscreen = ChromeTarget(hwnd=1, outer=(0, 0, 1920, 1080), frame=(0, 0, 1920, 1080), dpi=96,
                              fullscreen=True, content=None)
    assert target_bar_geometry(fullscreen, None).top == 0


def test_popup_overlap_uses_half_open_rectangles():
    assert rects_intersect((0, 0, 10, 10), (9, 9, 20, 20))
    assert not rects_intersect((0, 0, 10, 10), (10, 0, 20, 10))
    assert not rects_intersect((0, 0, 10, 10), (0, 10, 10, 20))


def test_fallback_geometry_uses_frame_offset_and_per_monitor_dpi():
    geometry = bar_geometry((100, 200, 1100, 900), 144, fullscreen=False)
    assert geometry.left == 100
    assert geometry.top == 200 + round(CONTENT_TOP_OFFSET_DIP * 1.5)
    assert geometry.width == 1000
    assert geometry.height == round(BAR_HEIGHT_DIP * 1.5)


def test_fullscreen_geometry_and_detection_use_window_top_edge():
    rect = (0, 0, 1920, 1080)
    assert is_fullscreen_rect(rect, rect)
    assert not is_fullscreen_rect((0, 0, 1910, 1080), rect)
    assert bar_geometry(rect, 96, fullscreen=True).top == 0


def test_fresh_panel_measurement_shortens_the_line_in_chrome_dpi_pixels():
    state = {"panelWidthDip": 300, "panelMeasuredAt": 1_000_000}
    inset = fresh_panel_width_px(state, 144, 1800, 1_006_999)
    assert inset == 450
    bar = bar_geometry((0, 100, 1800, 900), 144, fullscreen=False, right_inset_px=inset)
    assert bar.width == 1350


def test_stale_or_invalid_panel_measurement_returns_full_chrome_width():
    stale = {"panelWidthDip": 300, "panelMeasuredAt": 1_000_000}
    assert fresh_panel_width_px(stale, 96, 1600, 1_007_001) == 0
    assert fresh_panel_width_px({"panelWidthDip": -1, "panelMeasuredAt": 1_000_000}, 96, 1600, 1_000_001) == 0
    assert fresh_panel_width_px({}, 96, 1600, 1_000_001) == 0


def test_panel_width_is_clamped_so_broken_measurements_do_not_hide_the_page():
    state = {"panelWidthDip": 9_999, "panelMeasuredAt": 1_000_000}
    assert fresh_panel_width_px(state, 96, 500, 1_000_001) == 340


def test_missing_native_render_acknowledgement_is_an_api_visible_error():
    with pytest.raises(RuntimeError, match="не подтвердил"):
        _require_render_acknowledgement(False, thread_alive=True, render_error=None)


def test_native_render_failure_or_dead_gui_thread_is_not_accepted():
    with pytest.raises(RuntimeError, match="не смог отрисовать"):
        _require_render_acknowledgement(True, thread_alive=True, render_error=OSError("GDI failed"))
    with pytest.raises(RuntimeError, match="остановился"):
        _require_render_acknowledgement(True, thread_alive=False, render_error=None)


def test_topmost_position_flags_reassert_z_order_without_focus_or_flicker():
    resize = topmost_position_flags(resize=True)
    hold = topmost_position_flags(resize=False)
    assert resize & 0x0010  # SWP_NOACTIVATE
    assert resize & 0x0008  # SWP_NOREDRAW
    assert not resize & 0x0001  # resize is allowed when Chrome moves
    assert hold & 0x0001  # SWP_NOSIZE
    assert hold & 0x0002  # SWP_NOMOVE


def test_portable_api_is_a_noop_off_windows():
    if available():
        return
    update({"status": "idle", "durationMs": 1, "elapsedMs": 0}, False)
