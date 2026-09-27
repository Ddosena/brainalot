"""One version for the whole product: the release build and the extension must agree with it."""
import json
from pathlib import Path

from megamozg import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_extension_manifest_and_package_have_the_program_version():
    assert json.loads((ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))["version"] == __version__
    assert json.loads((ROOT / "extension" / "package.json").read_text(encoding="utf-8"))["version"] == __version__


def test_runtime_requirements_match_the_tested_lock():
    def pins(name):
        lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
        return dict(line.split("==") for line in lines if "==" in line and not line.startswith("#"))
    runtime, lock = pins("requirements-runtime.txt"), pins("requirements-lock.txt")
    assert {name: version for name, version in runtime.items() if lock.get(name) != version} == {}
    assert not {"pytest", "httpx", "Pygments", "pluggy", "iniconfig"} & runtime.keys()
