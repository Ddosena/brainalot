import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import * as model from '../widget-model.js';
import {FOCUS_OVERLAY_COLOR_KEY, NEON_COLORS, normalizeNeonColor} from '../focus-timer.js';

async function start({saved={},state={status:'idle',mode:'pomodoro',elapsed_ms:0,duration_ms:1500000},pending=null,catalog=Promise.resolve({notes:[]}),apiOverride=null}={}) {
  const source=(await readFile(new URL('../widget.js',import.meta.url),'utf8')).replace(/^import .*;\r?\n/gm,'');
  const nodes=new Map(),intervals=[],events={},actions=[],writes=[];
  const element=()=>({dataset:{},style:{},value:'',options:[],children:[],classList:{toggle(){}},setAttribute(){},toggleAttribute(){},focus(){},
    append(...items){this.children.push(...items);},querySelector(){return this.button??=element();},
    replaceChildren(...children){this.options=this.children=children;},hidden:false,textContent:''});
  const document={body:{dataset:{}},documentElement:{dataset:{},style:{setProperty(){}}},hidden:false,addEventListener(){},createElement:element,
    getElementById(id){if(!nodes.has(id)){const el=element();el.hidden=['widget-error','widget-retry','widget-popup'].includes(id);nodes.set(id,el);}return nodes.get(id);}};
  const chrome={storage:{local:{async get(keys){return typeof keys==='string'?{[keys]:saved[keys]}:Object.fromEntries((keys||Object.keys(saved)).map(key=>[key,saved[key]]));},
    async set(items){writes.push(items);Object.assign(saved,items);}},onChanged:{addListener(){}}}};
  let listener,failRefresh=false,gesture;
  const timeClient={state,pending,async refresh(){if(failRefresh){failRefresh=false;throw new Error('Actual network failure');}listener?.();return this.state;},subscribe(fn){listener=fn;fn();}};
  const native={async bootstrap(){return{widget_mode:'bar'};},async window_action(action,args){actions.push([action,args]);return{ok:true};}};
  const env={native,timeClient,sharedApi:async(route,...args)=>apiOverride?apiOverride(route,...args):route==='/api/notes'?catalog:route==='/api/time/tasks'?{tasks:(await catalog).notes.map(note=>({source:'library',id:note.id,title:note.title}))}:route==='/api/time/totals'?{rows:[]}:{events:[]},
    elapsedAt:()=>2000,formatTime:()=> '00:02',formatTotal:()=> '0 мин',getTimePreset:()=>{},timerFields:()=>{},
    applyDocument(){},async syncLocale(){},localeChange:()=>false,t:(text,args)=>text.replace(/\{(\w+)\}/g,(_,key)=>args?.[key]??''),calendarImportanceLevel:()=>null,
    ...model,installWindowGestures:(_native,options)=>{gesture=options;},document,chrome,
    FOCUS_OVERLAY_COLOR_KEY,NEON_COLORS,normalizeNeonColor,mountSpotifyPlayer:()=>({visible:false,setVisible(){}}),
    window:{addEventListener(name,fn){events[name]=fn;}},Option:function(text,value){return{text,value};},setInterval:fn=>intervals.push(fn)};
  const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
  await new AsyncFunction(...Object.keys(env),source)(...Object.values(env));
  await new Promise(setImmediate);
  return {nodes,intervals,events,actions,writes,timeClient,saved,get gesture(){return gesture;},fail(){failRefresh=true;},catalog(value){catalog=Promise.resolve(value);}};
}
test('startup preserves selected hot task until authoritative catalog, then recovers deletion',async()=>{
  let release;
  const saved={'desktop:selected-fire':'library:b'};
  const starting=start({saved,catalog:new Promise(resolve=>{release=resolve;})});
  await new Promise(setImmediate);
  assert.equal(saved['desktop:selected-fire'],'library:b');
  release({notes:[{id:'a',kind:'task',title:'A',importance:'critical',status:'active'}, {id:'b',kind:'task',title:'B',importance:'critical',status:'active'}]});
  const app=await starting;
  assert.equal(app.nodes.get('widget-current').textContent,'Горит: B');
  assert.equal(app.writes.length,0);
  app.catalog({notes:[{id:'a',kind:'task',title:'A',importance:'critical',status:'active'}]});
  await app.intervals[0]();
  assert.equal(app.saved['desktop:selected-fire'],'library:a');
});
test('pending recovery stays visible in menu without auto-expansion; only double clicks switch windows',async()=>{
  const app=await start({state:{status:'running',mode:'stopwatch',elapsed_ms:2000,duration_ms:0},pending:{request_id:'restored'}});
  const {nodes,timeClient}=app;
  assert.equal(nodes.get('widget-run').disabled,true);
  assert.equal(nodes.get('widget-retry').hidden,false);
  assert.match(nodes.get('widget-error').textContent,/неподтверждённое действие/);
  assert.equal(nodes.get('widget-tab').dataset.pending,'true');
  assert.deepEqual(app.actions,[]);
  assert.equal(app.gesture.onClick,undefined,'single-click tab expansion was removed');
  await app.gesture.onDoubleClick();
  await new Promise(setImmediate);
  assert.equal(app.actions.at(-1)[0],'card');
  app.events['brainalot-widget-mode']({detail:'card'});
  timeClient.pending=null;
  await app.intervals[0]();
  assert.equal(nodes.get('widget-run').disabled,false);
  assert.equal(nodes.get('widget-retry').hidden,true);
  assert.equal(nodes.get('widget-error').hidden,true);
  app.fail(); await app.intervals[0]();
  assert.equal(nodes.get('widget-error').textContent,'Actual network failure');
  await app.intervals[0]();
  assert.equal(nodes.get('widget-error').hidden,true);
  timeClient.command=async()=>{throw new Error('Save rejected');};
  await nodes.get('widget-run').onclick();
  assert.equal(nodes.get('widget-error').textContent,'Save rejected');
  await app.intervals[0]();
  assert.equal(nodes.get('widget-error').hidden,false);
});

test('peer paused task supersedes stale persisted choice; intentional paused choice is revision-bound and survives reload',async()=>{
  const a={source:'library',id:'a',title:'A'},b={source:'library',id:'b',title:'B'};
  const catalog=Promise.resolve({notes:[{...a,kind:'task',status:'active'},{...b,kind:'task',status:'active'}]});
  const app=await start({saved:{'desktop:selected-task':a,'desktop:selected-task-revision':1},
    state:{status:'running',mode:'pomodoro',task:b,revision:4,elapsed_ms:1000,duration_ms:1500000},catalog});
  assert.equal(app.nodes.get('widget-burning').textContent,'B');
  app.timeClient.state={...app.timeClient.state,status:'paused',revision:5};await app.intervals[0]();
  assert.equal(app.nodes.get('widget-burning').textContent,'B','peer pause must retain authoritative B');
  const commands=[];
  app.timeClient.command=async(action,fields={})=>{
    commands.push({action,...fields});app.timeClient.state={...app.timeClient.state,revision:app.timeClient.state.revision+1,
      ...(action==='switch_task'?{task:fields.task}:{}),...(action==='resume'?{status:'running'}:{})};return app.timeClient.state;
  };
  await app.nodes.get('widget-run').onclick();
  assert.deepEqual(commands,[{action:'resume'}],'Play must not switch B back to stale saved A');
  app.timeClient.state={...app.timeClient.state,status:'paused',revision:7};await app.intervals[0]();
  app.events['brainalot-widget-mode']({detail:'card'});await app.nodes.get('widget-task').onclick();await new Promise(setImmediate);
  const rowA=app.nodes.get('widget-task-list').children.find(row=>row.dataset.task==='library:a');await rowA.onclick();await new Promise(setImmediate);
  assert.equal(app.nodes.get('widget-burning').textContent,'A');assert.equal(app.saved['desktop:selected-task-revision'],7);
  assert.equal(commands.length,1,'intentional selection while paused sends no command');
  const reloaded=await start({saved:app.saved,state:app.timeClient.state,catalog});
  assert.equal(reloaded.nodes.get('widget-burning').textContent,'A','same paused revision preserves explicit selection after reload');
  reloaded.timeClient.state={...reloaded.timeClient.state,revision:8};await reloaded.intervals[0]();
  assert.equal(reloaded.nodes.get('widget-burning').textContent,'B','next shared revision invalidates the local selection');
  await app.nodes.get('widget-run').onclick();
  assert.deepEqual(commands.slice(1),[{action:'switch_task',task:a},{action:'resume'}],'explicit paused selection takes effect only on Play');
});

for (const resumed of ['a','b']) test(`completion patch-stage recovery stops matching task only (peer resumed ${resumed})`,async()=>{
  const a={source:'library',id:'a',title:'A'},b={source:'library',id:'b',title:'B'},calls=[];
  let notes=[{...a,kind:'task',status:'active',version:1},{...b,kind:'task',status:'active',version:1}],app;
  const apiOverride=async(route,method,body)=>{
    if(route==='/api/notes')return{notes};
    if(method==='PATCH'){
      calls.push('patch');assert.equal(app.timeClient.state.status,resumed==='a'?'idle':'running');
      assert.equal(body.expected_version,1);notes[0]={...notes[0],status:'done',version:2};return notes[0];
    }
    return route==='/api/time/tasks'?{tasks:[a,b]}:{rows:[],events:[]};
  };
  app=await start({saved:{'desktop:widget-operation':{kind:'complete',task:a,version:1,stage:'patch'}},
    state:{status:'running',task:resumed==='a'?a:b,revision:5,mode:'pomodoro',elapsed_ms:2000,duration_ms:1500000},apiOverride});
  app.timeClient.command=async action=>{calls.push(action);app.timeClient.state={...app.timeClient.state,status:'idle',revision:6};return app.timeClient.state;};
  await app.nodes.get('widget-retry').onclick();
  assert.deepEqual(calls,resumed==='a'?['stop','patch']:['patch']);
  assert.equal(notes[0].status,'done');assert.equal(app.saved['desktop:widget-operation'],null);
});
