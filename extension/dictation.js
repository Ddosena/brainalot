// Dictation in the capture window.
//
// The local service parses the text (megamozg/dictation.py) and says where
// «Записать» puts it: a Google Calendar event or a Brainalot record. A form field
// left on «Авто» is the parser's to fill; a field set by hand becomes a
// correction (`overrides`). This module turns the draft into those corrections
// and the service's preview into short chips. It has no DOM, so Node tests
// cover it; capture.js draws the result.
import { addDays } from "./panel-state.js";
import { localeTag, t, translateServerMessage } from "./i18n.js";

export const AUTO = "auto";
/** The worker bumps this after a dictation became an event, so the panel reloads its calendar. */
export const CALENDAR_CHANGED_KEY = "panel:calendar-changed";
/** The parser reads at most this many characters; longer text is saved as it is. */
export const DICTATION_LIMIT = 2000;
const TASK_KINDS = new Set(["task", "purchase"]);
const DAY_CODES = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"];

export function usesDictation(text) {
  const clean = typeof text === "string" ? text.trim() : "";
  return clean.length > 0 && clean.length <= DICTATION_LIMIT;
}

/** «Когда» set by hand: no date, a planning basket or a day. */
export function scheduleOverride({ schedule, exactDate }, today = null) {
  if (schedule === "") return { due: null, planning_horizon: null };
  if (schedule === "week" || schedule === "month") return { due: null, planning_horizon: schedule };
  const due = schedule === "custom" ? exactDate : schedule;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(due || "")) throw new Error(t("Выберите точную дату."));
  if (today && due < today) throw new Error(t("Выберите сегодняшний или будущий день."));
  return { due, planning_horizon: null };
}

/**
 * The user's corrections for POST /api/dictation. While typing (`strict`
 * false) an unfinished date is skipped; on save it is an error to show.
 */
export function dictationOverrides(draft, { today = null, strict = false } = {}) {
  const overrides = {};
  const kind = draft.kind || AUTO;
  if (kind !== AUTO) {
    overrides.kind = kind;
    if (kind === "media") overrides.media_type = draft.mediaType || "movie";
  }
  if ((kind === AUTO || TASK_KINDS.has(kind)) && (draft.schedule ?? AUTO) !== AUTO) {
    try {
      Object.assign(overrides, scheduleOverride(draft, today));
    } catch (error) {
      if (strict) throw error;
    }
  }
  if ((draft.contextId ?? AUTO) !== AUTO) overrides.context_id = draft.contextId || null;
  if ((draft.importance ?? AUTO) !== AUTO) overrides.importance = draft.importance;
  if (draft.route === "note") overrides.route = "note";
  return overrides;
}

/** What the capture window queues; the worker adds the delivery key. */
export function dictationPayload({ text, overrides = {}, topic = "", showInUnscheduled = false, attachments = [], voice = false }) {
  return {
    text, overrides,
    topic: String(topic || "").trim() || null,
    show_in_unscheduled: showInUnscheduled === true,
    attachments: Array.isArray(attachments) ? attachments : [],
    source: voice ? "voice" : "chrome",
  };
}

/** Check a dictation before it enters the queue and keep only the fields the service accepts. */
export function checkDictationPayload(payload) {
  if (!payload || typeof payload !== "object") throw new Error(t("Неверная запись диктовки."));
  const { text, overrides = {}, topic = null, show_in_unscheduled: undated = false, attachments = [], source = "chrome" } = payload;
  if (!usesDictation(text)) throw new Error(t("Текст для разбора: от 1 до {limit} символов.", { limit: DICTATION_LIMIT }));
  if (overrides === null || typeof overrides !== "object" || Array.isArray(overrides) ||
      (topic !== null && (typeof topic !== "string" || topic.length > 100)) ||
      typeof undated !== "boolean" || !Array.isArray(attachments) || !["chrome", "voice"].includes(source)) {
    throw new Error(t("Неверная запись диктовки."));
  }
  return { text, overrides, topic, show_in_unscheduled: undated, attachments, source };
}

export function capitalize(text) {
  const value = String(text || "");
  return value.charAt(0).toLocaleUpperCase(localeTag()) + value.slice(1);
}

/** «сегодня», «завтра» or a short weekday with the date. */
export function dayLabel(iso, today = null) {
  if (today && iso === today) return t("сегодня");
  if (today && iso === addDays(today, 1)) return t("завтра");
  return new Intl.DateTimeFormat(localeTag(), { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" })
    .format(new Date(`${iso}T12:00:00Z`));
}

function weekdayName(code) {
  // 2026-09-28 is a Monday.
  return new Intl.DateTimeFormat(localeTag(), { weekday: "short", timeZone: "UTC" })
    .format(new Date(Date.UTC(2026, 8, 28 + DAY_CODES.indexOf(code))));
}

/** A spoken repeat as the parser writes it (RRULE) in a few words. */
export function recurrenceText(rules) {
  const source = String(Array.isArray(rules) ? rules[0] || "" : rules || "").replace(/^RRULE:/, "");
  const rule = Object.fromEntries(source.split(";").filter(Boolean).map(part => part.split("=")));
  const interval = Number(rule.INTERVAL) > 1 ? Number(rule.INTERVAL) : 1;
  const days = String(rule.BYDAY || "").split(",").filter(Boolean);
  const plain = days.filter(day => DAY_CODES.includes(day));
  const names = plain.map(weekdayName).join(", ");
  switch (rule.FREQ) {
    case "DAILY":
      return interval === 1 ? t("ежедневно") : t("раз в {count} дн.", { count: interval });
    case "WEEKLY": {
      if (interval > 1) return plain.length ? t("раз в {count} нед.: {days}", { count: interval, days: names })
        : t("раз в {count} нед.", { count: interval });
      const list = plain.join(",");
      if (list === "MO,TU,WE,TH,FR") return t("по будням");
      if (list === "SA,SU") return t("по выходным");
      return plain.length ? t("еженедельно: {days}", { days: names }) : t("еженедельно");
    }
    case "MONTHLY": {
      const nth = /^(-?\d)(MO|TU|WE|TH|FR|SA|SU)$/.exec(days[0] || "");
      if (!nth) return t("ежемесячно");
      const day = weekdayName(nth[2]);
      return nth[1] === "-1" ? t("ежемесячно: последний {day}", { day }) : t("ежемесячно: {number}-й {day}", { number: nth[1], day });
    }
    case "YEARLY":
      return t("ежегодно");
    default:
      return t("повторяется");
  }
}

/** Day and time of a planned record, or null when it has neither. */
export function whenText(plan, today = null) {
  const event = plan?.event;
  if (event) {
    const day = event.recurrence?.length ? recurrenceText(event.recurrence) : dayLabel(event.start.slice(0, 10), today);
    const time = event.all_day ? t("весь день") : `${event.start.slice(11, 16)}–${event.end.slice(11, 16)}`;
    return capitalize(`${day} · ${time}`);
  }
  if (plan?.due) return capitalize(dayLabel(plan.due, today));
  if (plan?.planning_horizon === "week") return t("На неделе");
  if (plan?.planning_horizon === "month") return t("На месяц");
  return TASK_KINDS.has(plan?.kind) ? t("Без срока") : null;
}

function hostOf(url) {
  try {
    return new URL(url).host || url;
  } catch {
    return url;
  }
}

/**
 * Chips for the service's preview (POST /api/dictation/parse → `plan`), the
 * title, notes worth a glance, the calendar switch, and the values «Авто»
 * stands for in each select. `labels` are the capture form's option texts.
 */
export function describePlan(preview, { today = null, labels = {} } = {}) {
  const plan = preview?.plan;
  if (!plan) return null;
  const kinds = labels.kinds || {}, media = labels.media || {}, importance = labels.importance || {};
  const kindText = kinds[plan.kind] || plan.kind;
  const chips = [{ kind: "kind", text: plan.kind === "media" && plan.media_type
    ? `${kindText} · ${media[plan.media_type] || plan.media_type}` : kindText }];
  const when = whenText(plan, today);
  if (when) chips.push({ kind: "when", text: when });
  if (plan.route === "calendar") {
    chips.push({ kind: "route", route: "calendar", text: preview.calendar?.name
      ? t("→ Google Календарь «{name}»", { name: preview.calendar.name }) : t("→ Google Календарь") });
  } else if (plan.reason) {
    chips.push({ kind: "route", route: "note", text: t("→ дело в Brainalot") });
  }
  if (plan.context_title) {
    chips.push({ kind: "context", text: plan.context_kind === "person"
      ? t("Человек: {title}", { title: plan.context_title }) : t("Проект: {title}", { title: plan.context_title }) });
  }
  const level = { low: t("Низкая важность"), high: t("Высокая важность"), critical: t("Очень высокая важность") }[plan.importance];
  if (level) chips.push({ kind: "importance", text: level });
  for (const tag of plan.tags || []) chips.push({ kind: "tag", text: `#${tag}` });
  if (plan.url) chips.push({ kind: "link", text: hostOf(plan.url) });
  const notes = [];
  if (plan.reason === "write_access") {
    notes.push({ tone: "info", text: t("Чтобы создавать события, подключите Google API в настройках панели. Пока время будет в названии дела.") });
  }
  if (plan.reason === "attachments") notes.push({ tone: "info", text: t("С файлами запись остаётся в Brainalot, время будет в названии дела.") });
  for (const warning of plan.warnings || []) notes.push({ tone: "warning", text: translateServerMessage(warning) });
  const toggle = plan.route === "calendar" ? { next: "note", text: t("Сохранить делом") }
    : plan.reason === "manual" && preview.calendar?.available ? { next: AUTO, text: t("Вернуть в календарь") } : null;
  const day = plan.event && !plan.event.recurrence?.length ? plan.event.start.slice(0, 10) : plan.due;
  return {
    kind: plan.kind, chips, title: plan.title, notes, toggle,
    auto: {
      kind: chips[0].text,
      schedule: day ? dayLabel(day, today) : plan.event ? recurrenceText(plan.event.recurrence)
        : plan.planning_horizon === "week" ? t("на неделе") : plan.planning_horizon === "month" ? t("на месяц") : t("без срока"),
      context: plan.context_title || t("без привязки"),
      importance: importance[plan.importance] || plan.importance,
    },
  };
}
