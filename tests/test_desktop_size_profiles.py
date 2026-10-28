from types import SimpleNamespace
import pytest
from megamozg import desktop


class Window:
    def __init__(self):
        self.rect = {"x": 100, "y": 50, "width": 410, "height": 800}
        self.events = SimpleNamespace(loaded=SimpleNamespace(is_set=lambda: False))
    def show(self): pass
    def restore(self): pass
    def hide(self): pass
    def evaluate_js(self, script): self.script = script


@pytest.fixture
def setup(tmp_path, monkeypatch):
    area = desktop.WorkArea(0, 0, 1920, 1040)
    host = desktop.DesktopHost(tmp_path, 1, "token", lambda: True)
    host.full, host.widget = Window(), Window()
    monkeypatch.setattr(host, "_areas", lambda: [area])
    monkeypatch.setattr(desktop, "native_rect", lambda w: dict(w.rect))
    monkeypatch.setattr(host, "_set_rect", lambda w, r, minimum=None: setattr(w, "rect", dict(r)))
    monkeypatch.setattr(host, "_show", lambda w: None)
    return host, area


def size(host):
    return host.full.rect["width"], host.full.rect["height"]


def test_switch_and_manual_resize_do_not_change_defaults(setup):
    host, _ = setup
    assert desktop.DesktopBridge(host).bootstrap()["size_default"] == {"width": 410, "height": 800, "custom": False}
    host.full.rect.update(width=700, height=720)
    host.window_action("view_profile", "brainalot")
    assert size(host) == (700, 720)
    host.window_action("view_profile", "work")
    assert size(host) == (1600, 1000)
    host.full.rect.update(width=1100, height=900)
    host.window_action("view_profile", "brainalot")
    assert size(host) == (410, 800)
    host.window_action("view_profile", "work")
    assert size(host) == (1600, 1000)


def test_capture_and_reset_are_independent_persistent_and_do_not_resize(setup, tmp_path, monkeypatch):
    host, area = setup
    host.full.rect.update(width=600, height=700)
    assert host.window_action("view_size_default", True)["size_default"] == {"width": 600, "height": 700, "custom": True}
    host.window_action("view_profile", "work")
    assert not host._size_default()["custom"]
    host.full.rect.update(width=1200, height=850)
    host.window_action("view_size_default", True)
    again = desktop.DesktopHost(tmp_path, 1, "token", lambda: True)
    assert again.view_profile == "work"
    assert again._size_default() == {"width": 1200, "height": 850, "custom": True}
    host.window_action("view_profile", "brainalot")
    assert size(host) == (600, 700)
    host.window_action("view_size_default", False)
    assert size(host) == (600, 700)
    host.window_action("view_profile", "work")
    assert size(host) == (1200, 850)
    host.window_action("view_profile", "brainalot")
    assert size(host) == (410, 800)


def test_dpi_clamping_and_capture(setup, monkeypatch):
    host, _ = setup
    area = desktop.WorkArea(-1600, 0, 1600, 900, dpi=144)
    monkeypatch.setattr(host, "_areas", lambda: [area])
    host.full.rect.update(x=-1400, width=900, height=750)
    host.window_action("view_size_default", True)
    assert host._size_default() == {"width": 600, "height": 500, "custom": True}
    host.window_action("view_profile", "work")
    assert size(host) == (1600, 900)
    assert area.x <= host.full.rect["x"] <= area.x + area.width - host.full.rect["width"]
    host.window_action("view_profile", "brainalot")
    assert size(host) == (900, 750)


def test_expanded_profile_switch_and_pin(setup):
    host, _ = setup
    host.window_action("pin", False)
    host.window_action("view_profile", "work")
    host.window_action("work_expand", True)
    assert size(host) == (1920, 1040)
    host.window_action("view_profile", "brainalot")
    assert size(host) == (410, 800)
    assert host._expanded_full_geometry is None and host.full_on_top is False
    host.window_action("view_profile", "work")
    assert size(host) == (1600, 1000)


def test_invalid_and_corrupt_values_and_legacy_startup(setup):
    host, _ = setup
    for action, value in [("view_profile", "other"), ("view_profile", []), ("view_size_default", 1)]:
        with pytest.raises(ValueError): host.window_action(action, value)
    host.state.data["view_size_defaults"] = {"brainalot": {"width": True, "height": 9}}
    assert not host._size_default()["custom"]
    host.state.data["geometry"]["full"] = {"x": 10, "y": 10, "width": 900, "height": 700, "units": "physical", "dpi": 96}
    host.state.data["full_geometry_layout"] = desktop.FULL_GEOMETRY_LAYOUT
    host._restore_geometry()
    assert size(host) == (410, 800)


def test_open_page_selects_profile(setup):
    host, _ = setup
    host.open_page("work.html")
    assert host.view_profile == "work" and size(host) == (1600, 1000)
    host.open_page("panel.html")
    assert host.view_profile == "brainalot" and size(host) == (410, 800)


def test_clamped_normal_work_is_not_treated_as_expanded_on_drag(setup, monkeypatch):
    host, _ = setup
    monkeypatch.setattr(host, "_areas", lambda: [desktop.WorkArea(0, 0, 1280, 800)])
    host.window_action("view_profile", "work")
    assert size(host) == (1280, 800)
    result = host.window_action("gesture_begin", {"right_button": True, "origin": [600, 400]})
    assert result["restored_work"] is False and size(host) == (1280, 800)


@pytest.mark.parametrize("invalid", [True, 1, [], {}, None, "unknown"])
def test_invalid_persisted_profile_falls_back_to_brainalot(tmp_path, invalid):
    state = desktop.DesktopState(tmp_path)
    state.data["view_profile"] = invalid
    state.save()
    host = desktop.DesktopHost(tmp_path, 1, "token", lambda: True)
    assert host.view_profile == "brainalot"
    assert host._size_default() == {"width": 410, "height": 800, "custom": False}
