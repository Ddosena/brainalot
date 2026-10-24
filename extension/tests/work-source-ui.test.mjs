import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createWorkConstructor} from '../work-constructor.js';
import {todayIn,shiftDate} from '../work-constructor-model.js';

test('source score editing uses note CAS and explicit monthly approval is the only plan write',async()=>{
  const globals=Object.fromEntries(['document','window','innerWidth','innerHeight','matchMedia','requestAnimationFrame','cancelAnimationFrame'].map(key=>[key,globalThis[key]]));
  const handlers=new Map(),calls=[],toasts=[],date=todayIn(),month=date.slice(0,7),note={id:'note',kind:'task',title:'Library task',importance_score:2,version:'v1'};
  const planning={settings:{timezone:'Europe/Moscow'},tasks:[{id:'task-a',title:'Current task'},{id:'task-b',title:'Real future task'}],constructor:{days:{[date]:{initialized:true}},cubes:{}},
    completed_cubes:{actual:{task_id:'task-a',percent:25,actual_minutes:17,planned_minutes:60,date}},result:{days:[],cubes:[{id:'future-real',task_id:'task-b',title:'Real future step',date:shiftDate(date,1),minutes:30}],constructor:{cubes:[],days:{[date]:{initialized:true,selected_cube_ids:[],possible_cube_ids:[],hidden_cube_ids:[]}}}}};
  const facts=structuredClone(planning.completed_cubes),root={innerHTML:'',querySelector(){return null;},querySelectorAll(){return [];},contains(){return true;},addEventListener(){},removeEventListener(){}};
  const document={activeElement:null,hidden:false,querySelector(){return null;},querySelectorAll(){return [];},addEventListener(name,fn){handlers.set(name,fn);},removeEventListener(){},body:{append(){}},createElement(){return {dataset:{},style:{},setAttribute(){},querySelectorAll(){return [];},remove(){}};}};
  Object.assign(globalThis,{document,window:{addEventListener(){},removeEventListener(){}},innerWidth:1260,innerHeight:900,matchMedia:()=>({matches:true}),requestAnimationFrame:()=>1,cancelAnimationFrame(){}});
  let conflict=false;
  const app=createWorkConstructor(root,{state:{planning},async pendingDraft(){return null;},async readDashboard(){return {today:[structuredClone(note)]};},toast:text=>toasts.push(text),
    async api(path,method,body){calls.push({path,method,body});if(conflict){note.version='v3';note.importance_score=4;throw Object.assign(new Error('Conflict'),{status:409});}assert.equal(body.expected_version,note.version);note.importance_score=body.patch.importance_score;note.version='v2';return structuredClone(note);},
    async action(action,data){calls.push({action,data});assert.equal(action,'monthly_plan_approve');planning.monthly_plan={id:'0b2bf8c5-26ab-4b22-a844-a45a619e6079',month:data.month,status:'approved',approved_at:'2030-03-04T09:00:00Z',task_ids:data.task_ids};return {state:{planning}};},
  });
  const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
  try{
    await tick();assert.deepEqual(calls,[]);assert.ok(root.innerHTML.includes('Нет согласованного месячного плана'));
    const monthlyRows=[...root.innerHTML.matchAll(/<input type="checkbox" data-field="month-plan-task"[^>]*>/g)].map(match=>match[0]);assert.equal(monthlyRows.length,2);assert.ok(monthlyRows.every(row=>!row.includes('checked')));
    handlers.get('change')({target:{dataset:{field:'source-importance',id:'brainalot-note'},value:'0',closest(){return null;}}});await tick();
    assert.deepEqual(calls,[{path:'/api/notes/note',method:'PATCH',body:{expected_version:'v1',patch:{importance_score:0}}}]);assert.equal(note.importance_score,0);
    conflict=true;handlers.get('change')({target:{dataset:{field:'source-importance',id:'brainalot-note'},value:'5',closest(){return null;}}});await tick();
    assert.equal(calls.length,2);assert.equal(calls[1].body.expected_version,'v2');assert.equal(note.importance_score,4);assert.ok(toasts.some(text=>text.includes('не перезаписана')));
    handlers.get('change')({target:{dataset:{field:'month-plan-task',task:'task-b'},checked:true,closest(){return null;}}});await tick();assert.equal(calls.length,2,'checkbox changes are a draft, not approval');
    const save={dataset:{action:'month-plan-save'},closest(selector){return selector==='[data-action]'?this:null;}};
    handlers.get('click')({target:save,preventDefault(){}});await tick();assert.deepEqual(calls.at(-1),{action:'monthly_plan_approve',data:{month,task_ids:['task-b']}});
    assert.ok(root.innerHTML.includes('Согласованный план'));assert.deepEqual(planning.completed_cubes,facts);
    if(shiftDate(date,1).startsWith(month))assert.ok(root.innerHTML.match(/data-source-pool="future"[\s\S]*data-row-id="planned-future-real"/),'actual future blueprint appears after explicit approval');
  }finally{app.destroy();Object.assign(globalThis,globals);}
});
