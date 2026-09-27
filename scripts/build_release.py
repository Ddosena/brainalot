"""Build a Windows release of Brainalot: the program, its installer and the Chrome Web Store package.

    .venv\\Scripts\\python.exe scripts\\build_release.py            # everything
    .venv\\Scripts\\python.exe scripts\\build_release.py --no-setup # skip Velopack (no vpk installed)

Steps: version check -> PyInstaller (dist/app/Brainalot) -> smoke test of the built programs on
temporary data -> Velopack (dist/releases: Brainalot-win-Setup.exe, update packages, a portable zip)
-> extension zip for the Chrome Web Store -> SHA256SUMS.txt. See docs/release-windows.md.
"""
from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import tomllib
from urllib.request import urlopen
import zipfile

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from megamozg import __version__  # noqa: E402

BUILD = ROOT / "build"
APP = ROOT / "dist" / "app" / "Brainalot"
RELEASES = ROOT / "dist" / "releases"
PACK_ID = "BrainalotApp"  # Velopack installs into %LOCALAPPDATA%\BrainalotApp; data lives in ...\Brainalot
EXTENSION_SKIP = {"tests", "dev", "package.json", "node_modules"}


def legal_root(root: Path) -> Path:
    """The development tree has templates; the public source package has root files."""
    templates = root / "packaging" / "public"
    return templates if templates.is_dir() else root


def checked_app_path() -> Path:
    """Resolve the cleanup target and reject paths outside this project's dist/app."""
    root = ROOT.resolve()
    dist_app = (ROOT / "dist" / "app").resolve(strict=False)
    target = APP.resolve(strict=False)
    if dist_app != root / "dist" / "app" or target != dist_app / "Brainalot":
        raise ValueError(f"Unsafe PyInstaller cleanup target: {target}")
    return target


def step(title: str) -> None:
    print(f"\n== {title}", flush=True)


def check_versions() -> None:
    manifest = json.loads((ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))["version"]
    package = json.loads((ROOT / "extension" / "package.json").read_text(encoding="utf-8"))["version"]
    if {manifest, package} != {__version__}:
        raise SystemExit(f"Versions differ: megamozg {__version__}, manifest {manifest}, package.json {package}")


def version_info() -> Path:
    numbers = tuple(int(part) for part in __version__.split(".")) + (0,)
    path = BUILD / "version-info.txt"
    BUILD.mkdir(exist_ok=True)
    path.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers[:4]}, prodvers={numbers[:4]}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'Brainalot'), StringStruct('FileDescription', 'Brainalot'),
    StringStruct('FileVersion', '{__version__}'), StringStruct('ProductName', 'Brainalot'),
    StringStruct('ProductVersion', '{__version__}'), StringStruct('LegalCopyright', 'Brainalot contributors')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])])
""", encoding="utf-8")
    return path


def pyinstaller() -> None:
    shutil.rmtree(checked_app_path(), ignore_errors=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(APP.parent), "--workpath", str(BUILD / "pyinstaller"),
                    str(ROOT / "packaging" / "pyinstaller" / "brainalot.spec")], check=True, cwd=ROOT)


def copy_legal_files() -> None:
    """Keep notices visible beside the EXEs as well as inside PyInstaller's _internal folder."""
    source = legal_root(ROOT)
    for name in ("LICENSE", "PRIVACY.md", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(source / name, APP / name)
    font_licenses = APP / "licenses" / "fonts"
    font_licenses.mkdir(parents=True, exist_ok=True)
    for name in ("OFL-Oswald.txt", "OFL-PTSans.txt"):
        shutil.copy2(ROOT / "extension" / "fonts" / name, font_licenses / name)
    _copy_dependency_licenses()


def _copy_dependency_licenses() -> None:
    """Record installed runtime package versions and preserve their available license files."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = project["project"]["dependencies"] + ["velopack"]
    declared += [line for line in (ROOT / "requirements-voice.txt").read_text(encoding="utf-8").splitlines()
                 if line.strip() and not line.lstrip().startswith("#")]
    pending = [Requirement(item).name for item in declared]
    seen = set()
    inventory = []
    license_root = APP / "licenses" / "python"
    while pending:
        name = pending.pop()
        key = canonicalize_name(name)
        if key in seen:
            continue
        seen.add(key)
        package = metadata.distribution(name)
        package_meta = package.metadata
        record = {"name": package_meta.get("Name", name), "version": package.version,
                  "license": package_meta.get("License-Expression") or package_meta.get("License") or "see license files",
                  "license_files": []}
        for file in package.files or []:
            relative = Path(str(file))
            if ".dist-info" not in str(relative) or not any(
                    part.lower().startswith(("license", "licence", "copying", "notice")) for part in relative.parts):
                continue
            source = Path(package.locate_file(file))
            if not source.is_file():
                continue
            destination = license_root / key / relative.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            record["license_files"].append(destination.relative_to(APP).as_posix())
        inventory.append(record)
        for requirement in package.requires or []:
            dependency = Requirement(requirement)
            if dependency.marker is None or dependency.marker.evaluate({"extra": ""}):
                pending.append(dependency.name)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        shutil.copy2(python_license, APP / "licenses" / "Python-LICENSE.txt")
    (APP / "licenses" / "python-packages.json").write_text(
        json.dumps(sorted(inventory, key=lambda item: item["name"].lower()), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")


def _native(exe: Path, env: dict, action: str) -> dict:
    payload = json.dumps({"action": action}).encode()
    result = subprocess.run([str(exe), "chrome-extension://abcdefghijklmnopabcdefghijklmnop/"],
                            input=struct.pack("<I", len(payload)) + payload, capture_output=True, env=env, timeout=60)
    return json.loads(result.stdout[4:])


def smoke_test() -> None:
    """The built programs on throwaway data: CLI, the service, the native host, a clean stop."""
    cli, app = APP / "mmm.exe", APP / "Brainalot.exe"
    with tempfile.TemporaryDirectory(prefix="brainalot-smoke-") as home:
        env = {**os.environ, "BRAINALOT_HOME": home}
        run = lambda *args: subprocess.run([str(cli), *args], capture_output=True, text=True, encoding="utf-8",
                                           env=env, timeout=60, check=True).stdout
        json.loads(run("init"))
        note = json.loads(run("capture", "--text", "Проверка сборки", "--kind", "idea", "--external-id", "smoke"))
        assert any(item["id"] == note["id"] for item in json.loads(run("list"))["notes"])
        Path(home, "config.json").write_text(json.dumps({"welcome_shown": True}), encoding="utf-8")
        process = subprocess.Popen([str(app), "--background"], env=env)
        try:
            answer = _native(cli, env, "hello")
            assert answer.get("ok") and answer.get("token") and answer.get("port"), answer
            with urlopen(f"http://127.0.0.1:{answer['port']}/api/health", timeout=5) as response:
                health = json.load(response)
            assert health["version"] == __version__, health
            with urlopen(f"http://127.0.0.1:{answer['port']}/welcome", timeout=5) as response:
                assert b"Brainalot" in response.read()
            print(f"   service {health['version']} on port {answer['port']}, native host ok")
        finally:
            process.kill()
            process.wait(10)
        time.sleep(0.5)


def velopack() -> None:
    vpk = shutil.which("vpk")
    if not vpk:
        raise SystemExit("vpk not found: dotnet tool install -g vpk (or run with --no-setup)")
    RELEASES.mkdir(parents=True, exist_ok=True)
    # The CI downloads the preceding full NUPKG here before building, so vpk can create a
    # delta. Remove stale feeds and assets; the old NUPKG is build input, not a new asset.
    previous_packages = [item for item in RELEASES.glob("*.nupkg") if f"-{__version__}-" not in item.name]
    for item in RELEASES.iterdir():
        if item.is_file() and item.suffix.lower() != ".nupkg":
            item.unlink()
    subprocess.run([vpk, "pack", "--packId", PACK_ID, "--packVersion", __version__, "--packDir", str(APP),
                    "--mainExe", "Brainalot.exe", "--packTitle", "Brainalot", "--packAuthors", "Brainalot",
                    "--icon", str(ROOT / "packaging" / "brand" / "brainalot.ico"),
                    "--instLicense", str(legal_root(ROOT) / "LICENSE"),
                    "--shortcuts", "StartMenuRoot", "--runtime", "win-x64", "--outputDir", str(RELEASES)], check=True, cwd=ROOT)
    for item in previous_packages:
        item.unlink(missing_ok=True)


def extension_zip() -> Path:
    RELEASES.mkdir(parents=True, exist_ok=True)
    target = RELEASES / f"Brainalot-extension-{__version__}.zip"
    target.unlink(missing_ok=True)
    source = ROOT / "extension"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in sorted(source.rglob("*")):
            relative = item.relative_to(source)
            if item.is_file() and relative.parts[0] not in EXTENSION_SKIP:
                archive.write(item, relative.as_posix())
    return target


def checksums() -> None:
    lines = []
    for item in sorted(RELEASES.iterdir()):
        if item.is_file() and item.name != "SHA256SUMS.txt":
            lines.append(f"{hashlib.sha256(item.read_bytes()).hexdigest()}  {item.name}")
    (RELEASES / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-setup", action="store_true", help="skip the Velopack installer")
    parser.add_argument("--no-build", action="store_true", help="reuse dist/app (smoke test and packaging only)")
    args = parser.parse_args()
    step(f"Brainalot {__version__}: versions")
    check_versions()
    if not args.no_build:
        step("PyInstaller")
        version_info()
        pyinstaller()
    copy_legal_files()
    step("Smoke test of the built programs")
    smoke_test()
    if not args.no_setup:
        step("Velopack installer")
        velopack()
    step("Chrome Web Store package")
    print("  ", extension_zip())
    checksums()
    step("Done")
    for item in sorted(RELEASES.iterdir()):
        print(f"   {item.stat().st_size / 1024 / 1024:7.1f} MB  {item.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
