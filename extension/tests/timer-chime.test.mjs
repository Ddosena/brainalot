import test from "node:test";
import assert from "node:assert/strict";
import { normalizeTimerChime, scheduleTimerChime } from "../timer-chime.js";

function fakeContext() {
  const log = {oscillators: 0, stops: [], badRamps: 0};
  const param = () => ({
    value: 0,
    setValueAtTime() {},
    linearRampToValueAtTime() {},
    exponentialRampToValueAtTime(value) { if (!(value > 0)) log.badRamps += 1; },
  });
  const node = extra => ({connect: target => target, ...extra});
  return {log, ctx: {
    currentTime: 0,
    createGain: () => node({gain: param()}),
    createOscillator: () => { log.oscillators += 1; return node({type: "", frequency: {value: 0}, start() {}, stop(when) { log.stops.push(when); }}); },
  }};
}

test("the chime is on unless it was switched off", () => {
  assert.equal(normalizeTimerChime(undefined), true);
  assert.equal(normalizeTimerChime(true), true);
  assert.equal(normalizeTimerChime(false), false);
});

test("the chime strikes three times and stays within a few seconds", () => {
  const {ctx, log} = fakeContext();
  const length = scheduleTimerChime(ctx, {});
  assert.equal(log.oscillators, 12);
  assert.equal(log.badRamps, 0);
  assert.ok(length > 1.5 && length < 3.5, `length ${length}`);
  assert.ok(Math.max(...log.stops) <= length + 0.05);
});
