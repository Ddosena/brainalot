// Comic fire prototype (Claude, 2026-09-24): pure layout for the authored flame modules in
// assets/comic-fire/comic-fire.svg. No DOM here, so the rules are unit-tested.
//
// The fire around a row is composed, never stretched: caps are anchored to the row corners,
// the top and bottom edges are filled with whole authored units (small gap tweaks only), and
// tall rows get extra side units. Coordinates are row pixels: the row is [0, width] × [0, height].

import { t } from "./i18n.js";

export const BAND = 4.5;
export const CANVAS_PAD = {left: 38, right: 40, top: 36, bottom: 30};
// How far each corner piece reaches along the edge, so the edge units start after it.
export const CAP_REACH = {topLeft: 15, topRight: 20, bottomLeft: 12, bottomRight: 15};
export const SIDE_START = 10;
// Authored order of the edge units: never the same unit twice in a row.
export const TOP_ORDER = ["top-a", "top-d", "top-c", "top-b", "top-e", "top-a", "top-c", "top-d", "top-b", "top-e"];
export const BOTTOM_ORDER = ["bottom-a", "bottom-c", "bottom-b", "bottom-a", "bottom-b", "bottom-c"];
const GOLDEN = 0.618034;
const frac = value => value - Math.floor(value);

/**
 * Fill `length` px with units taken from `order` (cyclic, starting at `start`), choosing the count
 * whose natural width needs the smallest per-unit gap tweak. Returns {ids, gap} or null if nothing fits.
 */
export function fitUnits(length, order, advance, {start = 0, maxGap = 6} = {}) {
  if (!(length > 0) || !order.length) return null;
  let best = null;
  let total = 0;
  for (let count = 1; count <= order.length * 8; count += 1) {
    total += advance[order[(start + count - 1) % order.length]];
    const gap = (length - total) / count;
    if (!best || Math.abs(gap) < Math.abs(best.gap)) best = {count, gap};
    if (total > length + 60) break;
  }
  if (!best || Math.abs(best.gap) > maxGap) {
    // Too short for a whole unit without squashing: keep the one unit that fits best.
    if (!best) return null;
  }
  const ids = Array.from({length: best.count}, (_, index) => order[(start + index) % order.length]);
  return {ids, gap: Math.max(-maxGap, Math.min(maxGap, best.gap))};
}

/** Place units along an edge: returns [{id, at}] with `at` = offset of each unit origin from `from`. */
function placeAlong(fit, advance, from) {
  if (!fit) return [];
  const out = [];
  let at = from + fit.gap / 2;
  for (const id of fit.ids) {
    out.push({id, at});
    at += advance[id] + fit.gap;
  }
  return out;
}

/**
 * The composition for a row of `width` × `height`.
 * `advance` maps unit ids to their authored advance (from the sprite's data-advance).
 * Returns placements [{id, x, y, phase}] in row coordinates plus the canvas box.
 */
export function planComicFire({width, height, advance}) {
  width = Math.max(0, Number(width) || 0);
  height = Math.max(0, Number(height) || 0);
  const canvas = {x: -CANVAS_PAD.left, y: -CANVAS_PAD.top, width: width + CANVAS_PAD.left + CANVAS_PAD.right, height: height + CANVAS_PAD.top + CANVAS_PAD.bottom};
  const placements = [];
  let serial = 0;
  const put = (id, x, y) => placements.push({id, x, y, phase: Math.round(frac(0.13 + serial++ * GOLDEN) * 1000) / 1000});
  if (width < 60 || height < 12) return {canvas, placements};

  put("cap-left-top", 0, 0);
  put("cap-left-bottom", 0, height);
  put("cap-right-top", width, 0);
  put("cap-right-bottom", width, height);

  const topFrom = CAP_REACH.topLeft;
  const top = fitUnits(width - CAP_REACH.topRight - topFrom, TOP_ORDER, advance, {start: 0});
  for (const unit of placeAlong(top, advance, topFrom)) put(unit.id, unit.at, -BAND);

  const bottomFrom = CAP_REACH.bottomLeft;
  const bottom = fitUnits(width - CAP_REACH.bottomRight - bottomFrom, BOTTOM_ORDER, advance, {start: 1});
  for (const unit of placeAlong(bottom, advance, bottomFrom)) put(unit.id, unit.at, height + BAND);

  // Tall rows (a wrapped title, an opened task): whole side units between the corner pieces.
  const side = height - SIDE_START * 2;
  const step = advance["side-left"] || 12;
  const count = Math.max(0, Math.round(side / step));
  if (count > 0) {
    const gap = (side - count * step) / count;
    for (let index = 0; index < count; index += 1) {
      const y = SIDE_START + gap / 2 + index * (step + gap);
      put("side-left", 0, y);
      put("side-right", width, y);
    }
  }
  return {canvas, placements};
}

/**
 * Halftone (ben-day) dots on a fixed grid, sized by the authored halos: bigger near the fire,
 * smaller farther out, never inside `hole` (the row plus its band, covered anyway).
 * halos: [{x, y, r, tone}] in row coordinates. Returns {tone: [[x, y, r], ...]}.
 */
export function halftoneDots(halos, box, {pitch = 3.7, maxRadius = 1.6, minRadius = 0.4, hole = null} = {}) {
  const dots = {};
  const rowStep = pitch * 0.866;
  const startY = Math.floor(box.y / rowStep) * rowStep;
  for (let line = Math.floor(box.y / rowStep), y = startY; y <= box.y + box.height; line += 1, y += rowStep) {
    const shift = (line & 1) ? pitch / 2 : 0;
    for (let x = Math.floor(box.x / pitch) * pitch + shift; x <= box.x + box.width; x += pitch) {
      if (hole && x > hole.x && x < hole.x + hole.width && y > hole.y && y < hole.y + hole.height) continue;
      let best = 0;
      let tone = null;
      for (const halo of halos) {
        const t = Math.hypot(x - halo.x, y - halo.y) / halo.r;
        if (t >= 1) continue;
        const strength = (halo.strength ?? 1) * (1 - t) ** 1.1;
        if (strength > best) { best = strength; tone = halo.tone; }
      }
      const radius = maxRadius * best;
      if (radius < minRadius) continue;
      (dots[tone] ||= []).push([Math.round(x * 10) / 10, Math.round(y * 10) / 10, Math.round(radius * 100) / 100]);
    }
  }
  return dots;
}

/**
 * A quiet, continuous tone along the top and bottom edges under the authored patches, so the halftone
 * reads as one screen rather than separate stamps (row coordinates).
 */
export function edgeHalos(width, height, {step = 9, top = 10, bottom = 8} = {}) {
  const halos = [];
  for (let x = 4; x <= width - 4; x += step) {
    halos.push({x, y: -BAND - 4, r: top, tone: "o", strength: 0.62});
    halos.push({x: x + step / 2, y: height + BAND + 3.5, r: bottom, tone: "y", strength: 0.55});
  }
  return halos;
}

/** One path for many dots of a tone (keeps the DOM small). */
export function dotsPath(list) {
  return list.map(([x, y, r]) => `M${Math.round((x - r) * 100) / 100} ${y}a${r} ${r} 0 1 0 ${Math.round(r * 200) / 100} 0a${r} ${r} 0 1 0 ${-Math.round(r * 200) / 100} 0`).join("");
}

/** Rounded-rectangle path around the row, inflated by `by` px (used for the glowing band). */
export function bandPath(width, height, radius, by) {
  const x = -by;
  const y = -by;
  const w = width + by * 2;
  const h = height + by * 2;
  const r = Math.max(0, Math.min(radius + by, w / 2, h / 2));
  const n = value => Math.round(value * 100) / 100;
  return `M${n(x + r)} ${n(y)}H${n(x + w - r)}A${n(r)} ${n(r)} 0 0 1 ${n(x + w)} ${n(y + r)}V${n(y + h - r)}A${n(r)} ${n(r)} 0 0 1 ${n(x + w - r)} ${n(y + h)}`
    + `H${n(x + r)}A${n(r)} ${n(r)} 0 0 1 ${n(x)} ${n(y + h - r)}V${n(y + r)}A${n(r)} ${n(r)} 0 0 1 ${n(x + r)} ${n(y)}Z`;
}

/** Embers to show: at most `max`, spread over the width (deterministic). */
export function pickEmberSpots(spots, max = 5) {
  if (spots.length <= max) return spots;
  const sorted = [...spots].sort((a, b) => a.x - b.x);
  return Array.from({length: max}, (_, index) => sorted[Math.round(index * (sorted.length - 1) / (max - 1))]);
}

// ---- ignition --------------------------------------------------------------------------------------
// Positions along the row's perimeter, clockwise from the top-left corner, as a fraction 0..1.
// The band's rounded corners are treated as square corners: close enough for timing.

/** Perimeter fraction of a point given in row coordinates (projected onto the nearest edge). */
export function perimeterAt(x, y, width, height) {
  const P = 2 * (width + height) || 1;
  const cx = Math.min(Math.max(x, 0), width);
  const cy = Math.min(Math.max(y, 0), height);
  const distances = [cy, width - cx, height - cy, cx]; // top, right, bottom, left
  const edge = distances.indexOf(Math.min(...distances));
  const s = edge === 0 ? cx : edge === 1 ? width + cy : edge === 2 ? width + height + (width - cx) : 2 * width + height + (height - cy);
  return s / P;
}

/** Where each placement sits on the perimeter (its middle), for the ignition order. */
export function placementPerimeter(placement, width, height, advance = {}) {
  const half = (advance[placement.id] || 0) / 2;
  if (placement.id.startsWith("top-")) return perimeterAt(placement.x + half, 0, width, height);
  if (placement.id.startsWith("bottom-")) return perimeterAt(placement.x + half, height, width, height);
  if (placement.id === "side-left") return perimeterAt(0, placement.y + half, width, height);
  if (placement.id === "side-right") return perimeterAt(width, placement.y + half, width, height);
  if (placement.id === "cap-left-top") return perimeterAt(0, Math.min(8, height / 2), width, height);
  if (placement.id === "cap-left-bottom") return perimeterAt(0, Math.max(height - 8, height / 2), width, height);
  if (placement.id === "cap-right-top") return perimeterAt(width, Math.min(8, height / 2), width, height);
  if (placement.id === "cap-right-bottom") return perimeterAt(width, Math.max(height - 8, height / 2), width, height);
  return perimeterAt(placement.x, placement.y, width, height);
}

export const IGNITION_VARIANTS = {
  ring: {label: t("Кольцо"), spread: 560},   // from the point both ways, meeting on the far side
  whirl: {label: t("Вихрь"), spread: 760},   // one lap clockwise
  burst: {label: t("Вспышка"), spread: 480}, // radially from the point (like the current fire)
};

/**
 * Delay (ms) before a flame at perimeter fraction `s` (or row point `at` for bursts) catches fire,
 * when the fire starts at perimeter fraction `s0` / row point `origin`.
 */
export function ignitionDelay(variant, {s, s0, at, origin, width, height}) {
  const {spread} = IGNITION_VARIANTS[variant] || IGNITION_VARIANTS.ring;
  if (variant === "whirl") return Math.round((((s - s0) % 1) + 1) % 1 * spread);
  if (variant === "burst") {
    const far = Math.hypot(Math.max(origin.x, width - origin.x), Math.max(origin.y, height - origin.y)) || 1;
    return Math.round(Math.min(1, Math.hypot(at.x - origin.x, at.y - origin.y) / far) * spread);
  }
  const d = Math.abs(s - s0);
  return Math.round(Math.min(d, 1 - d) * 2 * spread);
}
