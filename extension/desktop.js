import { native } from "./desktop-platform.js";
import { installWindowGestures } from "./desktop-gestures.js";
import { createDesktopNavigation, desktopFrameReady } from "./desktop-navigation.js";
import { timeClient, sharedApi, elapsedAt } from "./time-client.js";
import { resolveActiveTask, timerProgress } from "./widget-model.js";
import { calendarImportanceLevel } from "./importance-state.js";
import { WORK_ENABLED } from "./work-availability.js";
import { FOCUS_OVERLAY_COLOR_KEY, NEON_COLORS, normalizeNeonColor } from "./focus-timer.js";
import { mountSpotifyPlayer } from "./spotify-player.js";
await import("./background.js");
const { applyDocument, syncLocale, localeChange, t } = await import("./i18n.js");
if (!await syncLocale()) applyDocument();
const saved = await chrome.storage.local.get(["panel:theme", "panel:design", FOCUS_OVERLAY_COLOR_KEY]);
function applyProgressColor(value) {
  const color = NEON_COLORS.find(color => color.id === normalizeNeonColor(value));
  document.documentElement.style.setProperty("--timer-progress-color", color.tube);
}
applyProgressColor(saved[FOCUS_OVERLAY_COLOR_KEY]);
const spotifyPlayer = mountSpotifyPlayer(document.getElementById("desktop-spotify"), {onVisibility: visible => {
  document.getElementById("desktop-spotify-toggle").textContent = visible ? t("Скрыть Spotify") : t("Показать Spotify");
}});
document.getElementById("desktop-spotify-toggle").onclick = () => {
  document.querySelector(".desktop-menu").open = false;
  void spotifyPlayer.setVisible(!spotifyPlayer.visible).catch(showError);
};
document.documentElement.dataset.theme = saved["panel:theme"] || "light";
document.documentElement.dataset.design = saved["panel:design"] || "stream";
document.querySelector('[data-view="work"]').hidden = !WORK_ENABLED;
const navigation = createDesktopNavigation({frame: document.getElementById("desktop-content"),
  baseUrl: location.href, workEnabled: WORK_ENABLED, onView: view => {
  if (view !== "work" && document.body.classList.contains("work-expanded")) {
    void native.window_action("work_expand", false).then(() => document.body.classList.remove("work-expanded")).catch(error => showError(error));
  }
  document.querySelectorAll("[data-view]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.view === view)));
}});
const show = view => navigation.show(view);
const initialView = new URLSearchParams(location.search).get("view") || "day";
document.querySelectorAll("[data-view]").forEach(button => button.onclick = () => { document.querySelector(".desktop-menu").open = false; show(button.dataset.view); });
window.addEventListener("brainalot-view", event => show(event.detail));
const bootstrap = await native.bootstrap();
document.body.classList.toggle("work-expanded", !!bootstrap.work_expanded);
window.addEventListener("message", async event => {
  const frame = document.getElementById("desktop-content"), request = event.data;
  if (event.origin !== location.origin || event.source !== frame.contentWindow) return;
  if (request?.type === "brainalot:desktop-view") {
    navigation.fromPanel(request); return;
  }
  if (request?.type !== "brainalot-work-fullscreen-request" || typeof request.expanded !== "boolean" ||
      !frame.src.includes("/work.html")) return;
  try {
    const result = await native.window_action("work_expand", request.expanded);
    document.body.classList.toggle("work-expanded", !!result.expanded);
    event.source.postMessage({type: "brainalot-work-fullscreen-result", request_id: request.request_id, ...result}, event.origin);
  } catch (error) {
    event.source.postMessage({type: "brainalot-work-fullscreen-result", request_id: request.request_id, ok: false, error: error.message}, event.origin);
  }
});
installWindowGestures(native, {doubleClick: bootstrap.double_click, freeDoubleClick: true,
  onDoubleClick: () => void native.window_action("card")});
document.getElementById("desktop-content").addEventListener("load", event => {
  const frame = event.currentTarget;
  if (!desktopFrameReady(frame)) return;
  frame.dataset.loading = "false";
  try {
    if (frame.contentDocument?.body) installWindowGestures(native, {surface: frame.contentDocument.body,
      doubleClick: bootstrap.double_click, freeDoubleClick: true, onDoubleClick: () => void native.window_action("card")});
  } catch { /* Only same-origin embedded content receives our own gestures. */ }
});
show(initialView);
document.querySelectorAll("[data-panel-action]").forEach(button => { button.onclick = async () => {
  document.querySelector(".desktop-menu").open = false;
  await navigation.panelAction(button.dataset.panelAction);
}; });
function renderProgress() {
  const progress = timerProgress(timeClient.state, elapsedAt(timeClient.state));
  const bar = document.getElementById("desktop-progress");
  bar.hidden = progress === null;
  bar.querySelector("span").style.width = `${progress ?? 0}%`;
}
timeClient.subscribe(renderProgress);
renderProgress();
setInterval(() => { if (!document.hidden) renderProgress(); }, 1000);
const showError = error => { const el=document.getElementById("desktop-error"); el.textContent=error.message; el.hidden=false; };
document.getElementById("desktop-compact").onclick = async () => {
  try {
    const [state, notes, prefs] = await Promise.all([timeClient.refresh(), sharedApi("/api/notes"), chrome.storage.local.get("panel:calendar-importance")]);
    const now=new Date(), day=`${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,"0")}-${String(now.getDate()).padStart(2,"0")}`;
    const calendar=await sharedApi(`/api/calendar/events?start=${day}&end=${day}`).catch(()=>({events:[]}));
    const hasTask = Boolean(resolveActiveTask(state, notes.notes, calendar.events || [])) ||
      notes.notes.some(note => note.importance === "critical" && !["done","cancelled","archived"].includes(note.status)) ||
      (calendar.events||[]).some(event => calendarImportanceLevel(prefs["panel:calendar-importance"],event) === "critical");
    await native.window_action(hasTask ? "card" : "bar");
  } catch(error) { showError(error); }
};
document.getElementById("desktop-quit").onclick = async () => {
  try {const result=await native.window_action("quit"); if (!result.ok) throw new Error(t("Не удалось поставить таймер на паузу. Проверьте сервис и повторите выход."));}
  catch(error) {showError(error);}
};
chrome.storage.onChanged.addListener(changes => {
  if (localeChange(changes)) void syncLocale();
  if (changes["panel:theme"]) document.documentElement.dataset.theme = changes["panel:theme"].newValue;
  if (changes["panel:design"]) document.documentElement.dataset.design = changes["panel:design"].newValue;
  if (changes[FOCUS_OVERLAY_COLOR_KEY]) applyProgressColor(changes[FOCUS_OVERLAY_COLOR_KEY].newValue);
});
// One controller owns the header's task, modes, completion/outbox and activity UI.
// The standalone Chrome panel keeps its own time UI below its own masthead.
await import("./widget.js");
