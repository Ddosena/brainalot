import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createWorkConstructor} from '../work-constructor.js';
import {todayIn} from '../work-constructor-model.js';

const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
async function harness({library=false,pendingDelete=false,poolRetired=false}={}){
  const globals=Object.fromEntries(['document','window','innerWidth','innerHeight','matchMedia','requestAnimationFrame','cancelAnimationFrame'].map(key=>[key,globalThis[key]]));
  const handlers=new Map(),calls=[],date=todayIn(),note={id:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',kind:'task',status:'active',title:'Library <real> title',version:'note-version-1'},cube={id:'stage',kind:'task',task_id:'parent',segment_id:'segment:one',title:'One stage',minutes:30,comment:'Original comment',progress_percent:0,actual_minutes:10,selected_date:date};
  const planning={settings:{timezone:'Europe/Moscow'},tasks:[{id:'parent',title:'Parent task'}],constructor:{cubes:library?{}:{stage:cube},days:{[date]:{initialized:true}}},completed_cubes:{},result:{days:[],cubes:[],constructor:{cubes:library?[]:[cube],days:{[date]:{initialized:true,selected_cube_ids:library?[]:['stage'],possible_cube_ids:[],hidden_cube_ids:[]}}}}};
  if(poolRetired){
    const id='pool:parent:segment:one';planning.constructor.cubes={[id]:{...cube,id}};planning.cube_deletions={[id]:{cube_id:id,blueprint_ref:null,retired_minutes:60}};
    planning.result.constructor.cubes=[];planning.result.constructor.days[date].selected_cube_ids=[];
    planning.result.task_pool=[{task_id:'parent',title:'Parent task',segments:[{id:'segment:one',title:'One stage',available_for_manual_minutes:120}]}];
  }
  let metadata={pending:[],conflicts:[]},pendingDraft=null;
  const root={innerHTML:'',querySelector(){return null;},querySelectorAll(){return [];},contains(){return true;},addEventListener(){},removeEventListener(){}};
  const document={activeElement:null,popup:null,hidden:false,querySelector(selector){return selector==='[data-constructor-popup]'?this.popup:null;},querySelectorAll(){return [];},addEventListener(name,fn){handlers.set(name,fn);},removeEventListener(){},body:{append(element){if(Object.hasOwn(element.dataset,'constructorPopup'))document.popup=element;}},createElement(){
    const text={dataset:{popupField:'comment'},readOnly:false,matches(){return true;}};
    const element={dataset:{},style:{},innerHTML:'',offsetHeight:200,scrollTop:0,controls:[text],setAttribute(){},contains(){return false;},querySelector(){return null;},querySelectorAll(selector){return selector==='input,textarea,select,button'?this.controls:[];},remove(){if(document.popup===this)document.popup=null;}};return element;
  }};
  Object.assign(globalThis,{document,window:{addEventListener(){},removeEventListener(){}},innerWidth:1260,innerHeight:900,matchMedia:()=>({matches:true}),requestAnimationFrame:()=>1,cancelAnimationFrame(){}});
  const app=createWorkConstructor(root,{state:{planning},deletionState:()=>metadata,async pendingDraft(){return pendingDraft;},async readDashboard(){return library?{today:[note]}:{};},toast(){},async action(action,data){
    calls.push({action,data});
    if(pendingDelete){metadata={pending:[{request_id:'delete',cube_id:data.cube_id,note_id:note.id,status:'pending',error:null}],conflicts:[]};pendingDraft={action:'cube_delete'};throw Object.assign(new Error('Pending delete'),{status:503,delete_result:metadata.pending[0]});}
    assert.equal(action,'cube_delete');
    const id=data.cube_id;planning.constructor.cubes[id]={id,kind:'brainalot',source_ref:note.id,title:note.title,minutes:60};planning.cube_deletions={[id]:{cube_id:id,blueprint_ref:null,request_id:'delete'}};
    return {revision:42,state:{planning},delete_result:{status:'complete',cube_id:id,note_id:note.id,operation_id:'journal-op'}};
  }});
  await tick();
  const click=name=>handlers.get('click')({target:{dataset:{action:name},closest(selector){return selector==='[data-action]'?this:null;}},preventDefault(){}});
  function open(id,tray='possible'){
    const element={dataset:{ccube:id,tray},closest(selector){return selector==='[data-ccube]'?this:null;},getBoundingClientRect(){return {left:80,right:164,top:200,bottom:259};}};
    handlers.get('click')({target:element,preventDefault(){}});
  }
  return {app,root,document,planning,cube,note,calls,handlers,click,open,setMetadata(value){metadata=value;},dispose(){app.destroy();Object.assign(globalThis,globals);}};
}

test('unmaterialized library cube needs a named confirmation then one direct typed delete with the captured note version',async()=>{
  const h=await harness({library:true}),id='brainalot-'+h.note.id;
  try{
    h.open(id);assert.ok(h.document.popup.innerHTML.includes('data-action="delete-ask"'));h.click('delete-ask');await tick();
    assert.ok(h.document.popup.innerHTML.includes('Library &lt;real&gt; title'));assert.ok(h.document.popup.innerHTML.includes('Удалить задание Brainalot'));assert.deepEqual(h.calls,[]);
    h.click('delete-cancel');assert.deepEqual(h.calls,[]);h.click('delete-ask');await tick();h.click('delete-confirm');await tick();
    assert.deepEqual(h.calls,[{action:'cube_delete',data:{cube_id:id,expected_note_version:'note-version-1'}}]);
    assert.equal(h.document.popup,null);assert.ok(!h.root.innerHTML.includes('data-row-id="'+id+'"'),'a stale dashboard cannot recreate the deleted explicit source');assert.equal(h.planning.tasks[0].title,'Parent task');
  }finally{h.dispose();}
});

test('503 pending keeps the source visible, disables further mutation, and never displays deleted success',async()=>{
  const h=await harness({library:true,pendingDelete:true}),id='brainalot-'+h.note.id;
  try{
    h.open(id);h.click('delete-ask');await tick();h.click('delete-confirm');await tick();
    assert.equal(h.calls.length,1);assert.ok(h.root.innerHTML.includes('data-row-id="'+id+'"'));assert.ok(h.root.innerHTML.includes('Другие изменения Work временно приостановлены'));assert.ok(!h.root.innerHTML.includes('Кубик удалён.'));assert.equal(h.planning.cube_deletions,undefined);
    h.click('delete-confirm');await tick();assert.equal(h.calls.length,1,'pending cannot start a new destructive request');
  }finally{h.dispose();}
});

test('remote deletion retains the unsent popup draft for reading and prevents autosave resurrection',async()=>{
  const h=await harness();
  try{
    h.open('stage','selected');
    const input={dataset:{popupField:'comment'},value:'My unsent draft',closest(selector){return selector==='[data-constructor-popup]'?h.document.popup:null;}};
    h.document.activeElement=input;h.handlers.get('input')({target:input});
    h.planning.cube_deletions={stage:{cube_id:'stage',request_id:'remote',blueprint_ref:null}};h.planning.result.constructor.goal_cubes={stage:{...h.cube,report_date:todayIn()}};h.planning.result.constructor.cubes=[];h.planning.result.constructor.days[todayIn()].selected_cube_ids=[];
    h.app.remoteChanged();await tick();
    assert.ok(h.document.popup.innerHTML.includes('My unsent draft'));assert.ok(h.document.popup.innerHTML.includes('Ваш черновик сохранён для просмотра'));assert.equal(h.document.popup.controls[0].readOnly,true);
    input.value='Must not send';h.handlers.get('input')({target:input});h.handlers.get('change')({target:input});h.click('popup-close');
    await new Promise(resolve=>setTimeout(resolve,550));assert.deepEqual(h.calls,[]);assert.equal(h.planning.constructor.cubes.stage.comment,'Original comment');
  }finally{h.dispose();}
});

test('retired virtual pool pieces with colon-bearing stage IDs do not regenerate from remaining capacity',async()=>{
  const h=await harness({poolRetired:true});
  try{
    for(let repeat=0;repeat<2;repeat++){
      h.app.render();assert.ok(!h.root.innerHTML.includes('data-row-id="pool:parent:segment:one"'));assert.ok(h.root.innerHTML.includes('Parent task'));
    }
    assert.deepEqual(h.calls,[]);assert.equal(h.planning.result.task_pool[0].segments[0].available_for_manual_minutes,120);assert.equal(h.planning.tasks.length,1);
  }finally{h.dispose();}
});
