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

test("preset durations have one default and preserve 40/10 for all modes", async () => {
  const {normalizeTimePreset,timerFields,taskKey} = await import("../time-client.js");
  const preset=normalizeTimePreset({work_ms:2400000,break_ms:600000});
  assert.deepEqual(timerFields("pomodoro",preset),{mode:"pomodoro",duration_ms:2400000});
  assert.deepEqual(timerFields("break",preset),{mode:"break",duration_ms:600000});
  assert.deepEqual(timerFields("stopwatch",preset),{mode:"stopwatch"});
  assert.equal(taskKey({source:"library",id:"stable",title:"Old"}),taskKey({source:"library",id:"stable",title:"New"}));
  assert.notEqual(taskKey({source:"calendar",id:"stable"}),taskKey({source:"library",id:"stable"}));
});

test("pending transport command survives client recreation and storage failure never sends", async () => {
  const saved={}, calls=[];
  let diskFails=true;
  const storage={async get(){return structuredClone(saved);},async set(items){if(diskFails)throw new Error("disk full");Object.assign(saved,structuredClone(items));}};
  const api=async(path,method,body)=>{if(!method)return {revision:0};calls.push(structuredClone(body));throw new Error("lost response");};
  const first=createTimeClient(api,{storage});
  await assert.rejects(first.command("start"));
  assert.equal(calls.length,0);
  diskFails=false;
  await assert.rejects(first.retry());
  const next=createTimeClient(async(path,method,body)=>{calls.push(structuredClone(body));return {revision:1,status:"running"};},{storage});
  await next.retry();
  assert.deepEqual(calls[0],calls[1]);
  assert.equal(next.pending,null);
  assert.equal(saved["time:pending"],null);
});

test("a delayed refresh cannot replace a confirmed command with older state", async () => {
  let read=0, delayed;
  const client=createTimeClient(async(path,method)=>{
    if(method)return {revision:2,status:"running"};
    if(++read===1)return {revision:1};
    return new Promise(resolve=>{delayed=resolve;});
  });
  await client.refresh();
  const stale=client.refresh();
  await new Promise(resolve=>setImmediate(resolve));
  await client.command("start");
  delayed({revision:1});await stale;
  assert.equal(client.state.revision,2);
});

test("two pages sharing persistent storage retain their own lost-response receipts after reload", async () => {
  const {timeClientIdentity}=await import("../time-client.js");
  const session=()=>{const values=new Map();return {getItem:k=>values.get(k),setItem:(k,v)=>values.set(k,v)};};
  const leftSession=session(), rightSession=session(), saved={}, writes=[];
  const leftId=timeClientIdentity(leftSession), rightId=timeClientIdentity(rightSession);
  assert.notEqual(leftId,rightId);
  assert.equal(timeClientIdentity(leftSession),leftId);
  const storage={async get(key){return {[key]:structuredClone(saved[key])};},async set(items){Object.assign(saved,structuredClone(items));}};
  const options=id=>({storage,pendingKey:`time:pending:${id}`});
  const api=async(path,method,body)=>{if(!method)return {revision:0};writes.push(structuredClone(body));throw new Error("lost");};
  const left=createTimeClient(api,options(leftId)), right=createTimeClient(api,options(rightId));
  await Promise.all([assert.rejects(left.command("start",{task:{id:"a"}})),assert.rejects(right.command("start",{task:{id:"b"}}))]);
  assert.equal(writes.length,2);
  const committed=async(path,method,body)=>{if(!method)return {revision:1};assert.ok(writes.some(w=>JSON.stringify(w)===JSON.stringify(body)));return {revision:1};};
  const reloadedLeft=createTimeClient(committed,options(timeClientIdentity(leftSession)));
  await reloadedLeft.retry();
  assert.equal(saved[`time:pending:${leftId}`],null);
  assert.deepEqual(saved[`time:pending:${rightId}`],writes.find(w=>w.task.id==="b"));
  const reloadedRight=createTimeClient(committed,options(timeClientIdentity(rightSession)));
  await reloadedRight.retry();
  assert.equal(saved[`time:pending:${rightId}`],null);
});

test("historical replay receipt is reconciled after another client stopped", async () => {
  let latest={revision:0,status:"idle"}, lost=true, receipt;
  const client=createTimeClient(async(path,method,body)=>{
    if(!method)return {...latest};
    if(!receipt){receipt={revision:1,status:"running"};latest=receipt;}
    if(lost){lost=false;throw new Error("lost");}
    return receipt;
  });
  await assert.rejects(client.command("start"));
  latest={revision:2,status:"idle"};
  await client.refresh();
  const seen=[];client.subscribe(state=>seen.push(state.revision));
  await client.retry();
  assert.equal(client.state.revision,2);assert.equal(client.state.status,"idle");
  assert.deepEqual(seen,[2]);
});

test("ambiguous HTTP server failure retains exact receipt for explicit retry", async () => {
  const calls=[];let failed=true;
  const client=createTimeClient(async(path,method,body)=>{
    if(!method)return {revision:1,status:"paused"};
    calls.push(structuredClone(body));
    if(failed){failed=false;throw Object.assign(new Error("response failed"),{status:500});}
    return {revision:2,status:"idle"};
  });
  await assert.rejects(client.command("stop"));
  assert.ok(client.pending);
  assert.equal(calls.length,1);
  await client.retry();
  assert.deepEqual(calls[0],calls[1]);
  assert.equal(client.pending,null);
});

test("cloned tabs keep independent immutable receipts and a new process recovers all without automatic replay", async () => {
  const {timeClientIdentity}=await import("../time-client.js");
  const session=value=>({getItem:()=>value,setItem:()=>{}});
  const originalId=timeClientIdentity(session("copied-tab-id"));
  const clonedId=timeClientIdentity(session("copied-tab-id"));
  assert.equal(originalId,clonedId,"browser duplicated-tab sessionStorage snapshot");
  const saved={},writes=[];
  const storage={async get(key){return key===null?structuredClone(saved):{[key]:structuredClone(saved[key])};},async set(items){Object.assign(saved,structuredClone(items));},async remove(key){delete saved[key];}};
  const options=id=>({storage,pendingKey:`time:pending:${id}`,outboxPrefix:"time:command-outbox:"});
  const transport=async(path,method,body)=>{if(!method)return {revision:0};writes.push(structuredClone(body));throw new Error("lost response");};
  const original=createTimeClient(transport,options(originalId));
  const clone=createTimeClient(transport,options(clonedId));
  await Promise.all([assert.rejects(original.command("start",{task:{id:"original"}})),assert.rejects(clone.command("start",{task:{id:"clone"}}))]);
  const durable=()=>Object.values(saved).filter(value=>value?.request_id);
  assert.equal(durable().length,2,"neither cloned tab can overwrite the other receipt");
  const recovered=[];
  const newProcess=createTimeClient(async(path,method,body)=>{
    if(!method)return {revision:3,status:"idle"};
    assert.ok(writes.some(write=>JSON.stringify(write)===JSON.stringify(body)));
    recovered.push(body.request_id);return {revision:1,status:"running"};
  },options("new-session-after-process-exit"));
  await newProcess.refresh();
  assert.equal(recovered.length,0,"opening/refreshing never replays old mutations");
  assert.ok(newProcess.pending);
  await assert.rejects(newProcess.command("start"));
  await newProcess.retry();
  assert.equal(durable().length,1,"success only removes its own immutable receipt");
  assert.ok(newProcess.pending,"the second unresolved receipt remains visible");
  await newProcess.retry();
  assert.equal(durable().length,0);
  assert.equal(newProcess.pending,null);
  assert.equal(newProcess.state.revision,3,"historical receipts cannot regress authoritative state");
  assert.deepEqual([...recovered].sort(),writes.map(write=>write.request_id).sort());
});

test("preset receipt outbox recovers multiple windows and survives a new process with explicit save only", async () => {
  const previousChrome=globalThis.chrome, previousFetch=globalThis.fetch;
  const commands=[
    {request_id:"preset-a",expected_revision:0,patch:{"time:preset":{work_ms:1800000,break_ms:300000}}},
    {request_id:"preset-b",expected_revision:1,patch:{"time:preset":{work_ms:2400000,break_ms:600000}}},
  ];
  const saved={connection:{token:"isolated",port:45999},...Object.fromEntries(commands.map(body=>["time:preset-outbox:"+body.request_id,body]))};
  const removed=[], delivered=[], receipts=new Map(commands.map((body,index)=>[body.request_id,{revision:index+1,values:body.patch}]));
  let remote={revision:2,values:{"time:preset":{work_ms:2400000,break_ms:600000}}},failNext=false;
  const storage={async get(key){return key===null?structuredClone(saved):{[key]:structuredClone(saved[key])};},async set(items){Object.assign(saved,structuredClone(items));},async remove(key){delete saved[key];removed.push({key,remaining:Object.keys(saved).filter(k=>k.startsWith("time:preset-outbox:"))});}};
  globalThis.chrome={storage:{local:storage,onChanged:{addListener(){}}}};
  globalThis.fetch=async(_url,options)=>{
    if(options.method==="GET")return Response.json(remote);
    const body=JSON.parse(options.body);delivered.push(body);
    if(!receipts.has(body.request_id)){remote={revision:remote.revision+1,values:{...remote.values,...body.patch}};receipts.set(body.request_id,structuredClone(remote));}
    if(failNext){failNext=false;return Response.json({detail:"response lost after commit"},{status:500});}
    return Response.json(receipts.get(body.request_id));
  };
  try{
    const module=await import("../time-client.js?presetTest="+crypto.randomUUID());
    await assert.rejects(module.getTimePreset());assert.equal(delivered.length,0);
    await module.saveTimePreset(remote.values["time:preset"]);
    assert.deepEqual(delivered,commands);
    assert.equal(removed[0].remaining.length,1);assert.equal(removed[1].remaining.length,0);
    failNext=true;
    const nextPreset={work_ms:3000000,break_ms:600000};
    await assert.rejects(module.saveTimePreset(nextPreset));
    const originalRequest=delivered.at(-1);
    const restarted=await import("../time-client.js?newProcess="+crypto.randomUUID());
    await assert.rejects(restarted.getTimePreset());
    assert.deepEqual(delivered.at(-1),originalRequest);
    await restarted.saveTimePreset(nextPreset);
    assert.deepEqual(delivered.at(-1),originalRequest);
    assert.equal(Object.keys(saved).filter(k=>k.startsWith("time:preset-outbox:")).length,0);
    assert.deepEqual(await restarted.getTimePreset(),nextPreset);
  }finally{globalThis.chrome=previousChrome;globalThis.fetch=previousFetch;}
});

test("a background client clears a receipt recovered by a peer and can command again", async () => {
  const command={request_id:"peer-cleared",expected_revision:0,action:"start"};
  const saved={['time:command-outbox:'+command.request_id]:command};
  const storage={async get(key){return key===null?structuredClone(saved):{[key]:saved[key]};},async set(items){Object.assign(saved,structuredClone(items));},async remove(key){delete saved[key];}};
  let current={revision:1,status:"running"};
  const api=async(path,method,body)=>{if(!method)return {...current};if(body.request_id===command.request_id)return {revision:1,status:"running"};return current={revision:2,status:"paused"};};
  const options={storage,outboxPrefix:"time:command-outbox:"};
  const background=createTimeClient(api,options),panel=createTimeClient(api,options);
  await background.refresh();await panel.refresh();
  assert.equal(background.pending.request_id,command.request_id);
  await panel.retry();
  const notifications=[];background.subscribe(()=>notifications.push(background.pending));
  await background.refresh();
  assert.equal(background.pending,null);assert.equal(notifications.at(-1),null);
  await background.command("pause");assert.equal(background.state.status,"paused");
});

test("peer recovery during a local delivery cannot replace it or leave a stale block", async () => {
  const command={request_id:"still-delivering",expected_revision:0,action:"start"};
  const saved={['time:command-outbox:'+command.request_id]:command};
  const storage={async get(key){return key===null?structuredClone(saved):{[key]:saved[key]};},async set(items){Object.assign(saved,structuredClone(items));},async remove(key){delete saved[key];}};
  let entered,release;
  const enteredPromise=new Promise(resolve=>{entered=resolve;}), gate=new Promise(resolve=>{release=resolve;});
  let current={revision:2,status:"running"};
  const a=createTimeClient(async(path,method,body)=>{
    if(!method)return {...current};
    if(body.request_id===command.request_id){entered();await gate;throw Object.assign(new Error("late lost response"),{status:500});}
    return current={revision:3,status:"paused"};
  },{storage,outboxPrefix:"time:command-outbox:"});
  const b=createTimeClient(async()=>({revision:2,status:"running"}),{storage,outboxPrefix:"time:command-outbox:"});
  await a.refresh();
  const delivery=assert.rejects(a.retry());await enteredPromise;
  await b.refresh();await b.retry();
  assert.equal(Object.keys(saved).length,0);
  await a.refresh();
  assert.deepEqual(a.pending,command,"in-flight exact body survives peer clear and concurrent refresh");
  release();await delivery;
  await a.command("pause");
  assert.equal(a.pending,null);assert.equal(a.state.revision,3);
  assert.equal(Object.keys(saved).length,0,"recovered request was not resurrected");
});
