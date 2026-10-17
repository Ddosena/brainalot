import { native } from "./desktop-platform.js";
import { timeClient, sharedApi, elapsedAt, formatTime } from "./time-client.js";
import { applyDocument, syncLocale, localeChange, t } from "./i18n.js";
import { createFireLayer } from "./fire-layer.js";
import { calendarImportanceLevel, calendarImportanceKey } from "./importance-state.js";
const $ = id => document.getElementById(id);
applyDocument();
void syncLocale();
chrome.storage.onChanged.addListener(changes => { if (localeChange(changes)) void syncLocale(); });
const bootstrap = await native.bootstrap();
$("widget-top").setAttribute("aria-pressed", String(bootstrap.topmost));
let mode = new URLSearchParams(location.search).get("mode") === "card" ? "card" : "bar";
document.body.dataset.widgetMode = mode;
const fire = createFireLayer($("widget-fire"));
let burning = [], selectedFire = (await chrome.storage.local.get("desktop:selected-fire"))["desktop:selected-fire"] || "";
const refKey = task => task ? `${task.source}:${task.id}` : "";
function error(err) { $("widget-error").textContent = err.message; $("widget-error").hidden = false; }
async function action(action, fields = {}) { try { await timeClient.command(action, fields); $("widget-error").hidden = true; render(); } catch (err) { error(err); } }
function render() {
  const state = timeClient.state;
  if (!state) return;
  $("widget-time").textContent = formatTime(state.mode === "stopwatch" ? elapsedAt(state) : state.duration_ms - elapsedAt(state));
  $("widget-run").textContent = state.status === "running" ? t("Пауза") : state.status === "paused" ? t("Продолжить") : t("Начать");
  $("widget-mode").textContent = state.mode === "stopwatch" ? t("Секундомер") : state.mode === "break" ? t("Перерыв") : "Pomodoro";
  const task = burning.find(task => refKey(task) === selectedFire) || burning[0];
  $("widget-burning").textContent = task?.title || t("Нет горящих заданий");
  if (task) $("widget-burning").dataset.fireKey = refKey(task); else delete $("widget-burning").dataset.fireKey;
  const same = task && refKey(task) === refKey(state.task);
  $("widget-current").hidden = same;
  $("widget-current").textContent = `${t("Сейчас")}: ${state.task?.title || t("Без задачи")}`;
  $("widget-switch").textContent = mode === "bar" ? t("Карточка") : t("Плашка");
  fire.schedule();
}
$("widget-run").onclick = () => {
  const state = timeClient.state;
  void action(state?.status === "running" ? "pause" : state?.status === "paused" ? "resume" : "start", ["running","paused"].includes(state?.status) ? {} : { mode: state?.mode || "pomodoro", task: state?.task || null });
};
$("widget-stop").onclick = () => action("stop");
$("widget-mode").onclick = () => action("set_mode", { mode: timeClient.state?.mode === "pomodoro" ? "stopwatch" : "pomodoro" });
$("widget-open").onclick = () => native.window_action("full");
$("widget-hide").onclick = () => native.window_action("hide");
$("widget-switch").onclick = () => native.window_action(mode === "bar" ? "card" : "bar");
$("widget-top").onclick = async () => { const result = await native.window_action("topmost"); $("widget-top").setAttribute("aria-pressed", String(result.topmost)); };
$("widget-fire-choice").onchange = () => { selectedFire = $("widget-fire-choice").value; void chrome.storage.local.set({"desktop:selected-fire":selectedFire}); render(); };
async function refresh() {
  try {
    await timeClient.refresh();
    const [notes, prefs] = await Promise.all([sharedApi("/api/notes"), chrome.storage.local.get(["panel:calendar-importance","panel:theme","panel:design"])]);
    document.documentElement.dataset.theme = prefs["panel:theme"] || "light"; document.documentElement.dataset.design = prefs["panel:design"] || "stream";
    burning = notes.notes.filter(note => note.importance === "critical" && !["done","cancelled","archived"].includes(note.status)).map(note => ({source:"library",id:note.id,title:note.title}));
    const day = new Date().toLocaleDateString("en-CA");
    const calendar = await sharedApi(`/api/calendar/events?start=${day}&end=${day}`).catch(()=>({events:[]}));
    for (const event of calendar.events || []) if (calendarImportanceLevel(prefs["panel:calendar-importance"], event) === "critical") burning.push({source:"calendar", id:`${event.calendar_id}:${event.occurrence_id || event.id}`, title:event.title});
    const choice = $("widget-fire-choice"); choice.replaceChildren(...burning.map(task => new Option(task.title, refKey(task)))); choice.hidden = burning.length < 2; choice.value = selectedFire || refKey(burning[0]);
    render();
  } catch (err) { error(err); }
}
void refresh(); setInterval(refresh, 5000); setInterval(render, 1000);
