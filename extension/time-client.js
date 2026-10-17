import { createConnection } from "./connection.js";
import { t } from "./i18n.js";

export function elapsedAt(state, now = Date.now()) {
  if (!state) return 0;
  const elapsed = state.elapsed_ms + (state.status === "running" ? Math.max(0, now - state.server_now) : 0);
  return state.mode === "stopwatch" ? elapsed : Math.min(state.duration_ms, elapsed);
}
export function formatTime(ms) {
  const seconds = Math.floor(Math.max(0, ms) / 1000);
  return `${Math.floor(seconds / 3600) ? Math.floor(seconds / 3600) + ":" : ""}${String(Math.floor(seconds / 60) % 60).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}
export function focusSnapshot(state) {
  return { durationMs: state.duration_ms || 1500000, elapsedMs: state.mode === "pomodoro" ? state.elapsed_ms : 0,
    startedAt: state.mode === "pomodoro" && state.status === "running" ? state.server_now : null,
    status: state.mode === "pomodoro" ? state.status : "idle" };
}
export function createTimeClient(api) {
  let state = null, pending = null, busy = false;
  return {
    get state() { return state; }, get pending() { return pending; },
    async refresh() { state = await api("/api/time/state"); return state; },
    async command(action, fields = {}) {
      if (busy) throw new Error(t("Дождитесь ответа сервиса."));
      if (pending) throw new Error(t("Ответ на прошлую команду не получен. Нажмите «Проверить повтор»."));
      busy = true;
      try {
        if (!state) state = await api("/api/time/state");
        pending = { request_id: crypto.randomUUID(), expected_revision: state.revision, action, ...fields };
        return await this.retry();
      } finally { busy = false; }
    },
    async retry() {
      if (!pending) return this.refresh();
      try { state = await api("/api/time/command", "POST", pending); pending = null; return state; }
      catch (error) {
        if (error.status) { pending = null; if (error.status === 409) state = await api("/api/time/state"); }
        throw error;
      }
    },
  };
}
let connection;
export const sharedApi = (...args) => (connection ??= createConnection(globalThis.chrome)).api(...args);
export const timeClient = createTimeClient(sharedApi);
