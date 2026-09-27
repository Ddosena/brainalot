import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from megamozg import api as api_module
from megamozg.api import create_app
from megamozg.core import Store, ValidationError
from megamozg.voice import MAX_AUDIO_BYTES, FasterWhisperEngine, VoiceService, VoiceUnavailableError


class FakeEngine:
    name = "fake"
    model_name = "tiny"

    def __init__(self, text="  завтра в 10   созвон с командой ", installed=True, ready=True, error=None):
        self.text, self._installed, self.ready, self.error = text, installed, ready, error
        self.calls = []

    def installed(self):
        return self._installed

    def model_ready(self):
        return self.ready

    def load(self):
        if self.error:
            raise VoiceUnavailableError(self.error)
        self.ready = True

    def transcribe(self, path, *, language, hotwords):
        assert Path(path).exists()
        self.calls.append({"suffix": Path(path).suffix, "bytes": Path(path).read_bytes(), "language": language,
                           "hotwords": hotwords})
        if self.error:
            raise RuntimeError(self.error)
        return {"text": self.text, "language": language or "ru", "duration": 2.5}


def test_transcribe_passes_audio_language_and_library_names_as_hot_words(tmp_path):
    store = Store(tmp_path)
    store.capture("Проект", kind="project", title="Мегамозг")
    store.capture("Человек", kind="person", title="Маша  Иванова")
    archived = store.capture("Старое", kind="project", title="Старый проект")
    store.update(archived["id"], {"status": "archived"}, archived["version"])
    engine = FakeEngine()
    result = VoiceService(store, engine).transcribe(b"webm-bytes", "audio/webm;codecs=opus")
    assert result == {"text": "завтра в 10 созвон с командой", "language": "ru", "duration": 2.5,
                      "engine": "fake", "model": "tiny"}
    call = engine.calls[0]
    assert (call["suffix"], call["bytes"], call["language"]) == (".webm", b"webm-bytes", "ru")
    assert set(call["hotwords"].split(", ")) == {"Мегамозг", "Маша Иванова"}
    assert list((tmp_path / "state" / "voice").iterdir()) == []


@pytest.mark.parametrize("content_type, message", [
    ("text/plain", "Формат аудио"), ("", "Формат аудио"), ("audio/webm", "Пустая запись")])
def test_bad_audio_is_a_validation_error(tmp_path, content_type, message):
    with pytest.raises(ValidationError, match=message):
        VoiceService(Store(tmp_path), FakeEngine()).transcribe(b"" if content_type == "audio/webm" else b"x",
                                                               content_type)


def test_language_is_limited_and_auto_means_detection(tmp_path):
    engine = FakeEngine()
    service = VoiceService(Store(tmp_path), engine)
    service.transcribe(b"x", "audio/wav", language="auto")
    assert engine.calls[-1]["language"] is None and engine.calls[-1]["suffix"] == ".wav"
    with pytest.raises(ValidationError, match="Язык"):
        service.transcribe(b"x", "audio/wav", language="de")


def test_oversized_and_undecodable_audio_are_rejected(tmp_path):
    service = VoiceService(Store(tmp_path), FakeEngine(error="broken file"))
    with pytest.raises(ValidationError, match="размера"):
        service.transcribe(b"x" * (MAX_AUDIO_BYTES + 1), "audio/webm")
    with pytest.raises(ValidationError, match="Не удалось разобрать аудио"):
        service.transcribe(b"x", "audio/webm")
    assert list((tmp_path / "state" / "voice").iterdir()) == []


def test_status_reports_missing_package_download_and_ready_states(tmp_path):
    store = Store(tmp_path)
    assert VoiceService(store, FakeEngine(installed=False)).status()["state"] == "missing"
    assert VoiceService(store, FakeEngine(ready=False)).status()["state"] == "not_downloaded"
    assert VoiceService(store, FakeEngine()).status() == {
        "engine": "fake", "model": "tiny", "installed": True, "state": "ready", "error": None}


def test_prepare_loads_the_model_in_the_background(tmp_path):
    engine = FakeEngine(ready=False)
    service = VoiceService(Store(tmp_path), engine)
    service.prepare()
    service._preparing.join(5)
    assert service.status()["state"] == "ready"
    failing = VoiceService(Store(tmp_path), FakeEngine(ready=False, error="Не удалось загрузить модель"))
    failing.prepare()
    failing._preparing.join(5)
    assert failing.status()["state"] == "error"
    assert failing.status()["error"] == "Не удалось загрузить модель"
    with pytest.raises(VoiceUnavailableError):
        VoiceService(Store(tmp_path), FakeEngine(installed=False)).prepare()


def test_prepare_accepts_a_bundled_engine_in_a_frozen_app(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    engine = FakeEngine(ready=False)
    service = VoiceService(Store(tmp_path), engine)
    service.prepare()
    service._preparing.join(5)
    assert service.status()["state"] == "ready"


def test_real_engine_explains_how_to_install_when_the_package_is_missing(tmp_path, monkeypatch):
    engine = FasterWhisperEngine(tmp_path / "models", model="small")
    monkeypatch.setattr(FasterWhisperEngine, "installed", staticmethod(lambda: False))
    assert engine.model_ready() is False
    monkeypatch.setitem(__import__("sys").modules, "faster_whisper", None)
    with pytest.raises(VoiceUnavailableError, match="pip install"):
        engine.load()


def fake_faster_whisper(monkeypatch, *, cached, broken_cache=False):
    opened = []

    class WhisperModel:
        def __init__(self, name, *, device, compute_type, download_root, local_files_only):
            opened.append(local_files_only)
            if local_files_only and broken_cache:
                raise RuntimeError("model.bin is missing")

    def download_model(name, *, local_files_only, cache_dir):
        if not cached:
            raise RuntimeError("not in the cache")
        return cache_dir

    package, utils = types.ModuleType("faster_whisper"), types.ModuleType("faster_whisper.utils")
    package.WhisperModel, package.utils, utils.download_model = WhisperModel, utils, download_model
    monkeypatch.setitem(sys.modules, "faster_whisper", package)
    monkeypatch.setitem(sys.modules, "faster_whisper.utils", utils)
    return opened


@pytest.mark.parametrize("cached, broken_cache, opened", [
    (True, False, [True]),        # downloaded: no request to huggingface.co, works without a VPN
    (True, True, [True, False]),  # incomplete cache: repaired by a download
    (False, False, [False]),      # first use: download
])
def test_real_engine_opens_a_downloaded_model_without_the_network(tmp_path, monkeypatch, cached, broken_cache,
                                                                   opened):
    calls = fake_faster_whisper(monkeypatch, cached=cached, broken_cache=broken_cache)
    engine = FasterWhisperEngine(tmp_path / "models", model="small", device="cpu", compute_type="int8")
    assert engine.load() is engine.load()
    assert calls == opened


def test_engine_settings_come_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("MMM_WHISPER_MODEL", "large-v3-turbo")
    monkeypatch.setenv("MMM_WHISPER_DEVICE", "cpu")
    monkeypatch.setenv("MMM_WHISPER_COMPUTE", "int8")
    engine = FasterWhisperEngine(tmp_path)
    assert (engine.model_name, engine.device, engine.compute_type) == ("large-v3-turbo", "cpu", "int8")


def test_http_routes_accept_raw_audio_and_return_a_dictation_preview(tmp_path, monkeypatch):
    engine = FakeEngine()
    monkeypatch.setattr(api_module, "VoiceService", lambda store: VoiceService(store, engine))
    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765")
    auth = {"Authorization": "Bearer " + Store(tmp_path).token()}
    assert client.post("/api/voice/transcribe", content=b"x", headers={"Content-Type": "audio/webm"}).status_code == 401
    response = client.post("/api/voice/transcribe?preview=true", content=b"opus",
                           headers={**auth, "Content-Type": "audio/webm;codecs=opus"})
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "завтра в 10 созвон с командой"
    assert body["preview"]["route"] == "calendar" and body["preview"]["title"] == "Созвон с командой"
    assert client.get("/api/voice/status", headers=auth).json()["state"] == "ready"
    assert client.post("/api/voice/transcribe", content=b"x", headers={**auth, "Content-Type": "text/plain"}).status_code == 422


def test_http_body_limit_allows_audio_but_not_unbounded_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(api_module, "VoiceService", lambda store: VoiceService(store, FakeEngine()))
    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765")
    auth = {"Authorization": "Bearer " + Store(tmp_path).token(), "Content-Type": "audio/webm"}
    assert client.post("/api/voice/transcribe", content=b"x" * 1_000_000, headers=auth).status_code == 200
    assert client.post("/api/voice/transcribe", content=b"x" * (15 * 1024 * 1024), headers=auth).status_code == 413


def test_http_reports_missing_engine_as_service_unavailable(tmp_path, monkeypatch):
    engine = FakeEngine(installed=False)
    engine.transcribe = lambda *args, **kwargs: (_ for _ in ()).throw(VoiceUnavailableError("Голосовой ввод не установлен"))
    monkeypatch.setattr(api_module, "VoiceService", lambda store: VoiceService(store, engine))
    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765")
    auth = {"Authorization": "Bearer " + Store(tmp_path).token()}
    response = client.post("/api/voice/transcribe", content=b"x", headers={**auth, "Content-Type": "audio/webm"})
    assert response.status_code == 503 and "не установлен" in response.json()["detail"]
    assert client.post("/api/voice/prepare", headers=auth).status_code == 503
