"""The installed app must distinguish GitHub releases from an HTTP update feed."""

import sys
from types import SimpleNamespace

import pytest

from megamozg import app


def _fake_velopack():
    calls = []

    def source(kind):
        def create(url, **options):
            calls.append((kind, url, options))
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
    assert calls[0][2] == {"prerelease": True}


def test_non_work_build_ignores_work_prereleases(monkeypatch):
    monkeypatch.delenv("BRAINALOT_UPDATE_URL", raising=False)
    monkeypatch.setattr(app.features, "WORK_ENABLED", False)
    module, calls = _fake_velopack()
    app._update_manager(module)
    assert calls[0][2] == {"prerelease": False}


@pytest.mark.parametrize("channel", ["win", "win-work"])
def test_release_directory_keeps_only_its_edition_feed(monkeypatch, tmp_path, channel):
    import json
    from scripts import build_release
    monkeypatch.setattr(build_release, "RELEASES", tmp_path)
    monkeypatch.setattr(build_release, "RELEASE_CHANNEL", channel)
    package = tmp_path / "current.nupkg"
    package.write_bytes(b"package")
    feed = {"Assets": [{"Version": build_release.__version__, "Type": "Full", "FileName": package.name}]}
    for name in ("win", "win-work"):
        (tmp_path / f"releases.{name}.json").write_text(json.dumps(feed), encoding="utf-8")
        legacy = "RELEASES" if name == "win" else "RELEASES-win-work"
        (tmp_path / legacy).write_text("HASH current.nupkg 7\n", encoding="utf-8")
    build_release.prune_release_feeds()
    assert [p.name for p in tmp_path.glob("releases.*.json")] == [f"releases.{channel}.json"]
    legacy_names = [p.name for p in tmp_path.iterdir() if p.name == "RELEASES" or p.name.startswith("RELEASES-")]
    assert legacy_names == ["RELEASES" if channel == "win" else "RELEASES-win-work"]
    assert package.read_bytes() == b"package"


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
