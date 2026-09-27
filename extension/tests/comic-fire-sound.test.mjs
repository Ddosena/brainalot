import test from "node:test";
import assert from "node:assert/strict";
import { DEFAULT_IGNITION_SOUND, IGNITION_SOUNDS, normalizeIgnitionSound, scheduleIgnitionSound } from "../comic-fire-sound.js";

/** Just enough of an AudioContext to record what a voice schedules, with the real API's hard limits. */
function fakeContext() {
  const log = {sources: 0, overruns: []};
  const param = () => ({
    value: 0,
    cancelScheduledValues() {},
    setValueAtTime() {},
    linearRampToValueAtTime() {},
    exponentialRampToValueAtTime(value) { if (!(value > 0)) throw new RangeError("exponential ramp to a non-positive value"); },
    setValueCurveAtTime(values) { if (values.length < 2) throw new RangeError("a value curve needs two points"); },
  });
  const node = extra => ({connect: target => target, ...extra});
  const ctx = {
    sampleRate: 8000,
    currentTime: 0,
    createBuffer: (channels, length, rate) => {
      const data = new Float32Array(length);
      return {duration: length / rate, getChannelData: () => data};
    },
    createGain: () => node({gain: param()}),
    createBiquadFilter: () => node({type: "", frequency: param(), Q: param()}),
    createDynamicsCompressor: () => node({threshold: param(), knee: param(), ratio: param(), attack: param(), release: param()}),
    createStereoPanner: () => node({pan: param()}),
    createBufferSource() {
      const source = node({buffer: null, stop() {}});
      source.start = (when, offset = 0, duration = 0) => {
        log.sources += 1;
        if (offset + duration > source.buffer.duration + 1e-9) log.overruns.push({offset, duration});
      };
      return source;
    },
  };
  return {ctx, log};
}

test("the settings offer the three kept voices and silence, the campfire first", () => {
  assert.deepEqual(Object.keys(IGNITION_SOUNDS), ["campfire", "paper", "torch", "none"]);
  assert.equal(DEFAULT_IGNITION_SOUND, "campfire");
});

test("a saved sound that is gone or never existed falls back to the campfire", () => {
  for (const kind of ["campfire", "paper", "torch", "none"]) assert.equal(normalizeIgnitionSound(kind), kind);
  for (const stale of ["gas", "whoosh", "match", "", null, undefined, "toString", 3]) assert.equal(normalizeIgnitionSound(stale), "campfire");
});

test("every voice schedules within the noise buffers and the Web Audio limits", () => {
  for (const kind of ["campfire", "paper", "torch"]) {
    for (const length of [0.5, 0.75, 1.1]) {
      const {ctx, log} = fakeContext();
      assert.equal(scheduleIgnitionSound(ctx, {}, kind, {pan: -0.8, length}), true, kind);
      assert.ok(log.sources >= 2, `${kind} plays its layers`);
      assert.deepEqual(log.overruns, [], `${kind} reads past the end of a noise buffer`);
    }
  }
});

test("silence, unknown voices and zero volume schedule nothing", () => {
  for (const [kind, volume] of [["none", 0.5], ["gas", 0.5], ["toString", 0.5], ["campfire", 0]]) {
    const {ctx, log} = fakeContext();
    assert.equal(scheduleIgnitionSound(ctx, {}, kind, {volume}), false, kind);
    assert.equal(log.sources, 0);
  }
});
