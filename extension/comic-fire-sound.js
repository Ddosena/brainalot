// Ignition sounds (Claude, 2026-09-24), synthesized with Web Audio, so there are no audio files or
// licences. Play them only from a user gesture (the click or double click that sets the fire) —
// never while the fire keeps burning.
//
// Fire is broadband noise, so every voice here is pink or brown noise through gentle low/high-pass
// filters (Q ≤ 0.5: no resonant peak, nothing that sounds like a vowel), shaped by an envelope and a
// seeded, non-periodic flutter for the flame turbulence, plus short pops for crackle. The first
// drafts swept a narrow band-pass and a sine thump, which read as a voice saying "э".

import { t } from "./i18n.js";

let context = null;
let idleTimer = 0;
const buffers = new Map();

/** The voices the user kept, in the order of the settings menu. */
export const IGNITION_SOUNDS = {
  campfire: t("Костёр"),
  paper: t("Бумага"),
  torch: t("Факел"),
  none: t("Без звука"),
};
export const DEFAULT_IGNITION_SOUND = "campfire";

/** A saved setting that names no current voice falls back to the default. */
export function normalizeIgnitionSound(value) {
  return Object.hasOwn(IGNITION_SOUNDS, value) ? value : DEFAULT_IGNITION_SOUND;
}

function audio() {
  const AudioContextClass = globalThis.AudioContext || globalThis.webkitAudioContext;
  if (!AudioContextClass) return null;
  context ||= new AudioContextClass();
  // Always ask: a resume queued after an idle suspend that is still in flight wins over it.
  context.resume().catch(() => {});
  return context;
}

/** Seeded random numbers, so every ignition sounds exactly the same. */
function seeded(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

/** 2 s of white, pink (Paul Kellet's filter) or brown noise, generated once per sample rate. */
function noise(ctx, color) {
  const key = `${color}@${ctx.sampleRate}`;
  if (buffers.has(key)) return buffers.get(key);
  const length = Math.round(ctx.sampleRate * 2);
  const buffer = ctx.createBuffer(1, length, ctx.sampleRate);
  const data = buffer.getChannelData(0);
  const random = seeded({white: 0x2f6b1a3d, pink: 0x51ed2701, brown: 0x7c3a9e11}[color]);
  let b0 = 0, b1 = 0, b2 = 0, b3 = 0, b4 = 0, b5 = 0, b6 = 0, last = 0;
  for (let index = 0; index < length; index += 1) {
    const white = random() * 2 - 1;
    if (color === "pink") {
      b0 = 0.99886 * b0 + white * 0.0555179;
      b1 = 0.99332 * b1 + white * 0.0750759;
      b2 = 0.969 * b2 + white * 0.153852;
      b3 = 0.8665 * b3 + white * 0.3104856;
      b4 = 0.55 * b4 + white * 0.5329522;
      b5 = -0.7616 * b5 - white * 0.016898;
      data[index] = (b0 + b1 + b2 + b3 + b4 + b5 + b6 + white * 0.5362) * 0.11;
      b6 = white * 0.115926;
    } else if (color === "brown") {
      last = (last + 0.02 * white) / 1.02;
      data[index] = last * 3.5;
    } else {
      data[index] = white;
    }
  }
  buffers.set(key, buffer);
  return buffer;
}

/** Automate `param` through [[seconds, value], ...]: linear moves, exponential fades to silence for gains. */
function curve(param, at, points, {linear = false} = {}) {
  param.cancelScheduledValues(at);
  param.setValueAtTime(points[0][1], at + points[0][0]);
  for (const [time, value] of points.slice(1)) {
    if (!linear && value <= 0.0001) param.exponentialRampToValueAtTime(0.0001, at + time);
    else param.linearRampToValueAtTime(value, at + time);
  }
}

/** Flame turbulence: a gain that wanders around 1 along a smoothed, seeded random walk (never periodic). */
function flutter(ctx, at, length, {rate = 18, depth = 0.3, seed = 7}) {
  const node = ctx.createGain();
  const random = seeded(seed);
  const knots = Math.max(2, Math.ceil(length * rate) + 1);
  const values = Array.from({length: knots + 1}, () => random());
  const samples = new Float32Array(Math.max(2, Math.ceil(length * 400)));
  for (let index = 0; index < samples.length; index += 1) {
    const t = (index / (samples.length - 1)) * (knots - 1);
    const knot = Math.floor(t);
    const blend = (1 - Math.cos((t - knot) * Math.PI)) / 2;
    const value = values[knot] * (1 - blend) + values[knot + 1] * blend;
    samples[index] = 1 - depth + 2 * depth * value;
  }
  node.gain.setValueCurveAtTime(samples, at, length);
  return node;
}

/** One noise layer through gentle filters, an envelope and an optional flutter. */
function layer(ctx, output, at, {color = "pink", length, offset = 0, filters = [], envelope, turbulence}) {
  const source = ctx.createBufferSource();
  source.buffer = noise(ctx, color);
  let node = source;
  for (const spec of filters) {
    const filter = ctx.createBiquadFilter();
    filter.type = spec.type;
    filter.Q.value = spec.q ?? 0.5;
    curve(filter.frequency, at, spec.frequency);
    node = node.connect(filter);
  }
  const amp = ctx.createGain();
  curve(amp.gain, at, envelope);
  node = node.connect(amp);
  if (turbulence) node = node.connect(flutter(ctx, at, length, turbulence));
  node.connect(output);
  // Read the noise from `offset`, or earlier when a long layer would run past the end of the buffer.
  source.start(at, Math.max(0, Math.min(offset, source.buffer.duration - length - 0.05)), length + 0.05);
  source.stop(at + length + 0.05);
}

/** A crackle pop: a few milliseconds of lightly coloured noise with a fast decay. */
function pop(ctx, output, at, {level, frequency, decay, offset}) {
  const source = ctx.createBufferSource();
  source.buffer = noise(ctx, "white");
  const band = ctx.createBiquadFilter();
  band.type = "bandpass";
  band.Q.value = 1.1;
  band.frequency.value = frequency;
  const amp = ctx.createGain();
  curve(amp.gain, at, [[0, 0.0001], [0.0008, level], [decay, 0.0001]]);
  source.connect(band).connect(amp).connect(output);
  source.start(at, offset, decay + 0.01);
  source.stop(at + decay + 0.01);
}

/** Crackle at seeded moments: denser early, mostly small pops, a few loud ones, optional deeper wood snaps. */
function crackles(ctx, output, at, {count, from, to, seed, level = 0.5, low = 1200, high = 4500, snaps = 0}) {
  const random = seeded(seed);
  for (let index = 0; index < count; index += 1) {
    const time = from + (to - from) * random() ** 1.6;
    const size = random() ** 2.2;
    pop(ctx, output, at + time, {level: level * (0.25 + 0.75 * size), frequency: low + (high - low) * random(),
      decay: 0.006 + 0.014 * random(), offset: 0.05 + random() * 1.2});
  }
  for (let index = 0; index < snaps; index += 1) {
    pop(ctx, output, at + from + (to - from) * random(), {level: level * 1.1, frequency: 650 + 500 * random(),
      decay: 0.03 + 0.02 * random(), offset: 0.05 + random() * 1.2});
  }
}

const VOICES = {
  /** Torch: one broad "фшух" of air, rising and falling, with the flame flutter. */
  torch(ctx, out, at, length) {
    layer(ctx, out, at, {color: "brown", length, offset: 0.1, filters: [{type: "lowpass", frequency: [[0, 220], [0.16, 1400], [length, 380]], q: 0.45}],
      envelope: [[0, 0.0001], [0.07, 1.1], [0.2, 0.8], [length, 0.0001]], turbulence: {rate: 16, depth: 0.3, seed: 41}});
    layer(ctx, out, at + 0.02, {color: "pink", length, offset: 0.6,
      filters: [{type: "highpass", frequency: [[0, 300]]}, {type: "lowpass", frequency: [[0, 700], [0.18, 4200], [length, 1400]]}],
      envelope: [[0, 0.0001], [0.09, 0.5], [0.25, 0.3], [length, 0.0001]], turbulence: {rate: 22, depth: 0.35, seed: 42}});
  },
  /** Campfire: a deep "вумф" and the crackle of wood catching. */
  campfire(ctx, out, at, length) {
    const tail = Math.max(0.6, length);
    layer(ctx, out, at, {color: "brown", length: 0.55, offset: 0.2, filters: [{type: "lowpass", frequency: [[0, 120], [0.05, 520], [0.55, 160]], q: 0.4}],
      envelope: [[0, 0.0001], [0.02, 1.5], [0.15, 0.55], [0.55, 0.0001]], turbulence: {rate: 14, depth: 0.25, seed: 51}});
    layer(ctx, out, at + 0.05, {color: "pink", length: tail, offset: 1,
      filters: [{type: "highpass", frequency: [[0, 900]]}, {type: "lowpass", frequency: [[0, 5000]]}],
      envelope: [[0, 0.0001], [0.08, 0.16], [tail, 0.0001]], turbulence: {rate: 28, depth: 0.45, seed: 52}});
    crackles(ctx, out, at, {count: 16, from: 0.06, to: tail + 0.05, seed: 53, level: 0.55, snaps: 3});
  },
  /** Paper: a quick bright hiss and a fizz of fine crackles. */
  paper(ctx, out, at, length) {
    const tail = Math.max(0.5, length - 0.1);
    layer(ctx, out, at, {color: "pink", length: tail, offset: 0.3,
      filters: [{type: "highpass", frequency: [[0, 1600], [0.3, 1100]]}, {type: "lowpass", frequency: [[0, 3500], [0.1, 8000], [tail, 4000]]}],
      envelope: [[0, 0.0001], [0.06, 0.45], [0.2, 0.25], [tail, 0.0001]], turbulence: {rate: 34, depth: 0.5, seed: 61}});
    crackles(ctx, out, at, {count: 30, from: 0.03, to: Math.max(0.55, length), seed: 62, level: 0.3, low: 2800, high: 7500});
  },
};

// Per-voice level trims, so the variants sound equally loud (measured on offline renders).
const TRIM = {torch: 0.74, campfire: 1.4, paper: 1.8};

/**
 * Build one ignition sound into `destination` of any AudioContext (live or offline).
 * `pan` −1..1 follows where the fire started; `length` (s) matches the ignition sweep.
 */
export function scheduleIgnitionSound(ctx, destination, kind, {volume = 0.5, pan = 0, length = 0.75, at = ctx.currentTime + 0.01} = {}) {
  const voice = Object.hasOwn(VOICES, kind) ? VOICES[kind] : null;
  if (!voice || volume <= 0) return false;
  const master = ctx.createGain();
  master.gain.value = Math.min(1, volume) * 0.6 * (TRIM[kind] ?? 1);
  // A gentle limiter keeps the loudest pops from clipping.
  const limiter = ctx.createDynamicsCompressor();
  limiter.threshold.value = -6;
  limiter.knee.value = 6;
  limiter.ratio.value = 8;
  limiter.attack.value = 0.002;
  limiter.release.value = 0.12;
  const panner = ctx.createStereoPanner ? ctx.createStereoPanner() : null;
  if (panner) {
    curve(panner.pan, at, [[0, Math.max(-1, Math.min(1, pan * 0.6))], [length, -pan * 0.15]], {linear: true});
    master.connect(limiter).connect(panner).connect(destination);
  } else {
    master.connect(limiter).connect(destination);
  }
  voice(ctx, master, at, length);
  return true;
}

/** Play an ignition sound now (call it from the click that sets the fire). */
export function playIgnitionSound(kind, options = {}) {
  if (!Object.hasOwn(VOICES, kind)) return false;
  const ctx = audio();
  if (!ctx || !scheduleIgnitionSound(ctx, ctx.destination, kind, options)) return false;
  // The panel stays open all day: let the audio device sleep between ignitions.
  clearTimeout(idleTimer);
  idleTimer = setTimeout(() => { if (ctx.state === "running") void ctx.suspend(); }, ((options.length ?? 0.75) + 1.5) * 1000);
  return true;
}
