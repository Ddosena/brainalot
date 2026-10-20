import { native } from "./desktop-platform.js";
import { timeClient, sharedApi, elapsedAt, formatTime, formatTotal, getTimePreset, saveTimePreset, normalizeTimePreset, timerFields } from "./time-client.js";
import { applyDocument, syncLocale, localeChange, t } from "./i18n.js";
import { calendarImportanceLevel } from "./importance-state.js";
import { compactTasks, taskKey, resolveActiveTask, untrackedStep, eventKey, currentEvent, timerProgress } from "./widget-model.js";
import { installWindowGestures } from "./desktop-gestures.js";
import { FOCUS_OVERLAY_COLOR_KEY, NEON_COLORS, normalizeNeonColor } from "./focus-timer.js";
import { mountSpotifyPlayer } from "./spotify-player.js";
const $ = id => document.getElementById(id);
const isFull = document.body.classList?.contains("desktop-shell") === true;
// Full and compact windows must not overwrite each other's uncertain note action.
const OP_KEY = isFull ? "desktop:full-operation" : "desktop:widget-operation", SELECT_KEY = "desktop:selected-task", SELECT_REV_KEY = "desktop:selected-task-revision";
applyDocument(); void syncLocale();
const bootstrap = await native.bootstrap();
const saved = await chrome.storage.local.get(["desktop:selected-fire", SELECT_KEY, SELECT_REV_KEY, OP_KEY, "desktop:spotify-visible", FOCUS_OVERLAY_COLOR_KEY]);
let mode = isFull ? "full" : bootstrap.widget_mode === "card" ? "card" : "bar";
let burning = [], selectedFire = saved["desktop:selected-fire"] || "", selectedTask = saved[SELECT_KEY] || null;
let selectedRevision = saved[SELECT_REV_KEY] ?? null;
let operation = saved[OP_KEY] || null;
let selectedMode = null, selectedPreview = null, refreshing = false, busy = false, catalogReady = false;
let liveNotes = [], liveEvents = [], taskCatalog = [], totals = [];
let catalogToday = null, currentPreset = normalizeTimePreset(null);
let pendingNotice = false, pollNotice = false;
let gap = null, asking = false, snoozeUntil = 0, activitySince = null;
let popup = null, popupQueue = Promise.resolve();
let mediaVisible = !isFull && (bootstrap.media_visible ?? saved["desktop:spotify-visible"] !== false);
let spotifyPlayer = null;
document.body.dataset.side = bootstrap.side || "right";
document.body.dataset.widgetMode = mode;
document.body.dataset.mediaVisible = String(mediaVisible);
function applyProgressColor(value) {
  const color = NEON_COLORS.find(color => color.id === normalizeNeonColor(value));
  document.documentElement.style.setProperty("--timer-progress-color", color.tube);
}
applyProgressColor(saved[FOCUS_OVERLAY_COLOR_KEY]);
function mediaVisibility(visible) {
  if (!visible && popup === "media") void setPopup(null);
  mediaVisible = visible; document.body.dataset.mediaVisible = String(visible);
  $("widget-spotify-toggle").textContent = visible ? t("Скрыть Spotify") : t("Показать Spotify");
  // Share the popup geometry queue so hide/show cannot race a late popup resize.
  popupQueue = popupQueue.catch(() => {}).then(async () => {
    const result = await native.window_action("widget_media", {visible});
    if (result?.ok === false) throw new Error(t("Не удалось изменить размер плеера. Повторите действие."));
    if (result?.popup_above !== undefined) document.body.dataset.popupAbove = String(result.popup_above);
  });
  return popupQueue.then(() => { if (popup) return syncPopup(); }).catch(error);
}
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
async function reloadSelectionIntent() {
  const latest = await chrome.storage.local.get([SELECT_KEY, SELECT_REV_KEY]);
  selectedTask = latest[SELECT_KEY] || null; selectedRevision = latest[SELECT_REV_KEY] ?? null;
}
function pending() { return Boolean(timeClient.pending || operation); }
function clearError() { $("widget-error").hidden = true; $("widget-error").textContent = ""; pendingNotice = pollNotice = false; }
function error(err, {poll = false, show = true} = {}) {
  pendingNotice = false; pollNotice = poll;
  $("widget-error").textContent = err.message; $("widget-error").hidden = false;
  render();
  if (show && !poll && mode !== "bar") void setPopup(popup || "notice");
}
function syncPopup() {
  if (isFull) { document.body.dataset.popupAbove = "false"; return Promise.resolve(); }
  const requested = popup;
  // Serialize native geometry mutations: a late open must never undo a close.
  popupQueue = popupQueue.catch(() => {}).then(async () => {
    const height = (requested === "menu" ? 387 : requested === "notice" ? 247 : 472) + (mediaVisible ? 52 : 0);
    const result = await native.window_action("widget_popup", {open: Boolean(requested), height});
    if (result?.ok === false) throw new Error(t("Не удалось открыть список. Повторите действие."));
    document.body.dataset.popupAbove = String(Boolean(result?.popup_above));
  });
  return popupQueue;
}
async function setPopup(next, {focus = false} = {}) {
  if (mode === "bar") next = null;
  if (next !== "media") spotifyPlayer?.closeSettings?.();
  popup = next;
  $("widget-popup").hidden = !next;
  $("widget-picker").hidden = !["tasks", "activity"].includes(next);
  $("widget-menu").hidden = next !== "menu";
  $("widget-modes").hidden = next !== "modes";
  $("widget-music-settings").hidden = next !== "media";
  $("widget-mode-visible").setAttribute("aria-expanded", String(next === "modes"));
  $("widget-ask-choice").setAttribute("aria-expanded", String(next === "activity"));
  $("widget-task").setAttribute("aria-expanded", String(next === "tasks"));
  $("widget-more").setAttribute("aria-expanded", String(next === "menu" || next === "notice"));
  if (["tasks", "activity"].includes(next)) renderTasks();
  try { await syncPopup(); }
  catch (err) {
    // A failed native resize must not recursively try opening its own error popup.
    popup = null; $("widget-popup").hidden = true;
    $("widget-task").setAttribute("aria-expanded", "false");
    $("widget-more").setAttribute("aria-expanded", "false");
    error(err, {show: false}); return false;
  }
  if (focus && popup === next) (["tasks", "activity"].includes(next) ? $("widget-search") : next === "modes" ? $("widget-work-minutes") : $("widget-done")).focus();
}
async function windowMode(next) { await setPopup(null); return native.window_action(next); }
function setMode(value) {
  if (isFull) return;
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
  $("widget-progress").toggleAttribute("hidden", progress === null);
  $("widget-tab-progress").toggleAttribute("hidden", progress === null);
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
  $("widget-complete").disabled = $("widget-done").disabled;
  $("widget-complete").title = $("widget-done").title;
  $("widget-mode").disabled = busy || waiting;
  $("widget-mode-visible").disabled = busy || waiting;
  $("widget-task").disabled = busy || waiting;
  $("widget-new").disabled = busy || waiting;
  $("widget-new-form").querySelector("button").disabled = busy || waiting;
  const modeKind = selectedMode || state?.mode || "pomodoro";
  const modeName = modeKind === "stopwatch" ? t("Секундомер") : modeKind === "break" ? t("Перерыв") : t("Помодоро");
  $("widget-mode").textContent = t("Сменить режим: {mode}", {mode: modeName});
  const duration = ["running", "paused"].includes(state?.status) && state.mode === modeKind ? state.duration_ms : modeKind === "break" ? currentPreset.break_ms : currentPreset.work_ms;
  $("widget-mode-visible").querySelector("span").textContent = modeKind === "stopwatch" ? modeName : t("{mode} · {minutes} мин", {mode: modeName, minutes: Math.round(duration / 60000)});
  document.querySelectorAll("[data-timer-mode]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.timerMode === modeKind)));
  const title = task?.title || t("Выбрать задачу");
  $("widget-burning").textContent = title; $("widget-task").title = title;
  const isCritical = Boolean(task && model.tasks.some(item => taskKey(item) === taskKey(task)));
  $("widget-task").classList.toggle("is-critical", isCritical);
  $("widget-critical-mark").toggleAttribute("hidden", !isCritical);
  $("widget-task").setAttribute("aria-label", isCritical ? `${title}. ${t("Горящая задача")}` : title);
  const hot = taskKey(model.hot) !== taskKey(task) ? model.hot : model.tasks.find(item => taskKey(item) !== taskKey(task));
  $("widget-current").hidden = !hot;
  $("widget-current").textContent = hot ? t("Горит: {title}", {title: hot.title}) : "";
  $("widget-current").title = $("widget-current").textContent;
  $("widget-more").dataset.notice = String(!$("widget-error").hidden);
  $("widget-ask").hidden = !asking; document.body.dataset.ask = String(asking);
  if (asking && gap) {
    const minutes = Math.max(1, Math.floor((Date.now() - gap.since) / 60_000));
    $("widget-ask-title").textContent = t("Чем занимаешься?");
    $("widget-ask-choice").textContent = t("Выбрать · {minutes} мин", {minutes});
    $("widget-ask-choice").title = t("{minutes} мин без учёта", {minutes});
    $("widget-tab").title = t("Чем занимаешься?");
  }
}
function renderTasks() {
  const query = $("widget-search").value.trim().toLocaleLowerCase();
  const active = taskKey(currentTask());
  const activity = popup === "activity";
  $("widget-picker-title").textContent = activity ? t("Чем занимаешься?") : t("Перейти к задаче");
  const unique = new Map();
  if (activity) {
    const add = (task, group) => { if (!unique.has(taskKey(task))) unique.set(taskKey(task), {...task, group}); };
    add({source: "label", id: "rest", title: t("Отдых")}, "");
    burning.forEach(task => add(task, t("Горящие")));
    liveEvents.filter(event => !event.all_day && !event.time_skipped).forEach(event => add({source: "calendar", id: eventKey(event), title: event.title || t("Событие")}, t("Сегодня в календаре")));
    taskCatalog.forEach(task => add(task, task.source === "calendar" ? t("Сегодня в календаре") : t("Задачи")));
  }
  const tasks = (activity ? [...unique.values()] : taskCatalog).filter(task => task.title.toLocaleLowerCase().includes(query));
  const nodes = tasks.map(task => {
    const row = document.createElement("button"); row.type = "button"; row.dataset.task = taskKey(task);
    row.dataset.active = String(active === taskKey(task)); row.disabled = busy || pending();
    const title = document.createElement("strong"); title.textContent = task.title;
    const detail = document.createElement("small");
    const total = totals.find(item => taskKey(item.task) === taskKey(task))?.total_ms || 0;
    detail.textContent = `${activity && task.group ? task.group + " · " : ""}${active === taskKey(task) ? t("Выбрана") + " · " : ""}${t("Всего {time}", {time: formatTotal(total)})}`;
    row.append(title, detail); row.onclick = () => void (activity ? answerActivity(task) : chooseTask(task));
    return row;
  });
  if (!nodes.length) { const empty = document.createElement("p"); empty.textContent = t("Задач не найдено"); nodes.push(empty); }
  if (activity) {
    const later = document.createElement("button"); later.id = "widget-activity-later"; later.type = "button"; later.textContent = t("Спросить позже");
    later.onclick = () => { snoozeUntil = Date.now() + 30 * 60_000; asking = false; render(); void setPopup(null); }; nodes.push(later);
  }
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
    const nextMode = selectedMode || (["pomodoro", "stopwatch", "break"].includes(state?.mode) ? state.mode : "pomodoro");
    if (await action("start", {...timerFields(nextMode, await getTimePreset()), task})) {
      selectedMode = selectedPreview = null; render();
    }
  } catch (err) { error(err); }
};
$("widget-stop").onclick = async () => {
  const task = currentTask();
  if (await action("stop")) { await rememberSelection(task); await setPopup(null); }
};
async function selectMode(next) {
  if (busy || pending()) return;
  try {
    const task = currentTask();
    currentPreset = await getTimePreset();
    if (["running", "paused"].includes(timeClient.state?.status)) {
      if (timeClient.state.mode !== next && !await action("set_mode", timerFields(next, currentPreset))) return;
      selectedMode = selectedPreview = null;
      await rememberSelection(task);
    } else {
      const fields = timerFields(next, currentPreset);
      selectedMode = next; selectedPreview = {...fields, status: "idle", elapsed_ms: 0};
    }
    render(); await setPopup(null);
  } catch (err) { error(err); }
}
async function openModes() {
  try {
    currentPreset = await getTimePreset();
    $("widget-work-minutes").value = currentPreset.work_ms / 60000;
    $("widget-break-minutes").value = currentPreset.break_ms / 60000;
    render(); await setPopup(popup === "modes" ? null : "modes");
  } catch (err) { error(err); }
}
$("widget-mode").onclick = openModes;
$("widget-mode-visible").onclick = openModes;
document.querySelectorAll("[data-timer-mode]").forEach(button => { button.onclick = () => void selectMode(button.dataset.timerMode); });
$("widget-preset-form").onsubmit = async event => {
  event.preventDefault(); if (busy || pending()) return;
  const work = Number($("widget-work-minutes").value), rest = Number($("widget-break-minutes").value);
  if (![work, rest].every(value => Number.isInteger(value) && value >= 1 && value <= 180)) { error(new Error(t("Укажите целое время от 1 до 180 минут."))); return; }
  busy = true; render();
  try { currentPreset = await saveTimePreset({work_ms: work * 60000, break_ms: rest * 60000}); clearError(); await setPopup(null); }
  catch (err) { error(err); }
  finally { busy = false; render(); }
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
    const capturedTask = {source: "library", id: note.id, title: note.title};
    if (op.activity) {
      if (operation.stage !== "activated") {
        await timeClient.refresh();
        if (!operation.activation) {
          if (operation.stage === "activate") throw new Error(t("Не сохранилась квитанция запуска. Задача сохранена; проверьте учёт в другом окне."));
          if (timeClient.state?.status === "running") throw new Error(t("Учёт уже идёт в другом окне. Проверьте его перед запуском."));
          const activation = {request_id: crypto.randomUUID(), expected_revision: timeClient.state.revision, action: "start",
            ...timerFields("stopwatch", await getTimePreset()), task: capturedTask, backfill_from: op.activity.backfill_from};
          await saveOperation({...operation, stage: "activate", activation});
        }
        try { await timeClient.commandWithReceipt(operation.activation); }
        catch (err) {
          if (err.status >= 400 && err.status < 500 && ![408, 425, 429].includes(err.status)) {
            // This exact activation was definitely rejected, not lost. Its CAS
            // must not be regenerated or block the saved task behind endless Retry.
            await reloadSelectionIntent(); await saveOperation(null);
            gap = null; asking = false; activitySince = null;
            $("widget-new-form").hidden = true; $("widget-new-title").value = "";
            throw Object.assign(new Error(`${t("Задача сохранена, но запуск отклонён. Проверьте текущий учёт и выберите действие заново.")} ${err.message}`), {status: err.status});
          }
          throw err;
        }
        await saveOperation({...operation, stage: "activated"});
      }
      gap = null; asking = false; activitySince = null; selectedMode = selectedPreview = null;
      // An old receipt may have been followed by a peer stop/task choice. Only
      // shared time state proves the current activity; do not restamp old A at
      // the peer's newer revision or erase a newer explicit local choice.
      await reloadSelectionIntent();
    } else {
      if (timeClient.state?.status === "running" && taskKey(timeClient.state.task) !== taskKey(capturedTask)) {
        // Ordinary creation follows the same running switch rule as existing tasks.
        await timeClient.command("switch_task", {task: capturedTask});
      }
      await rememberSelection(capturedTask);
    }
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
  try {
    if (timeClient.pending) {
      const recovered = timeClient.pending;
      await timeClient.retry();
      if (operation?.kind === "capture" && operation.activity && operation.stage === "activate" && operation.activation && recovered.request_id === operation.activation.request_id && JSON.stringify(recovered) === JSON.stringify(operation.activation)) {
        await timeClient.refresh();
        await saveOperation({...operation, stage: "activated"});
      }
    }
    await finishOperation();
  }
  catch (err) {
    // A definite rejection cannot be retried with the old CAS. Let a fresh user
    // action use the newly refreshed note, while transport failures keep the op.
    const acknowledged = operation?.kind === "capture" && operation.note || operation?.kind === "complete" && operation.stage === "confirm";
    if (!acknowledged && err.status >= 400 && err.status < 500 && ![408, 425, 429].includes(err.status)) {
      await saveOperation(null); await refresh();
    }
    error(err);
  } finally { busy = false; render(); if (["tasks", "activity"].includes(popup)) renderTasks(); }
}
$("widget-done").onclick = async () => {
  if (busy || pending()) return;
  const task = currentTask(), note = task?.source === "library" && liveNotes.find(note => note.id === task.id);
  if (!note || !["task", "purchase"].includes(note.kind) || ["done", "cancelled", "archived"].includes(note.status)) return;
  let prepared = false; busy = true; render();
  try { await saveOperation({kind: "complete", task, version: note.version, stage: "stop"}); prepared = true; }
  catch (err) { error(err); }
  finally { busy = false; render(); }
  if (prepared) await runOperation();
};
$("widget-complete").onclick = $("widget-done").onclick;
$("widget-new-form").onsubmit = async event => {
  event.preventDefault(); if (busy || pending()) return;
  const title = $("widget-new-title").value.trim(); if (!title) return;
  let prepared = false; busy = true; render();
  try {
    const {today} = await sharedApi("/api/time/tasks");
    if (!/^\d{4}-\d{2}-\d{2}$/.test(today || "")) throw new Error(t("Сервис не сообщил сегодняшнюю дату. Обновите приложение."));
    catalogToday = today;
    await saveOperation({kind: "capture", payload: {text: title, title, kind: "task", due: catalogToday, source: "desktop-widget", external_id: `widget:${crypto.randomUUID()}`},
      ...(popup === "activity" ? {activity: {backfill_from: Math.floor(activitySince ?? gap?.since ?? Date.now())}} : {})});
    prepared = true;
  } catch (err) { error(err); }
  finally { busy = false; render(); }
  if (prepared) await runOperation();
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
if (!isFull) installWindowGestures(native, {tab: true, doubleClick: bootstrap.double_click,
  onDoubleClick: () => void windowMode(mode === "bar" ? "card" : "bar").catch(error),
  onRightDoubleClick: () => void windowMode("full").catch(error)});
window.addEventListener("brainalot-widget-mode", event => setMode(event.detail));
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && popup) { event.preventDefault(); const was = popup; void setPopup(null).then(() => $(was === "tasks" ? "widget-task" : "widget-more").focus()).catch(error); }
});
document.addEventListener("pointerdown", event => {
  if (popup && !event.target.closest("#widget-popup,#widget-task,#widget-more,#widget-mode-visible,#widget-ask-choice,#widget-spotify")) void setPopup(null).catch(error);
});
async function answerActivity(task) {
  task = {source: task.source, id: task.id, title: task.title};
  try {
    if (await action("start", {...timerFields("stopwatch", await getTimePreset()), task, backfill_from: Math.floor(activitySince ?? gap?.since ?? Date.now())})) {
      gap = null; asking = false; activitySince = null; selectedMode = selectedPreview = null; await rememberSelection(task); await setPopup(null);
    }
  } catch (err) { error(err); }
  render();
}
$("widget-ask-choice").onclick = () => {
  activitySince = gap?.since ?? Date.now(); $("widget-search").value = "";
  void setPopup(popup === "activity" ? null : "activity", {focus: true}).catch(error);
};
async function watchUntracked() {
  if (typeof native.input_idle_ms !== "function") return;
  try {
    if (document.hidden) await timeClient.refresh();
    const now = Date.now(), tracked = timeClient.state?.status === "running" || Boolean(currentEvent(liveEvents, now));
    const step = untrackedStep(gap, {tracked, idleMs: Number(await native.input_idle_ms()) || 0, now}); gap = step.gap;
    const ask = step.ask && now >= snoozeUntil && !pending();
    if (ask !== asking) { asking = ask; render(); }
  } catch { /* Sampling never blocks the common timer; the next sample retries. */ }
}
chrome.storage.onChanged.addListener(changes => {
  if (!busy && changes[OP_KEY]) { operation = changes[OP_KEY].newValue || null; render(); }
  if (!busy && (changes[SELECT_KEY] || changes[SELECT_REV_KEY])) {
    if (changes[SELECT_KEY]) selectedTask = changes[SELECT_KEY].newValue || null;
    if (changes[SELECT_REV_KEY]) selectedRevision = changes[SELECT_REV_KEY].newValue ?? null;
    render();
  }
  if (localeChange(changes)) void syncLocale().then(render);
  if (changes["panel:theme"]) document.documentElement.dataset.theme = changes["panel:theme"].newValue;
  if (changes["panel:design"]) document.documentElement.dataset.design = changes["panel:design"].newValue;
  if (changes[FOCUS_OVERLAY_COLOR_KEY]) applyProgressColor(changes[FOCUS_OVERLAY_COLOR_KEY].newValue);
  if (changes["panel:calendar-importance"]) void refresh();
});
async function refresh(force = false) {
  if (refreshing || !force && document.hidden) return;
  refreshing = true;
  try {
    await timeClient.refresh();
    const [notes, localPrefs, sharedPrefs, tasks, timeTotals] = await Promise.all([sharedApi("/api/notes"), chrome.storage.local.get(["panel:calendar-importance", "panel:theme", "panel:design", FOCUS_OVERLAY_COLOR_KEY]), sharedApi("/api/ui/preferences"), sharedApi("/api/time/tasks"), sharedApi("/api/time/totals")]);
    // Read the common preferences directly. Calendar fire and color must update
    // even before a hidden full-window preference sync happens to poll.
    const prefs = {...localPrefs, ...sharedPrefs.values};
    liveNotes = notes.notes; taskCatalog = (tasks.tasks || []).filter(task => task.source !== "work"); totals = timeTotals.rows || [];
    catalogToday = /^\d{4}-\d{2}-\d{2}$/.test(tasks.today || "") ? tasks.today : null;
    currentPreset = normalizeTimePreset(sharedPrefs.values?.["time:preset"]);
    document.documentElement.dataset.theme = prefs["panel:theme"] || "light"; document.documentElement.dataset.design = prefs["panel:design"] || "stream";
    applyProgressColor(prefs[FOCUS_OVERLAY_COLOR_KEY]);
    burning = liveNotes.filter(note => ["task", "purchase"].includes(note.kind) && note.importance === "critical" && !["done", "cancelled", "archived"].includes(note.status)).map(note => ({source: "library", id: note.id, title: note.title}));
    const now = new Date(), day = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,"0")}-${String(now.getDate()).padStart(2,"0")}`;
    const calendar = await sharedApi(`/api/calendar/events?start=${day}&end=${day}`).catch(() => ({events: []})); liveEvents = calendar.events || [];
    for (const event of liveEvents) if (!event.time_skipped && calendarImportanceLevel(prefs["panel:calendar-importance"], event) === "critical") burning.push({source: "calendar", id: eventKey(event), title: event.title});
    catalogReady = true;
    const before = selectedFire;
    if (pollNotice) clearError();
    render(); if (["tasks", "activity"].includes(popup)) renderTasks();
    if (selectedFire !== before) void chrome.storage.local.set({"desktop:selected-fire": selectedFire});
  } catch (err) { error(err, {poll: true}); }
  finally { refreshing = false; }
}
timeClient.subscribe(render); render();
document.addEventListener("visibilitychange", () => { if (!document.hidden) void refresh(); });
await refresh(true);
if (!isFull) {
  spotifyPlayer = mountSpotifyPlayer($("widget-spotify"), {onVisibility: mediaVisibility,
    settingsContainer: $("widget-music-settings"), onSettingsOpen: open => void setPopup(open ? "media" : null).catch(error)});
  $("widget-spotify-toggle").onclick = () => { void spotifyPlayer.setVisible(!spotifyPlayer.visible).catch(error); };
} else {
  $("widget-spotify-toggle").hidden = true;
  $("widget-open").hidden = true;
}
document.body.dataset.widgetReady = "true";
setInterval(refresh, 5000); setInterval(() => { if (!document.hidden) render(); }, 1000); setInterval(watchUntracked, 15_000);
