import { native } from "./desktop-platform.js";
import { timeClient, sharedApi, elapsedAt, formatTime, getTimePreset, timerFields } from "./time-client.js";
import { applyDocument, syncLocale, localeChange, t } from "./i18n.js";
import { createFireLayer } from "./fire-layer.js";
import { calendarImportanceLevel } from "./importance-state.js";
import { compactTasks, taskKey, resolveActiveTask } from "./widget-model.js";
import { installWindowGestures } from "./desktop-gestures.js";
const $ = id => document.getElementById(id);
applyDocument(); void syncLocale();
const bootstrap = await native.bootstrap();
let mode = bootstrap.widget_mode === "card" ? "card" : "bar";
function setMode(value) { mode = value === "card" ? "card" : "bar"; document.body.dataset.widgetMode = mode; render(); }
document.body.dataset.side = bootstrap.side || "right";
document.body.dataset.widgetMode = mode;
const fire = createFireLayer($("widget-fire"));
let burning = [], selectedFire = (await chrome.storage.local.get("desktop:selected-fire"))["desktop:selected-fire"] || "";
let selectedMode = null, refreshing = false, busy = false, catalogReady = false;
let liveNotes = [], liveEvents = [];
let pendingNotice = false;
function error(err) { pendingNotice = false; $("widget-error").textContent = err.message; $("widget-error").hidden = false; $("widget-retry").hidden = !timeClient.pending; }
async function action(action, fields = {}) {
  if (busy) return;
  busy = true; render();
  try { await timeClient.command(action, fields); $("widget-error").hidden = true; $("widget-retry").hidden = true; }
  catch (err) { error(err); }
  finally { busy = false; render(); }
}
function render() {
  const state = timeClient.state;
  const pending = Boolean(timeClient.pending);
  $("widget-retry").hidden = !pending;
  $("widget-tab").dataset.pending = String(pending);
  $("widget-tab").title = pending ? `${t("Карточка")} · ${t("Проверить повтор")}` : t("Карточка");
  if (pending && $("widget-error").hidden) {
    pendingNotice = true;
    $("widget-error").textContent = t("Есть неподтверждённая команда другого окна или прошлого запуска. Текущее состояние показано выше. Нажмите «Проверить повтор».");
    $("widget-error").hidden = false;
  } else if (!pending && pendingNotice) {
    $("widget-error").hidden = true;
    $("widget-error").textContent = "";
    pendingNotice = false;
  }
  const model = compactTasks(burning, selectedFire, state, resolveActiveTask(state, liveNotes, liveEvents));
  if (catalogReady) selectedFire = model.selected;
  const value = state ? formatTime(state.mode === "stopwatch" ? elapsedAt(state) : state.duration_ms-elapsedAt(state)) : "00:00";
  $("widget-time").textContent = $("widget-tab-time").textContent = value;
  document.body.dataset.longTime = String(value.length > 5);
  const label = state?.status === "running" ? t("Пауза") : state?.status === "paused" ? t("Продолжить") : t("Начать");
  $("widget-run").title = label; $("widget-run").setAttribute("aria-label", label);
  $("widget-run-icon").setAttribute("d", state?.status === "running" ? "M7 5h4v14H7zM14 5h4v14h-4z" : "m9 5 10 7-10 7z");
  $("widget-run").disabled = busy || !state || Boolean(timeClient.pending);
  $("widget-stop").disabled = busy || !["running","paused"].includes(state?.status) || Boolean(timeClient.pending);
  $("widget-mode").textContent = (selectedMode || state?.mode) === "stopwatch" ? t("Секундомер") : state?.mode === "break" && !selectedMode ? t("Перерыв") : "Pomodoro";
  const title = model.hot?.title || model.current?.title || t("Нет горящих заданий");
  $("widget-burning").textContent = title; $("widget-burning").title = title;
  if (model.hot) $("widget-burning").dataset.fireKey = taskKey(model.hot); else delete $("widget-burning").dataset.fireKey;
  $("widget-burning").classList.toggle("is-critical", Boolean(model.hot));
  $("widget-current").hidden = !model.hot || model.same || !model.current;
  $("widget-current").textContent = model.current ? `${t("Сейчас")}: ${model.current.title}` : "";
  $("widget-current").title = $("widget-current").textContent;
  const choice = $("widget-fire-choice");
  if (JSON.stringify([...choice.options].map(o => [o.value,o.text])) !== JSON.stringify(model.tasks.map(task => [taskKey(task),task.title]))) choice.replaceChildren(...model.tasks.map(task => new Option(task.title, taskKey(task))));
  choice.hidden = model.tasks.length < 2; choice.value = model.selected;
  fire.schedule();
}
$("widget-run").onclick = async () => {
  const state = timeClient.state;
  if (["running","paused"].includes(state?.status)) return action(state.status === "running" ? "pause" : "resume");
  try {
    const preset = await getTimePreset();
    const model = compactTasks(burning, selectedFire, state);
    await action("start", { ...timerFields(selectedMode || (state?.mode === "stopwatch" ? "stopwatch" : "pomodoro"), preset), task:model.hot || null });
  } catch (err) { error(err); }
};
$("widget-stop").onclick = () => action("stop");
$("widget-mode").onclick = () => { selectedMode = (selectedMode || timeClient.state?.mode) === "stopwatch" ? "pomodoro" : "stopwatch"; render(); };
$("widget-retry").onclick = async () => { try {await timeClient.retry(); $("widget-error").hidden = true; $("widget-retry").hidden = true; render();} catch(err) {error(err);} };
$("widget-open").onclick = () => native.window_action("full");
$("widget-switch").onclick = () => native.window_action("bar");
$("widget-tab").onkeydown = event => {if (["Enter"," "].includes(event.key)) {event.preventDefault(); void native.window_action("card");}};
installWindowGestures(native, {tab:true, onClick:() => {if(mode === "bar") void native.window_action("card");}});
window.addEventListener("brainalot-widget-mode", event => setMode(event.detail));
$("widget-fire-choice").onchange = () => { selectedFire = $("widget-fire-choice").value; void chrome.storage.local.set({"desktop:selected-fire":selectedFire}); render(); };
chrome.storage.onChanged.addListener(changes => {
  if (localeChange(changes)) void syncLocale().then(render);
  if (changes["panel:theme"]) document.documentElement.dataset.theme = changes["panel:theme"].newValue;
  if (changes["panel:design"]) document.documentElement.dataset.design = changes["panel:design"].newValue;
});
async function refresh() {
  if (refreshing || document.hidden) return;
  refreshing = true;
  try {
    await timeClient.refresh();
    const [notes, prefs] = await Promise.all([sharedApi("/api/notes"), chrome.storage.local.get(["panel:calendar-importance","panel:theme","panel:design"])]);
    liveNotes = notes.notes;
    document.documentElement.dataset.theme = prefs["panel:theme"] || "light"; document.documentElement.dataset.design = prefs["panel:design"] || "stream";
    burning = notes.notes.filter(note => note.importance === "critical" && !["done","cancelled","archived"].includes(note.status)).map(note => ({source:"library",id:note.id,title:note.title}));
    const now = new Date(), day = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,"0")}-${String(now.getDate()).padStart(2,"0")}`;
    const calendar = await sharedApi(`/api/calendar/events?start=${day}&end=${day}`).catch(()=>({events:[]}));
    liveEvents = calendar.events || [];
    for (const event of calendar.events || []) if (calendarImportanceLevel(prefs["panel:calendar-importance"], event) === "critical") burning.push({source:"calendar", id:`${event.calendar_id}:${event.occurrence_id || event.id}`, title:event.title});
    catalogReady = true;
    const before = selectedFire; render();
    if (selectedFire !== before) void chrome.storage.local.set({"desktop:selected-fire":selectedFire});
  } catch (err) { error(err); }
  finally { refreshing = false; }
}
timeClient.subscribe(render);
setMode((await native.bootstrap()).widget_mode);
document.addEventListener("visibilitychange", () => {if (!document.hidden) void refresh();});
void refresh(); setInterval(refresh,5000); setInterval(() => {if (!document.hidden) render();},1000);
