// Comic fire prototype (Claude, 2026-09-24): draws the authored flame modules from
// assets/comic-fire/comic-fire.svg around burning rows. Layout rules live in comic-fire-layout.js.
//
// The panel draws it through fire-layer.js (since 2026-09-24); the dev-only Fire Lab
// (dev/fire-lab.html) reuses it for side-by-side review.
// Decoration only: every layer is aria-hidden, pointer-events: none and outside the layout flow.
import {BAND, IGNITION_VARIANTS, bandPath, dotsPath, edgeHalos, halftoneDots, ignitionDelay, perimeterAt, pickEmberSpots, placementPerimeter, planComicFire} from "./comic-fire-layout.js";

const SVG = "http://www.w3.org/2000/svg";
export const COMIC_FIRE_URL = new URL("./assets/comic-fire/comic-fire.svg", import.meta.url).href;
const LAYERS = ["ink", "red", "orange", "yellow"];
const TONES = {o: "var(--cf-dot-orange, #f7931e)", y: "var(--cf-dot-yellow, #fbb933)"};
let libraryPromise = null;
const plans = new WeakMap();   // svg -> its current composition (for ignition)
const timers = new WeakMap();  // svg -> pending end-of-ignition cleanup

function svgNode(tag, attributes = {}, parent = null) {
  // Create in the parent's document: the Fire Lab draws into same-origin iframes of the panel.
  const element = (parent?.ownerDocument || document).createElementNS(SVG, tag);
  for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, String(value));
  parent?.append(element);
  return element;
}

/** Parse the sprite once: modules with their layered paths, halos and spots, plus the pose CSS. */
export function parseComicFire(text) {
  const doc = new DOMParser().parseFromString(text, "image/svg+xml");
  const css = [...doc.querySelectorAll("style")].map(style => style.textContent).join("\n");
  const modules = new Map();
  const advance = {};
  for (const group of doc.querySelectorAll("g.cf-module")) {
    const id = group.id.replace(/^cf-/, "");
    const module = {id, kind: group.dataset.kind, anchor: group.dataset.anchor || null, paths: [], halos: [], stars: [], embers: []};
    if (group.dataset.advance) advance[id] = Number(group.dataset.advance);
    for (const child of group.children) {
      const classes = child.classList;
      if (child.localName === "path") {
        const layer = classes.contains("cf-stroke") ? "stroke" : LAYERS.find(name => classes.contains(`cf-${name}`)) || (classes.contains("cf-star") ? "star" : "red");
        module.paths.push({layer, className: child.getAttribute("class"), style: child.getAttribute("style") || "", d: child.getAttribute("d")});
      } else if (child.localName === "circle") {
        const spot = {x: Number(child.getAttribute("cx")), y: Number(child.getAttribute("cy")), r: Number(child.getAttribute("r"))};
        if (classes.contains("cf-halo")) module.halos.push({...spot, tone: child.dataset.tone || "o"});
        else if (classes.contains("cf-star-spot")) module.stars.push(spot);
        else if (classes.contains("cf-ember-spot")) module.embers.push(spot);
      }
    }
    modules.set(id, module);
  }
  return {modules, advance, css};
}

/** Fetch and parse the sprite (packaged file; allowed by the extension CSP). */
export function loadComicFire(url = COMIC_FIRE_URL) {
  libraryPromise ||= fetch(url).then(response => {
    if (!response.ok) throw new Error(`comic fire sprite: ${response.status}`);
    return response.text();
  }).then(parseComicFire);
  return libraryPromise;
}

/** Inject the pose keyframes into a document once. */
export function ensureComicFireStyle(doc, library) {
  if (doc.getElementById("comic-fire-style")) return;
  const style = doc.createElement("style");
  style.id = "comic-fire-style";
  style.textContent = library.css;
  doc.head.append(style);
}

/** Where a module grows from when it catches fire: the middle of an edge unit, or the row corner of a cap. */
function pivotOf(module, library) {
  const advance = library.advance[module.id] || 0;
  if (module.kind === "top" || module.kind === "bottom") return [advance / 2, 0];
  if (module.anchor === "left" || module.anchor === "right") return [0, advance / 2];
  return [0, 0];
}

function clonePaths(module, layer, parent, placement, pivot, ignitable) {
  const own = module.paths.filter(path => path.layer === layer);
  if (!own.length) return;
  const group = svgNode("g", {transform: `translate(${placement.x} ${placement.y})`}, parent);
  group.style.setProperty("--cf-phase", placement.phase);
  // The inner group carries the ignition pop, so it never fights the placement transform.
  const inner = svgNode("g", {class: "cf-ign"}, group);
  inner.style.transformOrigin = `${pivot[0]}px ${pivot[1]}px`;
  ignitable.push(inner);
  for (const path of own) {
    const node = svgNode("path", {class: path.className, d: path.d}, inner);
    if (path.style) node.setAttribute("style", path.style);
  }
}

/**
 * Fill `svg` with the fire for a row of `width` × `height` (row coordinates; the caller positions the
 * svg so that its viewBox origin sits on the row's top-left corner).
 */
export function renderComicFire(svg, {width, height, radius = 7}, library) {
  const plan = planComicFire({width, height, advance: library.advance});
  const {canvas} = plan;
  svg.replaceChildren();
  svg.setAttribute("viewBox", `${canvas.x} ${canvas.y} ${canvas.width} ${canvas.height}`);
  svg.setAttribute("width", canvas.width);
  svg.setAttribute("height", canvas.height);
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  if (!plan.placements.length) return plan;

  const groups = {};
  for (const name of ["dots", "ink", "stroke", "red", "orange", "yellow", "light", "accents"]) groups[name] = svgNode("g", {class: `cf-l-${name}`}, svg);
  // The glowing body hugging the row: plain offsets of the row box, so they never stretch art.
  const bands = [
    svgNode("path", {class: "cf-ink cf-band", d: bandPath(width, height, radius, BAND + 1.75)}, groups.ink),
    svgNode("path", {class: "cf-red cf-band", d: bandPath(width, height, radius, BAND)}, groups.red),
    svgNode("path", {class: "cf-orange cf-band", d: bandPath(width, height, radius, BAND - 1.1)}, groups.orange),
    svgNode("path", {class: "cf-yellow cf-band", d: bandPath(width, height, radius, BAND - 2.1)}, groups.yellow),
    svgNode("path", {class: "cf-light cf-band", d: bandPath(width, height, radius, BAND - 3.1)}, groups.light),
  ];
  const ignition = [];

  const halos = [];
  const stars = [];
  const embers = [];
  for (const placement of plan.placements) {
    const module = library.modules.get(placement.id);
    if (!module) continue;
    const pivot = pivotOf(module, library);
    const parts = [];
    for (const layer of [...LAYERS, "stroke"]) clonePaths(module, layer, groups[layer], placement, pivot, parts);
    const at = {x: placement.x + pivot[0], y: placement.y + pivot[1]};
    for (const element of parts) ignition.push({element, s: placementPerimeter(placement, width, height, library.advance), at});
    for (const halo of module.halos) halos.push({...halo, x: halo.x + placement.x, y: halo.y + placement.y});
    for (const star of module.stars) stars.push({...star, x: star.x + placement.x, y: star.y + placement.y, phase: placement.phase});
    for (const ember of module.embers) embers.push({...ember, x: ember.x + placement.x, y: ember.y + placement.y});
  }
  const hole = {x: -BAND, y: -BAND, width: width + BAND * 2, height: height + BAND * 2};
  const dots = halftoneDots([...edgeHalos(width, height), ...halos], canvas, {hole});
  for (const [tone, list] of Object.entries(dots)) svgNode("path", {class: "cf-dots", d: dotsPath(list), fill: TONES[tone] || TONES.o}, groups.dots);

  const star = library.modules.get("star");
  for (const [index, spot] of stars.entries()) {
    if (!star) break;
    const holder = svgNode("g", {transform: `translate(${spot.x} ${spot.y})`}, groups.accents);
    const twinkle = svgNode("g", {class: "cf-twinkle"}, holder);
    twinkle.style.setProperty("--cf-phase", (spot.phase + index * 0.37) % 1);
    const scaled = svgNode("g", {transform: `scale(${spot.r / 10})`}, twinkle);
    for (const path of star.paths) svgNode("path", {class: path.className, d: path.d}, scaled);
  }
  const emberShapes = ["ember-a", "ember-b"].map(id => library.modules.get(id)).filter(Boolean);
  for (const [index, spot] of pickEmberSpots(embers).entries()) {
    if (!emberShapes.length) break;
    const holder = svgNode("g", {transform: `translate(${spot.x} ${spot.y})`}, groups.accents);
    const flight = svgNode("g", {class: "cf-ember"}, holder);
    const drift = [3, -4, 2, -2, 4][index % 5];
    const rise = spot.y > 0 ? 6 + (index * 5) % 6 : -(6 + (index * 5) % 6);  // embers drift away from the row
    flight.style.cssText = `--cf-dx:${drift}px;--cf-dy:${rise}px;--cf-rot:${drift * 4}deg;`
      + `--cf-ember-delay:${-((index * 0.61) % 1).toFixed(2)};--cf-ember-dur:${(1.7 + (index % 3) * 0.35).toFixed(2)}s`;
    for (const path of emberShapes[index % emberShapes.length].paths) svgNode("path", {class: path.className, d: path.d}, flight);
  }
  Object.assign(plan, {width, height, radius, groups, bands, ignition});
  plans.set(svg, plan);
  return plan;
}

/** The composition currently drawn in `svg` (row size, canvas, placements), or undefined. */
export function comicFirePlanOf(svg) {
  return plans.get(svg);
}

let sweepIds = 0;

/** Length of a whole ignition in ms: the spread plus the last flames popping up and the flash fading. */
export function comicIgnitionMs(variant = "burst") {
  return (IGNITION_VARIANTS[variant] || IGNITION_VARIANTS.ring).spread + 420;
}
const reducedMotion = view => view?.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;

/**
 * Light the fire from `point` (row coordinates; default: the right end, where the importance dot is).
 * variant: "burst" (radially from the point — chosen by the user on 2026-09-24), "ring" (both ways
 * round the row) or "whirl" (one lap clockwise). Returns the total duration in ms (0 when motion is reduced).
 */
export function igniteComicFire(svg, {point = null, variant = "burst"} = {}) {
  const plan = plans.get(svg);
  if (!plan?.ignition?.length) return 0;
  const doc = svg.ownerDocument;
  svg.querySelectorAll(".cf-ignite-fx").forEach(node => node.remove());
  svg.classList.remove("cf-igniting");
  if (reducedMotion(doc.defaultView) || svg.closest(".cf-still")) return 0;
  const {width, height, radius, canvas} = plan;
  const origin = point ? {x: Math.min(Math.max(point.x, 0), width), y: Math.min(Math.max(point.y, 0), height)} : {x: width - 14, y: height / 2};
  const s0 = perimeterAt(origin.x, origin.y, width, height);
  const spread = (IGNITION_VARIANTS[variant] || IGNITION_VARIANTS.ring).spread;
  for (const item of plan.ignition) {
    item.element.style.setProperty("--cf-d", `${ignitionDelay(variant, {s: item.s, s0, at: item.at, origin, width, height})}ms`);
  }
  // The band, the halftone and the accents are revealed by a sweep mask that follows the same clock.
  const id = `cf-sweep-${++sweepIds}`;
  const defs = svgNode("defs", {class: "cf-ignite-fx"}, svg);
  const mask = svgNode("mask", {id, maskUnits: "userSpaceOnUse", x: canvas.x, y: canvas.y, width: canvas.width, height: canvas.height}, defs);
  // The mask runs on a loop pushed outwards, so a thin row never reveals the opposite edge early.
  const track = bandPath(width, height, radius, 20);
  const runway = bandPath(width, height, radius, BAND - 2);
  const P = 1000;
  const start = s0 * P;
  if (variant === "burst") {
    const far = Math.hypot(Math.max(origin.x, width - origin.x) + 40, Math.max(origin.y, height - origin.y) + 36);
    const disc = svgNode("circle", {class: "cf-sweep-disc", cx: origin.x, cy: origin.y, r: 0, fill: "#fff"}, mask);
    disc.style.setProperty("--cf-r", `${far}px`);
    disc.style.setProperty("--cf-spread", `${spread}ms`);
  } else {
    const sweep = svgNode("path", {class: "cf-sweep", d: track, pathLength: P, fill: "none", stroke: "#fff", "stroke-width": 56}, mask);
    sweep.style.setProperty("--cf-spread", `${spread}ms`);
    sweep.style.setProperty("--cf-off0", `${-start}`);
    sweep.style.setProperty("--cf-off1", `${variant === "whirl" ? -start : -(start - P / 2)}`);
  }
  const masked = [...plan.bands, plan.groups.dots];
  for (const node of masked) node.setAttribute("mask", `url(#${id})`);
  // Runners: bright sparks racing ahead along the band (two for a ring, one for a whirl).
  const fx = svgNode("g", {class: "cf-ignite-fx"}, svg);
  const runners = variant === "ring" ? [1, -1] : variant === "whirl" ? [1] : [];
  for (const direction of runners) {
    const runner = svgNode("path", {class: "cf-runner", d: runway, pathLength: P, fill: "none"}, fx);
    const travel = variant === "whirl" ? P : P / 2;
    runner.style.setProperty("--cf-spread", `${spread}ms`);
    runner.style.setProperty("--cf-run0", `${-(start - 14)}`);
    runner.style.setProperty("--cf-run1", `${-(start - 14 + direction * travel)}`);
  }
  const flash = svgNode("circle", {class: "cf-flash", cx: origin.x, cy: origin.y, r: 14}, fx);
  flash.style.transformOrigin = `${origin.x}px ${origin.y}px`;
  plan.groups.accents.style.setProperty("--cf-accents-d", `${Math.round(spread * 0.7)}ms`);
  void svg.getBoundingClientRect();
  svg.classList.add("cf-igniting");
  const total = comicIgnitionMs(variant);
  clearTimeout(timers.get(svg));
  timers.set(svg, setTimeout(() => {
    svg.classList.remove("cf-igniting");
    for (const node of masked) node.removeAttribute("mask");
    svg.querySelectorAll(".cf-ignite-fx").forEach(node => node.remove());
  }, total));
  return total;
}

/** Visible vertical span of `element` after every clipping ancestor below `stop` (as in fire-layer.js). */
function visibleSpan(element, stop) {
  const view = element.ownerDocument.defaultView;
  const rect = element.getBoundingClientRect();
  let top = rect.top;
  let bottom = rect.bottom;
  for (let parent = element.parentElement; parent && parent !== stop; parent = parent.parentElement) {
    const style = view.getComputedStyle(parent);
    if (style.overflowY === "visible" && style.overflowX === "visible") continue;
    const box = parent.getBoundingClientRect();
    top = Math.max(top, box.top + parent.clientTop);
    bottom = Math.min(bottom, box.top + parent.clientTop + parent.clientHeight);
  }
  return {rect, top, bottom};
}

/**
 * Keep a comic fire behind every `[data-fire-key]` row inside `host`. The layer sits inside the host
 * (like the current .fire-layer), so the rows keep their own stacking above it.
 */
export function createComicFireLayer(host, library, {rowSelector = "[data-fire-key]"} = {}) {
  const doc = host.ownerDocument;
  ensureComicFireStyle(doc, library);
  const layer = doc.createElement("div");
  layer.className = "comic-fire-layer";
  layer.setAttribute("aria-hidden", "true");
  layer.style.cssText = "position:absolute;inset:0;pointer-events:none;z-index:1";
  host.append(layer);
  const fires = new Map();
  let frame = 0;

  function sync() {
    frame = 0;
    const rows = [...host.querySelectorAll(rowSelector)].filter(row => !layer.contains(row) && row.closest("[data-comic-fire-host]") === host);
    const seen = new Set();
    const origin = layer.getBoundingClientRect();
    for (const row of rows) {
      const key = row.dataset.fireKey || row;
      seen.add(key);
      let fire = fires.get(key);
      if (!fire) {
        const svg = doc.createElementNS(SVG, "svg");
        svg.setAttribute("class", "comic-fire");
        // Not data-fire-key: that attribute marks burning ROWS (for this layer and the current one).
        if (typeof key === "string") svg.dataset.comicFireFor = key;
        svg.style.cssText = "position:absolute;left:0;top:0;overflow:visible;pointer-events:none";
        layer.append(svg);
        fire = {svg, size: ""};
        fires.set(key, fire);
      }
      const {rect, top, bottom} = visibleSpan(row, host);
      if (rect.width < 1 || rect.height < 1 || bottom <= top) { fire.svg.style.display = "none"; continue; }
      fire.svg.style.display = "";
      const radius = parseFloat(doc.defaultView.getComputedStyle(row).borderTopLeftRadius) || 7;
      const size = `${Math.round(rect.width)}x${Math.round(rect.height)}x${radius}`;
      if (size !== fire.size) {
        fire.size = size;
        fire.plan = renderComicFire(fire.svg, {width: Math.round(rect.width), height: Math.round(rect.height), radius}, library);
      }
      const {canvas} = fire.plan;
      fire.svg.style.transform = `translate(${Math.round((rect.left - origin.left + canvas.x) * 10) / 10}px, ${Math.round((rect.top - origin.top + canvas.y) * 10) / 10}px)`;
      const cutTop = top > rect.top + 0.5 ? top - rect.top - canvas.y : 0;
      const cutBottom = bottom < rect.bottom - 0.5 ? rect.bottom - bottom + (canvas.height + canvas.y - rect.height) : 0;
      fire.svg.style.clipPath = cutTop || cutBottom ? `inset(${cutTop}px 0 ${cutBottom}px 0)` : "";
    }
    for (const [key, fire] of fires) if (!seen.has(key)) { fire.svg.remove(); fires.delete(key); }
  }
  const view = doc.defaultView;
  const schedule = () => { if (!frame) frame = view.requestAnimationFrame(sync); };
  host.dataset.comicFireHost = "";
  host.addEventListener("scroll", schedule, {capture: true, passive: true});
  host.addEventListener("toggle", schedule, true);
  const resizes = new view.ResizeObserver(schedule);
  resizes.observe(host);
  // Our own svg updates happen inside `layer`; only changes to the rows themselves need a sync.
  const mutations = new view.MutationObserver(records => { if (records.some(record => !layer.contains(record.target))) schedule(); });
  mutations.observe(host, {subtree: true, childList: true, attributes: true, attributeFilter: ["class", "data-fire-key", "open"]});
  view.addEventListener("resize", schedule);
  schedule();
  return {
    sync,
    schedule,
    layer,
    destroy() {
      view.cancelAnimationFrame(frame);
      mutations.disconnect();
      resizes.disconnect();
      host.removeEventListener("scroll", schedule, {capture: true});
      host.removeEventListener("toggle", schedule, true);
      view.removeEventListener("resize", schedule);
      layer.remove();
      delete host.dataset.comicFireHost;
    },
  };
}
