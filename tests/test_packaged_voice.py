"""Release packaging checks that do not build or download a speech model."""

import json
from importlib import metadata

import pytest

from scripts import build_release


def test_legal_files_and_runtime_inventory_are_in_the_packaged_directory(tmp_path, monkeypatch):
    for name in ("faster-whisper", "velopack", "hf-xet"):
        try:
            metadata.version(name)
        except metadata.PackageNotFoundError:
            pytest.skip(f"release-only dependency {name} is not installed")
    monkeypatch.setattr(build_release, "APP", tmp_path)
    build_release.copy_legal_files()
    for name in ("LICENSE", "PRIVACY.md", "THIRD_PARTY_NOTICES.md"):
        assert (tmp_path / name).is_file()
    for name in ("OFL-Oswald.txt", "OFL-PTSans.txt"):
        assert (tmp_path / "licenses" / "fonts" / name).is_file()
    packages = json.loads((tmp_path / "licenses" / "python-packages.json").read_text(encoding="utf-8"))
    names = {item["name"].lower() for item in packages}
    assert {"faster-whisper", "ctranslate2", "onnxruntime", "velopack"} <= names
    assert all(item["version"] for item in packages)


def test_public_source_layout_uses_root_legal_files(tmp_path, monkeypatch):
    for name in ("LICENSE", "PRIVACY.md", "THIRD_PARTY_NOTICES.md"):
        (tmp_path / name).write_text("public " + name, encoding="utf-8")
    fonts = tmp_path / "extension" / "fonts"
    fonts.mkdir(parents=True)
    for name in ("OFL-Oswald.txt", "OFL-PTSans.txt"):
        (fonts / name).write_text(name, encoding="utf-8")
    app = tmp_path / "dist" / "app" / "Brainalot"
    app.mkdir(parents=True)
    monkeypatch.setattr(build_release, "ROOT", tmp_path)
    monkeypatch.setattr(build_release, "APP", app)
    monkeypatch.setattr(build_release, "_copy_dependency_licenses", lambda: None)
    assert build_release.legal_root(tmp_path) == tmp_path
    build_release.copy_legal_files()
    assert (app / "LICENSE").read_text(encoding="utf-8") == "public LICENSE"
    assert (app / "PRIVACY.md").is_file() and (app / "THIRD_PARTY_NOTICES.md").is_file()
    assert (app / "licenses" / "fonts" / "OFL-Oswald.txt").is_file()


def test_cleanup_target_cannot_escape_dist_app(tmp_path, monkeypatch):
    monkeypatch.setattr(build_release, "ROOT", tmp_path)
    expected = tmp_path / "dist" / "app" / "Brainalot"
    monkeypatch.setattr(build_release, "APP", expected)
    assert build_release.checked_app_path() == expected
    monkeypatch.setattr(build_release, "APP", tmp_path / "other" / "Brainalot")
    with pytest.raises(ValueError, match="Unsafe PyInstaller cleanup target"):
        build_release.checked_app_path()


def test_previous_nupkg_is_available_to_pack_then_removed_from_release_assets(tmp_path, monkeypatch):
    releases = tmp_path / "releases"
    releases.mkdir()
    previous = releases / "BrainalotApp-0.1.37-full.nupkg"
    previous.write_bytes(b"previous")
    (releases / "releases.win.json").write_text("old", encoding="utf-8")
    monkeypatch.setattr(build_release, "RELEASES", releases)
    monkeypatch.setattr(build_release.shutil, "which", lambda name: "vpk")

    def fake_pack(command, *, check, cwd):
        assert previous.is_file()  # vpk can use it as the delta base
        assert not (releases / "releases.win.json").exists()
        (releases / f"BrainalotApp-{build_release.__version__}-full.nupkg").write_bytes(b"new")
        (releases / "releases.win.json").write_text("new", encoding="utf-8")

    monkeypatch.setattr(build_release.subprocess, "run", fake_pack)
    build_release.velopack()
    assert not previous.exists()
    assert {item.name for item in releases.iterdir()} == {
        f"BrainalotApp-{build_release.__version__}-full.nupkg", "releases.win.json"}
