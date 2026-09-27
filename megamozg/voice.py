"""Local speech-to-text for dictated captures.

The capture window records the microphone and posts the audio here. It is
transcribed on this computer by faster-whisper (bundled in the Windows installer,
optional for a source checkout); nothing goes to a cloud speech service.
The model is downloaded once into ``data/state/models``. Titles of active
projects and people are passed as hot words, so names from the library are
recognised.

Settings (environment variables): ``MMM_WHISPER_MODEL`` (default ``small``;
``large-v3-turbo`` is more accurate on a fast computer), ``MMM_WHISPER_DEVICE``
(``auto``, ``cpu`` or ``cuda``) and ``MMM_WHISPER_COMPUTE`` (``default``,
``int8``, ``float16``).
"""
from __future__ import annotations

import importlib.util
import os
import re
import tempfile
import threading
from pathlib import Path

from .core import ValidationError


MAX_AUDIO_BYTES = 12 * 1024 * 1024
DEFAULT_MODEL = "small"
LANGUAGES = {"ru": "ru", "en": "en", "auto": None}
AUDIO_TYPES = {"audio/webm": ".webm", "audio/ogg": ".ogg", "audio/wav": ".wav", "audio/x-wav": ".wav",
               "audio/wave": ".wav", "audio/mp4": ".m4a", "audio/mpeg": ".mp3", "audio/flac": ".flac"}
# A short Russian sample with punctuation nudges Whisper to punctuate its output.
PROMPTS = {"ru": "Надиктованные дела и мысли. Завтра в 10:00 созвон с командой, потом купить продукты."}


class VoiceUnavailableError(RuntimeError):
    """Speech recognition is not installed or its model could not be loaded."""


class FasterWhisperEngine:
    name = "faster-whisper"

    def __init__(self, models_dir: Path, *, model: str | None = None, device: str | None = None,
                 compute_type: str | None = None):
        self.models_dir = Path(models_dir)
        self.model_name = model or os.environ.get("MMM_WHISPER_MODEL") or DEFAULT_MODEL
        self.device = device or os.environ.get("MMM_WHISPER_DEVICE") or "auto"
        self.compute_type = compute_type or os.environ.get("MMM_WHISPER_COMPUTE") or "default"
        self._model = None
        self._lock = threading.Lock()

    @staticmethod
    def installed() -> bool:
        return importlib.util.find_spec("faster_whisper") is not None

    def model_ready(self) -> bool:
        if self._model is not None:
            return True
        return self.installed() and self._downloaded()

    def _downloaded(self) -> bool:
        try:
            from faster_whisper.utils import download_model
            download_model(self.model_name, local_files_only=True, cache_dir=str(self.models_dir))
        except Exception:  # noqa: BLE001 - any failure means «not downloaded yet»
            return False
        return True

    def load(self):
        with self._lock:
            if self._model is None:
                try:
                    from faster_whisper import WhisperModel
                except ImportError as exc:
                    raise VoiceUnavailableError(
                        "Голосовой ввод не установлен. Выполните: pip install \"faster-whisper>=1.1,<2\"") from exc
                self.models_dir.mkdir(parents=True, exist_ok=True)

                def open_model(local_files_only: bool):
                    return WhisperModel(self.model_name, device=self.device, compute_type=self.compute_type,
                                        download_root=str(self.models_dir), local_files_only=local_files_only)

                model = None
                if self._downloaded():
                    # A downloaded model opens without asking the Hugging Face Hub for updates, so voice
                    # works offline and when huggingface.co is unreachable (for example, without a VPN).
                    try:
                        model = open_model(True)
                    except Exception:  # noqa: BLE001 - an incomplete cache is repaired by the download below
                        model = None
                if model is None:
                    try:
                        model = open_model(False)
                    except Exception as exc:  # noqa: BLE001 - download, device and format errors alike
                        raise VoiceUnavailableError("Не удалось загрузить модель распознавания речи") from exc
                self._model = model
            return self._model

    def transcribe(self, path: Path, *, language: str | None, hotwords: str | None) -> dict:
        model = self.load()
        with self._lock:
            segments, info = model.transcribe(
                str(path), language=language, beam_size=5, vad_filter=True,
                initial_prompt=PROMPTS.get(language or ""), hotwords=hotwords or None,
                condition_on_previous_text=False)
            text = " ".join(segment.text.strip() for segment in segments).strip()
        return {"text": text, "language": info.language, "duration": round(float(info.duration), 2)}


class VoiceService:
    def __init__(self, store, engine=None):
        self.store = store
        self.engine = engine or FasterWhisperEngine(store.state / "models")
        self._preparing: threading.Thread | None = None
        self._prepare_error: str | None = None

    def status(self) -> dict:
        installed = self.engine.installed()
        preparing = self._preparing is not None and self._preparing.is_alive()
        ready = installed and not preparing and self.engine.model_ready()
        state = ("missing" if not installed else "preparing" if preparing else "ready" if ready
                 else "error" if self._prepare_error else "not_downloaded")
        return {"engine": self.engine.name, "model": self.engine.model_name, "installed": installed,
                "state": state, "error": self._prepare_error if state == "error" else None}

    def prepare(self) -> dict:
        """Download and load the model in the background; poll status() for the result."""
        if not self.engine.installed():
            raise VoiceUnavailableError("Голосовой ввод не установлен. Выполните: pip install \"faster-whisper>=1.1,<2\"")
        if self._preparing is None or not self._preparing.is_alive():
            self._prepare_error = None

            def run():
                try:
                    self.engine.load()
                except VoiceUnavailableError as exc:
                    self._prepare_error = str(exc)

            self._preparing = threading.Thread(target=run, name="mmm-voice-prepare", daemon=True)
            self._preparing.start()
        return self.status()

    def hotwords(self) -> str:
        titles = []
        for note in self.store.list_notes():
            if note["kind"] in {"project", "person"} and note["status"] not in {"archived", "cancelled", "done"}:
                title = " ".join(note["title"].split())
                if title and title not in titles:
                    titles.append(title)
        return ", ".join(titles)[:400]

    def transcribe(self, data: bytes, content_type: str, *, language: str = "ru") -> dict:
        if language not in LANGUAGES:
            raise ValidationError("Язык распознавания: ru, en или auto")
        media_type = (content_type or "").split(";")[0].strip().lower()
        suffix = AUDIO_TYPES.get(media_type)
        if suffix is None:
            raise ValidationError("Формат аудио не поддерживается")
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise ValidationError("Пустая запись")
        if len(data) > MAX_AUDIO_BYTES:
            raise ValidationError("Запись длиннее допустимого размера")
        temporary = self.store.state / "voice"
        temporary.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="dictation-", suffix=suffix, dir=temporary)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
            try:
                result = self.engine.transcribe(Path(name), language=LANGUAGES[language], hotwords=self.hotwords())
            except VoiceUnavailableError:
                raise
            except Exception as exc:  # noqa: BLE001 - a broken or unsupported file
                raise ValidationError("Не удалось разобрать аудио") from exc
        finally:
            Path(name).unlink(missing_ok=True)
        text = re.sub(r"\s+", " ", result.get("text") or "").strip()
        return {"text": text, "language": result.get("language"), "duration": result.get("duration"),
                "engine": self.engine.name, "model": self.engine.model_name}
