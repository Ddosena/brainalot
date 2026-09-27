import test from "node:test";
import assert from "node:assert/strict";
import { NEON_COLORS, normalizeNeonColor, normalizeTimer, resetTimerForExtensionUpdate, timerTransition, timerView, validateMinutes } from "../focus-timer.js";

test("extension update resets elapsed time, default duration and stops on pause", () => {
  const reset = resetTimerForExtensionUpdate();
  assert.deepEqual(reset, {durationMs:25 * 60_000, elapsedMs:0, startedAt:null, status:"paused"});
  assert.equal(timerView(reset, Date.now() + 60_000).elapsedMs, 0);
  assert.equal(timerTransition(reset, "resume", 123).status, "running");
});

test("presets and custom duration accept only whole minutes from 1 to 180", () => {
  for (const value of [1, 20, 25, 30, 50, 180]) assert.equal(validateMinutes(value), value);
  for (const value of [0, 181, 1.5, "", "abc"]) assert.throws(() => validateMinutes(value), /1 до 180/);
});

test("running timer derives elapsed time from wall clock after panel closes", () => {
  const started = timerTransition(null, "start", 1_000_000, 25);
  assert.deepEqual(timerView(started, 1_000_000 + 10 * 60_000).remainingMs, 15 * 60_000);
  const restored = normalizeTimer(JSON.parse(JSON.stringify(started)));
  assert.equal(timerView(restored, 1_000_000 + 25 * 60_000).status, "finished");
  assert.equal(timerView(restored, 1_000_000 + 60 * 60_000).progress, 100);
});

test("pause freezes elapsed time, resume continues from that point, reset clears it", () => {
  const started = timerTransition(null, "start", 1000, 20);
  const paused = timerTransition(started, "pause", 1000 + 5 * 60_000);
  assert.equal(timerView(paused, 1000 + 60 * 60_000).elapsedMs, 5 * 60_000);
  const resumed = timerTransition(paused, "resume", 1000 + 60 * 60_000);
  assert.equal(timerView(resumed, 1000 + 67 * 60_000).remainingMs, 8 * 60_000);
  const reset = timerTransition(resumed, "reset", 1000 + 67 * 60_000);
  assert.equal(timerView(reset, 1000 + 90 * 60_000).elapsedMs, 0);
  assert.equal(reset.durationMs, 20 * 60_000);
});

test("completed timer stays completed with a full bar and cannot resume", () => {
  const started = timerTransition(null, "start", 0, 1);
  const done = timerView(started, 60_000);
  assert.equal(done.status, "finished");
  assert.equal(done.remainingMs, 0);
  assert.equal(done.progress, 100);
  assert.throws(() => timerTransition(started, "resume", 120_000), /приостановите/);
  const again = timerTransition(started, "start", 120_000, 30);
  assert.equal(timerView(again, 120_000).remainingMs, 30 * 60_000);
});

test("neon colors match the service ids and unknown choices fall back to fire", () => {
  assert.deepEqual(NEON_COLORS.map(color => color.id), ["fire", "pink", "violet", "cyan", "lime", "amber"]);
  for (const color of NEON_COLORS) {
    assert.match(color.tube, /^#[0-9a-f]{6}$/);
    assert.match(color.core, /^#[0-9a-f]{6}$/);
    assert.ok(color.name.length > 1);
  }
  assert.equal(normalizeNeonColor("cyan"), "cyan");
  assert.equal(normalizeNeonColor("rainbow"), "fire");
  assert.equal(normalizeNeonColor(undefined), "fire");
});
