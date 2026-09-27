# Third-party software

Brainalot is distributed under AGPL-3.0-only; see LICENSE. Its bundled dependencies retain their own licenses. A machine-readable list of Python package names, versions and their upstream license metadata, together with available license files, is generated in the installed `licenses/` directory during the release build.

The application includes Python, FastAPI, Starlette, Uvicorn, Pydantic, PyYAML, filelock, icalendar, recurring-ical-events, Velopack and their dependencies. Voice recognition uses faster-whisper, CTranslate2, NumPy, PyAV, ONNX Runtime, tokenizers, Hugging Face Hub and their dependencies. The generated inventory records the installed runtime dependencies of the build environment, including optional dependency metadata; it is not an exact analysis of PyInstaller's final module graph. The source distribution pins runtime and voice requirements separately.

Oswald and PT Sans fonts are distributed under the SIL Open Font License. Their original notices are included as `extension/fonts/OFL-Oswald.txt` and `extension/fonts/OFL-PTSans.txt`.

The Whisper speech model is downloaded separately when voice input is first enabled. Model weights are not included in the installer; their upstream repository and license are distinct from the Brainalot source license. Audio is processed locally.

Source and support: https://github.com/Ddosena/brainalot
