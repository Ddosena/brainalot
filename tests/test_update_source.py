"""The installed app must distinguish GitHub releases from an HTTP update feed."""

import sys
from types import SimpleNamespace

import pytest

from megamozg import app


def _fake_velopack():
    calls = []

    def source(kind):
        def create(url):
            calls.append((kind, url))
            return (kind, url)
        return create

    def manager(value):
        calls.append(("manager", value))
        return value

    return SimpleNamespace(GithubSource=source("github"), HttpSource=source("http"),
                           UpdateManager=manager), calls


def test_default_updates_use_public_github_release_source(monkeypatch):
    monkeypatch.delenv("BRAINALOT_UPDATE_URL", raising=False)
    module, calls = _fake_velopack()
    assert app._update_manager(module) == ("github", "https://github.com/Ddosena/brainalot")
    assert calls[-1] == ("manager", ("github", "https://github.com/Ddosena/brainalot"))


def test_http_override_uses_http_source(monkeypatch):
    monkeypatch.setenv("BRAINALOT_UPDATE_URL", " https://updates.example.test/brainalot/ ")
    module, calls = _fake_velopack()
    assert app._update_manager(module) == ("http", "https://updates.example.test/brainalot/")
    assert calls[0][0] == "http"


@pytest.mark.parametrize("url", ["", "file:///tmp/feed", "not-a-url"])
def test_invalid_override_is_reported(monkeypatch, url):
    monkeypatch.setenv("BRAINALOT_UPDATE_URL", url)
    module, _ = _fake_velopack()
    with pytest.raises(ValueError, match="BRAINALOT_UPDATE_URL"):
        app._update_manager(module)


def test_broken_update_source_notifies_the_user_and_logs(monkeypatch, tmp_path, caplog):
    notices = []
    application = app.App(tmp_path)
    application.tray = SimpleNamespace(notify=lambda title, message: notices.append((title, message)))
    monkeypatch.setitem(sys.modules, "velopack", SimpleNamespace())
    monkeypatch.setattr(app.paths, "frozen", lambda: True)
    monkeypatch.setattr(app, "_update_manager", lambda module: (_ for _ in ()).throw(ValueError("bad feed")))
    application._check_updates(SimpleNamespace())
    assert notices and "недоступна" in notices[0][1]
    assert "bad feed" in caplog.text
