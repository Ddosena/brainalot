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

export function microphoneError(error, { desktop = globalThis.chrome?.desktop === true } = {}) {
  switch (error?.name) {
    case "NotAllowedError":
    case "SecurityError":
      return desktop
        ? t("Нет доступа к микрофону. Разрешите микрофон для Brainalot и проверьте доступ для настольных приложений в настройках Windows.")
        : t("Нет доступа к микрофону. Разрешите микрофон для Brainalot в настройках Chrome и попробуйте снова.");
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
    onError = () => {},
    onAutoStop,
    ...limits
  } = options;
  const settings = { ...RECORDER_DEFAULTS, ...limits };
  let state = "idle", stream = null, recorder = null, context = null, analyser = null, buffer = null;
  let timer = null, chunks = [], mimeType = "", startedAt = 0, lastSpeech = 0, heardSpeech = false;
  let stopping = null, generation = 0;

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
    const run = ++generation;
    setState("starting");
    let acquired;
    try {
      acquired = await getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true,
        autoGainControl: true } });
    } catch (error) {
      if (run !== generation) return;
      setState("idle");
      throw new Error(microphoneError(error));
    }
    if (run !== generation) {
      // cancel() ran while Chrome was asking for the microphone: give it back at once.
      for (const track of acquired?.getTracks?.() || []) track.stop();
      return;
    }
    stream = acquired;
    try {
      mimeType = pickMimeType(MediaRecorderClass);
      recorder = new MediaRecorderClass(stream, mimeType ? { mimeType, audioBitsPerSecond: 32_000 } : undefined);
      chunks = [];
      const recordedChunks = chunks;
      recorder.addEventListener("dataavailable", event => { if (event.data?.size) recordedChunks.push(event.data); });
      recorder.addEventListener("error", () => {
        // A disconnected device can fail before the user presses Stop. The stop()
        // owner handles errors while stopping; this handles an active recording.
        if (run !== generation || state !== "recording") return;
        release(); recorder = null; setState("idle");
        onError(new Error(t("Запись прервалась.")));
      });
      if (AudioContextClass) {
        context = new AudioContextClass();
        analyser = context.createAnalyser();
        analyser.fftSize = 2048;
        buffer = new Float32Array(analyser.fftSize);
        context.createMediaStreamSource(stream).connect(analyser);
        if (context.state === "suspended") await context.resume();
      }
      if (run !== generation) return;
      startedAt = lastSpeech = now();
      heardSpeech = false;
      recorder.start(settings.timesliceMs);
      timer = every(tick, settings.levelIntervalMs);
      setState("recording");
    } catch (error) {
      if (run !== generation) return;
      release();
      recorder = null;
      setState("idle");
      throw error;
    }
  }

  /** Stop recording; resolves {blob, durationMs, heardSpeech}. Safe to call twice. */
  function stop() {
    if (stopping) return stopping;
    if (state !== "recording") return Promise.reject(new Error(t("Запись не идёт.")));
    setState("stopping");
    const current = recorder;
    const run = generation, recordedChunks = chunks, started = startedAt, speech = heardSpeech;
    let settle;
    const pending = new Promise((resolve, reject) => { settle = { resolve, reject }; });
    // Assign before recorder.stop(): its "stop" event may fire synchronously.
    stopping = pending;
    current.addEventListener("stop", () => {
      const blob = new Blob(recordedChunks, { type: current.mimeType || mimeType || "audio/webm" });
      const result = { blob, durationMs: now() - started, heardSpeech: speech };
      if (run === generation) { release(); stopping = null; recorder = null; setState("idle"); }
      settle.resolve(result);
    }, { once: true });
    current.addEventListener("error", () => {
      if (run === generation) { release(); stopping = null; recorder = null; setState("idle"); }
      settle.reject(new Error(t("Запись прервалась.")));
    }, { once: true });
    try { current.stop(); } catch (error) {
      release(); stopping = null; recorder = null; setState("idle"); settle.reject(error);
    }
    return pending;
  }

  function cancel() {
    ++generation;
    if (recorder && recorder.state !== "inactive") {
      try { recorder.stop(); } catch { /* already stopped */ }
    }
    release();
    recorder = null;
    stopping = null;
    if (state !== "idle") setState("idle");
  }

  return { start, stop, cancel, get state() { return state; } };
}

/** Send recorded audio to the local service; resolves {text, language, duration, preview?}. */
export async function transcribeAudio(blob, { token, fetcher = fetch, language = "ru", preview = false, signal,
  timeoutMs = 180_000 } = {}) {
  if (!token) throw new Error(t("Укажите токен подключения в настройках."));
  if (!blob?.size) throw new Error(t("Запись пустая. Попробуйте ещё раз."));
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (signal?.aborted) controller.abort();
  else signal?.addEventListener("abort", abort, { once: true });
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
    signal?.removeEventListener("abort", abort);
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
  let finishing = null, starting = null, generation = 0, transcription = null;
  const recorder = createVoiceRecorder({
    ...recorderOptions,
    onState: show,
    onError: error => { report(error.message, true); onError(error); },
    onLevel: level => button.style?.setProperty?.("--voice-level", Math.min(1, level * 8).toFixed(2)),
    onAutoStop: () => { void finish(); },
  });

  async function finish() {
    if (finishing) return finishing;
    const run = generation;
    const pending = (async () => {
      try {
        const { blob, heardSpeech } = await recorder.stop();
        if (run !== generation) return;
        if (!heardSpeech) {
          const error = new Error(t("Не слышно речи. Говорите ближе к микрофону."));
          report(error.message, true);
          return { error };
        }
        show("transcribing");
        report(t("Распознаю…"));
        const controller = new AbortController();
        transcription = controller;
        const token = await getToken();
        if (run !== generation) return;
        const result = await transcribe(blob, { token, preview, language, signal: controller.signal });
        if (run !== generation) return;
        if (result.text) insertText(textarea, result.text);
        report(result.text ? "" : t("Не удалось разобрать слова. Попробуйте ещё раз."), !result.text);
        onResult(result);
        return result.text ? { result } : { error: new Error(t("Не удалось разобрать слова. Попробуйте ещё раз.")) };
      } catch (error) {
        if (run !== generation) return;
        report(error.message, true);
        onError(error);
        return { error };
      } finally {
        if (run === generation) { show(recorder.state); finishing = null; transcription = null; }
      }
    })();
    finishing = pending;
    return finishing;
  }

  let checking = false;
  async function start() {
    if (finishing || checking) return;
    if (recorder.state === "recording") return finish();
    if (recorder.state !== "idle") return;
    const run = generation;
    try {
      checking = true;
      try {
        if (!(await beforeStart()) || run !== generation) return;
      } finally {
        checking = false;
      }
      report(t("Говорите… Можно думать и делать паузы. Чтобы закончить, нажмите микрофон ещё раз."));
      await recorder.start();
    } catch (error) {
      if (run !== generation) return;
      recorder.cancel();
      report(error.message, true);
      onError(error);
      return { error };
    }
  }
  button.addEventListener("click", () => {
    if (starting) return;
    const pending = start();
    starting = pending;
    void pending.finally(() => { if (starting === pending) starting = null; });
  });
  show("idle");
  const settle = async () => {
    const begun = starting ? await starting : null;
    if (begun?.error) throw begun.error;
    const outcome = await (finishing || (recorder.state === "recording" ? finish() : Promise.resolve()));
    if (outcome?.error) throw outcome.error;
    return outcome?.result;
  };
  return { recorder, finish, settle, report, cancel: () => {
    ++generation; transcription?.abort(); transcription = null; finishing = null; starting = null;
    recorder.cancel(); report(""); show("idle");
  } };
}
