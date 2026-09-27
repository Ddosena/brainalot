import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  BOTTOM_ORDER, CAP_REACH, TOP_ORDER, fitUnits, halftoneDots, ignitionDelay, perimeterAt, planComicFire,
} from "../comic-fire-layout.js";

const sprite = readFileSync(new URL("../assets/comic-fire/comic-fire.svg", import.meta.url), "utf8");
const advance = Object.fromEntries([...sprite.matchAll(/id="cf-([\w-]+)"[^>]*data-advance="([\d.]+)"/g)].map(([, id, value]) => [id, Number(value)]));

test("the sprite provides every module the layout places, each pose animation has keyframes", () => {
  for (const id of [...TOP_ORDER, ...BOTTOM_ORDER, "side-left", "side-right"]) assert.ok(advance[id] > 0, `${id} needs data-advance`);
  for (const id of ["cap-left-top", "cap-left-bottom", "cap-right-top", "cap-right-bottom", "star", "ember-a", "ember-b"]) {
    assert.ok(sprite.includes(`id="cf-${id}"`), `${id} is missing`);
  }
  const used = new Set([...sprite.matchAll(/class="cf-[a-z]+ cf-anim (cf-a\d+)"/g)].map(match => match[1]));
  assert.ok(used.size > 50);
  for (const name of used) assert.ok(sprite.includes(`@keyframes ${name}{`), `${name} has no keyframes`);
});

test("edge units fill the row without stretching and never repeat back to back", () => {
  for (let width = 180; width <= 1200; width += 7) {
    const {placements} = planComicFire({width, height: 32, advance});
    for (const edge of ["top-", "bottom-"]) {
      const units = placements.filter(placement => placement.id.startsWith(edge));
      assert.ok(units.length >= 1, `${edge} units at ${width}`);
      for (let index = 1; index < units.length; index += 1) {
        assert.notEqual(units[index].id, units[index - 1].id, `${edge} repeats at ${width}`);
        const gap = units[index].x - units[index - 1].x - advance[units[index - 1].id];
        assert.ok(Math.abs(gap) <= 6.01, `${edge} gap ${gap} at ${width}`);
      }
      const reachLeft = edge === "top-" ? CAP_REACH.topLeft : CAP_REACH.bottomLeft;
      const reachRight = edge === "top-" ? CAP_REACH.topRight : CAP_REACH.bottomRight;
      const last = units.at(-1);
      assert.ok(units[0].x >= reachLeft - 6 && last.x + advance[last.id] <= width - reachRight + 6 + 1e-9, `${edge} span at ${width}`);
    }
  }
});

test("flame density stays about the same from narrow to wide rows", () => {
  const perHundred = [234, 354, 514, 634, 914].map(width => planComicFire({width, height: 32, advance}).placements.filter(p => p.id.startsWith("top-")).length / (width / 100));
  assert.ok(Math.max(...perHundred) - Math.min(...perHundred) < 1.1, perHundred.join(", "));
});

test("tall rows get whole side units instead of stretched caps", () => {
  const sides = height => planComicFire({width: 354, height, advance}).placements.filter(p => p.id.startsWith("side-"));
  assert.equal(sides(26).length, 0);
  assert.ok(sides(46).length >= 2 && sides(46).length % 2 === 0);
  assert.ok(sides(103).length > sides(46).length);
  const tall = sides(103);
  assert.deepEqual(tall.filter(p => p.id === "side-left").map(p => p.y), tall.filter(p => p.id === "side-right").map(p => p.y));
  for (const height of [26, 32, 46, 103]) {
    const caps = planComicFire({width: 354, height, advance}).placements.filter(p => p.id.startsWith("cap-"));
    assert.deepEqual(caps.map(p => [p.x, p.y]), [[0, 0], [0, height], [354, 0], [354, height]]);
  }
});

test("rows too small to burn get no fire", () => {
  assert.deepEqual(planComicFire({width: 40, height: 32, advance}).placements, []);
  assert.deepEqual(planComicFire({width: 300, height: 8, advance}).placements, []);
  assert.equal(fitUnits(0, TOP_ORDER, advance), null);
});

test("halftone dots grow towards the fire and stay out of the row", () => {
  const box = {x: -40, y: -40, width: 200, height: 120};
  const hole = {x: 0, y: 0, width: 100, height: 40};
  const dots = halftoneDots([{x: 50, y: -20, r: 30, tone: "o"}], box, {hole});
  assert.ok(dots.o.length > 10);
  for (const [x, y, r] of dots.o) {
    assert.ok(!(x > 0 && x < 100 && y > 0 && y < 40), "dot inside the row");
    assert.ok(Math.hypot(x - 50, y + 20) < 30);
  }
  const near = dots.o.reduce((a, b) => (Math.hypot(a[0] - 50, a[1] + 20) < Math.hypot(b[0] - 50, b[1] + 20) ? a : b));
  const far = dots.o.reduce((a, b) => (Math.hypot(a[0] - 50, a[1] + 20) > Math.hypot(b[0] - 50, b[1] + 20) ? a : b));
  assert.ok(near[2] > far[2]);
});

test("ignition runs from the click point: both ways, one lap, or radially", () => {
  const width = 300, height = 32;
  assert.equal(perimeterAt(0, 0, width, height), 0);
  assert.ok(Math.abs(perimeterAt(width, height / 2, width, height) - (width + height / 2) / (2 * (width + height))) < 1e-9);
  const s0 = perimeterAt(150, 0, width, height);
  const at = s => ignitionDelay("ring", {s, s0});
  assert.equal(at(s0), 0);
  assert.equal(at((s0 + 0.1) % 1), at((s0 - 0.1 + 1) % 1));
  assert.ok(at((s0 + 0.5) % 1) >= at((s0 + 0.25) % 1));
  const whirl = [0, 0.1, 0.3, 0.6, 0.9].map(d => ignitionDelay("whirl", {s: (s0 + d) % 1, s0}));
  assert.deepEqual([...whirl].sort((a, b) => a - b), whirl);
  const origin = {x: 150, y: 0};
  const burst = ignitionDelay("burst", {at: {x: 150, y: 16}, origin, width, height});
  assert.ok(burst < ignitionDelay("burst", {at: {x: 0, y: 32}, origin, width, height}));
  for (const variant of ["ring", "whirl", "burst"]) {
    const value = ignitionDelay(variant, {s: 0.37, s0: 0.9, at: {x: 10, y: 10}, origin, width, height});
    assert.ok(value >= 0 && value <= 800, `${variant}: ${value}`);
  }
});
