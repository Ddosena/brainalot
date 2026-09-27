import test from "node:test";
import assert from "node:assert/strict";
import { FIRE_LIMIT, calendarImportanceKey, calendarImportanceLevel, calendarIsImportant, fireGroupOfTask, importanceStep, normalizeCalendarImportance, withCalendarImportance } from "../importance-state.js";

test("calendar importance follows the stable occurrence id through a move", () => {
  const source = {calendar_id:"work", id:"old-id", occurrence_id:"work:series:2026-09-23T10:00:00+03:00", start:"2026-09-23T10:00:00+03:00"};
  const moved = {...source, id:"new-id", start:"2026-09-30T10:00:00+03:00"};
  const saved = withCalendarImportance({}, source, true);
  assert.equal(calendarImportanceKey(source), calendarImportanceKey(moved));
  assert.equal(calendarIsImportant(saved, moved), true);
  assert.deepEqual(withCalendarImportance(saved, moved, false), {});
});

test("calendar importance keeps high and critical flags and scopes legacy events", () => {
  assert.deepEqual(normalizeCalendarImportance({good:"high", fire:"critical", old:"normal", bad:3}), {good:"high",fire:"critical"});
  const first = {calendar_id:"one", id:"same", start:"2026-09-23T10:00:00+03:00"};
  const other = {...first, calendar_id:"two"};
  const saved = withCalendarImportance({}, first, true);
  assert.equal(calendarIsImportant(saved, other), false);
});

test("fire cycles through the third level and refuses a second mark in one group", () => {
  assert.equal(FIRE_LIMIT, 1);
  assert.deepEqual(importanceStep("normal", FIRE_LIMIT), {next:"high",blocked:false});
  assert.deepEqual(importanceStep("high", FIRE_LIMIT - 1), {next:"critical",blocked:false});
  assert.deepEqual(importanceStep("high", FIRE_LIMIT), {next:"high",blocked:true});
  assert.deepEqual(importanceStep("critical", FIRE_LIMIT), {next:"normal",blocked:false});
  const occurrence = {calendar_id:"one",occurrence_id:"series-2026-09-23",start:"2026-09-23T10:00:00+03:00"};
  assert.equal(calendarImportanceLevel(withCalendarImportance({},occurrence,"critical"),occurrence),"critical");
});

test("overdue tasks share today's fire limit; dateless tasks have a separate group", () => {
  assert.equal(fireGroupOfTask({due:"2026-09-22"},"2026-09-23"),"2026-09-23");
  assert.equal(fireGroupOfTask({due:"2026-09-24"},"2026-09-23"),"2026-09-24");
  assert.equal(fireGroupOfTask({due:null},"2026-09-23"),"backlog");
});
