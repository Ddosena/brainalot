import test from 'node:test';
import assert from 'node:assert/strict';
import {orbitDropMinute} from '../work-orbit-drop.js';
import {nearestPlanningSlot,todayIn} from '../work-constructor-model.js';
import {createWorkConstructor} from '../work-constructor.js';

test('ring hit follows the 12–24 dial and rejects center and exterior',()=>{
  const rect={left:50,top:20,width:360,height:360};
  assert.equal(orbitDropMinute(rect,230,68),720);
  assert.equal(orbitDropMinute(rect,342,200),900);
  assert.equal(orbitDropMinute(rect,230,332),1080);
  assert.equal(orbitDropMinute(rect,118,200),1260);
  assert.equal(orbitDropMinute(rect,230,200),null);
  assert.equal(orbitDropMinute(rect,410,200),null);
  assert.equal(orbitDropMinute(rect,230,10),null);
  assert.equal(orbitDropMinute(null,230,68),null);
});

test('ring candidate shares canonical working-hour and collision rules',()=>{
  const candidate=orbitDropMinute({left:0,top:0,width:360,height:360},292,180),desired=candidate-30;
  assert.equal(candidate,900);
  assert.equal(nearestPlanningSlot({settings:{work_start:'09:00',work_end:'20:00'}},[],60,desired),870);
  const relocated=nearestPlanningSlot({settings:{work_start:'09:00',work_end:'20:00'}},[{start:870,minutes:60}],60,desired);
  assert.equal(relocated%5,0);assert.ok(relocated+60<=1200);assert.notEqual(relocated,870);
});

function planning(date){
  const selected={id:'selected',kind:'task',task_id:'task',title:'Selected',minutes:60,planned_date:date,selected_date:date,start:null,progress_percent:0};
  const source={id:'source',kind:'task',task_id:'task',title:'Source',minutes:60,planned_date:date,progress_percent:0};
  return {settings:{timezone:'Europe/Moscow',day_limit_minutes:300,work_start:'09:00',work_end:'20:00'},tasks:[{id:'task',title:'Task'}],task_segments:[],completed_cubes:{},constructor:{version:1,cubes:{selected,source},days:{[date]:{initialized:true}}},result:{days:[{date,budget_minutes:300,fixed_minutes:0}],cubes:[],tasks:[{id:'task',remaining_minutes:300}],task_pool:[],constructor:{cubes:[selected,source],days:{[date]:{date,initialized:true,selected_cube_ids:['selected'],possible_cube_ids:['source'],hidden_cube_ids:[],actual_minutes:0,credited_minutes:0,selected_minutes:60,fixed_minutes:0,total_work_minutes:60,budget_minutes:300,available_minutes:240,over_cap_minutes:0,events:[]}}}}};
}

async function dragToRing({id='selected',x=292,y=180,workEnd='20:00'}={}){
  const date=todayIn(),p=planning(date),calls=[],handlers=new Map(),rootHandlers=new Map(),originals={document:globalThis.document,window:globalThis.window,matchMedia:globalThis.matchMedia,requestAnimationFrame:globalThis.requestAnimationFrame,cancelAnimationFrame:globalThis.cancelAnimationFrame};
  p.settings.work_end=workEnd;
  const root={innerHTML:'',querySelector(){return null;},querySelectorAll(){return [];},contains(){return true;},addEventListener(name,fn){rootHandlers.set(name,fn);},removeEventListener(){}};
  const document={hidden:false,activeElement:null,body:{append(){}},querySelector(){return null;},querySelectorAll(){return [];},addEventListener(name,fn){handlers.set(name,fn);},removeEventListener(){},createElement(){return {className:'',dataset:{},style:{},innerHTML:'',getBoundingClientRect(){return {left:20,top:20,width:89,height:62};},remove(){}};}};
  Object.assign(globalThis,{document,window:{innerWidth:1200,innerHeight:900,addEventListener(){},removeEventListener(){}},matchMedia:()=>({matches:true}),requestAnimationFrame:()=>1,cancelAnimationFrame(){}});
  let app;
  try{
    app=createWorkConstructor(root,{state:{planning:p},async action(action,data){calls.push({action,data});return {state:{planning:p}};},async pendingDraft(){return null;},async readDashboard(){return {};},toast(){}});
    await new Promise(resolve=>setTimeout(resolve,0));
    root.querySelector=selector=>selector==='.cc-orbit-host svg'?{getBoundingClientRect(){return {left:0,top:0,width:360,height:360};}}:null;
    const cube={dataset:{ccube:id,tray:id==='source'?'possible':'selected'},style:{},classList:{remove(){}},closest(selector){return selector==='[data-ccube]'?this:null;}};
    rootHandlers.get('pointerdown')({button:0,target:cube,pointerId:7,clientX:20,clientY:20});
    handlers.get('pointermove')({pointerId:7,target:cube,clientX:x,clientY:y,preventDefault(){}});
    handlers.get('pointerup')({pointerId:7,clientX:x,clientY:y});
    await new Promise(resolve=>setTimeout(resolve,15));
    return {calls,date};
  }finally{app?.destroy();Object.assign(globalThis,originals);}
}

test('dropping an existing cube on the ring commits its same ID and snapped start once',async()=>{
  const {calls,date}=await dragToRing();
  assert.deepEqual(calls,[{action:'constructor_timeline',data:{cube_id:'selected',date,start:'14:30'}}]);
});

test('dropping a source cube on the ring selects exactly once without creating a copy',async()=>{
  const {calls,date}=await dragToRing({id:'source'});
  assert.equal(calls.length,1);assert.equal(calls[0].action,'constructor_select');
  assert.deepEqual(calls[0].data,{cube_id:'source',date,start:'14:30'});
  assert.ok(!Object.hasOwn(calls[0].data,'cube'));
});

test('dropping in the center or without a full slot makes no canonical write',async()=>{
  assert.deepEqual((await dragToRing({x:180,y:180})).calls,[]);
  assert.deepEqual((await dragToRing({workEnd:'09:30'})).calls,[]);
});
