"""An installed copy may explicitly use an existing checkout's data root."""
from __future__ import annotations

import json

import pytest

from megamozg import paths


def installed(monkeypatch, tmp_path):
    monkeypatch.delenv("BRAINALOT_HOME", raising=False)
    monkeypatch.setattr(paths, "frozen", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    return tmp_path / "local" / "Brainalot"


def source_root(tmp_path):
    source = tmp_path / "checkout" / "data"
    (source / "vault").mkdir(parents=True)
    (source / "state").mkdir()
    (source / "state" / "journal.sqlite3").touch()
    return source


def select(bootstrap, target):
    paths.write_config(bootstrap, {"vault": "untouched", "data_root": str(target)})


def test_installed_root_uses_explicit_existing_source_without_copying(monkeypatch, tmp_path):
    bootstrap = installed(monkeypatch, tmp_path)
    source = source_root(tmp_path)
    select(bootstrap, source)

    assert paths.default_root() == source.resolve()
    assert json.loads((bootstrap / "config.json").read_text(encoding="utf-8"))["vault"] == "untouched"
    assert not (bootstrap / "state").exists()


@pytest.mark.parametrize("target", ["relative/data", "", None, 17])
def test_invalid_pointer_fails_closed(monkeypatch, tmp_path, target):
    bootstrap = installed(monkeypatch, tmp_path)
    select(bootstrap, target)
    with pytest.raises(RuntimeError, match="data_root"):
        paths.default_root()


def test_missing_source_and_missing_journal_fail_closed(monkeypatch, tmp_path):
    bootstrap = installed(monkeypatch, tmp_path)
    source = source_root(tmp_path)
    for target in (tmp_path / "missing", source):
        if target == source:
            (source / "state" / "journal.sqlite3").unlink()
        select(bootstrap, target)
        with pytest.raises(RuntimeError, match="data_root"):
            paths.default_root()


def test_self_and_chained_redirects_fail_closed(monkeypatch, tmp_path):
    bootstrap = installed(monkeypatch, tmp_path)
    source = source_root(tmp_path)
    select(bootstrap, bootstrap)
    with pytest.raises(RuntimeError, match="data_root"):
        paths.default_root()
    paths.write_config(source, {"data_root": str(bootstrap)})
    select(bootstrap, source)
    with pytest.raises(RuntimeError, match="data_root"):
        paths.default_root()


def test_broken_bootstrap_config_fails_closed(monkeypatch, tmp_path):
    bootstrap = installed(monkeypatch, tmp_path)
    bootstrap.mkdir(parents=True)
    (bootstrap / "config.json").write_text("{", encoding="utf-8")
    with pytest.raises(RuntimeError, match="config.json"):
        paths.default_root()


def test_environment_override_keeps_priority(monkeypatch, tmp_path):
    bootstrap = installed(monkeypatch, tmp_path)
    select(bootstrap, "relative/data")
    explicit = tmp_path / "isolated"
    monkeypatch.setenv("BRAINALOT_HOME", str(explicit))
    assert paths.default_root() == explicit
