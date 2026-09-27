import { locale, localeTag, t } from "./i18n.js";
/** Serialize refreshes while preserving a request that arrives during an active read. */
export function createRefreshGate(read) {
  let running = null;
  let again = false;
  return () => {
    if (running) { again = true; return running; }
    running = (async () => {
      do { again = false; await read(); } while (again);
    })().finally(() => { running = null; });
    return running;
  };
}

export function capturePayload({text, kind, due, planningHorizon = null, mediaType, contextId, topic, importance, showInUnscheduled = false}) {
  const task = ["task", "purchase"].includes(kind);
  return {
    text, kind,
    due: task ? due || null : null,
    planning_horizon: task && !due && ["week", "month"].includes(planningHorizon) ? planningHorizon : null,
    media_type: kind === "media" ? mediaType : null,
    context_id: contextId || null,
    topic: topic || null,
    importance,
    show_in_unscheduled: kind === "thought" && Boolean(showInUnscheduled),
  };
}

/** Hide the generated document title while keeping the rest of the Markdown editable. */
export function editableNoteText(body = "") {
  return String(body).replace(/\n?<!-- mmm:attachments -->[\s\S]*?<!-- \/mmm:attachments -->\n?/g, "\n")
    .replace(/^\s*# [^\r\n]+(?:\r?\n(?:\r?\n)?|$)/, "").replace(/\s+$/, "");
}

/** The update API receives the complete Markdown body, including the current title. */
export function markdownNoteBody(title, text = "") {
  const content = String(text).replace(/^\s+|\s+$/g, "");
  return `\n# ${String(title).trim()}\n${content ? `\n${content}\n` : ""}`;
}

export function editNotePatch({title, text, kind, schedule = "none", exactDate = null, mediaType = null, contextId = null, topic = null, importance = "normal", url = null, showInCarousel = true, showInUnscheduled = false}) {
  const cleanTitle = String(title).trim();
  if (!cleanTitle) throw new Error(t("Укажите название записи."));
  const task = ["task", "purchase"].includes(kind);
  if (task && schedule === "date" && !exactDate) throw new Error(t("Выберите точную дату."));
  return {
    title: cleanTitle,
    body: markdownNoteBody(cleanTitle, text),
    kind,
    due: task && schedule === "date" ? exactDate : null,
    planning_horizon: task && ["week", "month"].includes(schedule) ? schedule : null,
    media_type: kind === "media" ? mediaType || null : null,
    context_id: contextId || null,
    topic: String(topic || "").trim() || null,
    importance,
    url: String(url || "").trim() || null,
    show_in_carousel: Boolean(showInCarousel),
    show_in_unscheduled: kind === "thought" && Boolean(showInUnscheduled),
  };
}

/** Convert any Brainalot record to the semantic destination represented by a drop zone. */
export function assignmentPatch(note, target) {
  if (!note || !target) return null;
  if (target.area === "inbox") {
    return {kind:"inbox", status:"inbox", due:null, planning_horizon:null, media_type:null, show_in_unscheduled:false};
  }
  if (target.area !== "today" && target.area !== "backlog") return null;
  const kind = note.kind === "purchase" ? "purchase" : "task";
  const patch = {kind, status:"active", due:null, planning_horizon:null, media_type:null, show_in_unscheduled:false};
  if (target.area === "today") {
    if (!target.date) return null;
    patch.due = target.date;
  } else if (["week", "month"].includes(target.bucket)) {
    patch.planning_horizon = target.bucket;
  } else if (target.bucket !== "unscheduled") return null;
  return patch;
}

export function clearIfUnchanged(current, submitted) {
  return current === submitted ? "" : current;
}

export const RESURFACE_HOLD_MS = 60 * 1000;

/** A client-side compatibility filter while older local services still suggest projects. */
export function resurfaceCandidates(dashboard) {
  const source = Array.isArray(dashboard?.resurface_candidates)
    ? dashboard.resurface_candidates : dashboard?.resurface ? [dashboard.resurface] : [];
  return source.filter(note => note?.kind !== "project");
}

/** Keep a manual card through refreshes, then resume the server's weighted choice. */
export function chooseResurface(candidates, suggested, currentId, holdUntil = 0, now = Date.now(), detailsOpen = false) {
  const unique = (Array.isArray(candidates) ? candidates : []).filter((note, index, all) =>
    note?.id && all.findIndex(item => item?.id === note.id) === index);
  if (!unique.length) return null;
  const current = unique.find(note => note.id === currentId);
  if (current && (detailsOpen || now < holdUntil)) return current;
  return unique.find(note => note.id === suggested?.id) || unique[0];
}

/** Return a cyclic neighbor and renew the hold after an explicit gesture. */
export function stepResurface(candidates, currentId, direction, now = Date.now()) {
  const unique = (Array.isArray(candidates) ? candidates : []).filter((note, index, all) =>
    note?.id && all.findIndex(item => item?.id === note.id) === index);
  if (!unique.length) return { id:null, holdUntil:0 };
  const index = unique.findIndex(note => note.id === currentId);
  const next = index < 0 ? 0 : (index + (direction < 0 ? -1 : 1) + unique.length) % unique.length;
  return { id:unique[next].id, holdUntil:now + RESURFACE_HOLD_MS };
}

/**
 * Resting pose of a Recall card by stack slot: -1 has left to the left, 0 is
 * the reading card, 1–2 peek out on the right, 3 waits hidden behind them.
 * The transform origin is the card's right edge, so `x` moves that edge and
 * `s` shrinks the card toward it, as in the approved Dribbble reference.
 */
export function stackPose(slot, cardWidth, peek = 30, peek2 = 24) {
  switch (slot) {
    case -1: return {x:-(cardWidth + 32), s:1, wash:0, spine:0, opacity:1};
    case 0: return {x:0, s:1, wash:0, spine:0, opacity:1};
    case 1: return {x:peek, s:.92, wash:.3, spine:1, opacity:1};
    case 2: return {x:peek + peek2, s:.845, wash:.52, spine:1, opacity:1};
    default: return {x:peek + peek2 + 18, s:.77, wash:.8, spine:0, opacity:0};
  }
}

export function mixPose(from, to, t) {
  const mix = key => from[key] + (to[key] - from[key]) * t;
  return {x:mix("x"), s:mix("s"), wash:mix("wash"), spine:mix("spine"), opacity:mix("opacity")};
}

/** Ease-out that settles like a light spring without overshooting the slot. */
export function stackEase(t) {
  const clamped = Math.min(1, Math.max(0, t));
  return 1 - Math.pow(1 - clamped, 3.4);
}

/** A released drag commits after a third of the way or a deliberate quick flick. */
export function stackDragCommits(progress, velocity) {
  return progress >= .32 || (progress >= .1 && velocity >= .5);
}

/**
 * Crop an image to fill its frame only when little would be lost; otherwise
 * show it whole over its own blurred backdrop.
 */
export function imageFit(naturalWidth, naturalHeight, boxWidth, boxHeight) {
  if (!(naturalWidth > 0 && naturalHeight > 0 && boxWidth > 0 && boxHeight > 0)) return "contain";
  const ratio = (naturalWidth / naturalHeight) / (boxWidth / boxHeight);
  return ratio >= .8 && ratio <= 1.25 ? "cover" : "contain";
}

const POSTER_HUES = [18, 42, 88, 152, 196, 232, 268, 305, 338];

/** A stable per-record hue, so each text card has its own poster colour. */
export function posterHue(id) {
  let hash = 2166136261;
  for (const char of String(id || "")) hash = Math.imul(hash ^ char.codePointAt(0), 16777619);
  return POSTER_HUES[(hash >>> 0) % POSTER_HUES.length];
}

/** Unknown or old saved values fall back to the operating-system theme. */
export function normalizeTheme(value) {
  return value === "light" || value === "dark" ? value : "system";
}

/** Return a complete stable order after dropping one task around another. */
export function reorderedIds(ids, draggedId, targetId, after = false) {
  if (!Array.isArray(ids) || !ids.includes(draggedId) || !ids.includes(targetId) || draggedId === targetId) return ids;
  const next = ids.filter(id => id !== draggedId);
  const target = next.indexOf(targetId);
  next.splice(target + (after ? 1 : 0), 0, draggedId);
  return next;
}

export function tabCapture(tab) {
  const text = tab.title || tab.url;
  return {
    text, kind: "link", url: tab.url,
    title: text.replace(/[\r\n\x00]/g, " ").trim().slice(0, 200) || t("Ссылка"),
  };
}

const inactiveStatuses = new Set(["done", "cancelled", "archived"]);

/** Work with calendar dates as YYYY-MM-DD strings so a browser timezone cannot move a task. */
export function addDays(date, amount) {
  const [year, month, day] = date.split("-").map(Number);
  const value = new Date(Date.UTC(year, month - 1, day + amount));
  return value.toISOString().slice(0, 10);
}

export function weekStart(date) {
  const [year, month, day] = date.split("-").map(Number);
  const value = new Date(Date.UTC(year, month - 1, day));
  const mondayOffset = (value.getUTCDay() + 6) % 7;
  return addDays(date, -mondayOffset);
}

export function weekDates(start) { return Array.from({length:7}, (_, index) => addDays(start, index)); }

/** Quick due dates never include an earlier day and stop at the current week's Sunday. */
export function currentWeekDueOptions(today) {
  const weekday = new Intl.DateTimeFormat(localeTag(), {weekday:"long", timeZone:"UTC"});
  return weekDates(weekStart(today))
    .filter(date => date >= today)
    .map(date => ({
      value: date,
      label: date === today ? t("Сегодня") : weekday.format(new Date(`${date}T12:00:00Z`)),
    }));
}

export function isVisibleTask(note) {
  return Boolean(note?.id && ["task", "purchase"].includes(note.kind) && !inactiveStatuses.has(note.status));
}

/** A view of active thoughts and ideas; it never changes a record's kind or schedule. */
export function thoughtRecords(notes) {
  return (Array.isArray(notes) ? notes : []).filter(note =>
    note?.id && ["thought", "idea"].includes(note.kind) && note.status === "active");
}

/** Combine overlapping dashboard buckets without ever showing one task twice. */
export function visibleWeekTasks(dashboard, dates) {
  const allowedDates = new Set(dates);
  const notes = [dashboard?.today, dashboard?.week, dashboard?.upcoming, dashboard?.unscheduled]
    .flatMap(records => Array.isArray(records) ? records : []);
  const ids = new Set();
  return notes.filter(note => {
    if (!isVisibleTask(note) || !allowedDates.has(note.due) || ids.has(note.id)) return false;
    ids.add(note.id);
    return true;
  });
}

export function dayTasks(tasks, date) { return tasks.filter(note => note.due === date); }

export function overdueTasks(dashboard, today) {
  const ids = new Set();
  return [dashboard?.overdue_yesterday, dashboard?.overdue_older, dashboard?.today]
    .flatMap(records => Array.isArray(records) ? records : [])
    .filter(note => {
      if (!isVisibleTask(note) || !note.due || note.due >= today || ids.has(note.id)) return false;
      ids.add(note.id);
      return true;
    });
}

export function calendarEventsForDate(events, date) {
  return (Array.isArray(events) ? events : []).filter(event => Array.isArray(event.dates) && event.dates.includes(date));
}

/** Give all-day events a quiet tint; timed events become more urgent near their start. */
export function calendarEventPhase(event, now = Date.now()) {
  const end = Date.parse(event.end);
  if (Number.isFinite(end) && now >= end) return "finished";
  if (event.all_day) return "all-day";
  const start = Date.parse(event.start);
  if (!Number.isFinite(start)) return "normal";
  if (now >= start) return Number.isFinite(end) ? "imminent" : "normal";
  const remaining = start - now;
  if (remaining <= 30 * 60 * 1000) return "imminent";
  if (remaining <= 2 * 60 * 60 * 1000) return "soon";
  return "normal";
}

/** Keep the compact calendar readable: all-day context before timed commitments. */
export function compareCalendarEvents(left, right) {
  const urgency = {"all-day":0, imminent:1, soon:2, normal:3};
  const leftPhase = left.phase || calendarEventPhase(left.event, left.now);
  const rightPhase = right.phase || calendarEventPhase(right.event, right.now);
  return urgency[leftPhase] - urgency[rightPhase]
    || String(left.event.start || "").localeCompare(String(right.event.start || ""), locale())
    || String(left.event.title || "").localeCompare(String(right.event.title || ""), locale());
}

export function calendarEventTime(event) {
  if (event.all_day) return t("Весь день");
  const format = value => new Intl.DateTimeFormat(localeTag(), {hour:"2-digit", minute:"2-digit"}).format(new Date(value));
  if (!event.start) return t("Время не указано");
  return event.end ? `${format(event.start)}–${format(event.end)}` : format(event.start);
}
