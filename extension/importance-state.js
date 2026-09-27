import { calendarOccurrenceKey } from "./optimistic.js";

export const CALENDAR_IMPORTANCE_KEY = "panel:calendar-importance";
/** «Очень важное» (огонь) is a scarce mark: at most one per day or among dateless tasks. */
export const FIRE_LIMIT = 1;
const SAVED_LEVELS = new Set(["high", "critical"]);

export function calendarImportanceKey(event) {
  return typeof event?.occurrence_id === "string" && event.occurrence_id
    ? JSON.stringify([event.calendar_id || "", event.occurrence_id]) : calendarOccurrenceKey(event);
}

export function normalizeCalendarImportance(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(Object.entries(value).filter(([key, importance]) =>
    typeof key === "string" && key && SAVED_LEVELS.has(importance)));
}

/** "normal", "high" or "critical" for one calendar occurrence. */
export function calendarImportanceLevel(saved, event) {
  return normalizeCalendarImportance(saved)[calendarImportanceKey(event)] || "normal";
}

export function calendarIsImportant(saved, event) {
  return calendarImportanceLevel(saved, event) !== "normal";
}

/** `level` is "high", "critical" or anything else to clear; `true`/`false` keep the old two-state calls working. */
export function withCalendarImportance(saved, event, level) {
  const next = normalizeCalendarImportance(saved);
  const key = calendarImportanceKey(event);
  const value = level === true ? "high" : level;
  if (SAVED_LEVELS.has(value)) next[key] = value;
  else delete next[key];
  return next;
}

/** The dot and the double click cycle: normal → high (outline) → critical (fire) → normal. */
export function nextImportance(level) {
  return level === "high" ? "critical" : level === "critical" ? "normal" : "high";
}

/** One press of the importance control; a fire beyond FIRE_LIMIT in the same group is refused. */
export function importanceStep(level, burningInGroup) {
  const next = nextImportance(level);
  return next === "critical" && burningInGroup >= FIRE_LIMIT ? {next: level, blocked: true} : {next, blocked: false};
}

/** Where a task competes for a fire: its day (overdue ones burn in today's list) or the dateless backlog. */
export function fireGroupOfTask(note, today) {
  if (!note?.due) return "backlog";
  return today && note.due < today ? today : note.due;
}
