import { createWorkConstructor } from "./work-constructor.js?v=constructor-20261023-a";
import { t } from "./i18n.js";
import { sharedApi } from "./time-client.js";
import { createDayBridge } from "./work-period/day-bridge.js";
const numeric=value=>Number.isFinite(Number(value))?Number(value):0;
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


// The canonical Work transport remains owned by work-store; the constructor only projects it.
export function createPlanning(root,ctx){return createDayBridge(root,ctx,{createConstructor:createWorkConstructor,api:ctx.api||sharedApi,loadingText:t('Загружаем план…')});}
