import {t} from '../i18n.js';
const percent = value => {
  const number = Math.max(0, Math.min(100, Number(value) || 0));
  return number < 100 ? Math.min(99, Math.round(number)) : 100;
};
const DAY = 86400000;
export function currentMonday(now = new Date()) {
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  return today - ((new Date(today).getUTCDay() + 6) % 7) * DAY;
}
export function iso(index, baseDate = currentMonday()) {
  return index == null || !Number.isFinite(index) ? null : new Date(baseDate + index * DAY).toISOString().slice(0, 10);
}
export function fromIso(value, baseDate = currentMonday()) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || '');
  if (!match) return null;
  const ms = Date.UTC(+match[1], +match[2] - 1, +match[3]);
  return new Date(ms).toISOString().slice(0, 10) === value ? Math.round((ms - baseDate) / DAY) : null;
}
const minute = value => {
  if (typeof value !== 'string') return null;
  const match = /^(\d{2}):(\d{2})/.exec(value);
  return match ? +match[1] * 60 + +match[2] : null;
};
export function adaptModel(model, {baseDate = currentMonday()} = {}) {
  const day = value => fromIso(value, baseDate);
  const projects = (model.projects || []).map(p => ({...p, name:p.title, c:p.color, hidden:p.visible === false, calendarPoints:(p.calendar_points ||
 []).map(e => ({...e, day:day(e.date), at:minute(e.start)}))}));
  const goals = (model.tasks || []).filter(t => t.owned !== false).map(t => ({...t, p:t.project_id, s:day(t.start), e:day(t.end), due:day(t.deadline),
 doneOn:day(t.completed_on), h:(t.minutes || 0)/60, vol:t.minutes || 0, pct:percent(t.progress_percent), auto:!!t.volume_by_cubes, autoVol:
!!t.volume_by_cubes}));
  const goalIds = new Set(goals.map(g => g.id));
  const cubes = (model.cubes || []).filter(c => c.status !== 'cancelled' && !(c.kind === 'prep' && !(c.minutes > 0)) && (!c.task_id ||
 goalIds.has(c.task_id))).map(c => {
    const sources = Object.hasOwn(c, 'sources') ? (c.sources || []) : (c.links || []);
    return {...c, goal:c.task_id || null, min:c.kind === 'calendar_optional' && c.duration_unknown ? 30 : c.minutes, source_minutes:c.kind ===
 'calendar_optional' && (c.duration_unknown || c.source_duration_unknown) ? null : c.source_minutes, pct:percent(c.progress_percent), day:
day(c.selected_date), at:minute(c.start), links:sources, sources, src:c.kind === 'brainalot' ? 'Brainalot' : c.kind === 'calendar_optional' ?
 t('Календарь') : c.repeatable_instance?t('Повторяемые'):null};
  });
  const events = (model.events || []).map(e => ({...e, day:day(e.date), at:minute(e.start), min:e.duration_minutes}));
  const hasStandalone=cubes.some(c=>c.standalone);
  const repeatables=(model.repeatables||[]).map(item=>({...item,sources:item.sources||[]}));
  const explicitWork=cubes.some(c=>c.repeatable_instance)||repeatables.some(item=>item.origin!=='system');
  return {v:1, base:baseDate, started:hasStandalone||model.planning_initialized !== false && (projects.length > 0||explicitWork), hasStandalone,
 planning_initialized:model.planning_initialized, revision:model.revision, today:day(model.today), settings:model.settings||{}, projects, goals,
 cubes, events, repeatables, calendarPalette:model.calendar_palette || [], warnings:model.warnings || []};
}








