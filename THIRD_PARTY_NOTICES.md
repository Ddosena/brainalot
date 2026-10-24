# Third-party software

Brainalot is distributed under AGPL-3.0-only; see LICENSE. Its bundled dependencies retain their own licenses. A machine-readable list of Python package names, versions and their upstream license metadata, together with available license files, is generated in the installed `licenses/` directory during the release build.

The application includes Python, FastAPI, Starlette, Uvicorn, Pydantic, PyYAML, filelock, icalendar, recurring-ical-events, Velopack, pywebview, pythonnet, Bottle, CFFI and their dependencies. The Windows UI uses Microsoft Edge WebView2 Runtime, installed separately by the setup bootstrapper under Microsoft terms. Voice recognition uses faster-whisper, CTranslate2, NumPy, PyAV, ONNX Runtime, tokenizers, Hugging Face Hub and their dependencies. The generated inventory records the installed runtime dependencies of the build environment, including optional dependency metadata; it is not an exact analysis of PyInstaller's final module graph. The source distribution pins runtime and voice requirements separately.

Oswald and PT Sans fonts are distributed under the SIL Open Font License. Their original notices are included as `extension/fonts/OFL-Oswald.txt` and `extension/fonts/OFL-PTSans.txt`.

The Work constructor includes Matter.js 0.20.0, Copyright (c) Liam Brummitt and
contributors, under the MIT License. The unmodified local build and its full
license are bundled in `extension/vendor/`; upstream: https://brm.io/matter-js/
and https://github.com/liabru/matter-js. No CDN is required at runtime.

The Spotify icon is an unmodified Black/White SVG from Spotify's official icon
asset package, used to identify and open the local Spotify application.
Spotify trademarks and brand assets remain the property of Spotify and are
subject to its brand guidelines, not Brainalot's source-code license:
https://developer.spotify.com/documentation/design.

The Whisper speech model is downloaded separately when voice input is first enabled. Model weights are not included in the installer; their upstream repository and license are distinct from the Brainalot source license. Audio is processed locally.

Source and support: https://github.com/Ddosena/brainalot
