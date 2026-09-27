# PyInstaller build of Brainalot (onedir: two programs sharing one _internal folder).
#   Brainalot.exe - the service with a tray icon, no console (megamozg/app.py)
#   mmm.exe       - the command line for people and agents, and Chrome's native-messaging host
# Built by scripts/build_release.py; see docs/release-windows.md.
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).resolve().parents[1]
LEGAL_ROOT = ROOT / "packaging" / "public" if (ROOT / "packaging" / "public").is_dir() else ROOT
ICON = str(ROOT / "packaging" / "brand" / "brainalot.ico")
EXTENSION_SKIP = {"tests", "dev", "package.json", "node_modules"}

datas = [(ICON, "packaging/brand")]
for name in ("LICENSE", "PRIVACY.md", "THIRD_PARTY_NOTICES.md"):
    datas.append((str(LEGAL_ROOT / name), "."))
for name in ("OFL-Oswald.txt", "OFL-PTSans.txt"):
    datas.append((str(ROOT / "extension" / "fonts" / name), "licenses/fonts"))
for item in (ROOT / "extension").rglob("*"):
    relative = item.relative_to(ROOT / "extension")
    if item.is_file() and relative.parts[0] not in EXTENSION_SKIP:
        datas.append((str(item), str(Path("extension") / relative.parent)))
datas += collect_data_files("tzdata")  # zoneinfo for calendars on Windows
datas += collect_data_files("faster_whisper", includes=["assets/*.onnx"])  # Silero VAD model, used locally
for distribution in ("faster-whisper", "ctranslate2", "huggingface-hub", "hf-xet", "httpx",
                     "onnxruntime", "tokenizers"):
    datas += copy_metadata(distribution)

hidden = (collect_submodules("uvicorn") + collect_submodules("megamozg") + collect_submodules("faster_whisper")
          + ["velopack", "ctranslate2", "onnxruntime", "av", "tokenizers", "httpx",
             "huggingface_hub._snapshot_download", "huggingface_hub.file_download", "hf_xet"])
binaries = collect_dynamic_libs("ctranslate2")
# Not in the product: the tkinter console (the panel replaces it) and tests.
excludes = ["tkinter", "_tkinter", "megamozg.manual", "pytest",
            "_pytest", "pygments", "IPython", "setuptools", "pip"]


def analysis(script):
    return Analysis([str(ROOT / "packaging" / "pyinstaller" / script)], pathex=[str(ROOT)], datas=datas,
                    binaries=binaries, hiddenimports=hidden, excludes=excludes, noarchive=False, optimize=1)


app = analysis("entry_app.py")
cli = analysis("entry_cli.py")
app_exe = EXE(PYZ(app.pure), app.scripts, [], exclude_binaries=True, name="Brainalot", icon=ICON,
              console=False, version=str(ROOT / "build" / "version-info.txt"), upx=False)
cli_exe = EXE(PYZ(cli.pure), cli.scripts, [], exclude_binaries=True, name="mmm", icon=ICON,
              console=True, version=str(ROOT / "build" / "version-info.txt"), upx=False)
COLLECT(app_exe, app.binaries, app.datas, cli_exe, cli.binaries, cli.datas, name="Brainalot", upx=False)
