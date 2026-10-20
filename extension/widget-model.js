// Pure compact-view rules; the common time service remains authoritative.
export const taskKey = task => task ? `${task.source}:${task.id}` : "";
export function activeTask(state) {
  return ["running", "paused"].includes(state?.status) ? state.task || null : null;
}
export function resolveActiveTask(state, notes, calendar = []) {
  const task = activeTask(state);
  if (!task) return null;
  if (task.source === "label" && task.id === "rest") return task;
  const live = task.source === "library" ? notes.find(note => note.id === task.id) :
    calendar.find(event => `${event.calendar_id}:${event.occurrence_id || event.id}` === task.id);
  const available = live && !["done", "cancelled", "archived"].includes(live.status);
  if (state.status === "paused" && !available) return null;
  return available ? {...task,title:live.title} : task;
}
export function compactTasks(tasks, selected, state, current = activeTask(state)) {
  const unique = [...new Map(tasks.map(task => [taskKey(task), task])).values()]
    .sort((a, b) => taskKey(a).localeCompare(taskKey(b)));
  const hot = unique.find(task => taskKey(task) === selected) || unique[0] || null;
  return {tasks: unique, hot, current, selected: taskKey(hot),
    same: Boolean(hot && taskKey(hot) === taskKey(current)),
    hasTask: Boolean(hot || current)};
}
// "What are you doing?" (owner decision 2026-10-19): ask after 10 minutes of
// activity while nothing is tracked; 5 minutes without input means the person
// is away and the stretch starts again on return.
export const ASK_AFTER_MS = 10 * 60_000;
export const AWAY_MS = 5 * 60_000;
export function untrackedStep(gap, {tracked, idleMs, now}) {
  if (tracked || idleMs >= AWAY_MS) return {gap: null, ask: false};
  const since = gap ? gap.since : now - idleMs;
  return {gap: {since}, ask: now - since >= ASK_AFTER_MS};
}
export function eventKey(event) {
  return `${event.calendar_id}:${event.occurrence_id || event.id}`;
}
export function currentEvent(events, now) {
  return events.find(event => !event.all_day && !event.time_skipped && Date.parse(event.start) <= now && now < Date.parse(event.end)) || null;
}
// Stopwatch has no denominator; never invent a percentage for it. The service
// owns elapsed/duration and the clients only interpolate its current snapshot.
export function timerProgress(state, elapsed) {
  if (!state || state.mode === "stopwatch" || !(state.duration_ms > 0)) return null;
  return Math.max(0, Math.min(100, elapsed / state.duration_ms * 100));
}
