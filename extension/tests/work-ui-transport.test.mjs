import test from 'node:test'; import assert from 'node:assert/strict';
import {createWorkTransport} from '../work-ui/transport.js';
test('serializes writes with current revision and suppresses unchanged polling',async()=>{
 let rev=0; const calls=[], models=[]; const conn={api:async(path,method,body)=>{if(method==='POST'){calls.push(body); await new Promise(r=>setTimeout(r,2)); rev++;} return {revision:rev,projects:[],tasks:[]};}};
 const t=createWorkTransport(conn,{onModel:m=>models.push(m.revision),requestId:()=>`id${calls.length}`});
 await t.refresh(); await t.refresh(); await Promise.all([t.command('a',{}),t.command('b',{})]); assert.deepEqual(calls.map(c=>c.expected_revision),[0,1]); assert.deepEqual(models,[0,1,2]);
});
test('polling pauses during editing and refreshes after resume',async()=>{let paused=true,calls=0; const t=createWorkTransport({api:async()=>{calls++; return {revision:0};}},{isPaused:()=>paused}); await t.refresh(); assert.equal(calls,0); paused=false; await t.resume(); assert.equal(calls,1);});
test('a failed write reloads authoritative state and next intent uses its revision',async()=>{
 let rev=1, fail=true; const revisions=[];
 const c={api:async(path,method,body)=>{if(method==='POST'){revisions.push(body.expected_revision);if(fail){fail=false;rev=3;throw Object.assign(new Error('conflict'),{status:409});}rev++;}return {revision:rev,projects:[],tasks:[]};}};
 const t=createWorkTransport(c,{requestId:()=> 'id'});await t.refresh();await assert.rejects(t.command('a',{}),/conflict/);await t.command('b',{});assert.deepEqual(revisions,[1,3]);
});
test('an in-flight poll cannot overwrite a newer write or render while paused',async()=>{
 let release, paused=false, calls=0;const seen=[];
 const c={api:async(path,method)=>{if(method==='POST')return {revision:2,projects:[],tasks:[]}; calls++; if(calls===1)return {revision:0,projects:[],tasks:[]};if(calls===2)return new Promise(r=>release=r);return {revision:2,projects:[],tasks:[]};}};
 const t=createWorkTransport(c,{onModel:m=>seen.push(m.revision),isPaused:()=>paused,requestId:()=> 'id'});await t.refresh();const read=t.refresh();paused=true;release({revision:1,projects:[],tasks:[]});await read;assert.deepEqual(seen,[0]);paused=false;await t.resume();assert.deepEqual(seen,[0,2]);
});
test('a write updates the authoritative revision but delays rendering during an edit',async()=>{let paused=false;const seen=[];const c={api:async(path,method)=>({revision:method==='POST'?2:1,projects:[],tasks:[]})};const t=createWorkTransport(c,{isPaused:()=>paused,onModel:m=>seen.push(m.revision),requestId:()=> 'x'});await t.refresh();paused=true;await t.command('x',{});assert.equal(t.model.revision,2);assert.deepEqual(seen,[1]);paused=false;await t.resume();assert.deepEqual(seen,[1,2]);});
test('theme callback and Work refresh share the same polling timer',async()=>{let tick, timers=0, calls=0;const t=createWorkTransport({api:async()=>({revision:1})},{setTimer:fn=>{tick=fn;timers++;return 1;},clearTimer:()=>{},onPoll:()=>{calls++;}});t.start();await new Promise(r=>setTimeout(r,0));tick();await new Promise(r=>setTimeout(r,0));assert.equal(timers,1);assert.equal(calls,2);t.stop();});
test('Day endpoint unwraps view_model before rendering and writing',async()=>{const calls=[];const c={api:async(path,method,body)=>{calls.push({path,method,body});return method==='POST'?{revision:5,projects:[],tasks:[]}:{revision:4,view_model:{revision:4,projects:[],tasks:[]}};}};const t=createWorkTransport(c,{path:'/api/work/period/day',requestId:()=> 'x'});await t.refresh();assert.ok(Array.isArray(t.model.projects));await t.command('cube_return',{cube_id:'x'});assert.equal(calls[1].body.expected_revision,4);});
test('a lost committed response retries the exact immutable envelope once',async()=>{const posts=[];let writes=0;const seen=new Map(),c={api:async(_path,method,body)=>{if(method!=='POST')return {revision:writes,projects:[],tasks:[]};posts.push(structuredClone(body));if(!seen.has(body.request_id)){writes++;seen.set(body.request_id,{revision:writes,projects:[],tasks:[]});throw Object.assign(new Error('lost response'),{status:0});}return seen.get(body.request_id);}};const t=createWorkTransport(c,{requestId:()=> 'exact'});await t.refresh();await t.command('standalone_create',{title:'One Note',date:null});assert.equal(writes,1);assert.equal(posts.length,2);assert.deepEqual(posts[0],posts[1]);});
test('two uncertain responses retain the original receipt for a same-intention reclick',async()=>{const posts=[];let failures=2,ids=0;const c={api:async(_path,method,body)=>{if(method!=='POST')return {revision:1,projects:[],tasks:[]};posts.push(structuredClone(body));if(failures-->0)throw Object.assign(new Error('unknown'),{status:0});return {revision:2,projects:[],tasks:[]};}};const t=createWorkTransport(c,{requestId:()=> 'id'+(++ids)});await t.refresh();await assert.rejects(t.command('standalone_create',{title:'One'}),/unknown/);await t.command('standalone_create',{title:'One'});assert.equal(ids,1);assert(posts.every(p=>p.request_id==='id1'));});
test('view handoff is rejected while an uncertain receipt remains and succeeds after its exact replay',async()=>{let fail=true;const t=createWorkTransport({api:async(_path,method)=>{if(method==='POST'&&fail)throw Object.assign(new Error('lost'),{status:0});return {revision:1,projects:[],tasks:[]};}},{requestId:()=> 'same'});await t.refresh();await assert.rejects(t.command('standalone_create',{title:'One'}));await assert.rejects(t.flush(),/Повторите последнее действие/);fail=false;await t.command('standalone_create',{title:'One'});await t.flush();});

function pollingFixture() {
 const doc = new EventTarget(), win = new EventTarget();
 doc.hidden = true;
 let next = 0, reads = 0, themes = 0, timers = 0;
 const scheduled = new Map();
 const transport = createWorkTransport({api:async()=>({revision:++reads})}, {
  document:doc, window:win,
  setTimer:fn=>{const id=next++;scheduled.set(id,fn);return id;},
  clearTimer:id=>scheduled.delete(id), onPoll:async()=>{themes++;timers++;},
 });
 const settle=async()=>{for(let n=0;n<12;n++)await Promise.resolve();};
 return {doc,win,transport,scheduled,settle,counts:()=>[reads,themes,timers],
  tick:async()=>{for(const fn of scheduled.values())fn();await settle();}};
}
test('hidden windows suspend model/theme/timer and visible/focus resumes immediately with one interval',async()=>{
 const f=pollingFixture();f.transport.start();await f.settle();
 assert.deepEqual(f.counts(),[0,0,0]);assert.equal(f.scheduled.size,0);
 f.doc.hidden=false;f.doc.dispatchEvent(new Event('visibilitychange'));await f.settle();
 assert.deepEqual(f.counts(),[1,1,1]);assert.equal(f.scheduled.size,1);
 await f.tick();assert.deepEqual(f.counts(),[2,2,2]);
 f.doc.hidden=true;f.doc.dispatchEvent(new Event('visibilitychange'));await f.tick();
 assert.deepEqual(f.counts(),[2,2,2]);assert.equal(f.scheduled.size,0);
 f.win.dispatchEvent(new Event('focus'));await f.tick();assert.deepEqual(f.counts(),[2,2,2]);
 f.doc.hidden=false;f.win.dispatchEvent(new Event('focus'));await f.settle();
 assert.deepEqual(f.counts(),[3,3,3]);assert.equal(f.scheduled.size,1);
 f.win.dispatchEvent(new Event('focus'));await f.settle();assert.deepEqual(f.counts(),[4,4,4]);
 assert.equal(f.scheduled.size,1);f.transport.stop();
 f.win.dispatchEvent(new Event('focus'));await f.tick();assert.deepEqual(f.counts(),[4,4,4]);
});
test('a queued ancillary poll does not start after visibility is lost',async()=>{
 const f=pollingFixture();f.doc.hidden=false;f.transport.start();
 f.doc.hidden=true;f.doc.dispatchEvent(new Event('visibilitychange'));await f.settle();
 assert.deepEqual(f.counts(),[1,0,0]);f.transport.stop();
});

test('fake clock enforces 750ms rhythm and spends no hidden polling intervals',async t=>{
 t.mock.timers.enable({apis:['setInterval']});
 const doc=new EventTarget(),win=new EventTarget();doc.hidden=false;
 let models=0,sidePolls=0;
 const transport=createWorkTransport({api:async()=>({revision:++models})},{document:doc,window:win,onPoll:()=>sidePolls++});
 const settle=async()=>{for(let n=0;n<12;n++)await Promise.resolve();};
 try {
  transport.start();await settle();assert.deepEqual([models,sidePolls],[1,1]);
  t.mock.timers.tick(749);await settle();assert.equal(models,1);
  t.mock.timers.tick(1);await settle();assert.deepEqual([models,sidePolls],[2,2]);
  doc.hidden=true;doc.dispatchEvent(new Event('visibilitychange'));
  t.mock.timers.tick(60000);await settle();assert.deepEqual([models,sidePolls],[2,2]);
  doc.hidden=false;doc.dispatchEvent(new Event('visibilitychange'));await settle();
  assert.deepEqual([models,sidePolls],[3,3]);
  t.mock.timers.tick(750);await settle();assert.deepEqual([models,sidePolls],[4,4]);
 } finally {transport.stop();}
});

test('stop cancels an ancillary callback queued in the same turn',async()=>{
 const f=pollingFixture();f.doc.hidden=false;f.transport.start();f.transport.stop();
 await f.settle();assert.deepEqual(f.counts(),[1,0,0]);assert.equal(f.scheduled.size,0);
});
