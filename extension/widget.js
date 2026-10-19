import { native } from "./desktop-platform.js";
import { timeClient, sharedApi, elapsedAt, formatTime, getTimePreset, timerFields } from "./time-client.js";
import { applyDocument, syncLocale, localeChange, t } from "./i18n.js";
import { calendarImportanceLevel } from "./importance-state.js";
import { compactTasks, taskKey, resolveActiveTask, untrackedStep, eventKey, currentEvent } from "./widget-model.js";
import { installWindowGestures } from "./desktop-gestures.js";
const $ = id => document.getElementById(id);
applyDocument(); void syncLocale();
const bootstrap = await native.bootstrap();
let mode = bootstrap.widget_mode === "card" ? "card" : "bar";
function setMode(value) { mode = value === "card" ? "card" : "bar"; document.body.dataset.widgetMode = mode; render(); }
document.body.dataset.side = bootstrap.side || "right";
document.body.dataset.widgetMode = mode;
let burning = [], selectedFire = (await chrome.storage.local.get("desktop:selected-fire"))["desktop:selected-fire"] || "";
let selectedMode = null, refreshing = false, busy = false, catalogReady = false;
let liveNotes = [], liveEvents = [];
// Owners of the visible notice: a failed background poll clears on the next healthy
// poll; an error of the user's own action stays until the next action or retry.
let pendingNotice = false, pollNotice = false;
// Untracked active stretch and the question about it; see widget-model.js.
let gap = null, asking = false, snoozeUntil = 0;
const askRefs = new Map();
function error(err, {poll = false} = {}) { pendingNotice = false; pollNotice = poll; $("widget-error").textContent = err.message; $("widget-error").hidden = false; $("widget-retry").hidden = !timeClient.pending; }
async function action(action, fields = {}) {
  if (busy) return;
  busy = true; render();
  try { await timeClient.command(action, fields); $("widget-error").hidden = true; $("widget-retry").hidden = true; pollNotice = false; }
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
  const modeKind = (selectedMode || state?.mode) === "stopwatch" ? "stopwatch" : state?.mode === "break" && !selectedMode ? "break" : "pomodoro";
  const modeName = modeKind === "stopwatch" ? t("Секундомер") : modeKind === "break" ? t("Перерыв") : "Pomodoro";
  const modeLabel = t("Режим: {mode}. Переключить", {mode: modeName});
  $("widget-mode").textContent = modeName; $("widget-mode").dataset.kind = modeKind;
  $("widget-mode").title = modeLabel; $("widget-mode").setAttribute("aria-label", modeLabel);
  const title = model.hot?.title || model.current?.title || t("Нет горящих заданий");
  $("widget-burning").textContent = title; $("widget-burning").title = title;
  $("widget-burning").classList.toggle("is-critical", Boolean(model.hot));
  $("widget-current").hidden = !model.hot || model.same || !model.current;
  $("widget-current").textContent = model.current ? `${t("Сейчас")}: ${model.current.title}` : "";
  $("widget-current").title = $("widget-current").textContent;
  const choice = $("widget-fire-choice");
  if (JSON.stringify([...choice.options].map(o => [o.value,o.text])) !== JSON.stringify(model.tasks.map(task => [taskKey(task),task.title]))) choice.replaceChildren(...model.tasks.map(task => new Option(task.title, taskKey(task))));
  choice.hidden = model.tasks.length < 2; choice.value = model.selected;
  $("widget-ask").hidden = !asking; document.body.dataset.ask = String(asking);
  if (asking && gap) {
    const minutes = Math.max(1, Math.floor((Date.now() - gap.since) / 60_000));
    $("widget-ask-title").textContent = t("Чем занимаешься?");
    const first = $("widget-ask-choice").options[0];
    if (first && first.value === "") first.textContent = t("Выбрать · {minutes} мин", {minutes});
    $("widget-ask-choice").title = t("{minutes} мин без учёта", {minutes});
    $("widget-tab").title = t("Чем занимаешься?");
  }
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
$("widget-retry").onclick = async () => { try {await timeClient.retry(); $("widget-error").hidden = true; $("widget-retry").hidden = true; pollNotice = false; render();} catch(err) {error(err);} };
$("widget-open").onclick = () => native.window_action("full");
$("widget-switch").onclick = () => native.window_action("bar");
$("widget-tab").onkeydown = event => {if (["Enter"," "].includes(event.key)) {event.preventDefault(); void native.window_action("card");}};
installWindowGestures(native, {tab:true, onClick:() => {if(mode === "bar") void native.window_action("card");}});
window.addEventListener("brainalot-widget-mode", event => setMode(event.detail));
async function fillAskChoices() {
  const choice = $("widget-ask-choice");
  askRefs.clear();
  const option = (text, value, ref) => { const item = new Option(text, value); if (ref) askRefs.set(value, ref); return item; };
  const group = (label, tasks) => { const node = document.createElement("optgroup"); node.label = label;
    node.append(...tasks.map(task => option(task.title, taskKey(task), task))); return node; };
  const hot = new Map(burning.map(task => [taskKey(task), task]));
  const events = liveEvents.filter(event => !event.all_day)
    .map(event => ({source: "calendar", id: eventKey(event), title: event.title || t("Событие")}));
  const listed = (await sharedApi("/api/time/tasks").catch(() => ({tasks: []}))).tasks
    .filter(task => task.source === "library" && !hot.has(taskKey(task)))
    .sort((a, b) => a.title.localeCompare(b.title));
  const placeholder = option(t("Выбрать…"), ""); placeholder.disabled = true; placeholder.selected = true;
  const items = [placeholder, option(t("Отдых"), "label:rest", {source: "label", id: "rest", title: t("Отдых")})];
  if (hot.size) items.push(group(t("Горящие"), [...hot.values()]));
  if (events.length) items.push(group(t("Сегодня в календаре"), events));
  if (listed.length) items.push(group(t("Задачи"), listed));
  items.push(option(t("Спросить позже"), "later"));
  choice.replaceChildren(...items);
}
$("widget-ask-choice").onchange = async () => {
  const value = $("widget-ask-choice").value;
  if (!value) return;
  if (value === "later") { snoozeUntil = Date.now() + 30 * 60_000; asking = false; render(); return; }
  const since = gap ? Math.floor(gap.since) : Date.now();
  await action("start", {...timerFields("stopwatch", await getTimePreset()), task: askRefs.get(value) || null, backfill_from: since});
  if ($("widget-error").hidden) { gap = null; asking = false; }
  render();
};
// Runs even while this window is hidden (timers are then only throttled).
async function watchUntracked() {
  if (typeof native.input_idle_ms !== "function") return;
  try {
    if (document.hidden) await timeClient.refresh();
    const now = Date.now();
    const tracked = timeClient.state?.status === "running" || Boolean(currentEvent(liveEvents, now));
    const step = untrackedStep(gap, {tracked, idleMs: Number(await native.input_idle_ms()) || 0, now});
    gap = step.gap;
    const ask = step.ask && now >= snoozeUntil && !timeClient.pending;
    if (ask && !asking) await fillAskChoices();
    if (ask !== asking) { asking = ask; render(); }
  } catch { /* the next sample retries; the question never blocks the timer */ }
}
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
    const before = selectedFire;
    if (pollNotice) { $("widget-error").hidden = true; $("widget-error").textContent = ""; pollNotice = false; }
    render();
    if (selectedFire !== before) void chrome.storage.local.set({"desktop:selected-fire":selectedFire});
  } catch (err) { error(err, {poll: true}); }
  finally { refreshing = false; }
}
timeClient.subscribe(render);
setMode((await native.bootstrap()).widget_mode);
document.addEventListener("visibilitychange", () => {if (!document.hidden) void refresh();});
void refresh(); setInterval(refresh,5000); setInterval(() => {if (!document.hidden) render();},1000);
setInterval(watchUntracked, 15_000);
