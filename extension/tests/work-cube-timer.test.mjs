import {test} from "node:test";
import assert from "node:assert/strict";
import {createCubeTimer, cubeTimerRef} from "../work-cube-timer.js";

test("cube pomodoro uses stable identity and duration without duplicate starts", async () => {
  const cube = {id:"one",title:"Make a demonstration",minutes:45}, calls=[];
  const client = {state:{status:"idle"},async refresh(){return this.state;},async command(action, fields={}) {
    calls.push({action,...fields}); this.state={...this.state,...fields,status:action==="pause"?"paused":"running"};return this.state;
  }};
  const timer=createCubeTimer(client,async()=>({rows:[]}));
  await timer.start(cube);await timer.start(cube);
  assert.deepEqual(calls,[{action:"start",task:cubeTimerRef(cube),mode:"pomodoro",duration_ms:2700000}]);
  await timer.pause(cube);await timer.start(cube);
  assert.deepEqual(calls.slice(1),[{action:"pause"},{action:"resume"}]);
  client.state={status:"running",task:{source:"library",id:"other",title:"Other"}};
  await timer.pause(cube);await timer.stop(cube);assert.equal(calls.length,3);
});

test("recorded minutes follow cube identity and do not credit completion",async()=>{
  const cube={id:"two",title:"Next",minutes:30};
  const timer=createCubeTimer({state:null},async()=>({rows:[{task:cubeTimerRef(cube),total_ms:450000},{task:{source:"work",id:"work-cube:other",title:"Other"},total_ms:600000}]}));
  assert.equal(await timer.recordedMinutes(cube),8);
  assert.deepEqual(timer.state(cube),{status:"idle",elapsed_ms:0,remaining_ms:1800000});
  assert.equal(cube.progress_percent,undefined);
});

test("constructor explicit duration supports 240 minutes without changing the shared preset",async()=>{
  const calls=[],client={state:null,async refresh(){return this.state;},async command(action,data){calls.push({action,...data});return this.state;}},timer=createCubeTimer(client,async()=>({rows:[]}));
  await timer.start({id:"long",title:"Long cube",minutes:240});
  assert.equal(calls[0].duration_ms,14400000);
  await assert.rejects(timer.start({id:"invalid",title:"Invalid",minutes:245}));
  await assert.rejects(timer.start({id:"zero",title:"Rest",minutes:0}));
});
