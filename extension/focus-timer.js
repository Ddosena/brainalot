import { t } from "./i18n.js";
export const FOCUS_KEY = "panel:focus-timer";
export const FOCUS_OVERLAY_KEY = "panel:focus-overlay-visible";
export const FOCUS_OVERLAY_COLOR_KEY = "panel:focus-overlay-color";
// Neon tube colors of the Chrome bar; ids match NEON_COLORS in megamozg/focus_overlay.py.
export const NEON_COLORS = Object.freeze([
  { id: "fire", name: t("Огонь"), tube: "#ff5a1f", core: "#fff3d6" },
  { id: "pink", name: t("Малина"), tube: "#ff2d95", core: "#ffe6f3" },
  { id: "violet", name: t("Фиалка"), tube: "#9b5cff", core: "#f0e8ff" },
  { id: "cyan", name: t("Лёд"), tube: "#19c6ff", core: "#e6fbff" },
  { id: "lime", name: t("Лайм"), tube: "#3ee66b", core: "#eaffef" },
  { id: "amber", name: t("Янтарь"), tube: "#ffb31a", core: "#fff6dc" },
]);

export function normalizeNeonColor(value) {
  return NEON_COLORS.some(color => color.id === value) ? value : NEON_COLORS[0].id;
}
const MINUTE = 60_000;
const DEFAULT_DURATION = 25 * MINUTE;

export function resetTimerForExtensionUpdate() {
  return { durationMs: DEFAULT_DURATION, elapsedMs: 0, startedAt: null, status: "paused" };
}

export function validateMinutes(value) {
  const minutes = Number(value);
  if (!Number.isInteger(minutes) || minutes < 1 || minutes > 180) {
    throw new Error(t("Укажите целое время от 1 до 180 минут."));
  }
  return minutes;
}

export function normalizeTimer(raw) {
  const durationMs = Number.isInteger(raw?.durationMs) && raw.durationMs >= MINUTE && raw.durationMs <= 180 * MINUTE
    ? raw.durationMs : DEFAULT_DURATION;
  const elapsedMs = Number.isFinite(raw?.elapsedMs) ? Math.max(0, Math.min(durationMs, raw.elapsedMs)) : 0;
  const status = ["idle", "running", "paused", "finished"].includes(raw?.status) ? raw.status : "idle";
  const startedAt = status === "running" && Number.isFinite(raw?.startedAt) ? raw.startedAt : null;
  return { durationMs, elapsedMs, startedAt, status: status === "running" && startedAt === null ? "paused" : status };
}

export function timerView(raw, now = Date.now()) {
  const state = normalizeTimer(raw);
  const runningMs = state.status === "running" ? Math.max(0, now - state.startedAt) : 0;
  const elapsedMs = Math.min(state.durationMs, state.elapsedMs + runningMs);
  const status = elapsedMs >= state.durationMs ? "finished" : state.status;
  return { ...state, status, elapsedMs, remainingMs: state.durationMs - elapsedMs,
    progress: Math.round(elapsedMs / state.durationMs * 1000) / 10 };
}

export function timerTransition(raw, action, now = Date.now(), minutes) {
  const view = timerView(raw, now);
  switch (action) {
    case "start": return { durationMs: validateMinutes(minutes) * MINUTE, elapsedMs: 0, startedAt: now, status: "running" };
    case "pause":
      if (view.status !== "running") throw new Error(t("Таймер сейчас не идёт."));
      return { durationMs: view.durationMs, elapsedMs: view.elapsedMs, startedAt: null, status: "paused" };
    case "resume":
      if (view.status !== "paused") throw new Error(t("Сначала приостановите таймер."));
      return { durationMs: view.durationMs, elapsedMs: view.elapsedMs, startedAt: now, status: "running" };
    case "reset": return { durationMs: view.durationMs, elapsedMs: 0, startedAt: null, status: "idle" };
    default: throw new Error(t("Неизвестное действие таймера."));
  }
}
