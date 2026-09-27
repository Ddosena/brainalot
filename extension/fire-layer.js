// «Огонь» layer (Claude, 2026-09-23; comic flames since 2026-09-24). Draws the authored comic
// flames of comic-fire.js (sprite: assets/comic-fire/comic-fire.svg) behind very important rows of
// one card, ignites them from the point the user clicked, keeps them glued to the row and refuses a
// third fire with a shake.
//
// The layer lives in the card, outside the scrolling lists, so the flames can spill over
// neighbouring rows like in the user's reference. The burning row itself sits above the layer
// (CSS: `.is-critical` gets z-index 2), so the ignition flash shows around it.
import {comicIgnitionMs, ensureComicFireStyle, igniteComicFire, loadComicFire, renderComicFire} from "./comic-fire.js";

const SVG = "http://www.w3.org/2000/svg";
const DENY_MS = 560;
const BUBBLE_MS = 2600;
const DYING_MS = 520;
/** The ignition the user chose on 2026-09-24: the fire spreads in a circle from the click. */
export const IGNITION_VARIANT = "burst";
/** How long the ignition runs, so the sound can last exactly as long. */
export const IGNITION_MS = comicIgnitionMs(IGNITION_VARIANT);

const reducedMotion = () => globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
const round = value => Math.round(value * 10) / 10;

// Steady burning is stepped at 12 frames a second, like hand-drawn animation «on twos»: the pose
// morphs are CSS `d` animations on ~250 paths per fire, main-thread style and paint work on every
// display frame (measured on the stand: ~18 % of a core at 60 Hz for one fire). The ignition keeps
// its full frame rate; only the pose, twinkle and ember loops are stepped.
const BOIL_STEP_MS = 1000 / 12;
const LOOPING = /^cf-(?:a\d+|twinkle|ember)$/;
const burningLayers = new Set();
let boilTimer = 0;

/** The looping animations of one drawing, paused once; later steps only seek them. */
function boilLoops(burst) {
  if (!burst.loops) {
    burst.loops = burst.svg.getAnimations({subtree: true}).filter(animation => LOOPING.test(animation.animationName || ""));
    for (const animation of burst.loops) animation.pause();
  }
  return burst.loops;
}

function stepBoil() {
  if (document.hidden) return;
  const time = document.timeline.currentTime;
  let burning = 0;
  for (const bursts of burningLayers) {
    for (const burst of bursts.values()) {
      if (burst.element.hidden || !burst.plan) continue;
      burning += 1;
      // One clock for every loop keeps the authored phase offsets (their negative delays).
      // Only writes here: reading animation state would force a style update per path.
      for (const animation of boilLoops(burst)) animation.currentTime = time;
    }
  }
  if (!burning) { clearInterval(boilTimer); boilTimer = 0; }
}

function startBoil() {
  if (!boilTimer && !reducedMotion()) boilTimer = setInterval(stepBoil, BOIL_STEP_MS);
}

// One sprite for the whole page; the layers draw as soon as it is parsed.
let library = null;
const libraryReady = loadComicFire().then(parsed => {
  library = parsed;
  ensureComicFireStyle(document, parsed);
  return parsed;
});

function restartClass(element, className, duration = 0) {
  element.classList.remove(className);
  void element.getBoundingClientRect();
  element.classList.add(className);
  if (duration) setTimeout(() => element.classList.remove(className), duration);
}

/** Visible vertical span of `element` after every clipping ancestor below `stop`. */
function visibleRect(element, stop) {
  const rect = element.getBoundingClientRect();
  let top = rect.top;
  let bottom = rect.bottom;
  for (let parent = element.parentElement; parent && parent !== stop; parent = parent.parentElement) {
    const style = getComputedStyle(parent);
    if (style.overflowY === "visible" && style.overflowX === "visible") continue;
    const box = parent.getBoundingClientRect();
    top = Math.max(top, box.top + parent.clientTop);
    bottom = Math.min(bottom, box.top + parent.clientTop + parent.clientHeight);
  }
  return {rect, top, bottom};
}

export function createFireLayer(host) {
  // Hosts may nest (the sticky schedule sits inside the day card): each layer
  // burns only the rows whose nearest host it is.
  host.dataset.fireHost = "";
  const layer = document.createElement("div");
  layer.className = "fire-layer";
  layer.setAttribute("aria-hidden", "true");
  const bubble = document.createElement("p");
  bubble.className = "fire-bubble";
  bubble.setAttribute("aria-hidden", "true");
  bubble.hidden = true;
  const live = document.createElement("p");
  live.className = "sr-only";
  live.setAttribute("role", "status");
  host.append(layer, bubble, live);

  const bursts = new Map();
  const ignitions = new Map();
  const dying = new Set();
  let bubbleTimer = null;
  let frameRequest = 0;
  burningLayers.add(bursts);

  function removeBurst(key, animate) {
    const burst = bursts.get(key);
    if (!burst) return;
    bursts.delete(key);
    clearTimeout(burst.timer);
    burst.row?.classList.remove("fire-row-igniting");
    if (!animate || reducedMotion() || document.hidden) { burst.element.remove(); return; }
    burst.element.classList.remove("is-igniting", "is-boiling", "is-appearing", "is-flaring");
    burst.element.classList.add("is-dying");
    setTimeout(() => burst.element.remove(), DYING_MS);
  }

  function ignite(burst, point, rect) {
    burst.element.classList.remove("is-appearing");
    // Row coordinates; without a point the fire starts at the importance dot on the right.
    const origin = point ? {x: point.x - rect.left, y: point.y - rect.top} : null;
    const duration = document.hidden ? 0 : igniteComicFire(burst.svg, {point: origin, variant: IGNITION_VARIANT});
    if (!duration) {
      burst.element.classList.add("is-boiling");
      return;
    }
    burst.element.classList.remove("is-boiling");
    restartClass(burst.element, "is-igniting");
    burst.heatStart = performance.now();
    burst.igniting = true;
    burst.row?.style.removeProperty("--heat-delay");
    burst.row?.classList.add("fire-row-igniting");
    clearTimeout(burst.timer);
    burst.timer = setTimeout(() => {
      burst.igniting = false;
      burst.row?.classList.remove("fire-row-igniting");
      burst.element.classList.remove("is-igniting");
      burst.element.classList.add("is-boiling");
    }, duration);
  }

  function sync() {
    cancelAnimationFrame(frameRequest);
    frameRequest = 0;
    const rows = new Map();
    for (const element of host.querySelectorAll("[data-fire-key]")) {
      if (element.closest("[data-fire-host]") === host && !rows.has(element.dataset.fireKey)) rows.set(element.dataset.fireKey, element);
    }
    for (const key of [...bursts.keys()]) if (!rows.has(key)) removeBurst(key, dying.has(key));
    dying.clear();
    // A requested fire whose row never showed up (a failed save) is forgotten.
    for (const [key, request] of ignitions) if (!rows.has(key) && Date.now() - request.at > 8000) ignitions.delete(key);
    // Until the sprite is parsed the ignitions wait; libraryReady schedules the first drawing.
    if (!rows.size || !library) return;
    const origin = layer.getBoundingClientRect();
    for (const [key, row] of rows) {
      const {rect, top, bottom} = visibleRect(row, host);
      let burst = bursts.get(key);
      if (!burst) {
        const element = document.createElement("div");
        element.className = "fire-burst";
        const svg = document.createElementNS(SVG, "svg");
        svg.setAttribute("class", "comic-fire");
        element.append(svg);
        layer.append(element);
        burst = {element, svg, plan: null, loops: null, size: "", timer: null, row, heatStart: 0, igniting: false};
        bursts.set(key, burst);
        startBoil();
        if (!ignitions.has(key)) {
          element.classList.add("is-boiling", "is-appearing");
          setTimeout(() => element.classList.remove("is-appearing"), 400);
        }
      }
      if (burst.row !== row) {
        // The list re-rendered mid-ignition: the new row continues the same heat glow.
        if (burst.igniting) {
          row.style.setProperty("--heat-delay", `${-Math.round(performance.now() - burst.heatStart)}ms`);
          row.classList.add("fire-row-igniting");
        }
        burst.row = row;
      }
      if (rect.width < 1 || rect.height < 1 || bottom <= top) { burst.element.hidden = true; continue; }
      burst.element.hidden = false;
      const width = Math.round(rect.width);
      const height = Math.round(rect.height);
      const radius = parseFloat(getComputedStyle(row).borderTopLeftRadius) || 7;
      const size = `${width}x${height}x${radius}`;
      if (burst.size !== size) {
        burst.size = size;
        burst.plan = renderComicFire(burst.svg, {width, height, radius}, library);
        burst.loops = null;  // new paths, new animations for the boil clock
        const {canvas} = burst.plan;
        burst.element.style.width = `${canvas.width}px`;
        burst.element.style.height = `${canvas.height}px`;
        // Flare and dying scale around the middle of the row.
        burst.svg.style.transformOrigin = `${round(width / 2 - canvas.x)}px ${round(height / 2 - canvas.y)}px`;
      }
      const {canvas} = burst.plan;
      burst.element.style.transform = `translate(${round(rect.left - origin.left + canvas.x)}px, ${round(rect.top - origin.top + canvas.y)}px)`;
      // A row scrolled half out of its list: cut the fire along the same edge.
      const cutTop = top > rect.top + 0.5 ? top - rect.top - canvas.y : 0;
      const cutBottom = bottom < rect.bottom - 0.5 ? rect.bottom - bottom + (canvas.y + canvas.height - rect.height) : 0;
      burst.element.style.clipPath = cutTop || cutBottom ? `inset(${round(cutTop)}px 0 ${round(cutBottom)}px 0)` : "";
      if (ignitions.has(key)) {
        ignite(burst, ignitions.get(key).point, rect);
        ignitions.delete(key);
      }
    }
  }

  function schedule() {
    if (!frameRequest) frameRequest = requestAnimationFrame(sync);
  }

  /** Flare every fire of this layer: they are the ones holding the places. */
  function flare() {
    for (const burst of bursts.values()) restartClass(burst.element, "is-flaring", 720);
  }

  host.addEventListener("scroll", schedule, {capture: true, passive: true});
  host.addEventListener("toggle", schedule, true);
  if (typeof ResizeObserver === "function") new ResizeObserver(schedule).observe(host);
  globalThis.addEventListener?.("resize", schedule);
  document.fonts?.ready.then(schedule);
  libraryReady.then(schedule, error => console.warn("Brainalot: comic fire sprite", error));

  return {
    sync,
    schedule,
    /** Play the ignition when `key` next appears; `point` is in client coordinates. */
    ignite(key, point = null) { ignitions.set(key, {point, at: Date.now()}); },
    /** Let the fire of `key` die down instead of vanishing when its row stops burning. */
    extinguish(key) { if (bursts.has(key)) dying.add(key); },
    flare,
    /** Refuse one more fire: shake the row, flare this layer's fires, say why. */
    deny(row, title, detail = "") {
      if (row) restartClass(row, "fire-denied", DENY_MS);
      flare();
      live.textContent = "";
      live.textContent = `${title}. ${detail}`.trim();
      clearTimeout(bubbleTimer);
      const strong = document.createElement("strong");
      strong.textContent = title;
      bubble.replaceChildren(strong);
      if (detail) {
        const small = document.createElement("span");
        small.textContent = detail;
        bubble.append(small);
      }
      bubble.hidden = false;
      bubble.classList.remove("is-leaving", "is-below");
      if (row) {
        const hostRect = host.getBoundingClientRect();
        const rect = row.getBoundingClientRect();
        const width = bubble.offsetWidth;
        const height = bubble.offsetHeight;
        const anchor = rect.right - hostRect.left - host.clientLeft - 14;
        const left = Math.max(4, Math.min(anchor + 14 - width, host.clientWidth - width - 4));
        let top = rect.top - hostRect.top - host.clientTop - height - 10;
        if (top < 2) { top = rect.bottom - hostRect.top - host.clientTop + 10; bubble.classList.add("is-below"); }
        bubble.style.left = `${round(left)}px`;
        bubble.style.top = `${round(top)}px`;
        bubble.style.setProperty("--arrow-x", `${round(Math.max(10, Math.min(width - 16, anchor - left - 5)))}px`);
      }
      restartClass(bubble, "is-showing");
      bubbleTimer = setTimeout(() => {
        bubble.classList.add("is-leaving");
        bubbleTimer = setTimeout(() => { bubble.hidden = true; bubble.classList.remove("is-leaving", "is-showing"); }, 220);
      }, BUBBLE_MS);
    },
  };
}
