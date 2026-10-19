import { timeClient, sharedApi, elapsedAt, formatTime, formatTotal, taskKey, getTimePreset, timerFields } from "./time-client.js";
import { t } from "./i18n.js";
import { WORK_ENABLED } from "./work-availability.js";
const icon = '<svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true"><circle cx="12" cy="14" r="8"/><path d="M12 6V2m-3 0h6m-3 8v5l3 2M18 7l2-2"/></svg>';
const tomato = '<svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true"><path d="M12 8c-4-4-10 0-8 6s5 7 8 5c3 2 6 1 8-5s-4-10-8-6Zm0 0V3m-4 2 4 3 4-3"/></svg>';
const play = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="m8 5 11 7-11 7Z"/></svg>';
const stop = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M6 6h12v12H6Z"/></svg>';
let currentUI, totalsSnapshot = null, fetchingTotals = null;
const rows = new Set(), modes = new Map();
const MODE_PREFIX = "time:row-mode:";
let modesReady = false, totalsByTask = new Map();
function rowMode(task, state = timeClient.state) {
  return modes.get(taskKey(task)) || (activeTask(state,task) && state.mode === "pomodoro" ? "pomodoro" : "stopwatch");
}
function initRowModes() {
  if (modesReady) return; modesReady = true;
  void chrome.storage.local.get(null).then(saved=>{for(const [key,value] of Object.entries(saved)) if(key.startsWith(MODE_PREFIX) && ["stopwatch","pomodoro"].includes(value)) modes.set(key.slice(MODE_PREFIX.length),value); renderRows();});
  chrome.storage.onChanged.addListener((changes,area)=>{if(area!=="local")return;for(const [key,change] of Object.entries(changes))if(key.startsWith(MODE_PREFIX)){if(["stopwatch","pomodoro"].includes(change.newValue))modes.set(key.slice(MODE_PREFIX.length),change.newValue);else modes.delete(key.slice(MODE_PREFIX.length));}renderRows();});
}
function activeTask(state, task) { return state && ["running","paused"].includes(state.status) && taskKey(state.task) === taskKey(task); }
function renderRows() {
  if (document.hidden) return;
  const state = timeClient.state;
  for (const entry of rows) {
    if (!entry.root.isConnected) { if (entry.mounted) rows.delete(entry); continue; }
    entry.mounted = true;
    const active = activeTask(state, entry.task), mode = rowMode(entry.task,state);
    entry.root.classList.toggle("is-current", !!active);
    entry.root.closest(".note,.calendar-event")?.classList.toggle("is-timing", !!active);
    if (entry.lastMode !== mode) {entry.choose.innerHTML = mode === "pomodoro" ? tomato : icon; entry.lastMode=mode;}
    entry.choose.title = entry.choose.ariaLabel = t("Режим: {mode}. Переключить", {mode:mode === "pomodoro" ? "Pomodoro" : t("Секундомер")});
    entry.choose.setAttribute("aria-pressed", String(mode === "pomodoro"));
    if (entry.lastActive !== active) {entry.action.innerHTML = active ? stop : play;entry.lastActive=active;}
    entry.action.title = entry.action.ariaLabel = active ? t("Завершить интервал: {title}", {title:entry.task.title}) : t("Считать время: {title}", {title:entry.task.title});
    let total = totalsByTask.get(taskKey(entry.task)) || 0;
    const snapshot = totalsSnapshot?.state;
    if (activeTask(snapshot, entry.task) && snapshot.status === "running" && snapshot.mode !== "break" && active && state.mode !== "break") {
      total += Math.max(0, elapsedAt(state) - snapshot.elapsed_ms);
    }
    const label = formatTotal(total); if (entry.total.textContent !== label) entry.total.textContent = label;
    entry.total.title = t("Суммарное время за весь период");
  }
}
async function refreshTotals() {
  if (fetchingTotals) return fetchingTotals;
  fetchingTotals = sharedApi("/api/time/totals").then(value => {totalsSnapshot = value; totalsByTask = new Map(value.rows.map(row=>[taskKey(row.task),row.total_ms])); renderRows();}).finally(() => {fetchingTotals = null;});
  return fetchingTotals;
}
timeClient.subscribe(() => {renderRows(); currentUI?.render();});
export function taskTimeButton(task) {
  initRowModes();
  const root = document.createElement("span"); root.className = "task-time-controls";
  const total = document.createElement("output"); total.className = "task-time-total";
  const choose = document.createElement("button"), action = document.createElement("button");
  choose.className = "task-time-button task-time-mode"; action.className = "task-time-button task-time-action";
  choose.type = action.type = "button";
  for (const name of ["pointerdown","click","dblclick"]) root.addEventListener(name, event => event.stopPropagation());
  choose.onclick = event => {event.preventDefault(); const key = taskKey(task), value = rowMode(task) === "pomodoro" ? "stopwatch" : "pomodoro"; modes.set(key,value); renderRows(); void chrome.storage.local.set({[MODE_PREFIX+key]:value}).catch(error=>currentUI?.error(error));};
  action.onclick = async event => {
    event.preventDefault(); action.disabled = true;
    try {
      await timeClient.refresh();
      if (activeTask(timeClient.state, task)) await timeClient.command("stop");
      else { const mode = rowMode(task); const preset = await getTimePreset(); await timeClient.command("start", {task,...timerFields(mode,preset)}); }
      await refreshTotals();
    } catch (error) {currentUI?.error(error);}
    finally {action.disabled = false; renderRows();}
  };
  root.append(total,choose,action); rows.add({root,total,choose,action,task,mounted:false});
  // Initial controls must be accessible before insertion into the document.
  choose.innerHTML = icon; action.innerHTML = play; total.textContent = formatTotal(0);
  choose.title = choose.ariaLabel = t("Режим: {mode}. Переключить", {mode:t("Секундомер")});
  action.title = action.ariaLabel = t("Считать время: {title}", {title:task.title});
  queueMicrotask(renderRows);
  return root;
}
export function mountTimeUI(host) {
  const wrap = document.createElement("details"); wrap.className = "time-control";
  wrap.innerHTML = `<summary>${icon}<span>${t("Учёт времени")}</span><span class="time-current-title"></span><output>00:00</output></summary><div class="time-body">
    <p class="time-current"></p><div class="time-actions"><button data-action="start">${t("Начать")}</button><button data-action="pause">${t("Пауза")}</button><button data-action="stop">${t("Завершить")}</button></div>
    <label>${t("Режим")}<select class="time-mode"><option value="stopwatch">${t("Секундомер")}</option><option value="pomodoro">Pomodoro</option><option value="break">${t("Перерыв")}</option></select></label>
    <label>${t("Выбрать задачу")}<input class="time-search" type="search" placeholder="${t("Поиск по всем задачам")}"></label><select class="time-tasks" aria-label="${t("Задача для учёта времени")}"><option value="">${t("Без задачи")}</option></select>
    <p class="time-help">${t("Считается одна текущая работа. Перерыв не входит в итог.")}</p><p class="time-error" role="alert" hidden></p><button class="time-retry" hidden>${t("Проверить повтор")}</button>
    <details class="time-summary"><summary>${t("Сводка за день")}</summary><label>${t("Дата")}<input class="time-day" type="date"></label><label>${t("Часовой пояс")}<input class="time-zone" type="text"></label><button class="time-report">${t("Показать сводку")}</button><div class="time-rows" aria-live="polite"></div></details></div>`;
  host.append(wrap);
  // Row actions can fail while the timer details are collapsed. Recovery must
  // remain visible without expanding a native window or moving keyboard focus.
  const alerts = document.createElement("div"); alerts.className = "time-alerts";
  alerts.append(wrap.querySelector(".time-error"),wrap.querySelector(".time-retry"));
  host.insertBefore(alerts,wrap);
  const conflict = document.createElement("div"); conflict.className="time-error"; conflict.hidden=true;
  const message=document.createElement("p"); message.textContent=t("Настройки изменены в другом окне. Ваш выбор сохранён локально.");
  for (const [keepLocal,label] of [[true,t("Сохранить мой выбор")],[false,t("Взять общие настройки")]]) {
    const button=document.createElement("button"); button.textContent=label;
    button.onclick=async()=>{const reply=await chrome.runtime.sendMessage({type:"preferences-resolve",keepLocal});if(!reply?.ok)message.textContent=reply?.error||t("Дождитесь ответа сервиса.");}; conflict.append(button);
  }
  conflict.prepend(message);host.prepend(conflict);
  chrome.storage.local.get("shared:preferences-conflict").then(saved=>{conflict.hidden=!saved["shared:preferences-conflict"];});
  chrome.storage.onChanged.addListener(changes=>{if(changes["shared:preferences-conflict"])conflict.hidden=!changes["shared:preferences-conflict"].newValue;});
  const $ = s => wrap.querySelector(s) || alerts.querySelector(s);
  let tasks = [], chosenMode = "stopwatch", pendingNotice = false, interruptionNotice = false, pollNotice = false;
  const error = err => { pollNotice = false; interruptionNotice = false; pendingNotice = !!timeClient.pending; $(".time-error").hidden = false; $(".time-error").textContent = err.message; $(".time-retry").hidden = !timeClient.pending; };
  async function act(action, fields = {}) {
    try { await timeClient.command(action, fields); $(".time-error").hidden = true; render(); await refreshTotals(); }
    catch (err) { error(err); }
  }
  function render() {
    if (document.hidden) return;
    const state = timeClient.state; if (!state) return;
    const elapsed = elapsedAt(state), active = ["running","paused"].includes(state.status);
    $("output").textContent = formatTime(state.mode === "stopwatch" ? elapsed : state.duration_ms - elapsed);
    const title = active ? state.task?.title || t("Без задачи") : t("Нет текущей задачи");
    const modeLabel = state.mode === "pomodoro" ? "Pomodoro" : state.mode === "break" ? t("Перерыв") : t("Секундомер");
    $(".time-current-title").textContent = active ? `${title} · ${modeLabel}` : title;
    $(".time-current-title").title = title;
    $(".time-current").textContent = `${title} · ${state.status === "running" ? t("Идёт") : state.status === "paused" ? t("Пауза") : t("Остановлен")}`;
    $("[data-action=start]").textContent = state.status === "paused" ? t("Продолжить") : t("Начать");
    $("[data-action=start]").disabled = state.status === "running";
    $("[data-action=pause]").disabled = state.status !== "running";
    $("[data-action=stop]").disabled = !active;
    if (timeClient.pendingRecovered) {
      pollNotice=false;interruptionNotice=false;pendingNotice=true;
      $(".time-error").hidden=false;
      $(".time-error").textContent=t("Есть неподтверждённая команда другого окна или прошлого запуска. Текущее состояние показано выше. Нажмите «Проверить повтор».");
      $(".time-retry").hidden=false;
    } else if (pendingNotice && !timeClient.pending) {
      pendingNotice=false;$(".time-error").hidden=true;$(".time-retry").hidden=true;
    }
    if (state.interruption) {
      if (!pendingNotice && ($(".time-error").hidden || interruptionNotice)) {
        pollNotice=false;interruptionNotice=true;$(".time-error").hidden=false;
        $(".time-error").textContent=t("Учёт приостановлен после простоя или перезапуска. Проверьте задачу и продолжите.");
      }
    } else if (interruptionNotice) {
      interruptionNotice=false;$(".time-error").hidden=true;
    }
  }
  $("[data-action=start]").onclick = async () => {
    if (timeClient.state?.status === "paused") return act("resume");
    try { const preset = await getTimePreset(); await act("start", {task:timeClient.state?.task || null,...timerFields(chosenMode,preset)}); } catch(err) {error(err);}
  };
  for (const action of ["pause", "stop"]) $(`[data-action=${action}]`).onclick = () => act(action);
  $(".time-mode").onchange = () => { chosenMode = $(".time-mode").value; };
  function taskOptions() {
    const query = $(".time-search").value.toLocaleLowerCase();
    const select = $(".time-tasks"); select.replaceChildren(new Option(t("Без задачи"), ""));
    for (const [source, label] of [["library", t("Библиотека")], ...(WORK_ENABLED ? [["work", "Work"]] : []), ["calendar", t("Календарь")]]) {
      const group = document.createElement("optgroup"); group.label = label;
      tasks.forEach((task, index) => { if (task.source === source && task.title.toLocaleLowerCase().includes(query)) group.append(new Option(task.title, String(index))); });
      if (group.children.length) select.append(group);
    }
    const current = timeClient.state?.task;
    const index = tasks.findIndex(task => current && task.source === current.source && task.id === current.id);
    select.value = index < 0 ? "" : String(index);
  }
  $(".time-search").oninput = taskOptions;
  $(".time-tasks").onchange = () => { const selected = $(".time-tasks").value === "" ? null : tasks[Number($(".time-tasks").value)]; void act("switch_task", { task: selected }); };
  wrap.addEventListener("toggle", async () => { if (!wrap.open) return; try { tasks = (await sharedApi("/api/time/tasks")).tasks.filter(task => WORK_ENABLED || task.source !== "work"); taskOptions(); } catch (err) { error(err); } });
  $(".time-retry").onclick = async () => { try { await timeClient.retry(); $(".time-error").hidden = true; $(".time-retry").hidden = true; render(); await refreshTotals(); } catch (err) { error(err); } };
  const today = new Date(); $(".time-day").value = `${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,"0")}-${String(today.getDate()).padStart(2,"0")}`;
  $(".time-zone").value = Intl.DateTimeFormat().resolvedOptions().timeZone;
  // Calendar time is recorded automatically (owner decision 2026-10-19); an
  // event that did not happen is removed here and can be restored.
  const showReport = async () => {
    try {
      const report = await sharedApi(`/api/time/summary?day=${$(".time-day").value}&timezone=${encodeURIComponent($(".time-zone").value)}`);
      const list = $(".time-rows"); list.replaceChildren();
      const skip = async (id, skipped) => {
        try { await sharedApi("/api/time/calendar-skip", "POST", { request_id: crypto.randomUUID(), id, skipped }); await showReport(); await refreshTotals(); }
        catch (err) { error(err); }
      };
      const line = (title, total, extra = []) => {
        const row = document.createElement("div"); const name = document.createElement("span"); const value = document.createElement("strong");
        name.textContent = title; value.textContent = total; row.append(name, value, ...extra); list.append(row); return name;
      };
      const button = (text, help, onclick) => { const node = document.createElement("button"); node.className = "time-row-skip";
        node.textContent = text; node.title = help; node.onclick = onclick; return node; };
      for (const row of [...report.rows, { task: { title: t("Всего") }, total_ms: report.total_ms }]) {
        const calendar = row.calendar_ms ? [button(t("Не было"), t("Событие не состоялось: убрать его время из учёта"), () => skip(row.task.id, true))] : [];
        const name = line(row.task?.title || t("Без задачи"), formatTime(row.total_ms), calendar);
        if (row.calendar_ms) { const note = document.createElement("small"); note.className = "time-row-note";
          note.textContent = row.calendar_ms === row.total_ms ? t("по календарю") : t("{time} по календарю", { time: formatTime(row.calendar_ms) });
          name.append(" ", note); }
      }
      for (const item of report.skipped || []) {
        const name = line(item.title, t("не было"), [button(t("Вернуть"), t("Вернуть время события в учёт"), () => skip(item.id, false))]);
        name.classList.add("time-row-skipped");
      }
    } catch (err) { error(err); }
  };
  $(".time-report").onclick = showReport;
  const pollError = err => {
    if (!$(".time-error").hidden && !pollNotice && !interruptionNotice) return;
    error(err);pollNotice=true;
  };
  const refresh = () => document.hidden ? Promise.resolve() : Promise.all([timeClient.refresh(),refreshTotals()]).then(() => {
    if(pollNotice) {pollNotice=false;$(".time-error").hidden=true;}
    render();
  }).catch(pollError);
  currentUI = {render,error}; void refresh();
  const poll = setInterval(refresh, 5000), tick = setInterval(() => {if (!document.hidden) {render();renderRows();}},1000);
  const visible = () => {if (!document.hidden) void refresh();};
  document.addEventListener("visibilitychange",visible);
  window.addEventListener("pagehide",()=>{clearInterval(poll);clearInterval(tick);document.removeEventListener("visibilitychange",visible);},{once:true});
  return currentUI;
}
