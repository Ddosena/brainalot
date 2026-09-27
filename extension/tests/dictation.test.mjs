import assert from "node:assert/strict";
import test from "node:test";

import {
  AUTO, checkDictationPayload, dictationOverrides, dictationPayload, describePlan, recurrenceText,
  scheduleOverride, usesDictation, whenText,
} from "../dictation.js";
import { setLocale } from "../i18n.js";

const TODAY = "2026-09-24";
const draft = (fields = {}) => ({ kind: AUTO, schedule: AUTO, exactDate: "", mediaType: "movie", contextId: AUTO,
  importance: AUTO, route: AUTO, ...fields });
const labels = {
  kinds: { inbox: "Входящие", task: "Дело", purchase: "Покупка", media: "Медиа", thought: "Мысль" },
  media: { movie: "Фильм", book: "Книга" },
  importance: { low: "Низкая", normal: "Обычная", high: "Высокая", critical: "Очень высокая" },
};

test.afterEach(() => setLocale("ru"));

test("text goes through the parser unless it is empty or too long for it", () => {
  assert.equal(usesDictation("завтра купить хлеб"), true);
  assert.equal(usesDictation("   "), false);
  assert.equal(usesDictation("я".repeat(2000)), true);
  assert.equal(usesDictation(" " + "я".repeat(2001)), false);
  assert.equal(usesDictation(undefined), false);
});

test("fields left on «Авто» are not corrections", () => {
  assert.deepEqual(dictationOverrides(draft()), {});
  assert.deepEqual(dictationOverrides(draft({ kind: "media", mediaType: "book" })), { kind: "media", media_type: "book" });
  assert.deepEqual(dictationOverrides(draft({ contextId: "" })), { context_id: null });
  assert.deepEqual(dictationOverrides(draft({ contextId: "a".repeat(32), importance: "high", route: "note" })),
    { context_id: "a".repeat(32), importance: "high", route: "note" });
});

test("«Когда» set by hand becomes a date, a basket or no date", () => {
  assert.deepEqual(dictationOverrides(draft({ schedule: "" })), { due: null, planning_horizon: null });
  assert.deepEqual(dictationOverrides(draft({ schedule: "week" })), { due: null, planning_horizon: "week" });
  assert.deepEqual(dictationOverrides(draft({ kind: "task", schedule: "2026-09-26" }), { today: TODAY }),
    { kind: "task", due: "2026-09-26", planning_horizon: null });
  assert.deepEqual(dictationOverrides(draft({ schedule: "custom", exactDate: "2026-10-02" })),
    { due: "2026-10-02", planning_horizon: null });
  // A manual kind without dates ignores the schedule, as the form disables it.
  assert.deepEqual(dictationOverrides(draft({ kind: "thought", schedule: "week" })), { kind: "thought" });
});

test("an unfinished date is skipped while typing and reported on save", () => {
  const unfinished = draft({ schedule: "custom", exactDate: "" });
  assert.deepEqual(dictationOverrides(unfinished), {});
  assert.throws(() => dictationOverrides(unfinished, { strict: true }), /точную дату/);
  assert.throws(() => scheduleOverride({ schedule: "2026-09-01" }, TODAY), /будущий день/);
});

test("the queued payload keeps only fields the service accepts", () => {
  const payload = dictationPayload({ text: "купить хлеб", overrides: { kind: "purchase" }, topic: "  ",
    showInUnscheduled: true, attachments: [{ localId: "x", name: "a.png", type: "image/png", size: 3 }], voice: true });
  assert.deepEqual(payload, { text: "купить хлеб", overrides: { kind: "purchase" }, topic: null, show_in_unscheduled: true,
    attachments: [{ localId: "x", name: "a.png", type: "image/png", size: 3 }], source: "voice" });
  assert.deepEqual(checkDictationPayload({ ...payload, extra: 1 }), payload);
  assert.equal(checkDictationPayload({ text: "хлеб" }).source, "chrome");
  assert.throws(() => checkDictationPayload({ text: " " }), /от 1 до 2000/);
  assert.throws(() => checkDictationPayload({ text: "хлеб", overrides: [] }), /Неверная запись/);
  assert.throws(() => checkDictationPayload({ text: "хлеб", source: "cloud" }), /Неверная запись/);
  assert.throws(() => checkDictationPayload(null), /Неверная запись/);
});

test("repeats read as a few words", () => {
  assert.equal(recurrenceText(["RRULE:FREQ=DAILY"]), "ежедневно");
  assert.equal(recurrenceText(["RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"]), "по будням");
  assert.equal(recurrenceText(["RRULE:FREQ=WEEKLY;BYDAY=SA,SU"]), "по выходным");
  assert.equal(recurrenceText(["RRULE:FREQ=WEEKLY;BYDAY=MO,WE"]), "еженедельно: пн, ср");
  assert.equal(recurrenceText(["RRULE:FREQ=WEEKLY;INTERVAL=2"]), "раз в 2 нед.");
  assert.equal(recurrenceText(["RRULE:FREQ=MONTHLY;BYDAY=2TU"]), "ежемесячно: 2-й вт");
  assert.equal(recurrenceText(["RRULE:FREQ=MONTHLY;BYDAY=-1FR"]), "ежемесячно: последний пт");
  assert.equal(recurrenceText(["RRULE:FREQ=YEARLY"]), "ежегодно");
  setLocale("en");
  assert.equal(recurrenceText(["RRULE:FREQ=WEEKLY;BYDAY=MO,WE"]), "weekly: Mon, Wed");
  assert.equal(recurrenceText(["RRULE:FREQ=MONTHLY;BYDAY=2TU"]), "monthly: Tue #2");
});

test("the day and time come from the event as spoken, in its own time zone", () => {
  const event = { start: "2026-09-25T09:00:00+03:00", end: "2026-09-25T10:30:00+03:00", all_day: false, recurrence: null };
  assert.equal(whenText({ kind: "task", event }, TODAY), "Завтра · 09:00–10:30");
  assert.equal(whenText({ kind: "task", event: { ...event, recurrence: ["RRULE:FREQ=DAILY"] } }, TODAY), "Ежедневно · 09:00–10:30");
  assert.equal(whenText({ kind: "task", event: { start: "2026-09-24", end: "2026-09-25", all_day: true } }, TODAY), "Сегодня · весь день");
  assert.equal(whenText({ kind: "task", due: "2026-09-28" }, TODAY), "Пн, 28 сент.");
  assert.equal(whenText({ kind: "task", planning_horizon: "week" }, TODAY), "На неделе");
  assert.equal(whenText({ kind: "task" }, TODAY), "Без срока");
  assert.equal(whenText({ kind: "idea" }, TODAY), null);
});

test("a calendar preview shows the event, its calendar and a switch to a task", () => {
  const view = describePlan({ calendar: { available: true, name: "Личный" }, plan: {
    route: "calendar", kind: "task", title: "Вынести мусор", due: TODAY, importance: "high", tags: ["дом"],
    url: null, context_title: "Мама", context_kind: "person", reason: null, warnings: ["Время уже прошло"],
    event: { start: "2026-09-24T15:00:00+03:00", end: "2026-09-24T16:00:00+03:00", all_day: false, recurrence: null } } },
  { today: TODAY, labels });
  assert.deepEqual(view.chips.map(chip => chip.text), ["Дело", "Сегодня · 15:00–16:00", "→ Google Календарь «Личный»",
    "Человек: Мама", "Высокая важность", "#дом"]);
  assert.equal(view.title, "Вынести мусор");
  assert.deepEqual(view.notes, [{ tone: "warning", text: "Время уже прошло" }]);
  assert.equal(view.kind, "task");
  assert.deepEqual(view.toggle, { next: "note", text: "Сохранить делом" });
  assert.deepEqual(view.auto, { kind: "Дело", schedule: "сегодня", context: "Мама", importance: "Высокая" });
});

test("a task instead of an event says why and offers the calendar back only when it can be used", () => {
  const plan = { route: "note", kind: "task", title: "Созвон (09:00–10:00)", due: "2026-09-25", importance: "normal",
    tags: [], url: "https://example.com/a", context_title: null, reason: "write_access", warnings: [], event: null };
  const offline = describePlan({ calendar: { available: false }, plan }, { today: TODAY, labels });
  assert.deepEqual(offline.chips.map(chip => chip.text), ["Дело", "Завтра", "→ дело в Brainalot", "example.com"]);
  assert.match(offline.notes[0].text, /подключите Google API/);
  assert.equal(offline.notes[0].tone, "info");
  assert.equal(offline.toggle, null);
  const manual = describePlan({ calendar: { available: true, name: "Личный" }, plan: { ...plan, reason: "manual" } }, { today: TODAY, labels });
  assert.deepEqual(manual.toggle, { next: AUTO, text: "Вернуть в календарь" });
  assert.deepEqual(manual.notes, []);
  const files = describePlan({ calendar: { available: true }, plan: { ...plan, reason: "attachments" } }, { today: TODAY, labels });
  assert.match(files.notes[0].text, /С файлами/);
  assert.equal(files.toggle, null);
});

test("a plain note shows its kind and nothing about routes", () => {
  const view = describePlan({ calendar: { available: false }, plan: { route: "note", kind: "media", media_type: "book",
    title: "Дюна", due: null, planning_horizon: null, importance: "normal", tags: [], url: null, context_title: null,
    reason: null, warnings: [], event: null } }, { today: TODAY, labels });
  assert.deepEqual(view.chips.map(chip => chip.text), ["Медиа · Книга"]);
  assert.deepEqual(view.auto, { kind: "Медиа · Книга", schedule: "без срока", context: "без привязки", importance: "Обычная" });
  assert.equal(describePlan(null), null);
});
