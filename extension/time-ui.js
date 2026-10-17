import { timeClient, sharedApi, elapsedAt, formatTime } from "./time-client.js";
import { t } from "./i18n.js";
import { WORK_ENABLED } from "./work-availability.js";
const icon = '<svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true"><circle cx="12" cy="14" r="8"/><path d="M12 6V2m-3 0h6m-3 8v5l3 2M18 7l2-2"/></svg>';
let currentUI;
export function taskTimeButton(task) {
  const button = document.createElement("button");
  button.className = "task-time-button"; button.type = "button"; button.innerHTML = icon;
  button.title = button.ariaLabel = t("Считать время: {title}", { title: task.title });
  button.addEventListener("pointerdown",event=>event.stopPropagation());
  button.onclick = async event => {
    event.preventDefault(); event.stopPropagation();
    button.disabled = true;
    try { await timeClient.refresh(); await timeClient.command("start", { mode: "stopwatch", task }); currentUI?.render(); }
    catch (error) { currentUI?.error(error); }
    finally { button.disabled = false; }
  };
  return button;
}
export function mountTimeUI(host) {
  const wrap = document.createElement("details"); wrap.className = "time-control";
  wrap.innerHTML = `<summary>${icon}<span>${t("Учёт времени")}</span><output>00:00</output></summary><div class="time-body">
    <p class="time-current"></p><div class="time-actions"><button data-action="start">${t("Начать")}</button><button data-action="pause">${t("Пауза")}</button><button data-action="stop">${t("Завершить")}</button></div>
    <label>${t("Режим")}<select class="time-mode"><option value="stopwatch">${t("Секундомер")}</option><option value="pomodoro">Pomodoro · 25 ${t("мин")}</option><option value="break">${t("Перерыв · 5 мин")}</option></select></label>
    <label>${t("Выбрать задачу")}<input class="time-search" type="search" placeholder="${t("Поиск по всем задачам")}"></label><select class="time-tasks" aria-label="${t("Задача для учёта времени")}"><option value="">${t("Без задачи")}</option></select>
    <p class="time-help">${t("Считается одна текущая работа. Перерыв не входит в итог.")}</p><p class="time-error" role="alert" hidden></p><button class="time-retry" hidden>${t("Проверить повтор")}</button>
    <details class="time-summary"><summary>${t("Сводка за день")}</summary><label>${t("Дата")}<input class="time-day" type="date"></label><label>${t("Часовой пояс")}<input class="time-zone" type="text"></label><button class="time-report">${t("Показать сводку")}</button><div class="time-rows" aria-live="polite"></div></details></div>`;
  host.append(wrap);
  const conflict = document.createElement("div"); conflict.className="time-error"; conflict.hidden=true;
  const message=document.createElement("p"); message.textContent=t("Настройки изменены в другом окне. Ваш выбор сохранён локально.");
  for (const [keepLocal,label] of [[true,t("Сохранить мой выбор")],[false,t("Взять общие настройки")]]) {
    const button=document.createElement("button"); button.textContent=label;
    button.onclick=async()=>{const reply=await chrome.runtime.sendMessage({type:"preferences-resolve",keepLocal});if(!reply?.ok)message.textContent=reply?.error||t("Дождитесь ответа сервиса.");};
    conflict.append(button);
  }
  conflict.prepend(message);host.prepend(conflict);
  chrome.storage.local.get("shared:preferences-conflict").then(saved=>{conflict.hidden=!saved["shared:preferences-conflict"];});
  chrome.storage.onChanged.addListener(changes=>{if(changes["shared:preferences-conflict"])conflict.hidden=!changes["shared:preferences-conflict"].newValue;});
  const $ = s => wrap.querySelector(s);
  let tasks = [], selected = null;
  const error = err => { $(".time-error").hidden = false; $(".time-error").textContent = err.message; $(".time-retry").hidden = !timeClient.pending; };
  async function act(action, fields = {}) {
    try { await timeClient.command(action, fields); $(".time-error").hidden = true; render(); }
    catch (err) { error(err); }
  }
  function render() {
    const state = timeClient.state; if (!state) return;
    const elapsed = elapsedAt(state);
    $("output").textContent = formatTime(state.mode === "stopwatch" ? elapsed : state.duration_ms - elapsed);
    $(".time-current").textContent = `${state.task?.title || t("Без задачи")} · ${state.status === "running" ? t("Идёт") : state.status === "paused" ? t("Пауза") : t("Остановлен")}`;
    $(".time-mode").value = state.mode;
    $("[data-action=start]").textContent = state.status === "paused" ? t("Продолжить") : t("Начать");
    $("[data-action=start]").disabled = state.status === "running";
    $("[data-action=pause]").disabled = state.status !== "running";
    if (state.interruption) { $(".time-error").hidden = false; $(".time-error").textContent = t("Учёт приостановлен после простоя или перезапуска. Проверьте задачу и продолжите."); }
  }
  $("[data-action=start]").onclick = () => act(timeClient.state?.status === "paused" ? "resume" : "start", timeClient.state?.status === "paused" ? {} : { task: timeClient.state?.task || null, mode: $(".time-mode").value, ...($(".time-mode").value === "stopwatch" ? {} : {duration_ms: $(".time-mode").value === "break" ? 300000 : 1500000}) });
  for (const action of ["pause", "stop"]) $(`[data-action=${action}]`).onclick = () => act(action);
  $(".time-mode").onchange = () => act("set_mode", { mode: $(".time-mode").value, ...($(".time-mode").value === "stopwatch" ? {} : {duration_ms: $(".time-mode").value === "break" ? 300000 : 1500000}) });
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
  $(".time-tasks").onchange = () => { selected = $(".time-tasks").value === "" ? null : tasks[Number($(".time-tasks").value)]; void act("switch_task", { task: selected }); };
  wrap.addEventListener("toggle", async () => { if (!wrap.open) return; try { tasks = (await sharedApi("/api/time/tasks")).tasks.filter(task => WORK_ENABLED || task.source !== "work"); taskOptions(); } catch (err) { error(err); } });
  $(".time-retry").onclick = async () => { try { await timeClient.retry(); $(".time-error").hidden = true; $(".time-retry").hidden = true; render(); } catch (err) { error(err); } };
  const today = new Date(); $(".time-day").value = `${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,"0")}-${String(today.getDate()).padStart(2,"0")}`;
  $(".time-zone").value = Intl.DateTimeFormat().resolvedOptions().timeZone;
  $(".time-report").onclick = async () => {
    try {
      const report = await sharedApi(`/api/time/summary?day=${$(".time-day").value}&timezone=${encodeURIComponent($(".time-zone").value)}`);
      const list = $(".time-rows"); list.replaceChildren();
      for (const row of [...report.rows, { task: { title: t("Всего") }, total_ms: report.total_ms }]) {
        const line = document.createElement("div"); const name = document.createElement("span"); const value = document.createElement("strong");
        name.textContent = row.task?.title || t("Без задачи"); value.textContent = formatTime(row.total_ms); line.append(name, value); list.append(line);
      }
    } catch (err) { error(err); }
  };
  const refresh = () => timeClient.refresh().then(render).catch(error);
  void refresh(); setInterval(refresh, 5000); setInterval(render, 1000);
  currentUI = { render, error }; return currentUI;
}
