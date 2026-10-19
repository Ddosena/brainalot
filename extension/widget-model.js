// Pure compact-view rules; the common time service remains authoritative.
export const taskKey = task => task ? `${task.source}:${task.id}` : "";
export function activeTask(state) {
  return ["running", "paused"].includes(state?.status) ? state.task || null : null;
}
export function resolveActiveTask(state, notes, calendar = []) {
  const task = activeTask(state);
  if (!task) return null;
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
