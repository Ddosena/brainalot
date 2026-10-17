import test from "node:test";
import assert from "node:assert/strict";
import { createTimeClient, elapsedAt, focusSnapshot } from "../time-client.js";
test("elapsed snapshot interpolates from server_now without double-counting started_at", () => {
  const state = {mode:"stopwatch",status:"running",elapsed_ms:10000,server_now:20000,started_at:10000};
  assert.equal(elapsedAt(state,25000),15000);
  assert.equal(elapsedAt({...state,status:"paused"},25000),10000);
  assert.equal(elapsedAt({...state,mode:"pomodoro",duration_ms:12000},25000),12000);
  assert.equal(focusSnapshot({...state,mode:"pomodoro",duration_ms:60000}).startedAt,20000);
});
test("unknown transport outcome repeats exact request id and body", async () => {
  const calls=[]; let lose=true;
  const client=createTimeClient(async (path, method, body)=>{
    if (!method) return {revision:2};
    calls.push(structuredClone(body)); if(lose){lose=false;throw Object.assign(new Error("lost"),{status:0});}
    return {revision:3,status:"running"};
  });
  await assert.rejects(client.command("start",{task:null}));
  await assert.rejects(client.command("stop"));
  await client.retry(); assert.deepEqual(calls[0],calls[1]);assert.equal(client.pending,null);
});
test("conflicting client refreshes but never silently replays a mutation", async () => {
  let writes=0;
  const client=createTimeClient(async(path,method)=>{
    if(method){writes++;throw Object.assign(new Error("conflict"),{status:409});}
    return {revision:5};
  });
  await assert.rejects(client.command("pause")); assert.equal(writes,1);assert.equal(client.state.revision,5);assert.equal(client.pending,null);
});
