import test from "node:test";
import assert from "node:assert/strict";
import {CaptureQueue, DICTATION, queueStatus} from "../queue.js";

function storage() {
  const values = {};
  return {
    values,
    async get() { return {...values}; },
    async set(items) { Object.assign(values,items); },
    async remove(key) { delete values[key]; },
  };
}
test("parallel enqueue persists distinct UUID records", async () => {
  const disk=storage(); let n=0;
  const queue=new CaptureQueue({storage:disk,send:async()=>({id:"ok"}),uuid:()=>String(++n)});
  await Promise.all(Array.from({length:50},(_,i)=>queue.enqueue({text:String(i)})));
  assert.equal((await queue.list()).length,50);
});
test("service down, restart and lost acknowledgement retain stable ID", async () => {
  const disk=storage();
  const first=new CaptureQueue({storage:disk,send:async()=>{throw new Error("down")},uuid:()=>"stable"});
  await first.enqueue({text:"saved"}); await first.flush();
  assert.equal((await first.list())[0].id,"stable");
  const sent=[];
  const second=new CaptureQueue({storage:disk,send:async p=>{sent.push(p.external_id);return {id:"note"}}});
  await second.flush();
  assert.deepEqual(sent,["stable"]);
  assert.equal((await second.list()).length,0);
});
test("failed storage write does not report capture",async()=>{
  const disk=storage(); disk.set=async()=>{throw new Error("quota")};
  const queue=new CaptureQueue({storage:disk,send:async()=>({id:"x"})});
  await assert.rejects(queue.enqueue({text:"x"}),/quota/);
});
test("queue status is bounded while reporting the full count", () => {
  const records = Array.from({length: 25}, (_,i) => ({id:String(i), payload:{text:"x".repeat(50000)}, last_error:"e".repeat(1000)}));
  const status = queueStatus(records);
  assert.equal(status.queue_count, 25);
  assert.equal(status.queue.length, 20);
  assert.equal(status.queue[0].payload.text.length, 150);
  assert.equal(status.queue[0].last_error.length, 300);
});
test("a dictation keeps its type, voice source and capture time for the service", async () => {
  const disk=storage(); const sent=[];
  const queue=new CaptureQueue({storage:disk,uuid:()=>"d1",now:()=>"2026-09-20T20:30:00.000Z",
    send:async(payload,record)=>{sent.push({payload,type:record.type,captured_at:record.captured_at});return {id:"note"};}});
  const record=await queue.enqueue({text:"завтра купить хлеб",overrides:{},source:"voice"},"d1",DICTATION);
  assert.equal(record.type,DICTATION);
  assert.equal(record.payload.source,"voice");
  // The same delivery key cannot turn into a plain capture or change its text.
  await assert.rejects(queue.enqueue({text:"завтра купить хлеб",overrides:{},source:"voice"},"d1"),/Черновик изменился/);
  await assert.rejects(queue.enqueue({text:"другое",overrides:{},source:"voice"},"d1",DICTATION),/Черновик изменился/);
  assert.equal((await queue.enqueue({text:"завтра купить хлеб",overrides:{},source:"voice"},"d1",DICTATION)).id,"d1");
  await queue.flush();
  assert.deepEqual(sent,[{payload:{text:"завтра купить хлеб",overrides:{},source:"voice",external_id:"d1"},
    type:DICTATION,captured_at:"2026-09-20T20:30:00.000Z"}]);
});
test("plain captures keep their stored shape and a chrome source", async () => {
  const disk=storage();
  const queue=new CaptureQueue({storage:disk,send:async()=>({id:"x"}),uuid:()=>"c1",now:()=>"t"});
  await queue.enqueue({text:"x",source:"cloud"});
  assert.deepEqual(disk.values["capture:c1"],{id:"c1",payload:{text:"x",source:"chrome",external_id:"c1"},
    captured_at:"t",attempts:0,last_error:null});
});
