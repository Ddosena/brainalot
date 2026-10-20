import assert from "node:assert/strict";
import test from "node:test";

import { setLocale } from "../i18n.js";
import {
  attachDictation, createVoiceRecorder, insertText, microphoneError, pickMimeType, rms, transcribeAudio,
} from "../voice-recorder.js";

class FakeRecorder extends EventTarget {
  static supported = new Set(["audio/webm;codecs=opus", "audio/webm"]);
  static isTypeSupported(type) { return FakeRecorder.supported.has(type); }
  constructor(stream, options) {
    super();
    this.stream = stream;
    this.mimeType = options?.mimeType || "";
    this.state = "inactive";
  }
  start(timeslice) { this.state = "recording"; this.timeslice = timeslice; }
  stop() {
    this.state = "inactive";
    const data = new Event("dataavailable");
    data.data = new Blob(["opus-audio"], { type: this.mimeType });
    this.dispatchEvent(data);
    this.dispatchEvent(new Event("stop"));
  }
}

function harness({ deny = null } = {}) {
  const clock = { now: 0, level: 0, ticks: null, stoppedTracks: 0, closed: false };
  const stream = { getTracks: () => [{ stop: () => { clock.stoppedTracks += 1; } }] };
  const options = {
    getUserMedia: async constraints => {
      clock.constraints = constraints;
      if (deny) throw Object.assign(new Error("denied"), { name: deny });
      return stream;
    },
    MediaRecorderClass: FakeRecorder,
    AudioContextClass: class {
      createAnalyser() { return { fftSize: 0, getFloatTimeDomainData: buffer => buffer.fill(clock.level) }; }
      createMediaStreamSource() { return { connect() {} }; }
      close() { clock.closed = true; return Promise.resolve(); }
    },
    every: callback => { clock.ticks = callback; return 1; },
    cancelEvery: () => { clock.ticks = null; },
    now: () => clock.now,
  };
  const advance = (ms, level) => { clock.now += ms; clock.level = level; clock.ticks?.(); };
  return { clock, options, advance };
}

test.afterEach(() => setLocale("ru"));

test("a pause to think keeps recording; only a forgotten microphone stops itself", async () => {
  const { clock, options, advance } = harness();
  const reasons = [], levels = [];
  const recorder = createVoiceRecorder({ ...options, onLevel: level => levels.push(level),
    onAutoStop: reason => reasons.push(reason) });
  await recorder.start();
  assert.equal(recorder.state, "recording");
  assert.equal(clock.constraints.audio.noiseSuppression, true);
  advance(100, 0.2);
  advance(20_000, 0);
  assert.deepEqual(reasons, [], "twenty seconds of thinking do not end the dictation");
  advance(100, 0.3);
  advance(59_800, 0);
  assert.deepEqual(reasons, []);
  advance(300, 0);
  assert.deepEqual(reasons, ["silence"]);
  const result = await recorder.stop();
  assert.equal(result.heardSpeech, true);
  assert.equal(result.blob.type, "audio/webm;codecs=opus");
  assert.equal(await result.blob.text(), "opus-audio");
  assert.equal(recorder.state, "idle");
  assert.equal(clock.stoppedTracks, 1);
  assert.equal(clock.closed, true);
  assert.ok(Math.abs(levels[0] - 0.2) < 1e-6);
});

test("silence from the start and the length limit end the recording too", async () => {
  const quiet = harness();
  const reasons = [];
  const recorder = createVoiceRecorder({ ...quiet.options, onAutoStop: reason => reasons.push(reason) });
  await recorder.start();
  quiet.advance(30_000, 0);
  assert.deepEqual(reasons, []);
  quiet.advance(30_000, 0);
  assert.deepEqual(reasons, ["no-speech"]);
  await recorder.stop();

  const long = harness();
  const limited = createVoiceRecorder({ ...long.options, maxDurationMs: 3000, onAutoStop: reason => reasons.push(reason) });
  await limited.start();
  long.advance(1500, 0.3);
  long.advance(1500, 0.3);
  assert.deepEqual(reasons, ["no-speech", "limit"]);
});

test("without an owner the recorder stops by itself and can be stopped twice safely", async () => {
  const { options, advance } = harness();
  const recorder = createVoiceRecorder(options);
  await recorder.start();
  advance(100, 0.5);
  advance(60_100, 0);
  assert.equal(recorder.state, "idle");
  await assert.rejects(recorder.stop(), /Запись не идёт/);
});

test("microphone problems become plain Russian or English messages", async () => {
  const { options } = harness({ deny: "NotAllowedError" });
  const recorder = createVoiceRecorder(options);
  await assert.rejects(recorder.start(), /Нет доступа к микрофону/);
  assert.equal(recorder.state, "idle");
  setLocale("en");
  assert.match(microphoneError({ name: "NotFoundError" }), /^No microphone was found/);
  assert.match(microphoneError({ name: "NotReadableError" }), /another program/);
});

test("microphone permission recovery refers to the owning desktop or browser", () => {
  for (const name of ["NotAllowedError", "SecurityError"]) {
    assert.match(microphoneError({ name }, { desktop: true }), /настольных приложений в настройках Windows/);
    assert.doesNotMatch(microphoneError({ name }, { desktop: true }), /Chrome/);
    assert.match(microphoneError({ name }, { desktop: false }), /настройках Chrome/);
  }
});

test("the preferred format is Opus in WebM and the level meter is RMS", () => {
  assert.equal(pickMimeType(FakeRecorder), "audio/webm;codecs=opus");
  assert.equal(pickMimeType({ isTypeSupported: () => false }), "");
  assert.equal(rms(new Float32Array([0.5, -0.5])), 0.5);
});

test("transcription posts raw audio with the token and returns the text", async () => {
  const calls = [];
  const fetcher = async (url, init) => {
    calls.push({ url, init });
    return { ok: true, json: async () => ({ text: "купить хлеб", language: "ru", duration: 1.2 }) };
  };
  const blob = new Blob(["a"], { type: "audio/webm;codecs=opus" });
  const result = await transcribeAudio(blob, { token: "secret", fetcher, preview: true });
  assert.equal(result.text, "купить хлеб");
  assert.equal(calls[0].url, "http://127.0.0.1:8765/api/voice/transcribe?language=ru&preview=true");
  assert.equal(calls[0].init.headers.Authorization, "Bearer secret");
  assert.equal(calls[0].init.headers["Content-Type"], "audio/webm");
  assert.equal(calls[0].init.body, blob);
});

test("transcription errors are readable: server detail, offline service and empty audio", async () => {
  const blob = new Blob(["a"], { type: "audio/webm" });
  setLocale("en");
  const unavailable = async () => ({ ok: false, status: 503,
    json: async () => ({ detail: "Не удалось загрузить модель распознавания речи" }) });
  await assert.rejects(transcribeAudio(blob, { token: "x", fetcher: unavailable }), /Could not load the speech recognition model/);
  setLocale("ru");
  const offline = async () => { throw new TypeError("Failed to fetch"); };
  await assert.rejects(transcribeAudio(blob, { token: "x", fetcher: offline }), /Локальный сервис недоступен/);
  await assert.rejects(transcribeAudio(new Blob([]), { token: "x" }), /Запись пустая/);
  await assert.rejects(transcribeAudio(blob, {}), /токен/);
});

test("recognised text is inserted at the cursor with spaces and an input event", () => {
  const events = [];
  const textarea = Object.assign(new EventTarget(), { value: "Купить хлеб", selectionStart: 11, selectionEnd: 11 });
  textarea.addEventListener("input", () => events.push("input"));
  insertText(textarea, "и молоко");
  assert.equal(textarea.value, "Купить хлеб и молоко");
  textarea.selectionStart = textarea.selectionEnd = 0;
  insertText(textarea, "Завтра");
  assert.equal(textarea.value, "Завтра Купить хлеб и молоко");
  assert.equal(textarea.selectionStart, 6);
  assert.deepEqual(events, ["input", "input"]);
});

test("the dictation button records, transcribes on the second click and fills the textarea", async () => {
  const { options, advance } = harness();
  const button = Object.assign(new EventTarget(), { dataset: {}, attributes: {},
    setAttribute(name, value) { this.attributes[name] = value; }, style: { setProperty() {} } });
  const textarea = Object.assign(new EventTarget(), { value: "", selectionStart: 0, selectionEnd: 0 });
  const status = { textContent: "", hidden: true, dataset: {} };
  const results = [];
  const control = attachDictation({ button, textarea, status, getToken: async () => "secret", recorderOptions: options,
    transcribe: async (blob, settings) => ({ text: `поставь на завтра созвон (${settings.token})` }),
    onResult: result => results.push(result) });
  assert.equal(button.dataset.state, "idle");
  button.dispatchEvent(new Event("click"));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(button.dataset.state, "recording");
  assert.equal(button.attributes["aria-pressed"], "true");
  advance(100, 0.4);
  button.dispatchEvent(new Event("click"));
  await control.finish();  // the click already started finishing; this awaits the same run
  assert.equal(textarea.value, "поставь на завтра созвон (secret)");
  assert.equal(results.length, 1);
  assert.equal(button.dataset.state, "idle");
  assert.equal(status.hidden, true);
});

test("a click without speech explains the problem instead of sending silence", async () => {
  const { options, advance } = harness();
  const button = Object.assign(new EventTarget(), { dataset: {}, setAttribute() {}, style: { setProperty() {} } });
  const status = { textContent: "", hidden: true, dataset: {} };
  let sent = false;
  const control = attachDictation({ button, textarea: new EventTarget(), status, getToken: async () => "x",
    recorderOptions: options, transcribe: async () => { sent = true; return { text: "" }; } });
  button.dispatchEvent(new Event("click"));
  await new Promise(resolve => setImmediate(resolve));
  advance(100, 0);
  await control.finish();
  assert.equal(sent, false);
  assert.match(status.textContent, /Не слышно речи/);
  assert.equal(status.dataset.error, "true");
});

test("beforeStart can refuse a click, and settle() waits for the words", async () => {
  const { options, advance } = harness();
  const button = Object.assign(new EventTarget(), { dataset: {}, attributes: {},
    setAttribute(name, value) { this.attributes[name] = value; }, style: { setProperty() {} } });
  const textarea = Object.assign(new EventTarget(), { value: "", selectionStart: 0, selectionEnd: 0 });
  const status = { textContent: "", hidden: true, dataset: {} };
  let ready = false;
  const languages = [];
  const control = attachDictation({ button, textarea, status, getToken: async () => "x", recorderOptions: options,
    language: "auto", beforeStart: async () => ready,
    transcribe: async (blob, settings) => { languages.push(settings.language); return { text: "купить хлеб" }; } });
  button.dispatchEvent(new Event("click"));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(button.dataset.state, "idle");
  await control.settle();
  ready = true;
  button.dispatchEvent(new Event("click"));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(button.dataset.state, "recording");
  assert.match(status.textContent, /Говорите/);
  advance(100, 0.4);
  await control.settle();
  assert.equal(textarea.value, "купить хлеб");
  assert.deepEqual(languages, ["auto"]);
  assert.equal(button.dataset.state, "idle");
});

test("cancel while Chrome asks for the microphone releases it instead of recording", async () => {
  const { options, clock } = harness();
  let grant;
  const recorder = createVoiceRecorder({ ...options, getUserMedia: () => new Promise(resolve => { grant = resolve; }) });
  const starting = recorder.start();
  assert.equal(recorder.state, "starting");
  recorder.cancel();
  assert.equal(recorder.state, "idle");
  grant({ getTracks: () => [{ stop: () => { clock.stoppedTracks += 1; } }] });
  await starting;
  assert.equal(recorder.state, "idle");
  assert.equal(clock.stoppedTracks, 1);
  assert.equal(clock.ticks, null);
});

test("a cancelled permission request cannot take over a newer recording", async () => {
  const { options } = harness();
  const grants = [], stopped = [];
  const recorder = createVoiceRecorder({ ...options, getUserMedia: () => new Promise(resolve => grants.push(resolve)) });
  const old = recorder.start();
  recorder.cancel();
  const next = recorder.start();
  grants[0]({ getTracks: () => [{ stop: () => stopped.push("old") }] });
  await old;
  assert.equal(recorder.state, "starting");
  grants[1]({ getTracks: () => [{ stop: () => stopped.push("next") }] });
  await next;
  assert.equal(recorder.state, "recording");
  assert.deepEqual(stopped, ["old"]);
  recorder.cancel();
  assert.deepEqual(stopped, ["old", "next"]);
});

test("a recorder construction failure returns the microphone immediately", async () => {
  const { options, clock } = harness();
  const recorder = createVoiceRecorder({ ...options, MediaRecorderClass: class {
    constructor() { throw new Error("unsupported recorder"); }
  } });
  await assert.rejects(recorder.start(), /unsupported/);
  assert.equal(recorder.state, "idle");
  assert.equal(clock.stoppedTracks, 1);
});

test("cancel during transcription ignores late words and callbacks", async () => {
  const { options, advance } = harness();
  const button = Object.assign(new EventTarget(), { dataset: {}, setAttribute() {}, style: { setProperty() {} } });
  const textarea = Object.assign(new EventTarget(), { value: "", selectionStart: 0, selectionEnd: 0 });
  const status = { textContent: "", hidden: true, dataset: {} };
  let respond, callbacks = 0;
  const control = attachDictation({ button, textarea, status, getToken: async () => "x", recorderOptions: options,
    transcribe: () => new Promise(resolve => { respond = resolve; }), onResult: () => callbacks++ });
  button.dispatchEvent(new Event("click"));
  await new Promise(resolve => setImmediate(resolve));
  advance(100, .4);
  const finishing = control.finish();
  await new Promise(resolve => setImmediate(resolve));
  control.cancel();
  respond({ text: "late words" });
  await finishing;
  assert.equal(textarea.value, "");
  assert.equal(callbacks, 0);
  assert.equal(status.hidden, true);
});

test("settle waits for a pending microphone permission then transcribes", async () => {
  const { options, advance } = harness();
  let grant;
  const button = Object.assign(new EventTarget(), { dataset: {}, setAttribute() {}, style: { setProperty() {} } });
  const textarea = Object.assign(new EventTarget(), { value: "", selectionStart: 0, selectionEnd: 0 });
  const control = attachDictation({ button, textarea, getToken: async () => "x", recorderOptions: {
    ...options, getUserMedia: () => new Promise(resolve => { grant = resolve; }) }, transcribe: async () => ({ text: "words" }) });
  button.dispatchEvent(new Event("click"));
  await new Promise(resolve => setImmediate(resolve));
  let settled = false;
  const pending = control.settle().then(() => { settled = true; });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(settled, false);
  grant(await options.getUserMedia({}));
  // The permission resolution starts the recorder; wait one microtask before its owner stops it.
  queueMicrotask(() => advance(100, .4));
  await pending;
  assert.equal(control.recorder.state, "idle");
});

test("a recording device error releases the stream and reports the failure", async () => {
  const { options, clock } = harness();
  let device;
  const failures = [];
  const recorder = createVoiceRecorder({ ...options, MediaRecorderClass: class extends FakeRecorder {
    constructor(...args) { super(...args); device = this; }
  }, onError: error => failures.push(error.message) });
  await recorder.start();
  device.dispatchEvent(new Event("error"));
  assert.equal(recorder.state, "idle");
  assert.equal(clock.stoppedTracks, 1);
  assert.equal(clock.closed, true);
  assert.match(failures[0], /Запись прервалась/);
});

test("transcription timeout and explicit cancellation abort the fetch", async () => {
  const blob = new Blob(["audio"], { type: "audio/webm" });
  const fetcher = (_url, { signal }) => new Promise((_resolve, reject) => {
    const abort = () => reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
    if (signal.aborted) abort(); else signal.addEventListener("abort", abort, { once: true });
  });
  await assert.rejects(transcribeAudio(blob, { token: "x", fetcher, timeoutMs: 5 }), /слишком много времени/);
  const controller = new AbortController();
  const pending = transcribeAudio(blob, { token: "x", fetcher, signal: controller.signal });
  controller.abort();
  await assert.rejects(pending, /слишком много времени/);
});

test("settle rejects a failed dictation so Submit cannot save only the older text", async () => {
  const { options, advance } = harness();
  const button = Object.assign(new EventTarget(), { dataset: {}, setAttribute() {}, style: { setProperty() {} } });
  const textarea = Object.assign(new EventTarget(), { value: "Existing draft", selectionStart: 14, selectionEnd: 14 });
  const status = { textContent: "", hidden: true, dataset: {} };
  const control = attachDictation({ button, textarea, status, getToken: async () => "x", recorderOptions: options,
    transcribe: async () => { throw new Error("ASR unavailable"); } });
  button.dispatchEvent(new Event("click"));
  await new Promise(resolve => setImmediate(resolve));
  advance(100, .4);
  await assert.rejects(control.settle(), /ASR unavailable/);
  assert.equal(textarea.value, "Existing draft");
  assert.equal(status.dataset.error, "true");
  assert.equal(control.recorder.state, "idle");
});
