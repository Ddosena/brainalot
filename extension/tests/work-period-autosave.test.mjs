import test from 'node:test';
import assert from 'node:assert/strict';
import {createAutosaveQueue} from '../work-period/autosave.js';
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(save){const timers=new Map(),errors=[];let id=0;const queue=createAutosaveQueue({save,onError:error=>errors.push(error.message),setTimer:fn=>{timers.set(++id,fn);return id;},clearTimer:id=>timers.delete(id)});return {queue,timers,errors};}
test('field edits debounce to the newest value and mode leave flushes it without a Save button',async()=>{
  const calls=[],f=fixture(async row=>calls.push(row));
  f.queue.schedule({key:'cube:a',expectedRevision:7,data:{start:'14:00'}});
  f.queue.schedule({key:'cube:a',expectedRevision:8,data:{start:'14:05'}});
  assert.equal(calls.length,0);await f.queue.flush();
  assert.deepEqual(calls,[{key:'cube:a',expectedRevision:7,data:{start:'14:05'}}]);assert.equal(f.queue.pending,0);assert.equal(f.timers.size,0);
});
test('edits to two cards serialize and a newer keystroke during a write is preserved',async()=>{
  const calls=[];let release;const f=fixture(async row=>{calls.push(row);if(calls.length===1)await new Promise(resolve=>{release=resolve;});});
  f.queue.schedule({key:'a',expectedRevision:1,data:'old'},{immediate:true});await tick();
  f.queue.schedule({key:'a',expectedRevision:1,data:'new'});f.queue.schedule({key:'b',expectedRevision:1,data:'other'});
  const flushing=f.queue.flush();await tick();assert.equal(calls.length,1);release();await flushing;
  assert.deepEqual(calls.map(row=>row.data),['old','new','other']);assert.equal(f.queue.pending,0);
});
test('conflict retains exact draft and leaving never silently retries it',async()=>{
  let calls=0;const f=fixture(async()=>{calls++;throw new Error('Conflict');});
  f.queue.schedule({key:'a',expectedRevision:3,data:'draft'},{immediate:true});await tick();
  await assert.rejects(f.queue.flush(),/Conflict/);await assert.rejects(f.queue.flush(),/Conflict/);
  assert.equal(calls,1);assert.equal(f.queue.pending,1);assert.deepEqual(f.errors,['Conflict']);
});
test('a deliberate new edit after rejection uses the current revision and can save',async()=>{
  const calls=[];const f=fixture(async row=>{calls.push(row);if(calls.length===1)throw new Error('Invalid');});
  f.queue.schedule({key:'a',expectedRevision:3,data:''},{immediate:true});await tick();
  f.queue.schedule({key:'a',expectedRevision:5,data:'valid'});await f.queue.flush();
  assert.equal(calls[1].expectedRevision,5);assert.equal(calls[1].data,'valid');assert.equal(f.queue.pending,0);
});
test('a disposed editor cannot enter a retry loop or write abandoned fields',async()=>{
  const calls=[],f=fixture(async row=>calls.push(row));f.queue.schedule({key:'a',expectedRevision:1,data:'draft'});f.queue.dispose();
  await assert.rejects(f.queue.flush(),/Editor is closed/);assert.equal(calls.length,0);assert.equal(f.timers.size,0);
});
