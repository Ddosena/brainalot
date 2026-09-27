import { addDays } from "./panel-state.js";
import { t } from "./i18n.js";

function noteInDashboard(dashboard, id) {
  return Object.values(dashboard || {}).some(value => Array.isArray(value) && value.some(item => item?.id === id))
    || dashboard?.resurface?.id === id;
}

export function createNoteDeletionOverlay() {
  const entries = new Map();
  let sequence = 0;
  return {
    begin(id) {
      const token = `${id}:${++sequence}`;
      entries.set(id, {token, status:"pending", minRead:Infinity});
      return token;
    },
    succeed(token, currentRead = 0) {
      for (const entry of entries.values()) if (entry.token === token) {
        entry.status = "success"; entry.minRead = currentRead + 1;
      }
    },
    fail(token) {
      for (const [id, entry] of entries) if (entry.token === token) entries.delete(id);
    },
    project(rawDashboard, rawNotes) {
      const hidden = new Set(entries.keys());
      if (!rawDashboard) return {dashboard:null, notes:(rawNotes || []).filter(note => !hidden.has(note.id))};
      const dashboard = {};
      for (const [key,value] of Object.entries(rawDashboard)) {
        dashboard[key] = Array.isArray(value) ? value.filter(item => !hidden.has(item?.id)) : value;
      }
      if (hidden.has(dashboard.resurface?.id)) dashboard.resurface = null;
      return {dashboard, notes:(rawNotes || []).filter(note => !hidden.has(note.id))};
    },
    reconcile(rawDashboard, rawNotes, readSeq = 0) {
      for (const [id,entry] of entries) {
        if (entry.status !== "success" || readSeq < entry.minRead) continue;
        if (!(rawNotes || []).some(note => note.id === id) && !noteInDashboard(rawDashboard,id)) entries.delete(id);
      }
    },
    has(id) { return entries.has(id); },
  };
}

export function calendarOccurrenceKey(event) {
  return JSON.stringify([event?.calendar_id || "", event?.id || "", event?.start || ""]);
}

function sameCalendarOccurrence(source, candidate) {
  if (source.calendar_id !== candidate.calendar_id) return false;
  if (source.occurrence_id && candidate.occurrence_id) return source.occurrence_id === candidate.occurrence_id;
  return source.id === candidate.id;
}

function shiftDatePrefix(value, days) {
  return typeof value === "string" && /^\d{4}-\d{2}-\d{2}/.test(value)
    ? addDays(value.slice(0,10), days) + value.slice(10) : value;
}

function fullEventDates(start, end) {
  const first = start.slice(0,10);
  let last = typeof end === "string" && /^\d{4}-\d{2}-\d{2}/.test(end) ? end.slice(0,10) : first;
  if (last > first && /^T00:00(?::00)?(?:[+\-Z]|$)/.test(end.slice(10))) last = addDays(last,-1);
  if (last < first) last = first;
  const dates = [];
  for (let day = first; day <= last && dates.length < 3660; day = addDays(day,1)) dates.push(day);
  return dates;
}

export function shiftCalendarEvent(event, newDate) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(newDate || "") || !/^\d{4}-\d{2}-\d{2}/.test(event?.start || "")) {
    throw new Error(t("Выберите новую дату события."));
  }
  const from = Date.parse(event.start.slice(0,10) + "T00:00:00Z");
  const to = Date.parse(newDate + "T00:00:00Z");
  const days = Math.round((to - from) / 86400000);
  return {
    ...event, start:shiftDatePrefix(event.start,days), end:shiftDatePrefix(event.end,days),
    dates:fullEventDates(shiftDatePrefix(event.start,days),shiftDatePrefix(event.end,days)),
    optimistic_pending:true,
  };
}

function dateInRange(date, range) {
  return Boolean(date && range?.start && range?.end && date >= range.start && date <= range.end);
}

export function createCalendarOverlay() {
  const entries = new Map();
  let sequence = 0;
  function begin(event, kind, newDate = null) {
    const key = calendarOccurrenceKey(event);
    if (entries.has(key)) return null;
    const token = `${key}:${++sequence}`;
    entries.set(key, {token, key, event, kind, newDate, status:"pending", minRead:Infinity});
    return token;
  }
  return {
    beginDelete(event) { return begin(event,"delete"); },
    beginMove(event,newDate) { shiftCalendarEvent(event,newDate); return begin(event,"move",newDate); },
    succeed(token,currentRead = 0) {
      for (const entry of entries.values()) if (entry.token === token) {
        entry.status = "success"; entry.minRead = currentRead + 1;
      }
    },
    fail(token) {
      for (const [key,entry] of entries) if (entry.token === token) entries.delete(key);
    },
    project(rawEvents) {
      const raw = Array.isArray(rawEvents) ? rawEvents : [];
      const visible = raw.filter(event => !entries.has(calendarOccurrenceKey(event)));
      for (const entry of entries.values()) {
        if (entry.kind !== "move") continue;
        const shifted = shiftCalendarEvent(entry.event,entry.newDate);
        const alreadyReturned = visible.findIndex(event => sameCalendarOccurrence(entry.event,event) &&
          event.start?.slice(0,10) === entry.newDate);
        if (alreadyReturned < 0) visible.push(shifted);
        else visible[alreadyReturned] = {...visible[alreadyReturned], optimistic_pending:true};
      }
      return visible;
    },
    reconcile(rawEvents, {start,end,stale = false,readSeq = 0} = {}) {
      if (stale) return;
      const raw = Array.isArray(rawEvents) ? rawEvents : [];
      const range = {start,end};
      for (const [key,entry] of entries) {
        if (entry.status !== "success" || readSeq < entry.minRead) continue;
        if (entry.kind === "move") {
          if (!dateInRange(entry.newDate,range)) continue;
          const movedPresent = raw.some(event => sameCalendarOccurrence(entry.event,event) &&
            event.start?.slice(0,10) === entry.newDate);
          if (!movedPresent) continue;
          const oldQueried = fullEventDates(entry.event.start,entry.event.end).some(date => dateInRange(date,range));
          if (oldQueried && raw.some(event => calendarOccurrenceKey(event) === key)) continue;
          entries.delete(key);
          continue;
        }
        const oldDates = fullEventDates(entry.event.start,entry.event.end);
        if (oldDates.some(date => dateInRange(date,range)) && !raw.some(event => calendarOccurrenceKey(event) === key)) entries.delete(key);
      }
    },
    has(event) { return entries.has(calendarOccurrenceKey(event)); },
  };
}
