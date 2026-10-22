import { esc, ic } from "./work-icons.js";
import { t } from "./i18n.js";

const safe = value => esc(String(value ?? ""));
const numeric = value => Number.isFinite(Number(value)) ? Number(value) : 0;
const minutes = value => {
  const n = Math.max(0, Math.round(numeric(value)));
  return t("{hours} ч {minutes} мин", { hours: Math.floor(n / 60), minutes: String(n % 60).padStart(2, "0") });
};
const asDate = iso => new Date(`${iso}T12:00:00Z`);
const dateText = iso => new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long", weekday: "long", timeZone: "UTC" }).format(asDate(iso));
const dateShift = (iso, count) => {
  const d = asDate(iso);
  d.setUTCDate(d.getUTCDate() + count);
  return d.toISOString().slice(0, 10);
};
const todayIn = zone => {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: zone || "Europe/Moscow", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date());
  const value = Object.fromEntries(parts.map(p => [p.type, p.value]));
  return `${value.year}-${value.month}-${value.day}`;
};
export const clampDate = (day, start, end) => !start || !end ? day : day < start ? start : day > end ? end : day;

export function daySummary(day, cubes, completed = {}, cap = 300) {
  const listed = cubes.filter(c => c.date === day?.date);
  const planned = listed.reduce((sum, c) => sum + numeric(c.minutes), 0);
  const credited = listed.reduce((sum, c) => {
    const percent = completed[c.id]?.percent ?? c.progress_percent ?? 0;
    return sum + numeric(c.minutes) * Math.max(0, Math.min(100, numeric(percent))) / 100;
  }, 0);
  const actual = listed.reduce((sum, c) => sum + numeric(completed[c.id]?.actual_minutes ?? c.actual_minutes), 0);
  const fixed = numeric(day?.fixed_minutes);
  const total = day?.total_work_minutes == null ? fixed + planned : numeric(day.total_work_minutes);
  const free = day?.unused_capacity_minutes == null ? Math.max(0, cap - total) : Math.max(0, numeric(day.unused_capacity_minutes));
  return { planned, credited, actual, fixed, total,
    free, unfinished: Math.max(0, planned - credited) };
}

export function taskRemaining(task, planning) {
  const outcomes = (planning?.day_outcomes || []).filter(row => row.task_id === task.id).sort((a, b) => a.date.localeCompare(b.date));
  const last = outcomes.at(-1);
  if (last?.done) return 0;
  if (last?.remaining_minutes != null) return Math.max(0, numeric(last.remaining_minutes));
  if (task.remaining_minutes != null) return Math.max(0, numeric(task.remaining_minutes));
  const central = numeric(task.estimate_minutes?.central);
  const credited = Object.values(planning?.completed_cubes || {}).filter(row => row.task_id === task.id)
    .reduce((sum, row) => sum + numeric(row.planned_minutes) * numeric(row.percent) / 100, 0);
  return Math.max(0, central - credited);
}

const validUrl = value => {
  try { const u = new URL(value); return ["https:", "http:"].includes(u.protocol) ? u.href : null; }
  catch { return null; }
};
const eventOn = (event, day) => {
  if (event.status === "cancelled" || event.transparency === "transparent" || event.my_response_status === "declined") return false;
  const start = String(event.start || "").slice(0, 10), end = String(event.end || "").slice(0, 10);
  return start <= day && (event.all_day ? day < end : day <= end);
};
const displayTime = event => event.all_day || !String(event.start).includes("T") ? t("Весь день") :
  `${String(event.start).slice(11, 16)}–${String(event.end).slice(11, 16)}`;
const projectClass = color => ["teal", "violet", "coral", "amber", "blue", "pink"].includes(color) ? `pc-${color}` : "pc-teal";
const clockMinute = value => {
  const match = String(value || "").match(/(?:T|^)(\d{2}):(\d{2})/);
  return match ? Number(match[1]) * 60 + Number(match[2]) : null;
};

export function createPlanning(root, ctx) {
  let selectedDate = null, selectedTaskId = null, busy = false, message = "", error = "", pending = null, remotePending = false;
  const planning = () => ctx.state?.planning;
  const currentDay = p => {
    selectedDate = clampDate(selectedDate || todayIn(p.settings?.timezone), p.settings?.start, p.settings?.end);
    return (p.result?.days || []).find(d => d.date === selectedDate) || { date: selectedDate };
  };
  const getTask = (p, day) => {
    const tasks = p.tasks || [];
    if (!tasks.some(x => x.id === selectedTaskId)) selectedTaskId = day.focus_task_id || tasks[0]?.id || null;
    return tasks.find(x => x.id === selectedTaskId) || null;
  };
  async function refreshPending() {
    pending = await ctx.pendingDraft();
    render();
  }
  async function run(action, data) {
    if (busy || pending) return;
    busy = true; error = ""; message = t("Сохраняем в Work…"); render();
    try {
      await ctx.action(action, data);
      message = action === "recalculate" ? t("План пересчитан и сохранён") : t("Изменение сохранено в Work");
    } catch (cause) {
      error = cause?.status === 409 ? t("Данные Work изменились. Запрос сохранён для безопасного повтора.") :
        t("Не удалось сохранить: {error}", { error: cause?.message || String(cause) });
      message = "";
    } finally {
      busy = false;
      pending = await ctx.pendingDraft().catch(() => null);
      render();
    }
  }
  function render() {
    remotePending = false;
    const p = planning();
    if (!p) {
      root.innerHTML = `<div class="plan-empty"><h1>${safe(t("План пока не загружен"))}</h1>
        <p>${safe(t("Агент может импортировать расчёт в этот Work. Проекты, доска, заметки и изображения сохранятся."))}</p>
        <p>${safe(t("После импорта здесь появятся даты, уроки, задачи, кубики и остаток часов."))}</p></div>`;
      return;
    }
    const day = currentDay(p), cubes = (p.result?.cubes || []).filter(c => c.date === selectedDate)
      .sort((a, b) => String(a.start).localeCompare(String(b.start)));
    const task = getTask(p, day), allTasks = p.tasks || [];
    const focusTask = allTasks.find(item => item.id === day.focus_task_id);
    const limit = day.day_limit_minutes || p.settings?.day_limit_minutes || 300;
    const summary = daySummary(day, cubes, p.completed_cubes, limit);
    const live = (p.live_events || []).filter(e => e.date === selectedDate);
    const overriddenEvents = new Set(live.map(e => e.calendar_event_id).filter(Boolean));
    const events = (p.calendar?.events || []).filter(e => eventOn(e, selectedDate) &&
      e.category !== "ignore" && !overriddenEvents.has(e.id) && !overriddenEvents.has(e.occurrence_id));
    const projects = [...new Map(allTasks.map(x => [x.project_id || x.course, x])).values()];
    const priorityTask = projects.find(x => x.course === p.settings?.priority_course);
    const priorityName = ctx.state.projects?.find(x => x.id === priorityTask?.project_id)?.name || priorityTask?.course || p.settings?.priority_course;
    const latest = p.history?.at(-1);
    const doneCount = cubes.filter(c => numeric(p.completed_cubes?.[c.id]?.percent ?? c.progress_percent) >= 100).length;
    const unavailable = busy || !!pending;
    root.innerHTML = `<div class="plan-shell">
      <header class="plan-head">
        <div><h1>${safe(t("План работы"))}</h1><p>${safe(t("Предварительная оценка по двум проектам. Факт и остаток сохраняются отдельно от плана."))}</p></div>
        <div class="plan-head-actions"><button type="button" data-plan-action="replan" ${unavailable ? "disabled" : ""}>${safe(t("Пересчитать от этого дня"))}</button>
          <label class="plan-refresh"><input type="checkbox" id="plan-refresh-calendar" ${unavailable ? "disabled" : ""}>${safe(t("Обновить календарь"))}</label></div>
      </header>
      ${pending ? `<div class="plan-alert" role="alert"><strong>${safe(t("Есть неподтверждённый запрос к Work"))}</strong>
        <span>${safe(pending.action || "")} · ${safe(new Date(pending.created).toLocaleString())}</span>
        <button type="button" data-plan-action="retry">${safe(t("Повторить тот же запрос"))}</button>
        <button type="button" data-plan-action="discard">${safe(t("Отменить запрос и обновить"))}</button></div>` : ""}
      ${error ? `<p class="plan-error" role="alert">${safe(error)}</p>` : ""}
      ${message ? `<p class="plan-message" role="status">${safe(message)}</p>` : ""}
      ${p.result?.provenance?.report_warning ? `<p class="plan-warning" role="status">${safe(p.result.provenance.report_warning)}</p>` : ""}
      <section class="plan-timeline"><div class="plan-navigate"><button type="button" data-plan-action="previous" aria-label="${safe(t("Предыдущий день"))}" ${selectedDate <= p.settings?.start ? "disabled" : ""}>${ic("chevL")}</button>
        <label>${safe(t("Дата"))}<input id="plan-date" type="date" value="${safe(selectedDate)}" min="${safe(p.settings?.start)}" max="${safe(p.settings?.end)}"></label>
        <button type="button" data-plan-action="next" aria-label="${safe(t("Следующий день"))}" ${selectedDate >= p.settings?.end ? "disabled" : ""}>${ic("chevR")}</button>
        <button type="button" data-plan-action="today">${safe(t("Сегодня"))}</button><strong>${safe(dateText(selectedDate))}</strong></div>
        ${timelineHtml(p, cubes, events, live)}</section>
      ${!p.calendar?.configured || p.calendar?.stale ? `<p class="plan-warning" role="status">${safe(t("Календарь не подтверждён как актуальный. Свободные часы требуют проверки."))}</p>` : ""}
      <div class="plan-columns"><section class="plan-day"><div class="plan-budget-panel">
        <h2>${safe(t("Этот день"))}${day.rest_provisional ? ` <span class="plan-rest">${safe(t("Выходной от курсов"))}</span>` : ""}</h2>
        <div class="plan-capacity" aria-label="${safe(t("Общая нагрузка"))}"><b>${safe(minutes(summary.total))}</b><span>/ ${safe(minutes(limit))}${day.emergency_approved ? ` · ${safe(t("утверждённое исключение"))}` : ""}</span></div>
        <div class="plan-capacity-track" role="img" aria-label="${safe(t("Использовано по дневному лимиту"))} ${safe(Math.round(summary.total / Math.max(1, limit) * 100))}%"><span style="width:${Math.min(100, Math.round(summary.total / Math.max(1, limit) * 100))}%"></span></div>
        <div class="plan-budget"><span>${safe(t("Календарь"))}<b>${safe(minutes(day.calendar_minutes))}</b></span><span>${safe(t("Подготовка к урокам"))}<b>${safe(minutes(day.lesson_prep_minutes))}</b></span>
          <span>${safe(t("Резерв на дела дня"))}<b>${safe(minutes(day.all_day_assumed_minutes))}</b></span><span>${safe(t("Кубики"))}<b>${safe(minutes(summary.planned))}</b></span>
          ${day.weekly_review ? `<span>${safe(t("Разбор следующей недели"))}<b>${safe(minutes(day.weekly_review.minutes))}</b></span>` : ""}
          <span>${safe(t("Свободно по лимиту"))}<b>${safe(minutes(summary.free))}</b></span></div></div>
        ${summary.total > limit ? `<p class="plan-error">${safe(t("Лимит превышен — проверьте утверждённое исключение."))}</p>` : ""}
        <div class="plan-day-outcome-panel"><h3>${safe(t("Итог дня"))}</h3>
          <div class="plan-day-result"><span>${safe(t("Выполнено блоков"))}<b>${doneCount} ${safe(t("из"))} ${cubes.length}</b></span>
          <span>${safe(t("Курсы (план)"))}<b>${safe(minutes(day.planned_development_minutes ?? summary.planned))}</b></span>
          <span>${safe(t("Готово по кубикам"))}<b>${safe(minutes(summary.credited))}</b></span>
          <span>${safe(t("Фактически затрачено"))}<b>${day.reported && day.reported_actual_minutes == null ? safe(t("не указано")) : safe(minutes(day.reported ? day.reported_actual_minutes : summary.actual))}</b></span>
          <span>${safe(t("Осталось в кубиках"))}<b>${safe(minutes(summary.unfinished))}</b></span>
          <span>${safe(t("Резерв дня"))}<b>${safe(minutes(summary.free))}</b></span></div></div>
        ${day.reported ? `<p class="plan-early-note">${safe(t("Отчётный день сохранён: прошлые кубики не переносятся задним числом."))}</p>` : ""}
        ${day.historical_unreported ? `<p class="plan-warning">${safe(t("Прошлый день без отчёта: запланированные часы не считаются выполненными."))}</p>` : ""}
        ${day.secondary_early_prep_exception ? `<p class="plan-early-note">${safe(t("Сегодня также есть один короткий кубик ранней подготовки второго проекта. Основной фокус остаётся прежним."))}</p>` : ""}
        ${weekHtml(p, day, unavailable)}
        <details class="plan-day-events"><summary>${safe(t("Уроки и события"))}</summary>${events.length || live.length ? `<ul class="plan-events">${events.map(e => `<li><time>${safe(displayTime(e))}</time><span>${safe(e.title || e.summary || t("Событие"))}</span></li>`).join("")}
          ${live.map(e => `<li><time>${safe(e.effective_start || e.start || t("время уточняется"))}</time><span>${safe(e.title || t("Эфир вебинара"))} · ${safe(minutes(e.duration_minutes))}</span></li>`).join("")}</ul>` : `<p class="plan-muted">${safe(t("На эту дату событий нет в сохранённом снимке календаря."))}</p>`}
        <p class="plan-source">${safe(t("Календарь"))}: ${safe(p.calendar?.fetched_at ? new Date(p.calendar.fetched_at).toLocaleString() : t("дата обновления неизвестна"))}</p></details>
        ${proposalHtml(p, day, unavailable)}
        ${recoveryHtml(p, day)}
      </section><section class="plan-work">
        <div class="plan-hero"><h2>${safe(t("Главный фокус дня"))}</h2>
          <p class="plan-focus">${focusTask ? `<b>${safe(focusTask.title)}</b><span>${safe(t("Остаток, предварительно"))}: ${safe(minutes(taskRemaining(focusTask, p)))}</span><span>${safe(t("Срок"))}: ${safe(focusTask.due || t("не указан"))}${focusTask.due_status === "inferred_provisional" ? ` · ${safe(t("расчётный"))}` : ""}</span>` : safe(t("На день не назначен фокус"))}</p>
          <div class="plan-projects">${projects.map((x, i) => {
            const project = ctx.state.projects?.find(q => q.id === x.project_id);
            const name = project?.name || x.course || t("Проект");
            const isPrimary = x.course === p.settings?.priority_course;
            return `<span class="plan-project ${isPrimary ? "is-primary" : "is-secondary"}"><b>${safe(name)}</b><small>${safe(isPrimary ? t("главный проект") : t("ранняя подготовка"))}${isPrimary && p.settings?.priority_until ? ` · ${safe(t("до"))} ${safe(p.settings.priority_until)}` : ""}</small></span>`;
          }).join("")}</div></div>
        <h2 class="plan-work-title">${safe(t("Рабочие блоки"))}</h2>
        ${cubes.length ? `<ol class="plan-cubes">${cubes.map(c => cubeHtml(c, p, unavailable)).join("")}</ol>` : `<p class="plan-muted">${safe(t("Кубиков на выбранный день нет. Невыполненные материалы остаются в плане."))}</p>`}
      </section></div>
      ${upcomingHtml(p)}
      <section class="plan-detail"><div><h2>${safe(t("Остаток и материалы"))}</h2><p>${safe(t("Уточняйте остаток по каждой задаче после реальной работы; оценка из импорта при этом сохраняется."))}</p></div>
        ${horizonHtml(p)}
        <div class="plan-task-select"><label>${safe(t("Задача"))}<select id="plan-task-select">${allTasks.map(x => `<option value="${safe(x.id)}" ${x.id === task?.id ? "selected" : ""}>${safe(x.title)}</option>`).join("")}</select></label></div>
        ${task ? taskHtml(task, p, ctx.state, selectedDate, unavailable) : ""}</section>
      <footer class="plan-foot">${safe(t("План — прогноз, а не обязательство. Сохранённый прогресс не меняет дату и часы, пока вы не запустите пересчёт."))}${latest ? ` · ${safe(t("Версия плана"))} ${safe(latest.version ?? "")}` : ""}</footer>
    </div>`;
  }
  function timelineHtml(p, cubes, events, live) {
    const segments = [
      ...cubes.map(c => ({ start: clockMinute(c.start), end: clockMinute(c.end), title: c.title, kind: "cube" })),
      ...events.filter(e => !e.all_day).map(e => ({ start: clockMinute(e.start), end: clockMinute(e.end), title: e.title || e.summary, kind: "event" })),
      ...live.map(e => { const start = clockMinute(e.effective_start || e.start); return {
        start, end: start == null ? null : start + numeric(e.duration_minutes), title: e.title || t("Эфир вебинара"), kind: "event",
      }; }),
    ].filter(x => x.start != null && x.end != null && x.end > x.start);
    const first = Math.min(8 * 60, ...segments.map(x => x.start));
    const last = Math.max(22 * 60, ...segments.map(x => x.end));
    const min = Math.max(0, Math.floor(first / 60) * 60), max = Math.min(24 * 60, Math.ceil(last / 60) * 60);
    const span = Math.max(60, max - min);
    const mark = value => `${((value - min) / span * 100).toFixed(3)}%`;
    const ticks = [];
    for (let n = min; n <= max; n += 60) ticks.push(`<span style="left:${mark(n)}">${String(Math.floor(n / 60)).padStart(2, "0")}:00</span>`);
    const clock = value => `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
    const bars = segments.map(x => `<span class="plan-time-block is-${x.kind}" style="left:${mark(x.start)};width:${((x.end - x.start) / span * 100).toFixed(3)}%" data-time="${clock(x.start)}–${clock(x.end)}" title="${safe(x.title)} · ${safe(minutes(x.end - x.start))}"><span>${safe(x.title)}</span></span>`).join("");
    return `<div class="plan-timeline-scroll"><div class="plan-timeline-canvas"><div class="plan-time-ticks">${ticks.join("")}</div>
      <div class="plan-time-track">${bars || `<span class="plan-time-empty">${safe(t("На этот день нет отрезков времени"))}</span>`}</div></div></div>`;
  }
  function upcomingHtml(p) {
    const week = (p.result?.weekly_reviews || []).filter(x => x.date && x.date > selectedDate && x.status !== "done")
      .sort((a, b) => a.date.localeCompare(b.date))[0];
    const live = (p.live_events || []).filter(x => x.date && x.date > selectedDate)
      .sort((a, b) => a.date.localeCompare(b.date))[0];
    return `<section class="plan-upcoming"><h2>${safe(t("Предстоящие"))}</h2><div class="plan-upcoming-list">
      ${week ? `<div class="plan-upcoming-item is-week"><strong>${safe(dateText(week.date))}</strong><span>${safe(week.title || t("Спланировать следующую неделю с агентом"))}</span><b>${safe(minutes(week.minutes))}</b></div>` : ""}
      ${live ? `<div class="plan-upcoming-item is-live"><strong>${safe(dateText(live.date))}</strong><span>${safe(live.title || t("Эфир вебинара"))}</span><b>${safe(live.effective_start || live.start || t("время уточняется"))}${(live.effective_start || live.start) && live.duration_minutes ? `–${safe(`${String(Math.floor((clockMinute(live.effective_start || live.start) + numeric(live.duration_minutes)) / 60)).padStart(2, "0")}:${String((clockMinute(live.effective_start || live.start) + numeric(live.duration_minutes)) % 60).padStart(2, "0")}`)}` : ""}</b></div>` : ""}
      ${!week && !live ? `<p>${safe(t("Подтверждённых дат впереди нет; недатированные эфиры показаны в оценке горизонта."))}</p>` : ""}
    </div></section>`;
  }
  function remoteChanged() {
    const active = document.activeElement;
    if (root.contains(active) && /^(INPUT|SELECT|TEXTAREA)$/.test(active?.tagName || "")) {
      remotePending = true;
      if (!root.querySelector(".plan-remote-notice")) {
        const note = document.createElement("p");
        note.className = "plan-remote-notice";
        note.setAttribute("role", "status");
        note.textContent = t("План обновился в Work. Завершите ввод — новые данные появятся без потери набранного текста.");
        root.prepend(note);
      }
    } else render();
  }
  root.addEventListener("focusout", () => setTimeout(() => {
    if (remotePending && !root.contains(document.activeElement)) render();
  }, 0));
  function cubeHtml(c, p, disabled) {
    const progress = p.completed_cubes?.[c.id];
    const percent = progress?.percent ?? c.progress_percent ?? 0;
    const actual = progress?.actual_minutes ?? c.actual_minutes ?? "";
    const choices = [...new Set([0, 25, 50, 75, 100, percent])].sort((a, b) => a - b);
    return `<li class="plan-cube" data-cube-id="${safe(c.id)}"><div class="plan-cube-time"><time>${safe(c.start)}–${safe(c.end)}</time><small class="${c.is_remainder ? "plan-remainder" : ""}">${c.is_remainder ? `${safe(t("Остаток"))} · ` : ""}${safe(minutes(c.minutes))}${c.locked ? ` · ${safe(t("закреплён"))}` : ""}</small></div>
      <div class="plan-cube-body"><h3>${safe(c.title)}</h3><p>${safe(c.output)}</p>
        ${Array.isArray(c.steps) && c.steps.length ? `<ol class="plan-cube-steps">${c.steps.map(step => `<li>${safe(step)}</li>`).join("")}</ol>` : ""}
        <div class="plan-cube-controls">
        <label>${safe(t("Готовность"))}<select data-field="percent" ${disabled ? "disabled" : ""}>${choices.map(v => `<option value="${safe(v)}" ${Number(v) === Number(percent) ? "selected" : ""}>${safe(v)}%</option>`).join("")}</select></label>
        <label>${safe(t("Факт, мин"))}<input data-field="actual" type="number" min="0" step="1" value="${safe(actual)}" placeholder="—" ${disabled ? "disabled" : ""}></label>
        <button type="button" data-plan-action="save-cube" ${disabled ? "disabled" : ""}>${safe(t("Сохранить"))}</button><button type="button" data-plan-action="done-cube" ${disabled ? "disabled" : ""}>${safe(t("Готово"))}</button></div>
        <details class="plan-place"><summary>${safe(t("Время и закрепление"))}</summary><div><label>${safe(t("Дата"))}<input data-field="place-date" type="date" min="${safe(p.settings?.start)}" max="${safe(p.settings?.end)}" value="${safe(c.date)}"></label>
          <label>${safe(t("Начало"))}<input data-field="place-time" type="time" value="${safe(c.start)}"></label><label><input data-field="lock" type="checkbox" ${c.locked ? "checked" : ""}>${safe(t("Закрепить"))}</label>
          <button type="button" data-plan-action="place-cube" ${disabled ? "disabled" : ""}>${safe(t("Сохранить место"))}</button></div></details></div></li>`;
  }
  function taskHtml(task, p, st, day, disabled) {
    const remaining = taskRemaining(task, p), outcome = (p.day_outcomes || []).find(x => x.task_id === task.id && x.date === day);
    const item = st.items?.find(x => x.id === task.work_item_id);
    return `<div class="plan-task-editor"><div class="plan-task-facts"><span>${safe(t("Предварительная оценка"))}: ${safe(minutes(task.estimate_minutes?.central))}</span>
      <span>${safe(t("Остаток"))}: <b>${safe(minutes(remaining))}</b></span><span>${safe(t("Диапазон оценки"))}: ${safe(minutes(task.estimate_minutes?.lower))}–${safe(minutes(task.estimate_minutes?.upper))}</span>
      <span>${safe(t("Срок"))}: ${safe(task.due || t("не указан"))}${task.due_status === "inferred_provisional" ? ` · ${safe(t("расчётный, не согласован"))}` : ""}</span>
      ${task.early_prep ? `<span>${safe(t("Разрешена ранняя лёгкая подготовка"))}</span>` : ""}</div>
      <div class="plan-outcome"><label>${safe(t("Осталось, мин"))}<input id="plan-remaining" type="number" min="0" step="1" value="${safe(outcome?.remaining_minutes ?? remaining)}" ${disabled ? "disabled" : ""}></label>
        <label class="plan-check"><input id="plan-task-done" type="checkbox" ${outcome?.done ? "checked" : ""} ${disabled ? "disabled" : ""}>${safe(t("Задача готова"))}</label>
        <label class="plan-note">${safe(t("Итог дня"))}<input id="plan-day-note" type="text" maxlength="500" value="${safe(outcome?.note || "")}" placeholder="${safe(t("Что сделано, что мешает"))}" ${disabled ? "disabled" : ""}></label>
        <button type="button" data-plan-action="save-outcome" ${disabled ? "disabled" : ""}>${safe(t("Сохранить итог"))}</button></div>
      <div class="plan-resources"><h3>${safe(t("Материалы и рабочее место"))}</h3>
        ${p.settings?.course_roots?.[task.course] ? `<p class="plan-course-root">${safe(t("Папка курса"))}: <code>${safe(p.settings.course_roots[task.course])}</code></p>` : ""}
        ${task.resources?.length ? `<ul>${task.resources.map(r => `<li>${r.type === "url" && validUrl(r.url) ? `<a href="${safe(validUrl(r.url))}" target="_blank" rel="noopener noreferrer">${safe(r.label || r.url)}</a>` : `<span>${safe(r.label || t("Локальный файл"))}: <code>${safe(r.path || "")}</code></span>`}</li>`).join("")}</ul>` : `<p class="plan-muted">${safe(t("Материалы ещё не прикреплены."))}</p>`}
        ${item ? `<button type="button" data-plan-action="workspace">${safe(t("Открыть рабочее место"))}</button>` : ""}
        <div class="plan-add-resource"><label>${safe(t("Тип"))}<select id="plan-resource-type"><option value="url">URL</option><option value="local_file">${safe(t("Локальный файл"))}</option></select></label>
          <label>${safe(t("Название"))}<input id="plan-resource-label" type="text" maxlength="160" placeholder="${safe(t("Краткое название"))}"></label>
          <label class="plan-resource-target">${safe(t("Адрес или путь"))}<input id="plan-resource-target" type="text" placeholder="https://…"></label>
          <button type="button" data-plan-action="add-resource" ${disabled ? "disabled" : ""}>${safe(t("Добавить материал"))}</button></div></div></div>`;
  }
  function horizonHtml(p) {
    const totals = p.result?.totals || {};
    const demand = totals.demand_hours || {};
    const ideal = totals.ideal_development_capacity_hours;
    if (demand.central == null && ideal == null) return "";
    const lower = numeric(demand.lower), central = numeric(demand.central), upper = numeric(demand.upper);
    const shortfall = ideal == null ? null : Math.max(0, lower - numeric(ideal));
    return `<section class="plan-horizon" aria-label="${safe(t("Оценка горизонта"))}"><strong>${safe(t("Оценка загрузки до конца периода"))}</strong>
      <span>${safe(t("Разработка"))}: ${safe(central.toLocaleString("ru-RU"))} ${safe(t("ч, ориентир"))}${upper ? ` · ${safe(t("диапазон"))} ${safe(lower.toLocaleString("ru-RU"))}–${safe(upper.toLocaleString("ru-RU"))} ${safe(t("ч"))}` : ""}</span>
      ${ideal != null ? `<span>${safe(t("Идеальная ёмкость при текущем лимите"))}: ${safe(numeric(ideal).toLocaleString("ru-RU"))} ${safe(t("ч"))}</span>` : ""}
      ${shortfall > 0 ? `<span class="plan-horizon-gap">${safe(t("Даже нижняя оценка превышает идеальную ёмкость на"))} ${safe(shortfall.toLocaleString("ru-RU"))} ${safe(t("ч"))}. ${safe(t("Нужны пересмотр объёма или сроков; дополнительные часы — отдельное решение."))}</span>` : ""}
      ${numeric(p.result?.unscheduled_live_count) ? `<span class="plan-horizon-gap">${safe(t("Эфиры без даты, вне дневной сетки"))}: ${safe(p.result.unscheduled_live_count)} · ${safe(minutes(p.result.unscheduled_live_minutes))}</span>` : ""}
      ${p.result?.needs_detail_review ? `<span class="plan-horizon-gap">${safe(t("Подробные шаги после пересчёта требуют сверки с агентом"))}: ${safe(p.result.orphaned_detail_count || 0)}</span>` : ""}
      ${(() => { const next = (p.result?.weekly_reviews || []).find(x => x.status !== "done" && x.date && x.date >= selectedDate);
        return next ? `<span>${safe(t("Ближайший разбор недели"))}: ${safe(next.date)} · ${safe(minutes(next.minutes))}</span>` : ""; })()}
      ${totals.tasks_missing_due ? `<span>${safe(t("Материалов с дефицитом к сроку"))}: ${safe(totals.tasks_missing_due)}</span>` : ""}</section>`;
  }
  function weekHtml(p, day, disabled) {
    const review = day.weekly_review && (p.result?.weekly_reviews || []).find(x => x.id === day.weekly_review.id);
    if (!review) return "";
    const done = p.weekly_review_done?.[review.id] === true || review.status === "done";
    return `<div class="plan-week-review" data-weekly-id="${safe(review.id)}"><h3>${safe(review.title || t("Спланировать следующую неделю с агентом"))}</h3>
      <p>${safe(review.start || day.weekly_review.start || t("время уточняется"))}${review.end || day.weekly_review.end ? `–${safe(review.end || day.weekly_review.end)}` : ""} · ${safe(minutes(review.minutes))} ${safe(t("в пределах дневного лимита"))}</p>
      <ul>${(review.checklist || []).map(item => `<li>${safe(item.label)}</li>`).join("")}</ul>
      <label><input type="checkbox" data-plan-action="week-done" ${done ? "checked" : ""} ${disabled ? "disabled" : ""}>${safe(t("Разбор проведён"))}</label>
      <small>${safe(t("Итоги обсудите с агентом; сохранение отметки не запускает модель и не пересчитывает план."))}</small></div>`;
  }
  function proposalHtml(p, day, disabled) {
    const proposal = (p.result?.emergency_proposals || []).find(x => x.date === day.date);
    if (!proposal || day.emergency_approved) return "";
    const max = Math.min(480, 300 + numeric(proposal.maximum_extra_minutes));
    return `<div class="plan-proposal"><h3>${safe(t("Отдельное предложение на перегруженный день"))}</h3>
      <p>${safe(proposal.reason || t("Близкий подтверждённый срок и день без уроков."))}</p>
      <p>${safe(t("В обычной сетке остаётся предел 5 часов. Исключение требует отдельного утверждения."))}</p>
      <div><label>${safe(t("Предел, мин"))}<input id="plan-emergency-minutes" type="number" min="301" max="${safe(max)}" step="1" value="${safe(max)}"></label>
        <label class="plan-emergency-reason">${safe(t("Причина"))}<input id="plan-emergency-reason" type="text" maxlength="300" value="${safe(proposal.reason || "")}"></label>
        <button type="button" data-plan-action="approve-capacity" ${disabled ? "disabled" : ""}>${safe(t("Утвердить исключение"))}</button></div></div>`;
  }
  function recoveryHtml(p, day) {
    const recovery = p.result?.recovery_proposals;
    if (!recovery || recovery.after_date !== day.date) return "";
    const ordinary = recovery.ordinary_free_days || [];
    return `<div class="plan-recovery"><h3>${safe(t("Варианты после итога дня"))}</h3>
      <p>${safe(t("Агент предложил свободные дни. План меняется только после явного пересчёта."))}</p>
      ${ordinary.length ? `<ul>${ordinary.slice(0, 5).map(x => `<li>${safe(x.date)} · ${safe(minutes(x.free_minutes))}</li>`).join("")}</ul>` : `<p>${safe(t("Свободных дней в текущем горизонте нет."))}</p>`}</div>`;
  }
  root.addEventListener("change", e => {
    if (e.target.id === "plan-date") { selectedDate = e.target.value; selectedTaskId = null; render(); }
    if (e.target.id === "plan-task-select") { selectedTaskId = e.target.value; render(); }
    if (e.target.id === "plan-resource-type") root.querySelector("#plan-resource-target").placeholder = e.target.value === "url" ? "https://…" : t("Путь к файлу");
    if (e.target.dataset.planAction === "week-done") {
      const id = e.target.closest("[data-weekly-id]")?.dataset.weeklyId;
      if (id) void run("weekly_review_done", { id, done: e.target.checked });
    }
  });
  root.addEventListener("click", async e => {
    const button = e.target.closest("[data-plan-action]");
    if (!button || button.disabled) return;
    const action = button.dataset.planAction, p = planning();
    if (!p) return;
    if (["previous", "next", "today"].includes(action)) {
      const value = action === "today" ? todayIn(p.settings?.timezone) : dateShift(selectedDate, action === "previous" ? -1 : 1);
      selectedDate = clampDate(value, p.settings.start, p.settings.end); selectedTaskId = null; render(); return;
    }
    if (action === "retry") {
      busy = true; error = ""; render();
      try { await ctx.retryDraft(); pending = null; message = t("Запрос подтверждён Work"); }
      catch (cause) { error = t("Повтор не подтверждён: {error}", { error: cause.message }); pending = await ctx.pendingDraft(); }
      finally { busy = false; render(); }
      return;
    }
    if (action === "discard") {
      busy = true; error = ""; render();
      try { await ctx.discardDraft(); pending = null; message = t("Спорный запрос отменён; показаны подтверждённые данные Work"); }
      catch (cause) { error = t("Не получилось отменить запрос: {error}", { error: cause.message }); pending = await ctx.pendingDraft(); }
      finally { busy = false; render(); }
      return;
    }
    if (action === "replan") return run("recalculate", { from_date: selectedDate,
      refresh_calendar: !!root.querySelector("#plan-refresh-calendar")?.checked,
      reason: "manual Work plan update" });
    if (action === "approve-capacity") {
      const minutesValue = Number(root.querySelector("#plan-emergency-minutes")?.value);
      const reason = root.querySelector("#plan-emergency-reason")?.value.trim();
      if (!Number.isInteger(minutesValue) || minutesValue <= 300 || minutesValue > 480 || !reason) {
        error = t("Укажите предел 301–480 минут и причину исключения."); render(); return;
      }
      return run("capacity_override", { date: selectedDate, minutes: minutesValue, reason, approved: true });
    }
    const cube = button.closest("[data-cube-id]");
    if (["save-cube", "done-cube", "place-cube"].includes(action) && cube) {
      const id = cube.dataset.cubeId;
      if (action === "place-cube") {
        const date = cube.querySelector('[data-field="place-date"]').value;
        const start = cube.querySelector('[data-field="place-time"]').value;
        const locked = cube.querySelector('[data-field="lock"]').checked;
        const original = (p.result?.cubes || []).find(x => x.id === id);
        if (!locked && (date !== original?.date || start !== original?.start)) {
          error = t("Чтобы перенести кубик, сначала отметьте «Закрепить». Без закрепления можно только снять прежнее."); render(); return;
        }
        return run("place_cube", { cube_id: id, date, start, locked });
      }
      const value = action === "done-cube" ? 100 : Number(cube.querySelector('[data-field="percent"]').value);
      const actualField = cube.querySelector('[data-field="actual"]').value;
      return run("cube_progress", { cube_id: id, percent: value,
        ...(actualField === "" ? {} : { actual_minutes: Number(actualField) }) });
    }
    const task = getTask(p, currentDay(p));
    if (!task) return;
    if (action === "save-outcome") {
      const value = root.querySelector("#plan-remaining").value;
      const done = root.querySelector("#plan-task-done").checked || (value !== "" && Number(value) === 0);
      return run("day_outcome", { task_id: task.id, date: selectedDate,
        remaining_minutes: done ? 0 : value === "" ? null : Number(value), done,
        note: root.querySelector("#plan-day-note").value.trim() });
    }
    if (action === "add-resource") {
      const type = root.querySelector("#plan-resource-type").value;
      const label = root.querySelector("#plan-resource-label").value.trim();
      const target = root.querySelector("#plan-resource-target").value.trim();
      if (!label || !target || (type === "url" && !validUrl(target))) {
        error = t("Укажите название и корректный адрес или путь."); render(); return;
      }
      return run("resource", { task_id: task.id, resource: { type, label, ...(type === "url" ? { url: target } : { path: target }) } });
    }
    if (action === "workspace") {
      const item = ctx.state.items?.find(x => x.id === task.work_item_id);
      if (item) await ctx.openWorkspace(item);
    }
  });
  void refreshPending().catch(cause => { error = cause.message; render(); });
  return { render, remoteChanged };
}
