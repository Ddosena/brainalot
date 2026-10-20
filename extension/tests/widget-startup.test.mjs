import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import * as model from '../widget-model.js';
import {FOCUS_OVERLAY_COLOR_KEY, NEON_COLORS, normalizeNeonColor} from '../focus-timer.js';
import {normalizeTimePreset,timerFields} from '../time-client.js';

async function start({saved={},state={status:'idle',mode:'pomodoro',elapsed_ms:0,duration_ms:1500000},pending=null,catalog=Promise.resolve({notes:[]}),apiOverride=null}={}) {
  const source=(await readFile(new URL('../widget.js',import.meta.url),'utf8')).replace(/^import .*;\r?\n/gm,'');
  const nodes=new Map(),intervals=[],events={},actions=[],writes=[];
  const element=()=>({dataset:{},style:{},value:'',options:[],children:[],classList:{toggle(){}},setAttribute(){},toggleAttribute(){},focus(){},
    append(...items){this.children.push(...items);},querySelector(){return this.button??=element();},
    replaceChildren(...children){this.options=this.children=children;},hidden:false,textContent:''});
  const document={body:{dataset:{}},documentElement:{dataset:{},style:{setProperty(){}}},hidden:false,addEventListener(){},createElement:element,querySelectorAll:()=>[],
    getElementById(id){if(!nodes.has(id)){const el=element();el.hidden=['widget-error','widget-retry','widget-popup'].includes(id);nodes.set(id,el);}return nodes.get(id);}};
  const chrome={storage:{local:{async get(keys){return typeof keys==='string'?{[keys]:saved[keys]}:Object.fromEntries((keys||Object.keys(saved)).map(key=>[key,saved[key]]));},
    async set(items){writes.push(items);Object.assign(saved,items);}},onChanged:{addListener(){}}}};
  let listener,failRefresh=false,gesture;
  const timeClient={state,pending,async refresh(){if(failRefresh){failRefresh=false;throw new Error('Actual network failure');}listener?.();return this.state;},subscribe(fn){listener=fn;fn();}};
  const native={async bootstrap(){return{widget_mode:'bar'};},async window_action(action,args){actions.push([action,args]);return{ok:true};}};
  const env={native,timeClient,sharedApi:async(route,...args)=>apiOverride?apiOverride(route,...args):route==='/api/notes'?catalog:route==='/api/time/tasks'?{tasks:(await catalog).notes.map(note=>({source:'library',id:note.id,title:note.title}))}:route==='/api/time/totals'?{rows:[]}:{events:[]},
    elapsedAt:()=>2000,formatTime:()=> '00:02',formatTotal:()=> '0 мин',getTimePreset:async()=>normalizeTimePreset(null),saveTimePreset:async value=>value,normalizeTimePreset,timerFields,
    applyDocument(){},async syncLocale(){},localeChange:()=>false,t:(text,args)=>text.replace(/\{(\w+)\}/g,(_,key)=>args?.[key]??''),calendarImportanceLevel:()=>null,
    ...model,installWindowGestures:(_native,options)=>{gesture=options;},document,chrome,
    FOCUS_OVERLAY_COLOR_KEY,NEON_COLORS,normalizeNeonColor,mountSpotifyPlayer:()=>({visible:false,setVisible(){}}),
    window:{addEventListener(name,fn){events[name]=fn;}},Option:function(text,value){return{text,value};},setInterval:fn=>intervals.push(fn)};
  const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
  await new AsyncFunction(...Object.keys(env),source)(...Object.values(env));
  await new Promise(setImmediate);
  return {nodes,getNode:document.getElementById,intervals,events,actions,writes,timeClient,saved,get gesture(){return gesture;},fail(){failRefresh=true;},catalog(value){catalog=Promise.resolve(value);}};
}

test('ordinary picker offers rest during running work and switches attribution without stopping the timer',async()=>{
  const task={source:'library',id:'a',title:'A'};
  const app=await start({state:{status:'running',mode:'stopwatch',task,revision:4,elapsed_ms:120000,duration_ms:0},
    catalog:Promise.resolve({notes:[{...task,kind:'task',status:'active'}]})});
  const commands=[];
  app.timeClient.command=async(action,fields)=>{
    commands.push({action,...fields});
    app.timeClient.state={...app.timeClient.state,...fields,revision:5};
    return app.timeClient.state;
  };
  app.events['brainalot-widget-mode']({detail:'card'});
  await app.nodes.get('widget-task').onclick();await new Promise(setImmediate);
  const rest=app.nodes.get('widget-task-list').children.find(row=>row.dataset.task==='label:rest');
  assert.ok(rest,'Rest is present outside the idle activity prompt');
  await rest.onclick();await new Promise(setImmediate);
  assert.deepEqual(commands,[{action:'switch_task',task:{source:'label',id:'rest',title:'Отдых'}}]);
  assert.equal(app.timeClient.state.status,'running');
  assert.equal(app.timeClient.state.elapsed_ms,120000);
  assert.equal(app.nodes.get('widget-burning').textContent,'Отдых');
  app.timeClient.state={...app.timeClient.state,status:'paused',revision:6};
  await app.intervals[0]();
  assert.equal(app.nodes.get('widget-burning').textContent,'Отдых');
});
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

test('Add gates duplicate submits before awaiting serverToday and captures the fresh server date once',async()=>{
  let date='2026-10-20',blocked=false,release;const captures=[],notes=[];
  const apiOverride=async(route,method,body)=>{
    if(route==='/api/notes')return{notes};
    if(route==='/api/time/tasks')return blocked?new Promise(resolve=>{release=()=>resolve({today:date,tasks:[]});}):{today:date,tasks:[]};
    if(route==='/api/captures'){captures.push(body);const note={id:'new',title:body.title,due:body.due,kind:'task',status:'active',version:1};notes.push(note);return note;}
    return{values:{},rows:[],events:[]};
  };
  const app=await start({state:{status:'idle',mode:'stopwatch',task:null,revision:1,elapsed_ms:0,duration_ms:0},apiOverride});
  date='2026-10-21';blocked=true;app.getNode('widget-new-title').value='One durable task';
  const first=app.nodes.get('widget-new-form').onsubmit({preventDefault(){}});
  const second=app.nodes.get('widget-new-form').onsubmit({preventDefault(){}});
  assert.equal(captures.length,0);release();await Promise.all([first,second]);
  assert.equal(captures.length,1);assert.equal(captures[0].due,'2026-10-21');assert.ok(captures[0].external_id.startsWith('widget:'));
});

test('idle break Play starts the visible break with its shared five-minute preset',async()=>{
  const task={source:'library',id:'a',title:'A'},commands=[];
  const app=await start({saved:{'desktop:selected-task':task,'desktop:selected-task-revision':1},state:{revision:1,status:'idle',mode:'break',task,elapsed_ms:0,duration_ms:300000},catalog:Promise.resolve({notes:[{...task,kind:'task',status:'active'}]})});
  app.timeClient.command=async(action,fields)=>{commands.push({action,...fields});return app.timeClient.state={...app.timeClient.state,...fields,status:'running',revision:2};};
  await app.nodes.get('widget-run').onclick();assert.deepEqual(commands,[{action:'start',mode:'break',duration_ms:300000,task}]);
});

for(const hasReceipt of[true,false])test(`activity activation recovery after peer stop ${hasReceipt?'resolves exact receipt':'retains missing-receipt legacy error'}`,async()=>{
  const task={source:'library',id:'a',title:'A'},note={id:'a',title:'A',kind:'task',status:'active',version:1};
  const activation={request_id:'original-start',expected_revision:2,action:'start',mode:'stopwatch',task,backfill_from:1000};
  const op={kind:'capture',note,payload:{text:'A',title:'A',kind:'task',due:'2026-10-20',external_id:'original-note'},activity:{backfill_from:1000},stage:'activate',...(hasReceipt?{activation}:{})};
  const app=await start({saved:{'desktop:widget-operation':op},state:{revision:5,status:'idle',mode:'stopwatch',task,elapsed_ms:2000,duration_ms:0},catalog:Promise.resolve({notes:[note]})});
  const receipts=[];app.timeClient.command=async()=>assert.fail('Retry must never generate a new activity start');app.timeClient.commandWithReceipt=async body=>{receipts.push(body);return app.timeClient.state;};
  await app.nodes.get('widget-retry').onclick();assert.equal(app.timeClient.state.status,'idle');assert.equal(app.timeClient.state.revision,5);
  if(hasReceipt){assert.deepEqual(receipts,[activation]);assert.equal(app.saved['desktop:widget-operation'],null);}
  else{assert.deepEqual(receipts,[]);assert.ok(app.saved['desktop:widget-operation']);assert.match(app.nodes.get('widget-error').textContent,/квитанция/);}
});

test('definitely rejected activity activation settles OP without recreating a start or blocking the saved task',async()=>{
  const task={source:'library',id:'a',title:'A'},note={id:'a',title:'A',kind:'task',status:'active',version:1};
  const activation={request_id:'rejected-start',expected_revision:2,action:'start',mode:'stopwatch',task,backfill_from:1000};
  const app=await start({saved:{'desktop:widget-operation':{kind:'capture',note,activity:{backfill_from:1000},stage:'activate',activation}},state:{revision:5,status:'idle',mode:'stopwatch',task,elapsed_ms:2000,duration_ms:0},catalog:Promise.resolve({notes:[note]})});
  let deliveries=0;app.timeClient.commandWithReceipt=async()=>{deliveries++;throw Object.assign(new Error('CAS rejected'),{status:409});};app.timeClient.command=async()=>assert.fail('no replacement start');app.timeClient.retry=async()=>app.timeClient.state;
  await app.nodes.get('widget-retry').onclick();assert.equal(deliveries,1);assert.equal(app.saved['desktop:widget-operation'],null);assert.equal(app.nodes.get('widget-retry').hidden,true);assert.equal(app.nodes.get('widget-run').disabled,false);assert.match(app.nodes.get('widget-error').textContent,/запуск отклонён/);assert.equal(note.status,'active');
  await app.nodes.get('widget-retry').onclick();assert.equal(deliveries,1);assert.equal(app.timeClient.state.revision,5);assert.equal(app.timeClient.state.status,'idle');
});

for(const rejected of[false,true])test(`overtaken activity ${rejected?'rejection':'receipt'} preserves peer paused task and newer explicit selection`,async()=>{
  const a={source:'library',id:'a',title:'A'},b={source:'library',id:'b',title:'B'},c={source:'library',id:'c',title:'C'};
  const notes=[a,b,c].map(task=>({...task,kind:'task',status:'active',version:1}));
  for(const local of[null,c]){
    const activation={request_id:'old-activity-a',expected_revision:1,action:'start',mode:'stopwatch',task:a,backfill_from:1000};
    const saved={'desktop:widget-operation':{kind:'capture',note:notes[0],activity:{backfill_from:1000},stage:'activate',activation},...(local?{'desktop:selected-task':local,'desktop:selected-task-revision':7}:{})};
    const app=await start({saved,state:{revision:7,status:'paused',mode:'stopwatch',task:b,elapsed_ms:2000,duration_ms:0},catalog:Promise.resolve({notes})});
    const expected=local||b;assert.equal(app.nodes.get('widget-burning').textContent,expected.title);
    app.timeClient.commandWithReceipt=async()=>{if(rejected)throw Object.assign(new Error('CAS rejected'),{status:409});return app.timeClient.state;};
    const commands=[];app.timeClient.command=async(action,fields={})=>{commands.push({action,...fields});app.timeClient.state={...app.timeClient.state,...(action==='switch_task'?{task:fields.task}:{}),status:action==='resume'?'running':app.timeClient.state.status,revision:app.timeClient.state.revision+1};return app.timeClient.state;};
    await app.nodes.get('widget-retry').onclick();assert.equal(app.nodes.get('widget-burning').textContent,expected.title);assert.deepEqual(app.saved['desktop:selected-task'],local||undefined);assert.equal(app.timeClient.state.revision,7);
    await app.nodes.get('widget-run').onclick();assert.deepEqual(commands,local?[{action:'switch_task',task:c},{action:'resume'}]:[{action:'resume'}]);
  }
});
