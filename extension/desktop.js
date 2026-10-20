import { native } from "./desktop-platform.js";
import { installWindowGestures } from "./desktop-gestures.js";
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
document.getElementById("desktop-spotify-toggle").onclick = () => void spotifyPlayer.setVisible(!spotifyPlayer.visible).catch(showError);
document.documentElement.dataset.theme = saved["panel:theme"] || "light";
document.documentElement.dataset.design = saved["panel:design"] || "stream";
document.querySelector('[data-view="work"]').hidden = !WORK_ENABLED;
function show(view) {
  if (view === "home") view = "day";
  if (view === "work" && !WORK_ENABLED) view = "day";
  const paths = { day: "panel.html", library: "panel.html?library-window=1", work: "work.html", capture: "capture.html" };
  const frame = document.getElementById("desktop-content");
  const next = new URL(paths[view] || paths.day, location.href).href;
  if (frame.src !== next) frame.src = next;
  document.querySelectorAll("[data-view]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.view === view)));
}
show(new URLSearchParams(location.search).get("view") || "day");
document.querySelectorAll("[data-view]").forEach(button => button.onclick = () => show(button.dataset.view));
window.addEventListener("brainalot-view", event => show(event.detail));
const bootstrap = await native.bootstrap();
installWindowGestures(native, {doubleClick: bootstrap.double_click,
  onDoubleClick: () => void native.window_action("card")});
function renderProgress() {
  const progress = timerProgress(timeClient.state, elapsedAt(timeClient.state));
  const bar = document.getElementById("desktop-progress");
  bar.hidden = progress === null;
  bar.querySelector("span").style.width = `${progress ?? 0}%`;
}
timeClient.subscribe(renderProgress);
renderProgress();
setInterval(() => { if (!document.hidden) renderProgress(); }, 1000);
setInterval(() => { if (!document.hidden) void timeClient.refresh().catch(() => {}); }, 5000);
void timeClient.refresh().catch(() => {});
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
