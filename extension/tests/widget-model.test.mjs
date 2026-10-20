import test from 'node:test';
import assert from 'node:assert/strict';
import {activeTask, compactTasks, resolveActiveTask, untrackedStep, currentEvent, timerProgress, ASK_AFTER_MS, AWAY_MS} from '../widget-model.js';
const a={source:'library',id:'a',title:'Hot'}, b={source:'calendar',id:'b',title:'Current'};
test('compact tasks keep selection, deduplicate and deterministically recover',()=>{
  const model=compactTasks([a,b,a],'library:a',{status:'paused',task:b});
  assert.equal(model.tasks.length,2); assert.equal(model.hot,a); assert.equal(model.current,b); assert.equal(model.same,false);
  assert.equal(compactTasks([a,b],'missing',null).selected,'calendar:b');
});
test('stopped/completed task does not retain card, pause does',()=>{
  assert.equal(activeTask({status:'paused',task:b}),b);
  for(const status of ['idle','stopped','completed']) assert.equal(compactTasks([],null,{status,task:b}).hasTask,false);
});
test('same stable source/id has one title and taskless timer is honest',()=>{
  assert.equal(compactTasks([a],'library:a',{status:'running',task:{...a,title:'New title'}}).same,true);
  assert.equal(compactTasks([],null,{status:'running',task:null}).hasTask,false);
});
test('paused completed/deleted tasks do not hold a card; live title follows stable id',()=>{
  const paused={status:'paused',task:a};
  assert.equal(resolveActiveTask(paused,[{...a,status:'done'}]),null);
  assert.equal(resolveActiveTask(paused,[]),null);
  assert.equal(resolveActiveTask(paused,[{...a,status:'active',title:'Renamed'}]).title,'Renamed');
  assert.equal(resolveActiveTask({...paused,status:'running'},[]).id,a.id);
});

test('asks after ten active untracked minutes, restarts after being away, never while tracked',()=>{
  const minute=60_000,start=1_000_000;
  let step=untrackedStep(null,{tracked:false,idleMs:20_000,now:start});
  assert.deepEqual(step,{gap:{since:start-20_000},ask:false});
  step=untrackedStep(step.gap,{tracked:false,idleMs:1_000,now:start+9*minute});
  assert.equal(step.ask,false);
  step=untrackedStep(step.gap,{tracked:false,idleMs:1_000,now:start+10*minute});
  assert.equal(step.ask,true,'10 minutes after the stretch began');
  assert.equal(step.gap.since,start-20_000,'the stretch keeps its beginning for the backfill');
  assert.deepEqual(untrackedStep(step.gap,{tracked:false,idleMs:AWAY_MS,now:start+11*minute}),{gap:null,ask:false});
  assert.deepEqual(untrackedStep(step.gap,{tracked:true,idleMs:0,now:start+11*minute}),{gap:null,ask:false});
  assert.equal(ASK_AFTER_MS,10*minute);
});

test('a running timed calendar event counts as tracked time, all-day events do not',()=>{
  const events=[{all_day:true,start:'2026-10-19T00:00:00+03:00',end:'2026-10-20T00:00:00+03:00'},
    {all_day:false,calendar_id:'c',id:'e',start:'2026-10-19T15:00:00+03:00',end:'2026-10-19T16:00:00+03:00'}];
  assert.equal(currentEvent(events,Date.parse('2026-10-19T15:30:00+03:00'))?.id,'e');
  assert.equal(currentEvent(events,Date.parse('2026-10-19T16:00:00+03:00')),null);
  assert.equal(currentEvent(events,Date.parse('2026-10-19T12:00:00+03:00')),null);
  assert.equal(currentEvent(events.map(event=>({...event,time_skipped:true})),Date.parse('2026-10-19T15:30:00+03:00')),null,'did-not-happen must not suppress the untracked question');
});

test('timer and break progress are real bounded elapsed fractions; stopwatch has none',()=>{
  for (const mode of ['pomodoro','break']) {
    assert.equal(timerProgress({mode,duration_ms:1000},250),25);
    assert.equal(timerProgress({mode,duration_ms:1000},1500),100);
    assert.equal(timerProgress({mode,duration_ms:1000},-20),0);
  }
  assert.equal(timerProgress({mode:'stopwatch',duration_ms:1000},500),null);
  assert.equal(timerProgress(null,500),null);
});
