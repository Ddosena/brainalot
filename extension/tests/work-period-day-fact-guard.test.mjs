import test from 'node:test';
import assert from 'node:assert/strict';
import {createWorkConstructor} from '../work-constructor.js';
import {todayIn} from '../work-constructor-model.js';
import {timeClient} from '../time-client.js';

async function fixture(exercise){
  const keys=['document','window','innerWidth','innerHeight','matchMedia','requestAnimationFrame','cancelAnimationFrame'];
  const previous=Object.fromEntries(keys.map(key=>[key,globalThis[key]])),timers={refresh:timeClient.refresh,command:timeClient.command};
  const handlers=new Map(),calls=[],timeCalls=[],messages=[],date=todayIn();
  const native={id:'native',kind:'task',task_id:'task',title:'Native cube',minutes:60,planned_date:date,selected_date:date,start:null,progress_percent:0,actual_minutes:0};
  const retained={...native,id:'retained',title:'Merged or retained block',period_can_record_progress:false,period_read_only:true};
  const planning={settings:{timezone:'Europe/Moscow',day_limit_minutes:300},tasks:[{id:'task',title:'Source task',course:'qa',estimate_minutes:{central:600}}],completed_cubes:{},constructor:{version:1,cubes:{native:{...native}},days:{[date]:{initialized:true}}},result:{days:[{date,focus_task_id:'task'}],cubes:[],tasks:[{id:'task',remaining_minutes:600}],task_pool:[],constructor:{cubes:[native,retained],days:{[date]:{date,initialized:true,selected_cube_ids:['native','retained'],possible_cube_ids:[],hidden_cube_ids:[],actual_minutes:0,credited_minutes:0,selected_minutes:120,fixed_minutes:0,total_work_minutes:120,budget_minutes:300,available_minutes:180,over_cap_minutes:0,events:[]}}}}};
  const root={innerHTML:'',querySelector(){return null;},querySelectorAll(){return [];},contains(){return true;},addEventListener(){},removeEventListener(){}};
  const document={activeElement:null,popup:null,hidden:false,querySelector(selector){return selector==='[data-constructor-popup]'?this.popup:null;},querySelectorAll(){return [];},addEventListener(name,callback){handlers.set(name,[...(handlers.get(name)||[]),callback]);},removeEventListener(){},body:{append(el){if(Object.hasOwn(el.dataset,'constructorPopup'))document.popup=el;}},createElement(){return {className:'',dataset:{},style:{},innerHTML:'',offsetWidth:332,offsetHeight:400,scrollTop:0,setAttribute(){},contains(){return false;},querySelector(){return null;},querySelectorAll(){return [];},remove(){if(document.popup===this)document.popup=null;}};}};
  Object.assign(globalThis,{document,window:{addEventListener(){},removeEventListener(){}},innerWidth:1440,innerHeight:900,matchMedia:()=>({matches:true}),requestAnimationFrame:()=>1,cancelAnimationFrame(){}});
  timeClient.refresh=async()=>{timeCalls.push('refresh');return {status:'idle',task:null};};
  timeClient.command=async(name,data)=>{timeCalls.push(name);return {status:name==='start'?'running':'idle',task:data?.task};};
  const app=createWorkConstructor(root,{state:{planning},async action(action,data){calls.push({action,data});if(action==='cube_progress'){const row=planning.result.constructor.cubes.find(row=>row.id===data.cube_id);Object.assign(row,{progress_percent:data.percent,actual_minutes:data.actual_minutes});}if(action==='constructor_cube'){const row=planning.result.constructor.cubes.find(row=>row.id===data.cube_id);Object.assign(row,data);}return {state:{planning}};},async pendingDraft(){return null;},async readDashboard(){return {};},toast(message){messages.push(message);}});
  const settle=async()=>{await new Promise(resolve=>setImmediate(resolve));await new Promise(resolve=>setImmediate(resolve));};
  const click=async(action,id)=>{const button={dataset:{action,...(id?{id}:{})},closest(selector){return selector==='[data-action]'?this:null;},getBoundingClientRect(){return {left:100,right:218,top:200,bottom:252};}};handlers.get('click')[0]({target:button,preventDefault(){}});await settle();};
  const input=(field,value)=>{const control={dataset:{popupField:field},value:String(value),closest(){return document.popup;}};handlers.get('input')[0]({target:control});return control;};
  const change=async control=>{handlers.get('change')[0]({target:control});await settle();};
  try{await settle();await exercise({app,document,planning,native,retained,calls,timeCalls,messages,click,input,change,settle});}
  finally{app.destroy();timeClient.refresh=timers.refresh;timeClient.command=timers.command;for(const key of keys){if(previous[key]===undefined)delete globalThis[key];else globalThis[key]=previous[key];}}
}

const inputTag=(html,field)=>html.match(new RegExp(`<input[^>]*data-popup-field="${field}"[^>]*>`))?.[0] || '';
const buttonTag=(html,action)=>html.match(new RegExp(`<button[^>]*data-action="${action}"[^>]*>`))?.[0] || '';

test('unsupported Day fact controls are disabled while title and placement controls stay available',async()=>{
  await fixture(async({document,click})=>{
    await click('inspect','retained');const html=document.popup.innerHTML;
    for(const field of ['progress_percent','actual_minutes'])assert.match(inputTag(html,field),/disabled/);
    for(const action of ['timer-start','done'])assert.match(buttonTag(html,action),/disabled/);
    assert.doesNotMatch(inputTag(html,'title'),/disabled/);assert.doesNotMatch(buttonTag(html,'return'),/disabled/);assert.doesNotMatch(buttonTag(html,'on-timeline'),/disabled/);
  });
});

test('programmatic start/done and fact-input events cannot create an unsupported timer or fact write',async()=>{
  await fixture(async({click,input,change,calls,timeCalls,messages})=>{
    await click('inspect','retained');await click('timer-start');await click('done');
    await change(input('progress_percent',75));await change(input('actual_minutes',25));
    assert.deepEqual(timeCalls,[]);assert.deepEqual(calls,[]);assert.ok(messages.some(message=>/исходного кубика Work/.test(message)));
  });
});

test('a stale fact draft is discarded after capability changes without blocking a title-only save',async()=>{
  await fixture(async({retained,document,click,input,change,calls})=>{
    retained.period_can_record_progress=true;retained.period_read_only=false;
    await click('inspect','retained');input('progress_percent',75);
    retained.period_can_record_progress=false;retained.period_read_only=true;
    await change(input('title','Updated title'));
    assert.ok(calls.some(call=>call.action==='constructor_cube' && call.data.title==='Updated title'));
    assert.ok(!calls.some(call=>call.action==='cube_progress'));assert.equal(retained.progress_percent,0);
    await click('inspect','retained');assert.match(inputTag(document.popup.innerHTML,'progress_percent'),/disabled/);
  });
});

test('unflagged native Day cubes retain timer start and progress recording',async()=>{
  await fixture(async({document,click,input,change,calls,timeCalls,native})=>{
    await click('inspect','native');assert.doesNotMatch(buttonTag(document.popup.innerHTML,'timer-start'),/disabled/);
    await click('timer-start');assert.ok(timeCalls.includes('start'));
    await change(input('progress_percent',25));assert.ok(calls.some(call=>call.action==='cube_progress' && call.data.percent===25));assert.equal(native.progress_percent,25);
  });
});

test('the read-only capability independently blocks facts while returning the block remains available',async()=>{
  await fixture(async({retained,document,click,calls,timeCalls})=>{
    retained.period_can_record_progress=true;retained.period_read_only=true;
    await click('inspect','retained');assert.match(buttonTag(document.popup.innerHTML,'timer-start'),/disabled/);
    await click('return');assert.ok(calls.some(call=>call.action==='constructor_return' && call.data.cube_id==='retained'));
    assert.ok(!calls.some(call=>call.action==='cube_progress'));assert.deepEqual(timeCalls,[]);
  });
});
