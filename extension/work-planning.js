import { esc, ic } from "./work-icons.js";
import { t } from "./i18n.js";
import { startCubeTimer, pauseCubeTimer, stopCubeTimer, cubeTimerState, recordedCubeMinutes } from "./work-cube-timer.js";
import { timeClient, formatTime } from "./time-client.js";

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

export function builderDaySummary(day, allCubes, completed = {}, budget = 300) {
  const selected = allCubes.filter(c => c.manual || c.locked ||
    numeric(completed[c.id]?.percent ?? c.progress_percent) > 0 ||
    numeric(completed[c.id]?.actual_minutes ?? c.actual_minutes) > 0);
  const selectedMinutes = day?.builder_selected_minutes ?? selected.reduce((sum, c) => sum + numeric(c.minutes), 0);
  const occupied = day?.builder_total_work_minutes ??
    numeric(day?.fixed_minutes) + numeric(day?.weekly_review_minutes) + selectedMinutes;
  const available = day?.builder_available_minutes ?? Math.max(0, budget - occupied);
  return { selected, ...daySummary({ ...day, total_work_minutes: occupied,
    unused_capacity_minutes: available }, selected, completed, budget) };
}

export async function preserveTimedCubeBeforeSegment(planning, segmentId, { timerState, stopTimer, recordedMinutes, action }) {
  const active = (planning?.result?.cubes || []).find(cube => cube.segment_id === segmentId &&
    ["running", "paused"].includes(timerState(cube)?.status));
  if (!active) return false;
  await stopTimer(active);
  const recorded = Math.max(0, Math.round(numeric(await recordedMinutes(active))));
  const saved = Math.max(0, numeric(planning?.completed_cubes?.[active.id]?.actual_minutes ?? active.actual_minutes));
  if (recorded > saved) {
    const percent = numeric(planning?.completed_cubes?.[active.id]?.percent ?? active.progress_percent);
    await action("cube_progress", { cube_id: active.id, percent, actual_minutes: recorded });
  }
  return true;
}

export function taskRemaining(task, planning) {
  const canonical = (planning?.result?.tasks || []).find(row => row.id === task.id || row.task_id === task.id);
  if (canonical?.remaining_minutes != null) return Math.max(0, numeric(canonical.remaining_minutes));
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
  let selectedDate = null, selectedTaskId = null, selectedCubeId = null, poolTaskId = null, selectedSegmentId = null;
  let energyPreview = null, poolIntent = false, busy = false, message = "", error = "", pending = null, remotePending = false;
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
  async function run(action, data, before = null) {
    if (busy || pending) return;
    busy = true; error = ""; message = t("Сохраняем в Work…"); render();
    try {
      if (before) await before();
      const response = await ctx.action(action, data);
      const released = response?.state?.planning?.result?.released_reservations?.length || 0;
      message = released ? t("Этап обновлён; {count} свободных кубиков возвращено в пул.", { count: released }) :
        action === "recalculate" ? t("План пересчитан и сохранён") : t("Изменение сохранено в Work");
    } catch (cause) {
      error = cause?.status === 409 ? t("Данные Work изменились. Запрос сохранён для безопасного повтора.") :
        t("Не удалось сохранить: {error}", { error: cause?.message || String(cause) });
      message = "";
    } finally {
      busy = false;
      if (action === "day_budget") energyPreview = null;
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
    const day = currentDay(p), allDayCubes = (p.result?.cubes || []).filter(c => c.date === selectedDate)
      .sort((a, b) => String(a.start).localeCompare(String(b.start)));
    const savedBudget = day.budget_minutes ?? day.day_limit_minutes ?? p.settings?.day_limit_minutes ?? 300;
    const initialSummary = builderDaySummary(day, allDayCubes, p.completed_cubes, savedBudget);
    const cubes = initialSummary.selected.sort((a, b) => numeric(a.order ?? 9999) - numeric(b.order ?? 9999));
    if (!cubes.some(c => c.id === selectedCubeId)) selectedCubeId = null;
    const task = getTask(p, day), allTasks = p.tasks || [];
    const limit = energyPreview?.date === selectedDate ? energyPreview.minutes : savedBudget;
    const builderTotal = initialSummary.total;
    const builderAvailable = energyPreview?.date === selectedDate ? Math.max(0, limit - builderTotal) :
      day.builder_available_minutes ?? Math.max(0, savedBudget - builderTotal);
    const summary = daySummary({ ...day, total_work_minutes: builderTotal,
      unused_capacity_minutes: builderAvailable }, cubes, p.completed_cubes, limit);
    const editable = selectedDate >= todayIn(p.settings?.timezone) && !busy && !pending;
    const pool = p.result?.task_pool || [];
    if (!pool.some(item => item.task_id === poolTaskId)) poolTaskId = pool[0]?.task_id || null;
    const poolTask = pool.find(item => item.task_id === poolTaskId);
    if (!poolTask?.segments?.some(item => item.id === selectedSegmentId)) selectedSegmentId = null;
    const focusTask = cubes.length ? allTasks.find(item => item.id === day.focus_task_id) || allTasks.find(item => item.id === cubes[0].task_id) :
      poolIntent ? allTasks.find(item => item.id === poolTaskId) : null;
    const focusSegment = cubes.length ? (p.task_segments || []).find(item => item.id === day.focus_segment_id) :
      poolIntent ? poolTask?.segments?.find(item => item.id === selectedSegmentId) : null;
    const focusSegmentInPool = focusSegment && pool.flatMap(item => item.segments || []).find(item => item.id === focusSegment.id);
    const focusSelectedMinutes = focusSegment ? cubes.filter(item => item.segment_id === focusSegment.id)
      .reduce((sum, item) => sum + numeric(item.minutes), 0) : 0;
    const priorityActive = !!p.settings?.priority_course && (!p.settings.priority_until || selectedDate < p.settings.priority_until);
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
        <div><h1>${safe(t("Конструктор дня"))}</h1><p>${safe(t("Выберите энергию и кубики; уроки уже учтены."))}</p></div>
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
        ${timelineHtml(p, events, live)}</section>
      ${!p.calendar?.configured || p.calendar?.stale ? `<p class="plan-warning" role="status">${safe(t("Календарь не подтверждён как актуальный. Свободные часы требуют проверки."))}</p>` : ""}
      <div class="plan-builder-layout"><div class="plan-columns"><section class="plan-day"><div class="plan-budget-panel">
        <h2>${safe(t("Этот день"))}${day.rest_provisional ? ` <span class="plan-rest">${safe(t("Выходной от курсов"))}</span>` : ""}</h2>
        <div class="plan-energy-control"><label for="plan-energy">${safe(t("Энергия дня"))} <strong class="plan-energy-value">${safe(Number((limit / 60).toFixed(2)).toLocaleString("ru-RU"))} ${safe(t("энергии"))}</strong></label>
          <input id="plan-energy" class="plan-energy-input" type="range" min="0" max="480" step="15" value="${safe(limit)}" ${editable ? "" : "disabled"} aria-label="${safe(t("Энергия дня"))}">
          <div class="plan-energy-scale"><span>0</span><span>4</span><span>8</span></div></div>
        <div class="plan-capacity" aria-label="${safe(t("Общая нагрузка"))}"><b>${safe(minutes(summary.total))}</b><span>/ ${safe(minutes(limit))}${day.emergency_approved ? ` · ${safe(t("утверждённое исключение"))}` : ""}</span></div>
        ${energyBarHtml(day, summary, limit)}
        <div class="plan-budget"><span>${safe(t("Календарь"))}<b>${safe(minutes(day.calendar_minutes))}</b></span><span>${safe(t("Подготовка к урокам"))}<b>${safe(minutes(day.lesson_prep_minutes))}</b></span>
          <span>${safe(t("Резерв на дела дня"))}<b>${safe(minutes(day.all_day_assumed_minutes))}</b></span><span>${safe(t("Кубики"))}<b>${safe(minutes(summary.planned))}</b></span>
          ${day.weekly_review ? `<span>${safe(t("Разбор следующей недели"))}<b>${safe(minutes(day.weekly_review.minutes))}</b></span>` : ""}
          <span>${safe(t("Свободно по лимиту"))}<b class="plan-free-value">${safe(minutes(summary.free))}</b></span></div>
        <p class="plan-raised" ${limit > 300 ? "" : "hidden"}>${safe(t("Повышенная нагрузка выбрана вручную."))}</p>
        ${prepHtml(day, p, editable)}</div>
        ${summary.total > limit ? `<p class="plan-error">${safe(t("Выбранный бюджет меньше занятого времени. Уроки и записанный факт сохранены."))}</p>` : ""}
        <div class="plan-day-outcome-panel"><h3>${safe(t("Итог дня"))}</h3>
          <div class="plan-day-result"><span>${safe(t("Выполнено блоков"))}<b>${doneCount} ${safe(t("из"))} ${cubes.length}</b></span>
          <span>${safe(t("Курсы (план)"))}<b>${safe(minutes(day.planned_development_minutes ?? summary.planned))}</b></span>
          <span>${safe(t("Готово по кубикам"))}<b>${safe(minutes(summary.credited))}</b></span>
          <span>${safe(t("Фактически затрачено"))}<b>${day.reported && day.reported_actual_minutes == null ? safe(t("не указано")) : safe(minutes(day.reported ? day.reported_actual_minutes : summary.actual))}</b></span>
          <span>${safe(t("Осталось в кубиках"))}<b>${safe(minutes(summary.unfinished))}</b></span>
          <span>${safe(t("Резерв дня"))}<b>${safe(minutes(summary.free))}</b></span></div></div>
        ${day.reported && selectedDate < todayIn(p.settings?.timezone) ? `<p class="plan-early-note">${safe(t("Отчётный день сохранён: прошлые кубики не переносятся задним числом."))}</p>` : ""}
        ${day.historical_unreported ? `<p class="plan-warning">${safe(t("Прошлый день без отчёта: запланированные часы не считаются выполненными."))}</p>` : ""}
        ${day.secondary_early_prep_exception ? `<p class="plan-early-note">${safe(t("Сегодня также есть один короткий кубик ранней подготовки второго проекта. Основной фокус остаётся прежним."))}</p>` : ""}
        ${weekHtml(p, day, unavailable)}
        <details class="plan-day-events"><summary>${safe(t("Уроки и события"))}</summary>${events.length || live.length ? `<ul class="plan-events">${events.map(e => `<li><time>${safe(displayTime(e))}</time><span>${safe(e.title || e.summary || t("Событие"))}</span></li>`).join("")}
          ${live.map(e => `<li><time>${safe(e.effective_start || e.start || t("время уточняется"))}</time><span>${safe(e.title || t("Эфир вебинара"))} · ${safe(minutes(e.duration_minutes))}</span></li>`).join("")}</ul>` : `<p class="plan-muted">${safe(t("На эту дату событий нет в сохранённом снимке календаря."))}</p>`}
        <p class="plan-source">${safe(t("Календарь"))}: ${safe(p.calendar?.fetched_at ? new Date(p.calendar.fetched_at).toLocaleString() : t("дата обновления неизвестна"))}</p></details>
        ${recoveryHtml(p, day)}
      </section><section class="plan-work">
        <div class="plan-hero"><h2>${safe(t("Главный фокус дня"))}</h2>
          <p class="plan-focus">${focusTask ? `<b>${safe(focusSegment?.title || focusTask.title)}</b>${focusSegment ? `<span>${safe(focusTask.title)}</span><span>${safe(t("Осталось в этапе"))}: ${safe(minutes(focusSegmentInPool?.remaining_minutes ?? 0))}</span><span>${safe(t("В дне"))}: ${safe(minutes(focusSelectedMinutes))}</span>` : `<span>${safe(t("Остаток, предварительно"))}: ${safe(minutes(taskRemaining(focusTask, p)))}</span>`}` : `<b>${safe(t("Соберите день из кубиков"))}</b><span>${safe(t("Пул ближайших задач доступен рядом."))}</span>`}</p>
          <div class="plan-projects">${projects.map((x, i) => {
            const project = ctx.state.projects?.find(q => q.id === x.project_id);
            const name = project?.name || x.course || t("Проект");
            const isPrimary = x.course === p.settings?.priority_course;
            return `<span class="plan-project ${priorityActive ? isPrimary ? "is-primary" : "is-secondary" : ""}"><b>${safe(name)}</b>${priorityActive ? `<small>${safe(isPrimary ? t("главный проект") : t("ранняя подготовка"))}${isPrimary && p.settings?.priority_until ? ` · ${safe(t("до"))} ${safe(p.settings.priority_until)}` : ""}</small>` : ""}</span>`;
          }).join("")}</div></div>
        <h2 class="plan-work-title">${safe(t("Рабочие блоки"))}</h2>
        <div class="plan-day-cubes" data-plan-drop="day">${cubes.length ? `<ol class="plan-cubes">${cubes.map((c, index) => cubeTileHtml(c, p, index, cubes.length, editable)).join("")}</ol>` : `<p class="plan-empty-day">${safe(t("Пока кубиков нет."))} <strong>${safe(minutes(summary.free))}</strong> ${safe(t("свободной энергии — выберите задачу из пула."))}</p>`}
        ${selectedCubeId ? cubeDetailHtml(cubes.find(c => c.id === selectedCubeId), p, editable) : ""}</div>
      </section></div>${poolHtml(p, pool, poolTask, builderAvailable, editable)}</div>
      ${upcomingHtml(p)}
      <details class="plan-detail"><summary>${safe(t("Остатки, материалы и горизонт"))}</summary><div><p>${safe(t("Уточняйте остаток по каждой задаче после реальной работы; оценка из импорта при этом сохраняется."))}</p></div>
        ${horizonHtml(p)}
        <div class="plan-task-select"><label>${safe(t("Задача"))}<select id="plan-task-select">${allTasks.map(x => `<option value="${safe(x.id)}" ${x.id === task?.id ? "selected" : ""}>${safe(x.title)}</option>`).join("")}</select></label></div>
        ${task ? taskHtml(task, p, ctx.state, selectedDate, unavailable) : ""}</details>
      <footer class="plan-foot">${safe(t("План — прогноз, а не обязательство. Сохранённый прогресс не меняет дату и часы, пока вы не запустите пересчёт."))}${latest ? ` · ${safe(t("Версия плана"))} ${safe(latest.version ?? "")}` : ""}</footer>
    </div>`;
  }
  function timelineHtml(p, events, live) {
    const segments = [
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
  function energyBarHtml(day, summary, limit) {
    const fixed = numeric(day.calendar_minutes) + numeric(day.all_day_assumed_minutes);
    const prep = numeric(day.lesson_prep_minutes), review = numeric(day.weekly_review_minutes);
    const extra = Math.max(0, summary.total - fixed - prep - review - summary.planned);
    const parts = [["calendar", fixed], ["prep", prep], ["review", review], ["course", summary.planned],
      ["actual", extra], ["free", summary.free]];
    const denominator = Math.max(1, limit);
    return `<div class="plan-energy-bar" style="--energy-hours:${Math.max(1, limit / 60)}" role="img" aria-label="${safe(t("Занято и свободно в бюджете дня"))}">
      ${parts.filter(([kind, value]) => value > 0 || kind === "free").map(([kind, value]) => `<span class="is-${kind}" data-minutes="${safe(value)}" style="width:${Math.max(0, Math.min(100, value / denominator * 100)).toFixed(2)}%" title="${safe(minutes(value))}"></span>`).join("")}</div>`;
  }
  function prepTimerCube(item, p) {
    const lesson = (p.calendar?.events || []).find(event => event.id === item.calendar_event_id || event.occurrence_id === item.calendar_event_id);
    return { id: item.id, title: `${t("Подготовка к уроку")}: ${lesson?.title || lesson?.summary || item.lesson_start || ""}`,
      minutes: Number(item.minutes) };
  }
  function prepHtml(day, p, editable) {
    const preparations = day.prep_cubes || [];
    if (!preparations.length) return "";
    return `<div class="plan-preparations"><h3>${safe(t("Подготовка к урокам"))}</h3>
      ${preparations.map(item => {
        const percent = item.progress_percent ?? 0, actual = numeric(item.actual_minutes) > 0 ? item.actual_minutes : "";
        const timer = cubeTimerState(prepTimerCube(item, p));
        return `<div class="plan-prep" data-prep-id="${safe(item.id)}"><div class="plan-prep-head"><b>${safe(t("Перед уроком"))} ${safe(item.lesson_start || "")}</b>
          <output>${safe(item.minutes)} ${safe(t("мин"))}</output></div>
          <input class="plan-prep-range" type="range" min="0" max="120" step="5" value="${safe(item.minutes)}" ${editable ? "" : "disabled"} aria-label="${safe(t("Минуты подготовки"))}">
          ${item.minutes === 0 ? `<button type="button" data-plan-action="restore-prep" ${editable ? "" : "disabled"}>${safe(t("Вернуть подготовку"))}</button>` : ""}
          ${item.minutes > 0 || percent > 0 || actual !== "" ? `<div class="plan-prep-timer"><button type="button" data-plan-action="prep-timer-start" ${editable && item.minutes > 0 && timer.status !== "running" ? "" : "disabled"}>${safe(timer.status === "paused" ? t("Продолжить") : t("Приступить"))}</button>
            <button type="button" data-plan-action="prep-timer-pause" ${timer.status === "running" ? "" : "disabled"}>${safe(t("Пауза"))}</button>
            <button type="button" data-plan-action="prep-timer-stop" ${["running", "paused"].includes(timer.status) ? "" : "disabled"}>${safe(t("Стоп"))}</button>
            <span data-prep-timer-text>${safe(timer.status === "idle" ? t("Таймер не запущен") : `${t("Осталось")}: ${formatTime(timer.remaining_ms)}`)}</span><span data-prep-timer-recorded></span></div>
          <details><summary>${safe(t("Готовность подготовки"))}: ${safe(percent)}%</summary><div class="plan-prep-progress"><select data-field="prep-percent" ${editable ? "" : "disabled"}>${[0,25,50,75,100,percent].filter((x,i,a)=>a.indexOf(x)===i).sort((a,b)=>a-b).map(n=>`<option value="${n}" ${Number(n)===Number(percent)?"selected":""}>${n}%</option>`).join("")}</select>
            <input data-field="prep-actual" type="number" min="0" step="1" value="${safe(actual)}" placeholder="${safe(t("Факт, мин"))}" ${editable ? "" : "disabled"}>
            <button type="button" data-plan-action="save-prep-progress" ${editable ? "" : "disabled"}>${safe(t("Сохранить"))}</button></div></details>` : ""}</div>`;
      }).join("")}</div>`;
  }
  function cubeTileHtml(c, p, index, count, editable) {
    const progress = p.completed_cubes?.[c.id];
    const percent = numeric(progress?.percent ?? c.progress_percent ?? 0);
    const timer = cubeTimerState(c);
    const started = percent > 0 || numeric(progress?.actual_minutes ?? c.actual_minutes) > 0 || ["running", "paused"].includes(timer.status);
    return `<li class="plan-cube-tile ${c.id === selectedCubeId ? "is-selected" : ""}" data-cube-id="${safe(c.id)}" draggable="${editable && !started}">
      <button type="button" class="plan-cube-select" data-plan-action="select-cube" aria-expanded="${c.id === selectedCubeId}"><span class="plan-cube-duration">${safe(minutes(c.minutes))}${c.is_remainder ? ` · ${safe(t("остаток"))}` : ""}</span>
        <span class="plan-cube-title">${safe(c.title || t("Рабочий кубик"))}</span>
        ${["running", "paused"].includes(timer.status) ? `<span class="plan-cube-running" data-cube-timer-label>${safe(timer.status === "paused" ? t("Пауза") : t("Идёт"))} · ${formatTime(timer.remaining_ms)}</span>` : ""}
        <span class="plan-cube-percent">${safe(percent)}%</span></button>
      <div class="plan-cube-quick"><button type="button" data-plan-action="move-up" title="${safe(t("Выше"))}" ${editable && !started && index > 0 ? "" : "disabled"}>${safe(t("Выше"))}</button>
        <button type="button" data-plan-action="move-down" title="${safe(t("Ниже"))}" ${editable && !started && index < count - 1 ? "" : "disabled"}>${safe(t("Ниже"))}</button>
        <button type="button" data-plan-action="remove-cube" ${editable && !started ? "" : "disabled"}>${safe(t("В пул"))}</button></div></li>`;
  }
  function cubeDetailHtml(c, p, editable) {
    if (!c) return "";
    const progress = p.completed_cubes?.[c.id];
    const percent = progress?.percent ?? c.progress_percent ?? 0;
    const savedActual = progress?.actual_minutes ?? c.actual_minutes;
    const actual = numeric(savedActual) > 0 ? savedActual : "";
    const timer = cubeTimerState(c), running = timer.status === "running", paused = timer.status === "paused";
    const started = numeric(percent) > 0 || numeric(actual) > 0 || running || paused;
    const choices = [...new Set([0, 25, 50, 75, 100, percent])].sort((a, b) => a - b);
    return `<div class="plan-cube-detail" data-cube-id="${safe(c.id)}"><h3>${safe(c.title)}</h3><p>${safe(c.output || t("Результат ещё не описан"))}</p>
      ${Array.isArray(c.steps) && c.steps.length ? `<ol class="plan-cube-steps">${c.steps.map(step => `<li>${safe(step)}</li>`).join("")}</ol>` : ""}
      <div class="plan-timer-line" data-timer-cube="${safe(c.id)}"><button type="button" data-plan-action="timer-start" ${editable && !running && Number(percent) < 100 ? "" : "disabled"}>${safe(paused ? t("Продолжить") : t("Приступить"))}</button>
        <button type="button" data-plan-action="timer-pause" ${running ? "" : "disabled"}>${safe(t("Пауза"))}</button>
        <button type="button" data-plan-action="timer-stop" ${running || paused ? "" : "disabled"}>${safe(t("Стоп"))}</button>
        <span data-timer-text>${safe(timer.status === "idle" ? t("Таймер не запущен") : `${t("Осталось")}: ${formatTime(timer.remaining_ms)}`)}</span><span data-timer-recorded></span></div>
      <p class="plan-timer-help">${safe(t("Отдых — в общем таймере; следующий кубик выбираете сами."))}</p>
      <div class="plan-cube-controls"><label>${safe(t("Готовность"))}<select data-field="percent" ${editable ? "" : "disabled"}>${choices.map(v => `<option value="${safe(v)}" ${Number(v) === Number(percent) ? "selected" : ""}>${safe(v)}%</option>`).join("")}</select></label>
        <label>${safe(t("Факт, мин"))}<input data-field="actual" type="number" min="0" step="1" value="${safe(actual)}" placeholder="—" ${editable ? "" : "disabled"}></label>
        <button type="button" data-plan-action="save-cube" ${editable ? "" : "disabled"}>${safe(t("Сохранить"))}</button><button type="button" data-plan-action="done-cube" ${editable ? "" : "disabled"}>${safe(t("Готово"))}</button></div>
      <div class="plan-cube-edit"><label>${safe(t("Длительность"))}<select data-field="resize-minutes" ${editable && c.manual && !started ? "" : "disabled"}>${[15,30,45,60,90,c.minutes].filter((x,i,a)=>a.indexOf(x)===i).sort((a,b)=>a-b).map(n=>`<option value="${n}" ${n===c.minutes?"selected":""}>${safe(minutes(n))}</option>`).join("")}</select></label>
        <button type="button" data-plan-action="resize-cube" ${editable && c.manual && !started ? "" : "disabled"}>${safe(t("Изменить"))}</button>
        <label>${safe(t("Перенести на день"))}<input data-field="move-date" type="date" min="${safe(p.settings?.start)}" max="${safe(p.settings?.end)}" value="${safe(c.date)}" ${editable && !started ? "" : "disabled"}></label>
        <button type="button" data-plan-action="move-cube" ${editable && !started ? "" : "disabled"}>${safe(t("Перенести"))}</button></div></div>`;
  }
  function poolHtml(p, pool, current, free, editable) {
    const options = [15,30,45,60,90];
    const available = numeric(current?.available_for_manual_minutes);
    const sizes = options.filter(n => n <= available);
    const defaultSize = sizes.filter(n => n <= free).at(-1) || sizes[0] || 15;
    const segments = current?.segments || [];
    const selectedSegment = segments.find(s => s.id === selectedSegmentId);
    return `<aside class="plan-pool"><header><h2>${safe(t("Пул ближайших задач"))}</h2><span class="plan-pool-free">${safe(minutes(free))} ${safe(t("свободно"))}</span></header>
      ${pool.length ? `<div class="plan-pool-near">${pool.slice(0, 6).map(item => `<button type="button" class="plan-pool-near-item ${item.task_id === current?.task_id ? "is-selected" : ""}" data-plan-action="pool-select" data-task-id="${safe(item.task_id)}" draggable="${editable}">
          <b>${safe(item.title)}</b><small>${safe(item.due || t("без срока"))} · ${safe(minutes(item.available_for_manual_minutes))}</small></button>`).join("")}</div>
        <label class="plan-pool-picker">${safe(t("Материал"))}<select id="plan-pool-task">${pool.map(item => `<option value="${safe(item.task_id)}" ${item.task_id===current?.task_id?"selected":""}>${safe(item.title)}</option>`).join("")}</select></label>
        ${current ? `<div class="plan-pool-selected"><h3>${safe(current.title)}</h3><p>${safe(t("Доступно для выбора"))}: ${safe(minutes(available))}</p>
          <div class="plan-pool-add"><label>${safe(t("Кубик"))}<select id="plan-pool-duration">${(sizes.length?sizes:options).map(n=>`<option value="${n}" ${n===defaultSize?"selected":""}>${safe(minutes(n))}</option>`).join("")}</select></label>
            <button type="button" data-plan-action="add-pool-cube" ${editable && defaultSize<=free && defaultSize<=available ? "" : "disabled"}>${safe(t("В день"))}</button></div></div>` : ""}
        ${segments.length ? `<div class="plan-pool-segments"><h3>${safe(t("Этапы материала"))}</h3>${segments.map(s=>`<button type="button" data-plan-action="segment-select" data-segment-id="${safe(s.id)}" class="${s.id===selectedSegmentId?"is-selected":""}"><b>${safe(s.title)}</b><span>${safe(minutes(s.remaining_minutes))} · ${safe(s.progress_percent||0)}%</span></button>`).join("")}
          ${selectedSegment ? `<div class="plan-segment-detail"><strong>${safe(selectedSegment.title)}</strong><p>${safe(selectedSegment.output || "")}</p>
            ${selectedSegment.steps?.length ? `<ul>${selectedSegment.steps.map(x=>`<li>${safe(x)}</li>`).join("")}</ul>` : ""}
            <div><label>${safe(t("Готовность этапа"))}<select id="plan-segment-percent">${[0,25,50,75,100,selectedSegment.progress_percent].filter((x,i,a)=>a.indexOf(x)===i).sort((a,b)=>a-b).map(n=>`<option value="${n}" ${n===selectedSegment.progress_percent?"selected":""}>${n}%</option>`).join("")}</select></label><button type="button" data-plan-action="save-segment-progress" ${editable?"":"disabled"}>${safe(t("Сохранить"))}</button></div>
            <div><label>${safe(t("Цель на день"))}<select id="plan-segment-hours">${[120,180,240,300].map(n=>`<option value="${n}">${safe(minutes(n))}</option>`).join("")}</select></label><button type="button" data-plan-action="add-segment" ${editable && free>=120 && numeric(selectedSegment.available_for_manual_minutes)>=120 ? "" : "disabled"}>${safe(t("Набрать блоки в день"))}</button>
              ${numeric(selectedSegment.available_for_manual_minutes) > 0 && numeric(selectedSegment.available_for_manual_minutes) < 120 ? `<button type="button" data-plan-action="add-segment-remaining" ${editable && free >= numeric(selectedSegment.available_for_manual_minutes) ? "" : "disabled"}>${safe(t("Добавить остаток этапа"))} · ${safe(minutes(selectedSegment.available_for_manual_minutes))}</button>` : ""}</div></div>` : ""}</div>` : ""}
        <details class="plan-new-segment"><summary>${safe(t("Свой этап"))}</summary><label>${safe(t("Название"))}<input id="plan-new-segment-title" maxlength="300"></label><label>${safe(t("Длительность, мин"))}<input id="plan-new-segment-minutes" type="number" min="15" max="300" step="15" value="60"></label><label>${safe(t("Результат"))}<input id="plan-new-segment-output" maxlength="1000"></label><button type="button" data-plan-action="create-segment" ${editable?"":"disabled"}>${safe(t("Добавить этап"))}</button></details>` : `<div class="plan-pool-empty"><p>${safe(t("Пул пока пуст. Завершённые задачи остаются в истории Work."))}</p></div>`}
    </aside>`;
  }
  function updateTimerText() {
    const p = planning();
    for (const tile of root.querySelectorAll(".plan-cube-tile[data-cube-id]")) {
      const cube = p?.result?.cubes?.find(item => item.id === tile.dataset.cubeId);
      if (!cube) continue;
      const state = cubeTimerState(cube);
      let label = tile.querySelector("[data-cube-timer-label]");
      if (!["running", "paused"].includes(state.status)) { label?.remove(); continue; }
      if (!label) {
        label = document.createElement("span"); label.className = "plan-cube-running";
        label.setAttribute("data-cube-timer-label", "");
        tile.querySelector(".plan-cube-select")?.insertBefore(label, tile.querySelector(".plan-cube-percent"));
      }
      label.textContent = `${state.status === "paused" ? t("Пауза") : t("Идёт")} · ${formatTime(state.remaining_ms)}`;
    }
    if (selectedCubeId) {
      const cube = p?.result?.cubes?.find(item => item.id === selectedCubeId);
      const target = root.querySelector("[data-timer-text]");
      if (cube && target) {
        const state = cubeTimerState(cube);
        target.textContent = state.status === "idle" ? t("Таймер не запущен") :
          `${t("Осталось")}: ${formatTime(state.remaining_ms)}`;
      }
    }
    const day = p?.result?.days?.find(item => item.date === selectedDate);
    for (const item of day?.prep_cubes || []) {
      const target = [...root.querySelectorAll("[data-prep-id]")].find(el => el.dataset.prepId === item.id)?.querySelector("[data-prep-timer-text]");
      if (!target) continue;
      const state = cubeTimerState(prepTimerCube(item, p));
      target.textContent = state.status === "idle" ? t("Таймер не запущен") :
        `${t("Осталось")}: ${formatTime(state.remaining_ms)}`;
    }
  }
  async function timerAction(action, cube, prepId = null) {
    const editor = prepId ? [...root.querySelectorAll("[data-prep-id]")].find(el => el.dataset.prepId === prepId) :
      [...root.querySelectorAll(".plan-cube-detail[data-cube-id]")].find(el => el.dataset.cubeId === cube.id);
    const draftPercent = editor?.querySelector(prepId ? '[data-field="prep-percent"]' : '[data-field="percent"]')?.value;
    const draftActual = editor?.querySelector(prepId ? '[data-field="prep-actual"]' : '[data-field="actual"]')?.value;
    try {
      if (action === "timer-start") await startCubeTimer(cube);
      else if (action === "timer-pause") await pauseCubeTimer(cube);
      else await stopCubeTimer(cube);
      render();
      const restored = prepId ? [...root.querySelectorAll("[data-prep-id]")].find(el => el.dataset.prepId === prepId) :
        [...root.querySelectorAll(".plan-cube-detail[data-cube-id]")].find(el => el.dataset.cubeId === cube.id);
      const percentField = restored?.querySelector(prepId ? '[data-field="prep-percent"]' : '[data-field="percent"]');
      const actualField = restored?.querySelector(prepId ? '[data-field="prep-actual"]' : '[data-field="actual"]');
      if (percentField && draftPercent != null) percentField.value = draftPercent;
      if (actualField && draftActual != null) actualField.value = draftActual;
      if (action === "timer-stop") {
        const recorded = await recordedCubeMinutes(cube);
        const field = actualField;
        if (field && field.value === "" && recorded > 0) field.value = String(recorded);
        const label = prepId ? [...root.querySelectorAll("[data-prep-id]")].find(el => el.dataset.prepId === prepId)?.querySelector("[data-prep-timer-recorded]") :
          root.querySelector(`.plan-cube-detail[data-cube-id="${safe(cube.id)}"] [data-timer-recorded]`);
        if (label && recorded > 0) label.textContent = `${t("Учтено таймером")}: ${minutes(recorded)}`;
      }
      updateTimerText();
    } catch (cause) { error = t("Не удалось управлять таймером: {error}", { error: cause.message }); render(); }
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
    if (e.target.id === "plan-date") { selectedDate = e.target.value; selectedTaskId = null; selectedCubeId = null; energyPreview = null; poolIntent = false; render(); }
    if (e.target.id === "plan-task-select") { selectedTaskId = e.target.value; render(); }
    if (e.target.id === "plan-pool-task") { poolTaskId = e.target.value; selectedSegmentId = null; poolIntent = true; render(); }
    if (e.target.id === "plan-pool-duration") {
      const task = (planning()?.result?.task_pool || []).find(item => item.task_id === poolTaskId);
      const day = currentDay(planning());
      const free = day.builder_available_minutes ?? Math.max(0, (day.budget_minutes ?? day.day_limit_minutes ?? 300) - numeric(day.fixed_minutes) - numeric(day.weekly_review_minutes) - numeric(day.builder_selected_minutes));
      const add = root.querySelector('[data-plan-action="add-pool-cube"]');
      if (add) add.disabled = Number(e.target.value) > free || Number(e.target.value) > numeric(task?.available_for_manual_minutes);
    }
    if (e.target.classList.contains("plan-energy-input")) void run("day_budget", { date: selectedDate, minutes: Number(e.target.value) });
    if (e.target.classList.contains("plan-prep-range")) {
      const id = e.target.closest("[data-prep-id]")?.dataset.prepId;
      if (id) void run("prep_set", { prep_id: id, minutes: Number(e.target.value) });
    }
    if (e.target.id === "plan-resource-type") root.querySelector("#plan-resource-target").placeholder = e.target.value === "url" ? "https://…" : t("Путь к файлу");
    if (e.target.dataset.planAction === "week-done") {
      const id = e.target.closest("[data-weekly-id]")?.dataset.weeklyId;
      if (id) void run("weekly_review_done", { id, done: e.target.checked });
    }
  });
  root.addEventListener("input", e => {
    if (e.target.classList.contains("plan-energy-input")) {
      const value = Number(e.target.value), p = planning(), day = currentDay(p);
      energyPreview = { date: selectedDate, minutes: value };
      const occupied = numeric(day.fixed_minutes) + numeric(day.weekly_review_minutes) + numeric(day.builder_selected_minutes);
      const free = Math.max(0, value - occupied);
      root.querySelector(".plan-energy-value").textContent = `${Number((value / 60).toFixed(2)).toLocaleString("ru-RU")} ${t("энергии")}`;
      root.querySelector(".plan-capacity span").textContent = `/ ${minutes(value)}`;
      root.querySelector(".plan-free-value").textContent = minutes(free);
      const poolFree = root.querySelector(".plan-pool-free"); if (poolFree) poolFree.textContent = `${minutes(free)} ${t("свободно")}`;
      const emptyFree = root.querySelector(".plan-empty-day strong"); if (emptyFree) emptyFree.textContent = minutes(free);
      const raised = root.querySelector(".plan-raised"); if (raised) raised.hidden = value <= 300;
      root.querySelector(".plan-energy-bar")?.style.setProperty("--energy-hours", String(Math.max(1, value / 60)));
      const freePart = root.querySelector(".plan-energy-bar .is-free"); if (freePart) freePart.dataset.minutes = String(free);
      root.querySelectorAll(".plan-energy-bar span[data-minutes]").forEach(span => {
        span.style.width = `${Math.min(100, numeric(span.dataset.minutes) / Math.max(1, value) * 100).toFixed(2)}%`;
      });
    }
    if (e.target.classList.contains("plan-prep-range")) {
      const output = e.target.closest("[data-prep-id]")?.querySelector("output");
      if (output) output.textContent = `${e.target.value} ${t("мин")}`;
    }
  });
  root.addEventListener("click", async e => {
    const button = e.target.closest("[data-plan-action]");
    if (!button || button.disabled) return;
    const action = button.dataset.planAction, p = planning();
    if (!p) return;
    if (["previous", "next", "today"].includes(action)) {
      const value = action === "today" ? todayIn(p.settings?.timezone) : dateShift(selectedDate, action === "previous" ? -1 : 1);
      selectedDate = clampDate(value, p.settings.start, p.settings.end); selectedTaskId = null; selectedCubeId = null; energyPreview = null; poolIntent = false; render(); return;
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
    if (action === "pool-select") { poolTaskId = button.dataset.taskId; selectedSegmentId = null; poolIntent = true; render(); return; }
    if (action === "segment-select") { selectedSegmentId = button.dataset.segmentId; poolIntent = true; render(); return; }
    if (action === "add-pool-cube") {
      const size = Number(root.querySelector("#plan-pool-duration")?.value);
      if (!poolTaskId || ![15, 30, 45, 60, 90].includes(size)) return;
      return run("day_cube_add", { date: selectedDate, task_id: poolTaskId, minutes: size,
        ...(selectedSegmentId && (p.result?.task_pool || []).find(item => item.task_id === poolTaskId)?.segments?.some(item => item.id === selectedSegmentId) ? { segment_id: selectedSegmentId } : {}) });
    }
    if (action === "add-segment") {
      const size = Number(root.querySelector("#plan-segment-hours")?.value);
      if (!selectedSegmentId || ![120, 180, 240, 300].includes(size)) return;
      return run("day_segment_add", { date: selectedDate, segment_id: selectedSegmentId, minutes: size });
    }
    if (action === "add-segment-remaining" && selectedSegmentId) return run("day_segment_add", { date: selectedDate, segment_id: selectedSegmentId });
    if (action === "save-segment-progress") {
      if (!selectedSegmentId) return;
      const percent = Number(root.querySelector("#plan-segment-percent")?.value);
      return run("segment_progress", { segment_id: selectedSegmentId, percent }, percent === 100 ?
        () => preserveTimedCubeBeforeSegment(p, selectedSegmentId, {
          timerState: cubeTimerState, stopTimer: stopCubeTimer, recordedMinutes: recordedCubeMinutes, action: ctx.action,
        }) : null);
    }
    if (action === "create-segment") {
      const title = root.querySelector("#plan-new-segment-title")?.value.trim();
      const duration = Number(root.querySelector("#plan-new-segment-minutes")?.value);
      const output = root.querySelector("#plan-new-segment-output")?.value.trim();
      if (!poolTaskId || !title || !Number.isInteger(duration) || duration < 15 || duration > 300) {
        error = t("Укажите название этапа и длительность 15–300 минут."); render(); return;
      }
      return run("segment_create", { task_id: poolTaskId, title, minutes: duration, ...(output ? { output } : {}) });
    }
    const prep = button.closest("[data-prep-id]");
    if (action === "restore-prep" && prep) return run("prep_set", { prep_id: prep.dataset.prepId, minutes: 25 });
    if (["prep-timer-start", "prep-timer-pause", "prep-timer-stop"].includes(action) && prep) {
      const item = currentDay(p).prep_cubes?.find(row => row.id === prep.dataset.prepId);
      if (!item) return;
      return timerAction(action.replace("prep-", ""), prepTimerCube(item, p), item.id);
    }
    if (action === "save-prep-progress" && prep) {
      const percent = Number(prep.querySelector('[data-field="prep-percent"]')?.value);
      let value = prep.querySelector('[data-field="prep-actual"]')?.value;
      if (percent === 100) {
        const item = currentDay(p).prep_cubes?.find(row => row.id === prep.dataset.prepId);
        if (item) {
          const timed = prepTimerCube(item, p);
          try { await stopCubeTimer(timed); if (value === "") { const recorded = await recordedCubeMinutes(timed); if (recorded > 0) value = String(recorded); } }
          catch (cause) { error = t("Сначала остановите таймер: {error}", { error: cause.message }); render(); return; }
        }
      }
      return run("prep_progress", { prep_id: prep.dataset.prepId,
        percent,
        ...(value === "" ? {} : { actual_minutes: Number(value) }) });
    }
    const cube = button.closest("[data-cube-id]");
    if (action === "select-cube" && cube) { selectedCubeId = selectedCubeId === cube.dataset.cubeId ? null : cube.dataset.cubeId; render(); return; }
    if (cube && ["remove-cube", "move-up", "move-down", "move-cube", "resize-cube", "timer-start", "timer-pause", "timer-stop", "save-cube", "done-cube"].includes(action)) {
      const id = cube.dataset.cubeId;
      const original = (p.result?.cubes || []).find(x => x.id === id);
      if (!original) return;
      const timerState = cubeTimerState(original);
      if (["remove-cube", "move-up", "move-down", "move-cube", "resize-cube"].includes(action) &&
          ["running", "paused"].includes(timerState.status)) {
        error = t("Сначала остановите таймер этого кубика."); render(); return;
      }
      if (action === "remove-cube") { if (id === selectedCubeId) selectedCubeId = null; return run("day_cube_remove", { cube_id: id }); }
      if (action === "move-up" || action === "move-down") {
        const tiles = [...root.querySelectorAll(".plan-cube-tile")];
        const index = tiles.findIndex(item => item.dataset.cubeId === id);
        return run("day_cube_move", { cube_id: id, date: selectedDate, index: index + (action === "move-up" ? -1 : 1) });
      }
      if (action === "move-cube") return run("day_cube_move", { cube_id: id, date: cube.querySelector('[data-field="move-date"]').value });
      if (action === "resize-cube") return run("day_cube_resize", { cube_id: id, minutes: Number(cube.querySelector('[data-field="resize-minutes"]').value) });
      if (["timer-start", "timer-pause", "timer-stop"].includes(action)) return timerAction(action, original);
      const value = action === "done-cube" ? 100 : Number(cube.querySelector('[data-field="percent"]').value);
      let actualField = cube.querySelector('[data-field="actual"]').value;
      if (value === 100) {
        try {
          await stopCubeTimer(original);
          if (actualField === "") { const recorded = await recordedCubeMinutes(original); if (recorded > 0) actualField = String(recorded); }
        } catch (cause) { error = t("Сначала остановите таймер: {error}", { error: cause.message }); render(); return; }
      }
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
  root.addEventListener("dragstart", e => {
    const cube = e.target.closest(".plan-cube-tile[draggable='true']");
    const task = e.target.closest(".plan-pool-near-item[draggable='true']");
    if (cube) e.dataTransfer?.setData("application/x-brainalot-cube", cube.dataset.cubeId);
    else if (task) e.dataTransfer?.setData("application/x-brainalot-task", task.dataset.taskId);
    else e.preventDefault();
  });
  root.addEventListener("dragover", e => {
    if (e.target.closest("[data-plan-drop='day'],.plan-cube-tile") &&
        [...(e.dataTransfer?.types || [])].some(type => type.startsWith("application/x-brainalot-"))) {
      e.preventDefault(); root.querySelector("[data-plan-drop='day']")?.classList.add("is-drag-over");
    }
  });
  root.addEventListener("dragleave", e => { if (!e.target.closest("[data-plan-drop='day']")) root.querySelector("[data-plan-drop='day']")?.classList.remove("is-drag-over"); });
  root.addEventListener("drop", e => {
    const zone = e.target.closest("[data-plan-drop='day']");
    root.querySelector("[data-plan-drop='day']")?.classList.remove("is-drag-over");
    if (!zone) return;
    const cubeId = e.dataTransfer?.getData("application/x-brainalot-cube");
    const taskId = e.dataTransfer?.getData("application/x-brainalot-task");
    if (cubeId) {
      e.preventDefault();
      const tiles = [...root.querySelectorAll(".plan-cube-tile")];
      const target = e.target.closest(".plan-cube-tile");
      const index = target ? tiles.indexOf(target) : tiles.length - 1;
      void run("day_cube_move", { cube_id: cubeId, date: selectedDate, index: Math.max(0, index) });
    } else if (taskId) {
      e.preventDefault();
      const p = planning(), item = p?.result?.task_pool?.find(row => row.task_id === taskId);
      const day = currentDay(p), free = numeric(day.builder_available_minutes);
      const size = [90,60,45,30,15].find(n => n <= free && n <= numeric(item?.available_for_manual_minutes));
      if (size) void run("day_cube_add", { date: selectedDate, task_id: taskId, minutes: size });
    }
  });
  timeClient.subscribe(updateTimerText);
  setInterval(updateTimerText, 1000);
  void refreshPending().catch(cause => { error = cause.message; render(); });
  return { render, remoteChanged };
}
