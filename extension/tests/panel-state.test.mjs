import test from "node:test";
import assert from "node:assert/strict";
import {addDays, assignmentPatch, calendarEventPhase, calendarEventTime, calendarEventsForDate, capturePayload, chooseResurface, clearIfUnchanged, compareCalendarEvents, createRefreshGate, currentWeekDueOptions, dayTasks, editableNoteText, editNotePatch, imageFit, markdownNoteBody, mixPose, normalizeTheme, overdueTasks, posterHue, reorderedIds, resurfaceCandidates, stackDragCommits, stackEase, stackPose, stepResurface, tabCapture, thoughtRecords, visibleWeekTasks, weekDates, weekStart} from "../panel-state.js";

test("recall stack poses shrink toward the right edge and leave fully to the left", () => {
  const front = stackPose(0, 280);
  const next = stackPose(1, 280);
  const after = stackPose(2, 280);
  assert.deepEqual(front, {x:0, s:1, wash:0, spine:0, opacity:1});
  assert.ok(next.x > 0 && next.s < 1 && next.wash > 0 && next.spine === 1);
  assert.ok(after.x > next.x && after.s < next.s && after.wash > next.wash);
  assert.equal(stackPose(3, 280).opacity, 0);
  assert.ok(stackPose(-1, 280).x <= -280, "the exiting card clears the stage");
  const middle = mixPose(front, next, .5);
  assert.equal(middle.x, next.x / 2);
  assert.equal(middle.s, (1 + next.s) / 2);
});

test("recall stack easing and drag release are bounded and flick-aware", () => {
  assert.equal(stackEase(-1), 0);
  assert.equal(stackEase(0), 0);
  assert.equal(stackEase(1), 1);
  assert.equal(stackEase(2), 1);
  assert.ok(stackEase(.5) > .5, "motion decelerates into the slot");
  assert.equal(stackDragCommits(.4, 0), true);
  assert.equal(stackDragCommits(.1, 0), false);
  assert.equal(stackDragCommits(.12, .8), true, "a quick flick commits early");
  assert.equal(stackDragCommits(.08, 3), false, "a fast twitch of a few pixels does not");
  assert.equal(stackDragCommits(.25, .2), false, "a slow short drag springs back");
});

test("card images fill the frame only when cropping stays small", () => {
  assert.equal(imageFit(640, 360, 290, 180), "cover");
  assert.equal(imageFit(360, 640, 290, 200), "contain");
  assert.equal(imageFit(480, 480, 290, 200), "contain");
  assert.equal(imageFit(0, 0, 290, 200), "contain");
});

test("poster hue is stable per record and varies between records", () => {
  assert.equal(posterHue("abc"), posterHue("abc"));
  const hues = new Set(["a", "b", "c", "d", "e", "f", "g", "h"].map(posterHue));
  assert.ok(hues.size >= 3);
  assert.equal(typeof posterHue(undefined), "number");
});

test("refresh gate serializes reads and performs a requested follow-up", async () => {
  let release;
  let active = 0;
  let calls = 0;
  const firstRead = new Promise(resolve => { release = resolve; });
  const refresh = createRefreshGate(async () => {
    active += 1;
    assert.equal(active, 1);
    calls += 1;
    if (calls === 1) await firstRead;
    active -= 1;
  });
  const first = refresh();
  const second = refresh();
  assert.equal(first, second);
  release();
  await first;
  assert.equal(calls, 2);
  await refresh();
  assert.equal(calls, 3);
});

test("capture ignores hidden due and keeps text typed during pending enqueue", () => {
  const payload = capturePayload({text:"Мысль",kind:"idea",due:"2026-09-20",mediaType:"movie",contextId:"",topic:"",importance:"normal"});
  assert.equal(payload.due, null);
  assert.equal(payload.planning_horizon, null);
  assert.equal(payload.media_type, null);
  assert.equal(payload.show_in_unscheduled, false);
  const both= capturePayload({text:"Посмотреть визуальные идеи",kind:"thought",due:null,mediaType:null,contextId:"",topic:"",importance:"normal",showInUnscheduled:true});
  assert.equal(both.kind, "thought");
  assert.equal(both.due, null);
  assert.equal(both.show_in_unscheduled, true);
  const task=capturePayload({text:"Дело",kind:"task",due:null,mediaType:null,contextId:"",topic:"",importance:"normal",showInUnscheduled:true});
  assert.equal(task.show_in_unscheduled, false);
  assert.equal(task.planning_horizon, null);
  const weekTask=capturePayload({text:"На неделе",kind:"task",due:null,planningHorizon:"week",mediaType:null,contextId:"",topic:"",importance:"normal"});
  assert.equal(weekTask.planning_horizon, "week");
  const datedTask=capturePayload({text:"В день",kind:"task",due:"2026-09-21",planningHorizon:"month",mediaType:null,contextId:"",topic:"",importance:"normal"});
  assert.equal(datedTask.planning_horizon, null);
  assert.equal(clearIfUnchanged("Новая мысль", "Мысль"), "Новая мысль");
  assert.equal(clearIfUnchanged("Мысль", "Мысль"), "");
});

test("editor strips the generated heading and sends a complete compatible Markdown patch", () => {
  assert.equal(editableNoteText("\n# Старое название\n\nТекст\n\n## Раздел\nЕщё"), "Текст\n\n## Раздел\nЕщё");
  assert.equal(markdownNoteBody("Новое название", "Текст"), "\n# Новое название\n\nТекст\n");
  const mediaPatch = editNotePatch({title:" Видео ", text:"Описание", kind:"media", schedule:"month", exactDate:"2026-09-23", mediaType:"movie", contextId:"p1", topic:"  Референсы ", importance:"high", url:" https://example.com ", showInCarousel:false, showInUnscheduled:true});
  assert.deepEqual(mediaPatch, {
    title:"Видео", body:"\n# Видео\n\nОписание\n", kind:"media", due:null, planning_horizon:null,
    media_type:"movie", context_id:"p1", topic:"Референсы", importance:"high",
    url:"https://example.com", show_in_carousel:false, show_in_unscheduled:false,
  });
  const datedTask = editNotePatch({title:"Дело", text:"", kind:"task", schedule:"date", exactDate:"2026-09-23"});
  assert.equal(datedTask.due, "2026-09-23");
  assert.equal(datedTask.planning_horizon, null);
  assert.throws(() => editNotePatch({title:"Дело", kind:"task", schedule:"date"}), /точную дату/);
});

test("drop assignment converts any record and clears fields incompatible with its destination", () => {
  const idea = {kind:"idea", status:"active", media_type:"movie", show_in_unscheduled:true};
  assert.deepEqual(assignmentPatch(idea, {area:"today", date:"2026-09-21"}), {
    kind:"task", status:"active", due:"2026-09-21", planning_horizon:null, media_type:null, show_in_unscheduled:false,
  });
  assert.deepEqual(assignmentPatch({kind:"purchase"}, {area:"backlog", bucket:"month"}), {
    kind:"purchase", status:"active", due:null, planning_horizon:"month", media_type:null, show_in_unscheduled:false,
  });
  assert.deepEqual(assignmentPatch({kind:"person"}, {area:"inbox"}), {
    kind:"inbox", status:"inbox", due:null, planning_horizon:null, media_type:null, show_in_unscheduled:false,
  });
  assert.equal(assignmentPatch({kind:"idea"}, {area:"backlog",bucket:"thoughts"}), null);
});

test("thought view includes active ideas and thoughts only, without turning them into tasks", () => {
  const notes = [
    {id:"a",kind:"thought",status:"active",show_in_unscheduled:true},
    {id:"b",kind:"idea",status:"active"},
    {id:"c",kind:"thought",status:"archived"},
    {id:"d",kind:"idea",status:"done"},
    {id:"e",kind:"idea",status:"cancelled"},
    {id:"f",kind:"thought",status:"inbox"},
    {id:"g",kind:"task",status:"active"},
  ];
  assert.deepEqual(thoughtRecords(notes).map(note => note.id),["a","b"]);
  assert.equal(notes[0].kind,"thought");
});

test("Recall excludes project cards even while the local service still suggests them", () => {
  const project = {id:"p",kind:"project"};
  const thought = {id:"t",kind:"thought"};
  assert.deepEqual(resurfaceCandidates({resurface:project,resurface_candidates:[project,thought]}),[thought]);
  assert.deepEqual(resurfaceCandidates({resurface:project}),[]);
});

test("theme accepts explicit light and dark values and safely falls back to system", () => {
  assert.equal(normalizeTheme("light"), "light");
  assert.equal(normalizeTheme("dark"), "dark");
  assert.equal(normalizeTheme("system"), "system");
  assert.equal(normalizeTheme("unknown"), "system");
  assert.equal(normalizeTheme(null), "system");
});

test("long tab title is valid metadata and retained in body", () => {
  const text = "x".repeat(250);
  const payload = tabCapture({title:text, url:"https://example.com"});
  assert.equal(payload.title.length, 200);
  assert.equal(payload.text, text);
});

test("week helpers use calendar dates and begin weeks on Monday", () => {
  assert.equal(addDays("2026-09-20", 1), "2026-09-21");
  assert.equal(weekStart("2026-09-20"), "2026-09-14");
  assert.deepEqual(weekDates("2026-09-14"), ["2026-09-14","2026-09-15","2026-09-16","2026-09-17","2026-09-18","2026-09-19","2026-09-20"]);
});

test("task reorder moves one id without losing the full order", () => {
  assert.deepEqual(reorderedIds(["a","b","c","d"], "a", "c", true), ["b","c","a","d"]);
  assert.deepEqual(reorderedIds(["a","b","c","d"], "d", "b", false), ["a","d","b","c"]);
  assert.deepEqual(reorderedIds(["a","b"], "missing", "b"), ["a","b"]);
});

test("quick due choices include today and only later days of the same week", () => {
  assert.deepEqual(currentWeekDueOptions("2026-09-18"), [
    {value:"2026-09-18",label:"Сегодня"},
    {value:"2026-09-19",label:"суббота"},
    {value:"2026-09-20",label:"воскресенье"},
  ]);
  assert.deepEqual(currentWeekDueOptions("2026-09-20"), [
    {value:"2026-09-20",label:"Сегодня"},
  ]);
  assert.deepEqual(currentWeekDueOptions("2026-12-31").map(choice => choice.value), [
    "2026-12-31", "2027-01-01", "2027-01-02", "2027-01-03",
  ]);
});

test("planner deduplicates active tasks and keeps overdue separate from today", () => {
  const active = {id:"a", kind:"task", status:"active", due:"2026-09-19"};
  const duplicate = {...active};
  const overdue = {id:"old", kind:"purchase", status:"active", due:"2026-09-17"};
  const archived = {id:"closed", kind:"task", status:"archived", due:"2026-09-19"};
  const dashboard = {today:[active, overdue], week:[duplicate, archived], upcoming:[], unscheduled:[], overdue_yesterday:[overdue], overdue_older:[overdue]};
  const visible = visibleWeekTasks(dashboard, weekDates("2026-09-14"));
  assert.deepEqual(visible.map(note => note.id), ["a", "old"]);
  assert.deepEqual(dayTasks(visible, "2026-09-19").map(note => note.id), ["a"]);
  assert.deepEqual(overdueTasks(dashboard, "2026-09-19").map(note => note.id), ["old"]);
});

test("calendar only exposes events whose declared dates include the selected day", () => {
  const timed = {id:"one", title:"Встреча", start:"2026-09-19T10:00:00+03:00", end:"2026-09-19T11:30:00+03:00", all_day:false, dates:["2026-09-19"]};
  const allDay = {id:"two", title:"Отпуск", all_day:true, dates:["2026-09-20","2026-09-21"]};
  assert.deepEqual(calendarEventsForDate([timed, allDay], "2026-09-19").map(event => event.id), ["one"]);
  assert.equal(calendarEventTime(allDay), "Весь день");
  assert.match(calendarEventTime(timed), /^\d{2}:\d{2}–\d{2}:\d{2}$/);
});

test("calendar event colors change at two hours and thirty minutes, then disappear at the end", () => {
  const event = {start:"2026-09-19T10:00:00+03:00", end:"2026-09-19T11:00:00+03:00", all_day:false};
  const start = Date.parse(event.start);
  const end = Date.parse(event.end);
  assert.equal(calendarEventPhase(event, start - 2*60*60*1000 - 1), "normal");
  assert.equal(calendarEventPhase(event, start - 2*60*60*1000), "soon");
  assert.equal(calendarEventPhase(event, start - 30*60*1000 - 1), "soon");
  assert.equal(calendarEventPhase(event, start - 30*60*1000), "imminent");
  assert.equal(calendarEventPhase(event, start), "imminent");
  assert.equal(calendarEventPhase(event, end - 1), "imminent");
  assert.equal(calendarEventPhase(event, end), "finished");
  const allDay = {start:"2026-09-20T00:00:00+03:00", end:"2026-09-21T00:00:00+03:00", all_day:true};
  assert.equal(calendarEventPhase(allDay, Date.parse(allDay.start) - 20*60*1000), "all-day");
  assert.equal(calendarEventPhase(allDay, Date.parse(allDay.start) + 12*60*60*1000), "all-day");
  assert.equal(calendarEventPhase(allDay, Date.parse(allDay.end)), "finished");
});

test("calendar places all-day context first, then timed urgency and time", () => {
  const now = Date.parse("2026-09-19T08:00:00+03:00");
  const events = [
    {event:{title:"Позже", start:"2026-09-19T15:00:00+03:00", end:"2026-09-19T16:00:00+03:00"}, phase:"normal"},
    {event:{title:"На весь день", all_day:true, start:"2026-09-19T00:00:00+03:00", end:"2026-09-20T00:00:00+03:00"}, phase:"all-day"},
    {event:{title:"Ближайшее", start:"2026-09-19T09:00:00+03:00", end:"2026-09-19T10:00:00+03:00"}, phase:"soon"},
    {event:{title:"Срочное", start:"2026-09-19T08:15:00+03:00", end:"2026-09-19T09:00:00+03:00"}, phase:"imminent"},
    {event:{title:"Раньше", start:"2026-09-19T13:00:00+03:00", end:"2026-09-19T14:00:00+03:00"}, phase:"normal"},
  ];
  events.sort(compareCalendarEvents);
  assert.deepEqual(events.map(item => item.event.title), ["На весь день", "Срочное", "Ближайшее", "Раньше", "Позже"]);
  assert.equal(compareCalendarEvents({event:events[0].event, now}, {event:events[1].event, now}) < 0, true);
});

test("manual resurface wraps, holds through refresh, then yields to automatic choice", () => {
  const cards = [{id:"a"}, {id:"b"}, {id:"c"}];
  const now = 10_000;
  assert.equal(chooseResurface(cards, cards[1], null, 0, now)?.id, "b");
  const previous = stepResurface(cards, "a", -1, now);
  assert.equal(previous.id, "c");
  assert.equal(stepResurface(cards, "c", 1, now).id, "a");
  assert.equal(chooseResurface(cards, cards[1], previous.id, previous.holdUntil, now + 30_000)?.id, "c");
  assert.equal(chooseResurface(cards, cards[1], previous.id, previous.holdUntil, previous.holdUntil)?.id, "b");
  assert.equal(chooseResurface(cards, cards[1], previous.id, 0, now, true)?.id, "c");
  assert.equal(chooseResurface(cards.filter(card => card.id !== "c"), cards[1], previous.id, previous.holdUntil, now)?.id, "b");
  assert.equal(chooseResurface([], null, previous.id, previous.holdUntil, now), null);
  assert.equal(stepResurface([], null, 1, now).id, null);
  assert.equal(stepResurface([cards[0]], "a", 1, now).id, "a");
});
