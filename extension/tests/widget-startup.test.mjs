import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {compactTasks,taskKey,resolveActiveTask} from '../widget-model.js';

test('real widget startup keeps saved hot selection until authoritative catalog, then recovers deletion',async()=>{
  const source=(await readFile(new URL('../widget.js',import.meta.url),'utf8')).replace(/^import .*;\r?\n/gm,'');
  const nodes=new Map();
  const element=()=>({dataset:{},options:[],classList:{toggle(){}},setAttribute(){},
    replaceChildren(...children){this.options=children;},hidden:false,textContent:''});
  const document={body:{dataset:{}},documentElement:{dataset:{}},hidden:false,
    addEventListener(){},getElementById(id){if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);}};
  const saved={'desktop:selected-fire':'library:b'},writes=[],intervals=[];
  const chrome={storage:{local:{async get(keys){return typeof keys==='string'?{[keys]:saved[keys]}:{};},
    async set(items){writes.push(items);Object.assign(saved,items);}},onChanged:{addListener(){}}}};
  let releaseCatalog, catalog=new Promise(resolve=>{releaseCatalog=resolve;});
  const sharedApi=async route=>route==='/api/notes'?catalog:{events:[]};
  let listener;
  const timeClient={state:{status:'idle',mode:'pomodoro',elapsed_ms:0,duration_ms:1500000},pending:null,
    async refresh(){listener?.();return this.state;},subscribe(fn){listener=fn;fn();}};
  const names=['native','timeClient','sharedApi','elapsedAt','formatTime','getTimePreset','timerFields',
    'applyDocument','syncLocale','localeChange','t','createFireLayer','calendarImportanceLevel',
    'compactTasks','taskKey','resolveActiveTask','installWindowGestures','document','chrome','window','Option','setInterval'];
  const args=[{async bootstrap(){return {widget_mode:'card'};}},timeClient,sharedApi,()=>0,()=> '25:00',()=>{},()=>{},
    ()=>{},async()=>{},()=>false,x=>x,()=>({schedule(){}}),()=>null,compactTasks,taskKey,resolveActiveTask,
    ()=>{},document,chrome,{addEventListener(){}},function(text,value){return{text,value};},fn=>intervals.push(fn)];
  const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
  await new AsyncFunction(...names,source)(...args);
  assert.equal(saved['desktop:selected-fire'],'library:b');
  releaseCatalog({notes:[{source:'library',id:'a',title:'A',importance:'critical',status:'active'},
                         {source:'library',id:'b',title:'B',importance:'critical',status:'active'}]});
  await new Promise(setImmediate);
  assert.equal(nodes.get('widget-fire-choice').value,'library:b');
  assert.equal(nodes.get('widget-burning').textContent,'B');
  assert.equal(writes.length,0);
  catalog=Promise.resolve({notes:[{id:'a',title:'A',importance:'critical',status:'active'}]});
  await intervals[0]();
  assert.equal(nodes.get('widget-fire-choice').value,'library:a');
  assert.equal(saved['desktop:selected-fire'],'library:a');
});

test('recovered pending command has retry after reload and clears only its notice on peer recovery',async()=>{
  const source=(await readFile(new URL('../widget.js',import.meta.url),'utf8')).replace(/^import .*;\r?\n/gm,'');
  const nodes=new Map(),intervals=[],events={},actions=[];
  const document={body:{dataset:{}},documentElement:{dataset:{}},hidden:false,addEventListener(){},
    getElementById(id){
      if(!nodes.has(id))nodes.set(id,{dataset:{},options:[],classList:{toggle(){}},setAttribute(){},
        replaceChildren(...children){this.options=children;},hidden:['widget-error','widget-retry'].includes(id),textContent:''});
      return nodes.get(id);
    }};
  const chrome={storage:{local:{async get(){return{};},async set(){}},onChanged:{addListener(){}}}};
  let listener,failRefresh=false,gesture;
  const timeClient={state:{status:'running',mode:'stopwatch',elapsed_ms:2000,duration_ms:0},pending:{request_id:'restored'},
    async refresh(){if(failRefresh){failRefresh=false;throw new Error('Actual network failure');}listener?.();return this.state;},
    subscribe(fn){listener=fn;fn();}};
  const names=['native','timeClient','sharedApi','elapsedAt','formatTime','getTimePreset','timerFields',
    'applyDocument','syncLocale','localeChange','t','createFireLayer','calendarImportanceLevel',
    'compactTasks','taskKey','resolveActiveTask','installWindowGestures','document','chrome','window','Option','setInterval'];
  const args=[{async bootstrap(){return{widget_mode:'bar'};},window_action(action){actions.push(action);}},timeClient,
    async route=>route==='/api/notes'?{notes:[]}:{events:[]},()=>2000,()=> '00:02',()=>{},()=>{},
    ()=>{},async()=>{},()=>false,x=>x,()=>({schedule(){}}),()=>null,compactTasks,taskKey,resolveActiveTask,
    (_native,options)=>{gesture=options;},document,chrome,{addEventListener(name,fn){events[name]=fn;}},
    function(text,value){return{text,value};},fn=>intervals.push(fn)];
  const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
  await new AsyncFunction(...names,source)(...args);
  await new Promise(setImmediate);
  assert.equal(nodes.get('widget-run').disabled,true);
  assert.equal(nodes.get('widget-stop').disabled,true);
  assert.equal(nodes.get('widget-retry').hidden,false);
  assert.match(nodes.get('widget-error').textContent,/неподтверждённая команда/);
  assert.equal(nodes.get('widget-tab').dataset.pending,'true');
  assert.deepEqual(actions,[],'recovery must not automatically expand the window');
  gesture.onClick();
  assert.deepEqual(actions,['card'],'normal tab click still opens the recovery card');
  events['brainalot-widget-mode']({detail:'card'});
  assert.equal(nodes.get('widget-error').hidden,false);
  timeClient.pending=null;
  await intervals[0]();
  assert.equal(nodes.get('widget-run').disabled,false);
  assert.equal(nodes.get('widget-stop').disabled,false);
  assert.equal(nodes.get('widget-retry').hidden,true);
  assert.equal(nodes.get('widget-error').hidden,true);
  assert.equal(nodes.get('widget-error').textContent,'');
  // A genuine failure is independent of the synthetic recovery notice.
  timeClient.pending={request_id:'another'};
  failRefresh=true;
  await intervals[0]();
  assert.equal(nodes.get('widget-error').textContent,'Actual network failure');
  timeClient.pending=null;
  await intervals[0]();
  assert.equal(nodes.get('widget-error').hidden,false);
  assert.equal(nodes.get('widget-error').textContent,'Actual network failure');
});
