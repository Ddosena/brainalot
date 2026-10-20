import { native } from "./desktop-platform.js";
import { timeClient, sharedApi, elapsedAt, formatTime, formatTotal, getTimePreset, timerFields } from "./time-client.js";
import { applyDocument, syncLocale, localeChange, t } from "./i18n.js";
import { calendarImportanceLevel } from "./importance-state.js";
import { compactTasks, taskKey, resolveActiveTask, untrackedStep, eventKey, currentEvent, timerProgress } from "./widget-model.js";
import { installWindowGestures } from "./desktop-gestures.js";
const $ = id => document.getElementById(id);
const OP_KEY = "desktop:widget-operation", SELECT_KEY = "desktop:selected-task", SELECT_REV_KEY = "desktop:selected-task-revision";
applyDocument(); void syncLocale();
const bootstrap = await native.bootstrap();
const saved = await chrome.storage.local.get(["desktop:selected-fire", SELECT_KEY, SELECT_REV_KEY, OP_KEY]);
let mode = bootstrap.widget_mode === "card" ? "card" : "bar";
let burning = [], selectedFire = saved["desktop:selected-fire"] || "", selectedTask = saved[SELECT_KEY] || null;
let selectedRevision = saved[SELECT_REV_KEY] ?? null;
let operation = saved[OP_KEY] || null;
let selectedMode = null, selectedPreview = null, refreshing = false, busy = false, catalogReady = false;
let liveNotes = [], liveEvents = [], taskCatalog = [], totals = [];
let pendingNotice = false, pollNotice = false;
let gap = null, asking = false, snoozeUntil = 0;
let popup = null, popupQueue = Promise.resolve();
const askRefs = new Map();
document.body.dataset.side = bootstrap.side || "right";
document.body.dataset.widgetMode = mode;
function currentTask() {
  const current = resolveActiveTask(timeClient.state, liveNotes, liveEvents);
  if (timeClient.state?.status === "running") return current;
  // Local choice while paused/idle belongs to the exact shared snapshot the
  // person saw. Any peer timer change supersedes that choice; a persisted task
  // from a previous session must never redirect a newly paused peer timer.
  if (selectedTask && Number.isInteger(selectedRevision) && selectedRevision === timeClient.state?.revision) {
    const listed = taskCatalog.find(task => taskKey(task) === taskKey(selectedTask));
    if (listed) return listed;
    if (!catalogReady || selectedTask.source === "label") return selectedTask;
  }
  return current;
}
async function rememberSelection(task) {
  selectedTask = task; selectedRevision = timeClient.state?.revision ?? null;
  await chrome.storage.local.set({[SELECT_KEY]: task, [SELECT_REV_KEY]: selectedRevision});
}
function pending() { return Boolean(timeClient.pending || operation); }
function clearError() { $("widget-error").hidden = true; $("widget-error").textContent = ""; pendingNotice = pollNotice = false; }
function error(err, {poll = false, show = true} = {}) {
  pendingNotice = false; pollNotice = poll;
  $("widget-error").textContent = err.message; $("widget-error").hidden = false;
  render();
  if (show && !poll && mode === "card") void setPopup(popup || "notice");
}
function syncPopup() {
  const requested = popup;
  // Serialize native geometry mutations: a late open must never undo a close.
  popupQueue = popupQueue.catch(() => {}).then(async () => {
    const result = await native.window_action("widget_popup", {open: Boolean(requested), height: requested === "menu" ? 340 : requested === "notice" ? 235 : 460});
    if (result?.ok === false) throw new Error(t("Не удалось открыть список. Повторите действие."));
    document.body.dataset.popupAbove = String(Boolean(result?.popup_above));
  });
  return popupQueue;
}
async function setPopup(next, {focus = false} = {}) {
  if (mode === "bar") next = null;
  popup = next;
  $("widget-popup").hidden = !next;
  $("widget-picker").hidden = next !== "tasks";
  $("widget-menu").hidden = next !== "menu";
  $("widget-task").setAttribute("aria-expanded", String(next === "tasks"));
  $("widget-more").setAttribute("aria-expanded", String(next === "menu" || next === "notice"));
  if (next === "tasks") renderTasks();
  try { await syncPopup(); }
  catch (err) {
    // A failed native resize must not recursively try opening its own error popup.
    popup = null; $("widget-popup").hidden = true;
    $("widget-task").setAttribute("aria-expanded", "false");
    $("widget-more").setAttribute("aria-expanded", "false");
    error(err, {show: false}); return false;
  }
  if (focus && popup === next) (next === "tasks" ? $("widget-search") : $("widget-done")).focus();
}
async function windowMode(next) { await setPopup(null); return native.window_action(next); }
function setMode(value) {
  mode = value === "card" ? "card" : "bar";
  document.body.dataset.widgetMode = mode;
  if (popup) void setPopup(null);
  render();
}
async function action(command, fields = {}) {
  if (busy || operation) return false;
  busy = true; render();
  try { await timeClient.command(command, fields); clearError(); return true; }
  catch (err) { error(err); return false; }
  finally { busy = false; render(); }
}
function render() {
  const state = timeClient.state, waiting = pending();
  $("widget-retry").hidden = !waiting;
  $("widget-tab").dataset.pending = String(waiting);
  $("widget-tab").title = waiting ? `${t("Плашка")} · ${t("Проверить повтор")}` : t("Плашка");
  if (waiting && $("widget-error").hidden) {
    pendingNotice = true;
    $("widget-error").textContent = t("Есть неподтверждённое действие. Откройте меню и нажмите «Проверить повтор».");
    $("widget-error").hidden = false;
  } else if (!waiting && pendingNotice) clearError();
  const model = compactTasks(burning, selectedFire, state);
  if (catalogReady) selectedFire = model.selected;
  const displayState = selectedPreview && !["running", "paused"].includes(state?.status) ? selectedPreview : state;
  const value = displayState ? formatTime(displayState.mode === "stopwatch" ? elapsedAt(displayState) : displayState.duration_ms - elapsedAt(displayState)) : "00:00";
  $("widget-time").textContent = $("widget-tab-time").textContent = value;
  document.body.dataset.longTime = String(value.length > 5);
  const progress = timerProgress(displayState, elapsedAt(displayState));
  $("widget-progress").style.strokeDasharray = $("widget-tab-progress").style.strokeDasharray = `${progress ?? 0} 100`;
  $("widget-time-label").textContent = displayState?.status === "paused" ? t("пауза") : displayState?.mode === "stopwatch" ? t("прошло") : t("осталось");
  const label = state?.status === "running" ? t("Пауза") : state?.status === "paused" ? t("Продолжить") : t("Начать");
  $("widget-run").title = label; $("widget-run").setAttribute("aria-label", label);
  $("widget-run-icon").setAttribute("d", state?.status === "running" ? "M7 5h4v14H7zM14 5h4v14h-4z" : "m9 5 10 7-10 7z");
  $("widget-run").disabled = busy || !state || waiting;
  $("widget-stop").disabled = busy || !["running", "paused"].includes(state?.status) || waiting;
  const task = currentTask();
  const note = task?.source === "library" && liveNotes.find(note => note.id === task.id);
  $("widget-done").disabled = busy || waiting || !note || !["task", "purchase"].includes(note.kind) || ["done", "cancelled", "archived"].includes(note.status);
  $("widget-done").title = note && ["task", "purchase"].includes(note.kind) ? t("Задача выполнена") : t("Выполнение доступно для задач и покупок");
  $("widget-mode").disabled = busy || waiting;
  $("widget-task").disabled = busy || waiting;
  $("widget-new").disabled = busy || waiting;
  $("widget-new-form").querySelector("button").disabled = busy || waiting;
  const modeKind = selectedMode || state?.mode || "pomodoro";
  const modeName = modeKind === "stopwatch" ? t("Секундомер") : modeKind === "break" ? t("Перерыв") : "Pomodoro";
  $("widget-mode").textContent = t("Сменить режим: {mode}", {mode: modeName});
  const title = task?.title || t("Выбрать задачу");
  $("widget-burning").textContent = title; $("widget-task").title = title;
  const hot = taskKey(model.hot) !== taskKey(task) ? model.hot : model.tasks.find(item => taskKey(item) !== taskKey(task));
  $("widget-current").hidden = !hot;
  $("widget-current").textContent = hot ? t("Горит: {title}", {title: hot.title}) : "";
  $("widget-current").title = $("widget-current").textContent;
  $("widget-more").dataset.notice = String(!$("widget-error").hidden);
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
function renderTasks() {
  const query = $("widget-search").value.trim().toLocaleLowerCase();
  const active = taskKey(currentTask());
  const tasks = taskCatalog.filter(task => task.title.toLocaleLowerCase().includes(query));
  const nodes = tasks.map(task => {
    const row = document.createElement("button"); row.type = "button"; row.dataset.task = taskKey(task);
    row.dataset.active = String(active === taskKey(task)); row.disabled = busy || pending();
    const title = document.createElement("strong"); title.textContent = task.title;
    const detail = document.createElement("small");
    const total = totals.find(item => taskKey(item.task) === taskKey(task))?.total_ms || 0;
    detail.textContent = `${active === taskKey(task) ? t("Выбрана") + " · " : ""}${t("Всего {time}", {time: formatTotal(total)})}`;
    row.append(title, detail); row.onclick = () => void chooseTask(task);
    return row;
  });
  if (!nodes.length) { const empty = document.createElement("p"); empty.textContent = t("Задач не найдено"); nodes.push(empty); }
  $("widget-task-list").replaceChildren(...nodes);
}
async function chooseTask(task) {
  if (busy || pending()) return;
  if (timeClient.state?.status === "running" && taskKey(timeClient.state.task) !== taskKey(task)) {
    if (!await action("switch_task", {task})) return;
  }
  await rememberSelection(task);
  render(); await setPopup(null); $("widget-task").focus();
}
$("widget-run").onclick = async () => {
  const state = timeClient.state;
  if (state?.status === "running") return action("pause");
  const task = currentTask();
  if (state?.status === "paused" && (!selectedMode || selectedMode === state.mode) && taskKey(task) === taskKey(state.task)) return action("resume");
  try {
    if (state?.status === "paused" && !selectedMode) {
      if (!await action("switch_task", {task})) return;
      return action("resume");
    }
    if (!task) return setPopup("tasks", {focus: true});
    if (await action("start", {...timerFields(selectedMode || (state?.mode === "stopwatch" ? "stopwatch" : "pomodoro"), await getTimePreset()), task})) {
      selectedMode = selectedPreview = null; render();
    }
  } catch (err) { error(err); }
};
$("widget-stop").onclick = async () => { if (await action("stop")) await setPopup(null); };
$("widget-mode").onclick = async () => {
  const next = (selectedMode || timeClient.state?.mode) === "stopwatch" ? "pomodoro" : "stopwatch";
  try {
    if (["running", "paused"].includes(timeClient.state?.status)) {
      if (!await action("set_mode", timerFields(next, await getTimePreset()))) return;
      selectedMode = selectedPreview = null;
    } else {
      const fields = timerFields(next, await getTimePreset());
      selectedMode = next; selectedPreview = {...fields, status: "idle", elapsed_ms: 0};
    }
    render(); await setPopup(null);
  } catch (err) { error(err); }
};
async function saveOperation(value) {
  // Save before delivery. A reload or uncertain response keeps the same capture id
  // or completion CAS and its stop-before-complete stage.
  await chrome.storage.local.set({[OP_KEY]: value}); operation = value;
}
async function stopRelatedTask(task) {
  await timeClient.refresh();
  if (["running", "paused"].includes(timeClient.state?.status) && taskKey(timeClient.state.task) === taskKey(task)) {
    await timeClient.command("stop");
    await rememberSelection(task);
  }
}
async function finishOperation() {
  const op = operation;
  if (!op) return;
  if (op.kind === "capture") {
    // Persist the acknowledged note before switching the running timer. A CAS
    // conflict in that second step must never turn Add into a second capture.
    const note = op.note || await sharedApi("/api/captures", "POST", op.payload);
    if (!op.note) await saveOperation({...op, stage: "select", note});
    taskCatalog = [...taskCatalog.filter(task => task.id !== note.id), {source: "library", id: note.id, title: note.title}];
    liveNotes = [...liveNotes.filter(item => item.id !== note.id), note];
    selectedTask = {source: "library", id: note.id, title: note.title};
    // Creating while a timer runs follows the same switch rule as an existing task.
    if (timeClient.state?.status === "running" && taskKey(timeClient.state.task) !== taskKey(selectedTask)) await timeClient.command("switch_task", {task: selectedTask});
    await rememberSelection(selectedTask);
  } else if (op.kind === "complete") {
    // Stop is not a permanent precondition: a peer may have resumed this task
    // since an uncertain PATCH. Recheck on every attempt, including stage patch.
    await stopRelatedTask(op.task);
    if (op.stage === "stop") {
      await saveOperation({...op, stage: "patch"});
    }
    const latest = (await sharedApi("/api/notes")).notes.find(note => note.id === op.task.id);
    if (!latest || !["task", "purchase"].includes(latest.kind)) throw Object.assign(new Error(t("Задача больше недоступна. Обновите список.")), {status: 409});
    // An uncertain PATCH can be acknowledged only from authoritative done status.
    if (latest.status !== "done") await sharedApi(`/api/notes/${encodeURIComponent(op.task.id)}`, "PATCH", {expected_version: op.version, patch: {status: "done"}});
    await saveOperation({...operation, stage: "confirm"});
    // Store and TimeStore are separate APIs. This closes a peer resume observed
    // during PATCH, without claiming the two writes form an atomic transaction.
    await stopRelatedTask(op.task);
    await rememberSelection(null);
    taskCatalog = taskCatalog.filter(task => taskKey(task) !== taskKey(op.task));
    liveNotes = liveNotes.map(note => note.id === op.task.id ? {...note, status: "done"} : note);
  }
  await saveOperation(null); clearError();
  if (op.kind === "complete") { $("widget-search").value = ""; await setPopup("tasks", {focus: true}); }
  else { $("widget-new-form").hidden = true; $("widget-new-title").value = ""; await setPopup(null); }
}
async function runOperation() {
  if (busy) return;
  busy = true; render();
  try { if (timeClient.pending) await timeClient.retry(); await finishOperation(); }
  catch (err) {
    // A definite rejection cannot be retried with the old CAS. Let a fresh user
    // action use the newly refreshed note, while transport failures keep the op.
    const acknowledged = operation?.kind === "capture" && operation.note || operation?.kind === "complete" && operation.stage === "confirm";
    if (!acknowledged && err.status >= 400 && err.status < 500 && ![408, 425, 429].includes(err.status)) {
      await saveOperation(null); await refresh();
    }
    error(err);
  } finally { busy = false; render(); if (popup === "tasks") renderTasks(); }
}
$("widget-done").onclick = async () => {
  if (busy || pending()) return;
  const task = currentTask(), note = task?.source === "library" && liveNotes.find(note => note.id === task.id);
  if (!note || !["task", "purchase"].includes(note.kind) || ["done", "cancelled", "archived"].includes(note.status)) return;
  try { await saveOperation({kind: "complete", task, version: note.version, stage: "stop"}); await runOperation(); }
  catch (err) { error(err); }
};
$("widget-new-form").onsubmit = async event => {
  event.preventDefault(); if (busy || pending()) return;
  const title = $("widget-new-title").value.trim(); if (!title) return;
  try {
    await saveOperation({kind: "capture", payload: {text: title, title, kind: "task", source: "desktop-widget", external_id: `widget:${crypto.randomUUID()}`}});
    await runOperation();
  } catch (err) { error(err); }
};
$("widget-retry").onclick = async () => {
  if (operation) return runOperation();
  if (busy) return;
  busy = true; render();
  try { await timeClient.retry(); clearError(); } catch (err) { error(err); }
  finally { busy = false; render(); }
};
$("widget-task").onclick = () => void setPopup(popup === "tasks" ? null : "tasks", {focus: true}).catch(error);
$("widget-more").onclick = () => void setPopup(popup === "menu" ? null : "menu").catch(error);
$("widget-close").onclick = () => void setPopup(null).then(() => $("widget-task").focus()).catch(error);
$("widget-search").oninput = renderTasks;
$("widget-new").onclick = () => { $("widget-new-form").hidden = !$("widget-new-form").hidden; if (!$("widget-new-form").hidden) $("widget-new-title").focus(); };
$("widget-open").onclick = () => void windowMode("full").catch(error);
$("widget-switch").onclick = () => void windowMode("bar").catch(error);
$("widget-tab").onkeydown = event => { if (["Enter", " "].includes(event.key)) { event.preventDefault(); void windowMode("card").catch(error); } };
installWindowGestures(native, {tab: true, doubleClick: bootstrap.double_click,
  onDoubleClick: () => void windowMode(mode === "bar" ? "card" : "bar").catch(error),
  onRightDoubleClick: () => void windowMode("full").catch(error)});
window.addEventListener("brainalot-widget-mode", event => setMode(event.detail));
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && popup) { event.preventDefault(); const was = popup; void setPopup(null).then(() => $(was === "tasks" ? "widget-task" : "widget-more").focus()).catch(error); }
});
document.addEventListener("pointerdown", event => {
  if (popup && !event.target.closest("#widget-popup,#widget-task,#widget-more")) void setPopup(null).catch(error);
});
async function fillAskChoices() {
  const choice = $("widget-ask-choice"); askRefs.clear();
  const option = (text, value, ref) => { const item = new Option(text, value); if (ref) askRefs.set(value, ref); return item; };
  const group = (label, tasks) => { const node = document.createElement("optgroup"); node.label = label; node.append(...tasks.map(task => option(task.title, taskKey(task), task))); return node; };
  const hot = new Map(burning.map(task => [taskKey(task), task]));
  const events = liveEvents.filter(event => !event.all_day && !event.time_skipped).map(event => ({source: "calendar", id: eventKey(event), title: event.title || t("Событие")}));
  const listed = taskCatalog.filter(task => task.source === "library" && !hot.has(taskKey(task)));
  const placeholder = option(t("Выбрать…"), ""); placeholder.disabled = true; placeholder.selected = true;
  const items = [placeholder, option(t("Отдых"), "label:rest", {source: "label", id: "rest", title: t("Отдых")})];
  if (hot.size) items.push(group(t("Горящие"), [...hot.values()]));
  if (events.length) items.push(group(t("Сегодня в календаре"), events));
  if (listed.length) items.push(group(t("Задачи"), listed));
  items.push(option(t("Спросить позже"), "later")); choice.replaceChildren(...items);
}
$("widget-ask-choice").onchange = async () => {
  const value = $("widget-ask-choice").value; if (!value) return;
  if (value === "later") { snoozeUntil = Date.now() + 30 * 60_000; asking = false; render(); return; }
  try {
    const task = askRefs.get(value) || null;
    if (await action("start", {...timerFields("stopwatch", await getTimePreset()), task, backfill_from: gap ? Math.floor(gap.since) : Date.now()})) {
      gap = null; asking = false; await rememberSelection(task);
    }
  } catch (err) { error(err); }
  render();
};
async function watchUntracked() {
  if (typeof native.input_idle_ms !== "function") return;
  try {
    if (document.hidden) await timeClient.refresh();
    const now = Date.now(), tracked = timeClient.state?.status === "running" || Boolean(currentEvent(liveEvents, now));
    const step = untrackedStep(gap, {tracked, idleMs: Number(await native.input_idle_ms()) || 0, now}); gap = step.gap;
    const ask = step.ask && now >= snoozeUntil && !pending();
    if (ask && !asking) await fillAskChoices();
    if (ask !== asking) { asking = ask; render(); }
  } catch { /* Sampling never blocks the common timer; the next sample retries. */ }
}
chrome.storage.onChanged.addListener(changes => {
  if (localeChange(changes)) void syncLocale().then(render);
  if (changes["panel:theme"]) document.documentElement.dataset.theme = changes["panel:theme"].newValue;
  if (changes["panel:design"]) document.documentElement.dataset.design = changes["panel:design"].newValue;
});
async function refresh(force = false) {
  if (refreshing || !force && document.hidden) return;
  refreshing = true;
  try {
    await timeClient.refresh();
    const [notes, prefs, tasks, timeTotals] = await Promise.all([sharedApi("/api/notes"), chrome.storage.local.get(["panel:calendar-importance", "panel:theme", "panel:design"]), sharedApi("/api/time/tasks"), sharedApi("/api/time/totals")]);
    liveNotes = notes.notes; taskCatalog = (tasks.tasks || []).filter(task => task.source !== "work"); totals = timeTotals.rows || [];
    document.documentElement.dataset.theme = prefs["panel:theme"] || "light"; document.documentElement.dataset.design = prefs["panel:design"] || "stream";
    burning = liveNotes.filter(note => ["task", "purchase"].includes(note.kind) && note.importance === "critical" && !["done", "cancelled", "archived"].includes(note.status)).map(note => ({source: "library", id: note.id, title: note.title}));
    const now = new Date(), day = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,"0")}-${String(now.getDate()).padStart(2,"0")}`;
    const calendar = await sharedApi(`/api/calendar/events?start=${day}&end=${day}`).catch(() => ({events: []})); liveEvents = calendar.events || [];
    for (const event of liveEvents) if (!event.time_skipped && calendarImportanceLevel(prefs["panel:calendar-importance"], event) === "critical") burning.push({source: "calendar", id: eventKey(event), title: event.title});
    catalogReady = true;
    const before = selectedFire;
    if (pollNotice) clearError();
    render(); if (popup === "tasks") renderTasks();
    if (selectedFire !== before) void chrome.storage.local.set({"desktop:selected-fire": selectedFire});
  } catch (err) { error(err, {poll: true}); }
  finally { refreshing = false; }
}
timeClient.subscribe(render); render();
document.addEventListener("visibilitychange", () => { if (!document.hidden) void refresh(); });
await refresh(true); document.body.dataset.widgetReady = "true";
setInterval(refresh, 5000); setInterval(() => { if (!document.hidden) render(); }, 1000); setInterval(watchUntracked, 15_000);
