// Voice input for the capture window.
//
// The microphone is recorded with MediaRecorder (Opus in WebM) and the audio is
// sent to the local service, which transcribes it on this computer
// (megamozg/voice.py). Recording stops on a click; pauses to think are fine
// (user, 2026-09-24). Only a forgotten microphone stops by itself: a minute of
// quiet or the time limit. Browser APIs come in as parameters, so the logic runs in Node
// tests; the real microphone and permissions are checked in Chrome.
//
// The side panel cannot show Chrome's microphone prompt, so recording belongs
// in the capture window (capture.html); once allowed there, the permission
// covers the whole extension.
import { apiBase } from "./api.js";
import { t, translateServerMessage } from "./i18n.js";

export const VOICE_MIME_TYPES = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/webm", "audio/mp4"];
export const RECORDER_DEFAULTS = Object.freeze({
  maxDurationMs: 300_000,   // hard limit for one dictation
  silenceMs: 60_000,        // a forgotten microphone: a minute of quiet after speech
  startTimeoutMs: 60_000,   // …or a minute without any speech at all
  speechLevel: 0.02,        // RMS level that counts as speech
  levelIntervalMs: 100,
  timesliceMs: 250,
});

export function pickMimeType(MediaRecorderClass) {
  return VOICE_MIME_TYPES.find(type => MediaRecorderClass?.isTypeSupported?.(type)) || "";
}

export function rms(samples) {
  let sum = 0;
  for (const sample of samples) sum += sample * sample;
  return samples.length ? Math.sqrt(sum / samples.length) : 0;
}

export function microphoneError(error) {
  switch (error?.name) {
    case "NotAllowedError":
    case "SecurityError":
      return t("Нет доступа к микрофону. Разрешите микрофон для Brainalot в настройках Chrome и попробуйте снова.");
    case "NotFoundError":
    case "OverconstrainedError":
      return t("Микрофон не найден. Подключите его и попробуйте снова.");
    case "NotReadableError":
    case "AbortError":
      return t("Микрофон занят другой программой.");
    default:
      return t("Не удалось включить микрофон.");
  }
}

/**
 * A microphone recorder with a level meter and automatic stop.
 * `onAutoStop(reason)` receives "silence", "no-speech" or "limit"; by default
 * it stops the recording, and the owner collects the result from stop().
 */
export function createVoiceRecorder(options = {}) {
  const {
    getUserMedia = constraints => globalThis.navigator.mediaDevices.getUserMedia(constraints),
    MediaRecorderClass = globalThis.MediaRecorder,
    AudioContextClass = globalThis.AudioContext,
    every = (callback, ms) => globalThis.setInterval(callback, ms),
    cancelEvery = id => globalThis.clearInterval(id),
    now = () => Date.now(),
    onLevel = () => {},
    onState = () => {},
    onAutoStop,
    ...limits
  } = options;
  const settings = { ...RECORDER_DEFAULTS, ...limits };
  let state = "idle", stream = null, recorder = null, context = null, analyser = null, buffer = null;
  let timer = null, chunks = [], mimeType = "", startedAt = 0, lastSpeech = 0, heardSpeech = false;
  let stopping = null;

  const setState = next => { state = next; onState(next); };
  const release = () => {
    if (timer !== null) cancelEvery(timer);
    timer = null;
    for (const track of stream?.getTracks?.() || []) track.stop();
    stream = null;
    void context?.close?.()?.catch?.(() => {});
    context = analyser = buffer = null;
  };

  function tick() {
    if (state !== "recording") return;
    let level = 0;
    if (analyser) {
      analyser.getFloatTimeDomainData(buffer);
      level = rms(buffer);
    }
    onLevel(level);
    const moment = now();
    if (level >= settings.speechLevel) {
      heardSpeech = true;
      lastSpeech = moment;
    }
    const elapsed = moment - startedAt;
    const reason = elapsed >= settings.maxDurationMs ? "limit"
      : heardSpeech && moment - lastSpeech >= settings.silenceMs ? "silence"
        : !heardSpeech && elapsed >= settings.startTimeoutMs ? "no-speech" : null;
    if (!reason) return;
    if (onAutoStop) onAutoStop(reason);
    else void stop().catch(() => {});
  }

  async function start() {
    if (state !== "idle") throw new Error(t("Запись уже идёт."));
    if (!MediaRecorderClass) throw new Error(t("Этот браузер не умеет записывать звук."));
    setState("starting");
    try {
      stream = await getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true,
        autoGainControl: true } });
    } catch (error) {
      if (state === "starting") setState("idle");
      throw new Error(microphoneError(error));
    }
    if (state !== "starting") {
      // cancel() ran while Chrome was asking for the microphone: give it back at once.
      for (const track of stream?.getTracks?.() || []) track.stop();
      stream = null;
      return;
    }
    mimeType = pickMimeType(MediaRecorderClass);
    recorder = new MediaRecorderClass(stream, mimeType ? { mimeType, audioBitsPerSecond: 32_000 } : undefined);
    chunks = [];
    recorder.addEventListener("dataavailable", event => { if (event.data?.size) chunks.push(event.data); });
    if (AudioContextClass) {
      context = new AudioContextClass();
      analyser = context.createAnalyser();
      analyser.fftSize = 2048;
      buffer = new Float32Array(analyser.fftSize);
      context.createMediaStreamSource(stream).connect(analyser);
    }
    startedAt = lastSpeech = now();
    heardSpeech = false;
    recorder.start(settings.timesliceMs);
    timer = every(tick, settings.levelIntervalMs);
    setState("recording");
  }

  /** Stop recording; resolves {blob, durationMs, heardSpeech}. Safe to call twice. */
  function stop() {
    if (stopping) return stopping;
    if (state !== "recording") return Promise.reject(new Error(t("Запись не идёт.")));
    setState("stopping");
    const current = recorder;
    let settle;
    const pending = new Promise((resolve, reject) => { settle = { resolve, reject }; });
    // Assign before recorder.stop(): its "stop" event may fire synchronously.
    stopping = pending;
    current.addEventListener("stop", () => {
      const blob = new Blob(chunks, { type: current.mimeType || mimeType || "audio/webm" });
      const result = { blob, durationMs: now() - startedAt, heardSpeech };
      release();
      stopping = null;
      setState("idle");
      settle.resolve(result);
    }, { once: true });
    current.addEventListener("error", () => {
      release();
      stopping = null;
      setState("idle");
      settle.reject(new Error(t("Запись прервалась.")));
    }, { once: true });
    current.stop();
    return pending;
  }

  function cancel() {
    if (recorder && recorder.state !== "inactive") {
      try { recorder.stop(); } catch { /* already stopped */ }
    }
    release();
    stopping = null;
    if (state !== "idle") setState("idle");
  }

  return { start, stop, cancel, get state() { return state; } };
}

/** Send recorded audio to the local service; resolves {text, language, duration, preview?}. */
export async function transcribeAudio(blob, { token, fetcher = fetch, language = "ru", preview = false,
  timeoutMs = 180_000 } = {}) {
  if (!token) throw new Error(t("Укажите токен подключения в настройках."));
  if (!blob?.size) throw new Error(t("Запись пустая. Попробуйте ещё раз."));
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  const query = new URLSearchParams({ language });
  if (preview) query.set("preview", "true");
  try {
    const response = await fetcher(`${apiBase()}/api/voice/transcribe?${query}`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": (blob.type || "audio/webm").split(";")[0] },
      body: blob, signal: controller.signal, credentials: "omit", redirect: "error", cache: "no-store",
    });
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      throw new Error(typeof data?.detail === "string" ? translateServerMessage(data.detail)
        : t("Сервис вернул ошибку {status}.", { status: response.status }));
    }
    if (typeof data?.text !== "string") throw new Error(t("Сервис вернул непонятный ответ."));
    return data;
  } catch (error) {
    if (error?.name === "AbortError") throw new Error(t("Распознавание заняло слишком много времени."));
    if (error instanceof TypeError) throw new Error(t("Локальный сервис недоступен. Запустите Brainalot и попробуйте снова."));
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

/** Insert recognised text at the cursor with sensible spacing and notify input listeners. */
export function insertText(textarea, text) {
  const value = textarea.value || "";
  const start = textarea.selectionStart ?? value.length;
  const end = textarea.selectionEnd ?? start;
  const before = value.slice(0, start), after = value.slice(end);
  const lead = before && !/\s$/.test(before) ? " " : "";
  const tail = after && !/^\s/.test(after) ? " " : "";
  textarea.value = before + lead + text + tail + after;
  const caret = (before + lead + text).length;
  textarea.selectionStart = textarea.selectionEnd = caret;
  textarea.dispatchEvent(new Event("input", { bubbles: true }));
}

/**
 * Wire a microphone button to a textarea: click to talk, click again
 * to finish. The button gets data-state (idle/starting/recording/stopping/
 * transcribing), aria-pressed and a --voice-level CSS variable (0…1).
 * `beforeStart` may refuse a click (for example while the model downloads);
 * `settle()` waits for a recording in progress to become text.
 */
export function attachDictation({ button, textarea, status, getToken, preview = false, language = "ru",
  recorderOptions = {}, transcribe = transcribeAudio, beforeStart = async () => true,
  onResult = () => {}, onError = () => {} }) {
  const report = (message, failed = false) => {
    if (!status) return;
    status.textContent = message;
    status.hidden = !message;
    status.dataset.error = failed ? "true" : "false";
  };
  const show = state => {
    button.dataset.state = state;
    button.setAttribute("aria-pressed", String(state === "recording"));
    button.setAttribute("aria-label", state === "recording" ? t("Остановить запись голоса") : t("Надиктовать"));
  };
  let finishing = null;
  const recorder = createVoiceRecorder({
    ...recorderOptions,
    onState: show,
    onLevel: level => button.style?.setProperty?.("--voice-level", Math.min(1, level * 8).toFixed(2)),
    onAutoStop: () => { void finish(); },
  });

  async function finish() {
    if (finishing) return finishing;
    finishing = (async () => {
      try {
        const { blob, heardSpeech } = await recorder.stop();
        if (!heardSpeech) { report(t("Не слышно речи. Говорите ближе к микрофону."), true); return; }
        show("transcribing");
        report(t("Распознаю…"));
        const result = await transcribe(blob, { token: await getToken(), preview, language });
        if (result.text) insertText(textarea, result.text);
        report(result.text ? "" : t("Не удалось разобрать слова. Попробуйте ещё раз."), !result.text);
        onResult(result);
      } catch (error) {
        report(error.message, true);
        onError(error);
      } finally {
        show(recorder.state);
        finishing = null;
      }
    })();
    return finishing;
  }

  let checking = false;
  button.addEventListener("click", async () => {
    if (finishing || checking) return;
    if (recorder.state === "recording") { await finish(); return; }
    if (recorder.state !== "idle") return;
    try {
      checking = true;
      try {
        if (!(await beforeStart())) return;
      } finally {
        checking = false;
      }
      report(t("Говорите… Можно думать и делать паузы. Чтобы закончить, нажмите микрофон ещё раз."));
      await recorder.start();
    } catch (error) {
      recorder.cancel();
      report(error.message, true);
      onError(error);
    }
  });
  show("idle");
  const settle = () => finishing || (recorder.state === "recording" ? finish() : Promise.resolve());
  return { recorder, finish, settle, report, cancel: () => { recorder.cancel(); report(""); } };
}
