import test from "node:test";
import assert from "node:assert/strict";
import { calendarOccurrenceKey, createCalendarOverlay, createNoteDeletionOverlay, shiftCalendarEvent } from "../optimistic.js";

function note(id) { return {id,kind:"thought",status:"active",version:"v1"}; }
function dashboard(records) {
  return {today:records,unscheduled:records,library:records,resurface:records[0] || null,resurface_candidates:records};
}
function event(calendarId,id,start,dates = [start.slice(0,10)]) {
  return {calendar_id:calendarId,id,occurrence_id:`${calendarId}:${id}:${start}`,start,end:start.replace("10:00","11:00"),dates,title:"Встреча",all_day:false};
}

test("delayed note delete hides every view; reads started before success cannot clear its overlay", async () => {
  const overlay = createNoteDeletionOverlay();
  const a = note("a"), b = note("b");
  let acknowledge;
  const send = new Promise(resolve => { acknowledge = resolve; });
  const token = overlay.begin("a");
  let view = overlay.project(dashboard([a,b]),[a,b]);
  assert.deepEqual(view.notes.map(item=>item.id),["b"]);
  assert.deepEqual(view.dashboard.today.map(item=>item.id),["b"]);
  assert.equal(view.dashboard.resurface,null);
  acknowledge(); await send;
  overlay.succeed(token,3);
  overlay.reconcile(dashboard([b]),[b],3); // A read that began before the write.
  assert.equal(overlay.has("a"),true);
  overlay.reconcile(dashboard([a,b]),[a,b],4); // A fresh but stale server snapshot.
  assert.equal(overlay.has("a"),true);
  view = overlay.project(dashboard([a,b]),[a,b]);
  assert.deepEqual(view.notes.map(item=>item.id),["b"]);
  overlay.reconcile(dashboard([b]),[b],5);
  assert.equal(overlay.has("a"),false);
});

test("failure restores only its note while another successful deletion remains hidden", () => {
  const overlay = createNoteDeletionOverlay();
  const a = note("a"), b = note("b");
  const tokenA = overlay.begin("a"), tokenB = overlay.begin("b");
  overlay.succeed(tokenA,1);
  overlay.fail(tokenB);
  assert.deepEqual(overlay.project(dashboard([a,b]),[a,b]).notes.map(item=>item.id),["b"]);
  overlay.reconcile(dashboard([a,b]),[a,b],2);
  assert.deepEqual(overlay.project(dashboard([a,b]),[a,b]).notes.map(item=>item.id),["b"]);
});

test("calendar move preserves wall time, duration, and every multi-day date", () => {
  const original = {calendar_id:"one",id:"all-day",start:"2026-09-23T00:00:00+03:00",end:"2026-09-26T00:00:00+03:00",dates:["2026-09-23","2026-09-24","2026-09-25"],all_day:true};
  const moved = shiftCalendarEvent(original,"2026-10-02");
  assert.equal(moved.start,"2026-10-02T00:00:00+03:00");
  assert.equal(moved.end,"2026-10-05T00:00:00+03:00");
  assert.deepEqual(moved.dates,["2026-10-02","2026-10-03","2026-10-04"]);
  assert.equal(Date.parse(moved.end)-Date.parse(moved.start),Date.parse(original.end)-Date.parse(original.start));
  assert.equal(original.start,"2026-09-23T00:00:00+03:00");
  const clipped = {...original,start:"2026-09-20T00:00:00+03:00",end:"2026-09-25T00:00:00+03:00",dates:["2026-09-23","2026-09-24"]};
  assert.deepEqual(shiftCalendarEvent(clipped,"2026-09-26").dates,["2026-09-26","2026-09-27","2026-09-28","2026-09-29","2026-09-30"]);
});

test("recurring occurrence and calendar identity isolate delete and move overlays", () => {
  const overlay = createCalendarOverlay();
  const first = event("calendar-1","same-uid","2026-09-23T10:00:00+03:00");
  const repeat = event("calendar-1","same-uid","2026-09-24T10:00:00+03:00");
  const otherCalendar = event("calendar-2","same-uid","2026-09-23T10:00:00+03:00");
  assert.notEqual(calendarOccurrenceKey(first),calendarOccurrenceKey(repeat));
  assert.notEqual(calendarOccurrenceKey(first),calendarOccurrenceKey(otherCalendar));
  overlay.beginDelete(first);
  overlay.beginMove(repeat,"2026-09-26");
  const view = overlay.project([first,repeat,otherCalendar]);
  assert.equal(view.some(item=>calendarOccurrenceKey(item)===calendarOccurrenceKey(first)),false);
  assert.equal(view.some(item=>calendarOccurrenceKey(item)===calendarOccurrenceKey(otherCalendar)),true);
  assert.equal(view.some(item=>item.start.startsWith("2026-09-26")),true);
  assert.equal(view.find(item=>item.optimistic_pending).calendar_id,"calendar-1");
});

test("calendar overlays survive stale and unrelated-week reads, then reconcile only a fresh old-week confirmation", () => {
  const overlay = createCalendarOverlay();
  const first = event("calendar-1","a","2026-09-23T10:00:00+03:00");
  const second = event("calendar-1","b","2026-09-23T10:00:00+03:00");
  const a = overlay.beginDelete(first), b = overlay.beginMove(second,"2026-09-25");
  overlay.succeed(a,2); overlay.succeed(b,2);
  overlay.reconcile([], {start:"2026-09-30",end:"2026-10-06",readSeq:3});
  assert.equal(overlay.has(first),true);
  overlay.reconcile([], {start:"2026-09-21",end:"2026-09-27",readSeq:3,stale:true});
  assert.equal(overlay.has(first),true);
  overlay.reconcile([first,second], {start:"2026-09-21",end:"2026-09-27",readSeq:3});
  assert.equal(overlay.has(first),true);
  assert.equal(overlay.project([first,second]).length,1);
  overlay.reconcile([], {start:"2026-09-21",end:"2026-09-27",readSeq:4});
  assert.equal(overlay.has(first),false);
  assert.equal(overlay.has(second),true); // Old event vanished, but the destination is not confirmed yet.
  const moved = {...shiftCalendarEvent(second,"2026-09-25"),id:"new-server-id"};
  delete moved.optimistic_pending;
  assert.equal(overlay.project([moved])[0].optimistic_pending,true);
  overlay.reconcile([moved], {start:"2026-09-21",end:"2026-09-27",readSeq:5});
  assert.equal(overlay.has(second),false);
});

test("move stays projected after old-week disappearance until its stable occurrence appears at destination", () => {
  const overlay = createCalendarOverlay();
  const source = event("one","old","2026-09-23T10:00:00+03:00");
  const token = overlay.beginMove(source,"2026-10-02");
  overlay.succeed(token,1);
  overlay.reconcile([], {start:"2026-09-21",end:"2026-09-27",readSeq:2});
  assert.equal(overlay.has(source),true);
  const arrived = {...shiftCalendarEvent(source,"2026-10-02"),id:"new"};
  delete arrived.optimistic_pending;
  overlay.reconcile([arrived], {start:"2026-09-28",end:"2026-10-04",readSeq:3});
  assert.equal(overlay.has(source),false);
});

test("same-title event at the destination cannot impersonate the moved occurrence", () => {
  const overlay = createCalendarOverlay();
  const source = event("one","old-id","2026-09-23T10:00:00+03:00");
  const impostor = event("one","other-id","2026-09-25T10:00:00+03:00");
  assert.equal(source.title,impostor.title);
  const token = overlay.beginMove(source,"2026-09-25");
  overlay.succeed(token,1);
  assert.equal(overlay.project([impostor]).length,2);
  overlay.reconcile([impostor],{start:"2026-09-21",end:"2026-09-27",readSeq:2});
  assert.equal(overlay.has(source),true);
  const actual = {...shiftCalendarEvent(source,"2026-09-25"),id:"new-id"};
  delete actual.optimistic_pending;
  assert.equal(overlay.project([impostor,actual]).length,2);
  overlay.reconcile([source,impostor,actual],{start:"2026-09-21",end:"2026-09-27",readSeq:3});
  assert.equal(overlay.has(source),true);
  overlay.reconcile([impostor,actual],{start:"2026-09-21",end:"2026-09-27",readSeq:3});
  assert.equal(overlay.has(source),false);
});

test("a failed calendar operation rolls back only that occurrence while another pending move remains", () => {
  const overlay = createCalendarOverlay();
  const first = event("calendar-1","a","2026-09-23T10:00:00+03:00");
  const second = event("calendar-1","b","2026-09-23T10:00:00+03:00");
  const a = overlay.beginDelete(first), b = overlay.beginMove(second,"2026-09-25");
  overlay.fail(a);
  const view = overlay.project([first,second]);
  assert.equal(view.some(item=>calendarOccurrenceKey(item)===calendarOccurrenceKey(first)),true);
  assert.equal(view.some(item=>calendarOccurrenceKey(item)===calendarOccurrenceKey(second)),false);
  assert.equal(view.some(item=>item.start.startsWith("2026-09-25")),true);
  overlay.succeed(b,3);
  overlay.reconcile([first,second], {start:"2026-09-21",end:"2026-09-27",readSeq:3});
  assert.equal(overlay.has(second),true);
});
