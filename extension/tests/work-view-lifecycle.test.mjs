import test from 'node:test';
import assert from 'node:assert/strict';
import {installWorkViewLifecycle} from '../work-view-lifecycle.js';
const origin='http://127.0.0.1:32123',tick=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(prepareLeave,search='?embedded-work=1&work-instance=child-1'){
  const listeners=new Map(),posts=[],parent={postMessage:(data,target)=>posts.push({data,target})};
  const child={location:{search},parent,addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:(name)=>listeners.delete(name)};
  const lifecycle=installWorkViewLifecycle({prepareLeave,windowObject:child,origin});
  const send=(data={},options={})=>listeners.get('message')?.({source:parent,origin,data:{type:'brainalot-work-prepare-leave',instance:'child-1',request_id:'leave-1',...data},...options});
  return {lifecycle,posts,send,parent,listeners};
}
test('child readiness and successful leave use only their own instance and wait for saved data',async()=>{
  let finish;const f=fixture(()=>new Promise(resolve=>{finish=resolve;}));f.lifecycle.ready();
  assert.deepEqual(f.posts[0],{target:origin,data:{type:'brainalot-work-ready',instance:'child-1',ok:true}});
  const saving=f.send();await tick();assert.equal(f.posts.length,1);finish({date:'2026-10-29'});await saving;
  assert.deepEqual(f.posts[1].data,{type:'brainalot-work-prepare-leave-result',request_id:'leave-1',instance:'child-1',ok:true,date:'2026-10-29'});
});
test('rejected preparation preserves the draft and repeated delivery does not repeat saves',async()=>{
  const draft={text:'Unsaved'};let count=0;const f=fixture(async()=>{count++;throw new Error('Save required');});
  await f.send();await f.send();assert.equal(count,1);assert.equal(f.posts.length,2);
  assert.equal(f.posts[0].data.ok,false);assert.equal(f.posts[0].data.error,'Save required');assert.deepEqual(draft,{text:'Unsaved'});
});
test('forged parent, origin and instance cannot trigger a child save',async()=>{
  let count=0;const f=fixture(async()=>{count++;});
  await f.send({}, {source:{}});await f.send({}, {origin:'https://other.example'});await f.send({instance:'stale'});
  assert.equal(count,0);assert.equal(f.posts.length,0);f.lifecycle.dispose();assert.equal(f.listeners.size,0);
});
test('standalone content cannot install an embedded lifecycle listener',()=>{
  const f=fixture(async()=>{},'');f.lifecycle.ready();assert.equal(f.posts.length,0);assert.equal(f.listeners.size,0);
});
