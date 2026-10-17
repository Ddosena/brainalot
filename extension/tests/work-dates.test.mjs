import test from "node:test";
import assert from "node:assert/strict";
import { addDays, diff, isWeekend, mondayOf, relDays } from "../work-dates.js";
import { setLocale } from "../i18n.js";

test("work dates step across month ends and count whole days", () => {
  assert.equal(addDays("2026-09-29", 3), "2026-10-02");
  assert.equal(addDays("2026-03-01", -1), "2026-02-28");
  assert.equal(diff("2026-09-29", "2026-10-04"), 5);
  assert.equal(diff("2026-10-04", "2026-09-29"), -5);
});

test("work weeks start on Monday and weekends are Saturday and Sunday", () => {
  assert.equal(mondayOf("2026-09-29"), "2026-09-28");
  assert.equal(mondayOf("2026-10-04"), "2026-09-28");
  assert.equal(mondayOf("2026-09-28"), "2026-09-28");
  assert.equal(isWeekend("2026-10-03"), true);
  assert.equal(isWeekend("2026-10-02"), false);
});

test("relative days read naturally in Russian and English", () => {
  setLocale("ru");
  assert.deepEqual([0, 1, -1, 2, 5, -3].map(relDays), ["сегодня", "завтра", "вчера", "послезавтра", "через 5 дн.", "3 дн. назад"]);
  setLocale("en");
  assert.deepEqual([0, 1, -1, 2, 5, -3].map(relDays), ["today", "tomorrow", "yesterday", "the day after tomorrow", "in 5 days", "3 days ago"]);
  setLocale("ru");
});
