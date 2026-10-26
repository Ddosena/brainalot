import { test } from "node:test";
import assert from "node:assert/strict";
import { cubeDimensions,trayCapacity,nearestSlot,nearestPlanningSlot,constructorModel,resourcesForSave,resourcesForEdit,launchGroups,validUrl,todayIn,monday,formatReadiness } from "../work-constructor-model.js";
import { slotLayout,createTraySlots,shouldUnstickPanel } from "../work-constructor-slots.js";
import { cubeCommand,constructorPatch,weekProposals,groupWeekCubes,createWorkConstructor } from "../work-constructor.js";
import { historyCubes,goalMembers,monthGoals,weightedReadiness,pedestalShelf,awardsPanel,monthHtml,dayAwards } from "../work-constructor-history.js";
import { AWARD_VARIANTS } from "../work-trophy-art.js";

test("approved compact tokens fit ordered slots regardless of their planned minutes",()=>{
  for(const variant of ["day","week"])for(let m=0;m<=240;m+=5)assert.deepEqual(cubeDimensions(m,variant),{width:89,height:62});
  for(const width of [290,345,410,540,800])for(let count=1;count<=25;count++){
    const sizes=Array.from({length:count},(_,i)=>cubeDimensions([15,30,60,120,240][i%5])),capacity=trayCapacity(width,sizes),boxes=sizes.map((s,i)=>({...s,x:8+i%capacity.columns*capacity.cellWidth,y:capacity.height-s.height-10-Math.floor(i/capacity.columns)*capacity.rowHeight}));
    for(const b of boxes){assert.ok(b.x>=0&&b.x+b.width<=width);assert.ok(b.y>=0&&b.y+b.height<=capacity.height);}
    for(let i=0;i<boxes.length;i++)for(let j=i+1;j<boxes.length;j++){const a=boxes[i],b=boxes[j];assert.ok(a.x+a.width<=b.x||b.x+b.width<=a.x||a.y+a.height<=b.y||b.y+b.height<=a.y);}
  }
});
test("snap stays on five-minute grid even at a 12:32 Calendar boundary",()=>{
  assert.equal(nearestSlot([{start:480,minutes:272}],60,752),755);
  assert.equal(nearestSlot([{start:480,minutes:272},{start:814,minutes:506}],60,752),null);
  for(let boundary=481;boundary<600;boundary++)for(const size of [5,15,30,60,120]){
    const start=nearestSlot([{start:480,minutes:boundary-480}],size,boundary);
    assert.equal(start%5,0);assert.ok(start>=boundary&&start+size<=1320);
  }
});
test("button and hover snap share canonical working-hour bounds while the axis stays 08–22",()=>{
  const p={settings:{work_start:"09:00",work_end:"20:00"}};
  assert.equal(nearestPlanningSlot(p,[],15,8*60),9*60);
  assert.equal(nearestPlanningSlot(p,[],15,21*60),19*60+45);
  assert.equal(nearestPlanningSlot(p,[{start:9*60,minutes:60}],15,8*60),10*60);
  assert.equal(nearestPlanningSlot({settings:{work_start:"11:00",work_end:"20:00"}},[],60,8*60),11*60);
});
function fixture(date="2030-03-04"){
  return {settings:{timezone:"Europe/Moscow",day_limit_minutes:300},tasks:[{id:"task",title:"Actual task",course:"course",estimate_minutes:{central:600}}],task_segments:[{id:"stage",task_id:"task",title:"Практика",minutes:600}],completed_cubes:{cube:{percent:25,actual_minutes:17}},constructor:{version:1,cubes:{cube:{id:"cube",kind:"task",task_id:"task",segment_id:"stage",minutes:60,title:"Saved",planned_date:date}},days:{[date]:{initialized:true}}},result:{days:[{date,focus_task_id:"task"}],cubes:[],tasks:[{id:"task",remaining_minutes:585}],task_pool:[],constructor:{cubes:[{id:"cube",kind:"task",task_id:"task",segment_id:"stage",minutes:60,title:"Saved",planned_date:date,progress_percent:25,actual_minutes:17,selected_date:date,start:null}],days:{[date]:{date,initialized:true,selected_cube_ids:["cube"],possible_cube_ids:[],hidden_cube_ids:[],actual_minutes:17,credited_minutes:15,selected_minutes:60,fixed_minutes:0,total_work_minutes:60,budget_minutes:300,available_minutes:240,over_cap_minutes:0,events:[]}}}}};
}
test("read projection preserves facts, never converts a legacy day or charges proposals",()=>{
  const p=fixture(),before=JSON.stringify(p),m=constructorModel(p,"2030-03-04",{today:[{id:"a",kind:"task",status:"open",title:"A"}],backlog_week:[{id:"a",kind:"task",title:"A"},{id:"b",kind:"task",title:"B"}],week:[{id:"b",kind:"task",title:"B"},{id:"c",kind:"task",title:"C"}],unscheduled:[{id:"excluded",kind:"task"}]});
  assert.equal(m.summary.actual,17);assert.equal(m.summary.selected,60);assert.deepEqual(m.possible.map(c=>c.source_ref),["a","b","c"]);assert.equal(JSON.stringify(p),before);
  delete p.constructor;delete p.result.constructor;p.result.cubes=[{id:"legacy",date:"2030-03-04",task_id:"task",manual:true,minutes:60}];p.result.days[0].prep_cubes=[{id:"prep-key",calendar_event_id:"event",minutes:25}];
  const legacyBefore=JSON.stringify(p),legacy=constructorModel(p,"2030-03-04");assert.equal(legacy.initialized,false);assert.equal(legacy.selected[0].id,"legacy");assert.equal(legacy.possible[0].id,"constructor:prep-key");assert.equal(legacy.possible[0].source_ref,"prep-key");assert.equal(JSON.stringify(p),legacyBefore);
});
test("configured weekly proposals remain visible on an uninitialized future day and after reload without charging or migration",()=>{
  const p=fixture(),next="2030-03-11",prepared={id:"prepared",kind:"task",task_id:"task",minutes:45,title:"Configured future step",planned_date:next,comment:"Keep settings",links:[{id:"link",title:"Local",url:"http://127.0.0.1:43855/?prepared=1"}],groups:[]},prep={id:"constructor:future-prep",kind:"prep",source_ref:"future-prep",minutes:20,title:"Canonical prep",planned_date:next};
  p.result.constructor.cubes.push(prepared,prep);
  p.result.constructor.days[next]={date:next,initialized:false,possible_cube_ids:["prepared",prep.id],selected_cube_ids:[],hidden_cube_ids:[]};
  p.result.days.push({date:next,budget_minutes:300,fixed_minutes:85,builder_selected_minutes:0,builder_total_work_minutes:85,builder_available_minutes:215,prep_cubes:[{id:"future-prep",minutes:25}]});
  const before=JSON.stringify(p);
  for(const state of [p,JSON.parse(before)]){
    const projected=constructorModel(state,next,{today:[],week:[],backlog_week:[]});
    assert.equal(projected.initialized,false);assert.deepEqual(projected.possible.map(c=>c.id),["prepared",prep.id]);assert.equal(projected.possible[0].minutes,45);assert.equal(projected.possible[0].comment,"Keep settings");assert.equal(projected.possible[0].links[0].id,"link");
    assert.equal(projected.possible.filter(c=>c.kind==="prep").length,1);assert.equal(projected.possible.find(c=>c.kind==="prep").minutes,20);
    assert.equal(projected.summary.selected,0);assert.equal(projected.summary.total,85);assert.equal(projected.summary.free,215);assert.equal(JSON.stringify(state),before);
  }
});
test("weekly materialization suppresses only its exact blueprint; splitting one of three preserves 180 minutes",()=>{
  const p=fixture();p.result.constructor.cubes[0].blueprint_ref="auto-a";p.result.cubes=[{id:"auto-a",date:"2030-03-04",task_id:"task",minutes:60},{id:"auto-a2",date:"2030-03-04",task_id:"task",minutes:60},{id:"auto-a3",date:"2030-03-04",task_id:"task",minutes:60},{id:"auto-b",date:"2030-03-05",task_id:"other",minutes:60}];
  const list=weekProposals(p,"2030-03-04");assert.deepEqual(list.map(c=>c.id),["cube","planned-auto-a2","planned-auto-a3","planned-auto-b"]);assert.equal(groupWeekCubes(list).length,2);
  p.result.constructor.cubes[0].minutes=30;p.result.constructor.cubes.push({...p.result.constructor.cubes[0],id:"split-part",minutes:30});
  assert.equal(weekProposals(p,"2030-03-04").filter(c=>c.task_id==="task").reduce((s,c)=>s+c.minutes,0),180);
  constructorModel(p,"2030-03-11");assert.equal(list[0].planned_date,"2030-03-04");
  p.result.tasks.push({id:"other",scope_exempt:true});assert.ok(!weekProposals(p,"2030-03-04").some(c=>c.task_id==="other"));
});
test("popup field patch preserves fresh unrelated title/resources and detects same-field conflicts",()=>{
  const base={id:"cube",title:"Old title",output:"Old output",steps:["Old"],comment:"Old comment",links:[{id:"a",title:"A",url:"https://example.test/a"}],groups:[]},draft={...structuredClone(base),comment:"My new comment"},latest={...structuredClone(base),title:"Remote title",links:[{id:"b",title:"B",url:"https://example.test/b"}]};
  assert.deepEqual(constructorPatch(base,draft,latest,new Set(["comment"])),{cube_id:"cube",comment:"My new comment"});
  assert.throws(()=>constructorPatch(base,draft,{...latest,comment:"Remote comment"},new Set(["comment"])),error=>error.code==="constructor-edit-conflict");
  assert.throws(()=>constructorPatch(base,{...draft,links:[]},latest,new Set(["resources"])),error=>error.code==="constructor-edit-conflict");
  assert.deepEqual(latest.links,[{id:"b",title:"B",url:"https://example.test/b"}]);
});
test("resource drafts persist safely, opening uses only canonical HTTP(S) without credentials",()=>{
  const c={links:[{id:"bad",title:"<script>",url:"javascript:alert(1)"},{id:"good",title:"Local",url:"http://127.0.0.1:43855/?a=1"}],groups:[{id:"group",title:"Actual resources",links:[{id:"b",title:"B",url:"https://example.test/b"}]}]};
  const payload=resourcesForSave(c);assert.equal(payload.links.length,1);assert.equal(payload.resource_drafts.links.length,2);assert.equal(resourcesForEdit(payload).links[0].url,"javascript:alert(1)");assert.equal(launchGroups(payload).length,2);
  for(const bad of ["javascript:alert(1)","data:text/html,x","file:///c:/secret","https://a:b@example.test/x","http://",""])assert.equal(validUrl(bad),null);
  const command=cubeCommand({...payload,id:"cube",kind:"task",task_id:"task",minutes:60,title:"A",selected_date:"2030-03-04",token:"never",candidate:true});assert.ok(!Object.hasOwn(command,"selected_date"));assert.ok(!Object.hasOwn(command,"token"));assert.ok(command.resource_drafts);
});
test("ordered slots expand in rows, ignore legacy coordinates and do not persist layout",()=>{
  const size=cubeDimensions(60),items=Array.from({length:40},(_,i)=>({dataset:{ccube:`cube-${i}`,minutes:'60'}}));
  for(const width of [260,390,820])for(const count of [0,1,8,10,24,40]){
    const layout=slotLayout(width,count,size);
    assert.equal(layout.positions.length,count);assert.equal(layout.rows,Math.max(1,Math.ceil(count/layout.columns)));
    for(const p of layout.positions){assert.equal(p.angle,0);assert.ok(p.x>=9&&p.x+size.width<=width-7);assert.ok(p.y>=9&&p.y+size.height<=layout.height-8);}
    assert.deepEqual(layout.positions,slotLayout(width,count,size).positions);
  }
  const classes=new Set(),container={clientWidth:390,querySelectorAll:()=>items,classList:{add:name=>classes.add(name)},style:{},dataset:{}};
  let writes=0;const slots=createTraySlots(container,{positions:{'cube-0':{x:999,y:999,angle:42}},onSettled(){writes++;}});
  assert.ok(classes.has('cc-slot-tray'));assert.deepEqual(slots.snapshot()['cube-0'],{x:9,y:9,angle:0});
  slots.pause();slots.wake();slots.resize('cube-0',size);slots.destroy();assert.equal(writes,0);
  assert.equal(shouldUnstickPanel(520,900,160),false);assert.equal(shouldUnstickPanel(740,900,160),true);
});
test("pedestals/month resolve aliased history once and show unfinished goal members without fake completion",()=>{
  const p=fixture("2030-03-04");p.completed_cubes={};
  const done={id:"done",canonical_id:"done",kind:"prep",title:"Finished source",minutes:60,progress_percent:100,actual_minutes:20,report_date:"2030-03-04",selected_date:null},unfinished={id:"unfinished",kind:"task",task_id:"task",title:"Still unfinished",minutes:120,progress_percent:25,actual_minutes:17,report_date:"2030-03-04"};
  p.result.constructor.cubes=[];p.result.constructor.goal_cubes={done,rawprep:{...done,id:"rawprep",alias_of:"done",canonical_id:"done"},unfinished};
  const goal={id:"week:2030-03-04",scope:"week",date:"2030-03-04",title:"Closed week",percent:50,status:"completed",achieved:false,cube_ids:["rawprep","done","unfinished"]};
  p.result.constructor.goals=[goal,{id:"day:2030-03-04",scope:"day",date:"2030-03-04",title:"Cancelled day",percent:50,status:"cancelled",achieved:false,cube_ids:["done","unfinished"]}];
  const before=JSON.stringify(p),members=goalMembers(p,goal);assert.equal(historyCubes(p).length,2);assert.equal(members.length,2);assert.equal(weightedReadiness(members),50);assert.equal(monthGoals(p,"2030-03").length,2);
  const shelf=pedestalShelf(p,"2030-03-04");assert.equal((shelf.match(/data-id="done"/g)||[]).length,1);assert.ok(!shelf.includes('data-id="unfinished"'));
  const month=monthHtml(p,"2030-03",[{goalId:goal.id},{goalId:goal.id,date:"2030-03-04"}]);assert.ok(month.includes("is-unfinished"));assert.ok(month.includes('width:25%'));assert.ok(month.includes("25%"));assert.ok(month.includes("Still unfinished"));assert.equal(JSON.stringify(p),before);
});
test("closure-only snapshots stay in explicit goal drilldown, not ordinary cube history",()=>{
  const p=fixture("2030-03-04");p.completed_cubes={};p.result.constructor.cubes=[];
  const snapshot={id:"snapshot",kind:"task",task_id:"task",title:"Original unfinished step",minutes:60,progress_percent:0,actual_minutes:0,planned_date:"2030-03-04",archived_goal_member:true};
  const reported={id:"reported",kind:"task",task_id:"task",title:"Actual reported step",minutes:60,progress_percent:25,actual_minutes:7,report_date:"2030-03-04",archived_goal_member:true};
  p.result.constructor.goal_cubes={snapshot,reported};
  const goal={id:"day:2030-03-04",scope:"day",date:"2030-03-04",title:"Closed goal",percent:13,status:"completed",cube_ids:["snapshot","reported"]};p.result.constructor.goals=[goal];
  assert.deepEqual(historyCubes(p).map(c=>c.id),["reported"]);assert.equal(goalMembers(p,goal).length,2);
  const ordinary=monthHtml(p,"2030-03",[{date:"2030-03-04"}]);assert.ok(!ordinary.includes("Original unfinished step"));assert.ok(ordinary.includes("Actual reported step"));
  const explicit=monthHtml(p,"2030-03",[{goalId:goal.id}]);assert.ok(explicit.includes("Original unfinished step"));assert.ok(explicit.includes("is-unfinished"));assert.equal(snapshot.progress_percent,0);
  snapshot.report_date="2030-03-04";assert.ok(historyCubes(p).some(c=>c.id==="snapshot"),"a real report date establishes ordinary history even at zero progress");
});
test("saved goal and cube awards keep distinct scales, stable variants and honest unfinished/cancelled states",()=>{
  const p=fixture(),date="2030-03-04";p.completed_cubes={};p.result.constructor.cubes=[];
  const done={id:"done",kind:"task",task_id:"task",title:"Done <script>",minutes:60,progress_percent:100,actual_minutes:17,report_date:date,award_variant:"crystal"},unfinished={id:"unfinished",kind:"task",task_id:"task",title:"Still working",minutes:15,progress_percent:0,planned_date:date,award_variant:"cup"};
  p.result.constructor.goal_cubes={done,raw:{...done,id:"raw",canonical_id:"done",alias_of:"done"},unfinished};
  const goal={id:"day:"+date,scope:"day",date,title:"Manual closure",status:"completed",percent:80,cube_ids:["raw","unfinished"],award_variant:"phoenix"},week={...goal,id:"week:"+date,scope:"week",title:"Earned week",award_variant:"laurel"},cancelled={...goal,id:"cancelled",status:"cancelled",award_variant:"crown"};
  p.result.constructor.goals=[goal,week,cancelled,{...goal,id:"future",date:"2030-03-11",award_variant:"book"}];
  const before=JSON.stringify(p),shelf=pedestalShelf(p,date),month=monthHtml(p,"2030-03");
  assert.deepEqual(dayAwards(p,date).map(g=>g.id),[goal.id,week.id]);assert.deepEqual(dayAwards(p,"2030-03-10").map(g=>g.id),[week.id]);assert.deepEqual(dayAwards(p,"2030-03-11").map(g=>g.id),["future"]);
  assert.equal((shelf.match(/data-award-variant="crystal"/g)||[]).length,1,"aliased cube has one compact award");assert.ok(shelf.includes('small-award-token'));assert.ok(!shelf.includes('data-award-variant="phoenix"'));assert.ok(awardsPanel(p,date).includes('data-award-variant="phoenix"'));assert.ok(!shelf.includes('data-award-variant="crown"'));assert.ok(!shelf.includes('data-award-variant="cup"'));
  assert.ok(month.includes('data-award-variant="phoenix"'));assert.ok(!month.includes('data-award-variant="crown"'));assert.ok(month.includes("is-cancelled"));assert.ok(month.includes("80%"));
  const members=monthHtml(p,"2030-03",[{goalId:goal.id}]);assert.ok(members.includes("is-unfinished"));assert.ok(members.includes('width:0%'));assert.ok(members.includes("&lt;script&gt;"));assert.equal(p.result.constructor.goal_cubes.unfinished.progress_percent,0);
  for(const variant of AWARD_VARIANTS){goal.award_variant=variant.id;assert.ok(awardsPanel(p,date).includes('data-award-variant="'+variant.id+'"'));}
  goal.award_variant="phoenix";assert.equal(pedestalShelf(p,date),shelf);assert.equal(JSON.stringify(p),before);assert.equal(pedestalShelf(JSON.parse(before),date),shelf);
});
test("near-complete goal and weighted history labels never claim 100 while a positive-weight member is unfinished",()=>{
  const date="2030-03-04",cubes=[{id:"done",kind:"task",minutes:240,progress_percent:100,report_date:date},{id:"not-done",kind:"task",title:"Actual 99",minutes:5,progress_percent:99,actual_minutes:17,report_date:date}];
  assert.equal(weightedReadiness(cubes),99.9);assert.equal(weightedReadiness([{minutes:60,progress_percent:100},{minutes:30,progress_percent:0}]),67);assert.equal(weightedReadiness([]),0);assert.equal(weightedReadiness([{minutes:0,progress_percent:100}]),0);assert.equal(weightedReadiness([{minutes:60,progress_percent:100},{minutes:0,progress_percent:0}]),100);
  assert.equal(formatReadiness(99.9,"ru-RU"),"99,9");assert.equal(formatReadiness(99.99,"en-US"),"99.9");assert.equal(formatReadiness(67,"en-US"),"67");assert.equal(formatReadiness(100,"en-US"),"100");
  const p=fixture(date);p.completed_cubes={};p.result.constructor.cubes=[];p.result.constructor.goal_cubes=Object.fromEntries(cubes.map(c=>[c.id,c]));const goal={id:"week:"+date,scope:"week",date,title:"Nearly finished",status:"completed",percent:99.9,cube_ids:cubes.map(c=>c.id),award_variant:"cup"};p.result.constructor.goals=[goal];
  const before=JSON.stringify(p),expected=formatReadiness(99.9)+"%",month=monthHtml(p,"2030-03"),shelf=pedestalShelf(p,date),week=monthHtml(p,"2030-03",[{goalId:goal.id}]),day=monthHtml(p,"2030-03",[{goalId:goal.id,date}]);
  assert.ok(month.includes(expected));assert.ok(!shelf.includes('data-id="not-done"'));
  for(const html of [week,day]){const heading=html.match(/<header class="cc-history-heading">[\s\S]*?<\/header>/)[0];assert.ok(heading.includes(expected));assert.ok(!heading.includes("100%"));}
  assert.ok(day.includes("99%"));assert.ok(day.includes("is-unfinished"));assert.equal(cubes[1].progress_percent,99);assert.equal(JSON.stringify(p),before);
});
test("mount only reads Work/dashboard; goal closure is separate from derived readiness",async()=>{
  const globals={document:globalThis.document,window:globalThis.window,innerWidth:globalThis.innerWidth,innerHeight:globalThis.innerHeight,matchMedia:globalThis.matchMedia,requestAnimationFrame:globalThis.requestAnimationFrame,cancelAnimationFrame:globalThis.cancelAnimationFrame};
  const handlers=new Map(),elements=[],calls=[],date=todayIn(),p=fixture(date),root={innerHTML:"",querySelector(){return null;},querySelectorAll(){return [];},contains(){return true;},addEventListener(){},removeEventListener(){}};
  const document={activeElement:null,hidden:false,querySelector(){return null;},querySelectorAll(){return [];},addEventListener(name,fn){const entries=handlers.get(name)||[];entries.push(fn);handlers.set(name,entries);},removeEventListener(){},body:{append(el){elements.push(el);}},createElement(){return {className:"",dataset:{},style:{},textContent:"",offsetWidth:180,offsetHeight:30,querySelectorAll(){return [];},querySelector(){return null;},setAttribute(){},remove(){this.removed=true;}};}};
  Object.assign(globalThis,{document,window:{addEventListener(){},removeEventListener(){}},innerWidth:1440,innerHeight:900,matchMedia:()=>({matches:true}),requestAnimationFrame:()=>1,cancelAnimationFrame(){}});
  const app=createWorkConstructor(root,{state:{planning:p},async action(action,data){calls.push({action,data});if(action==="constructor_goal"||action==="constructor_goal_status"){p.constructor.weeks??={};const map=data.scope==="week"?p.constructor.weeks:p.constructor.days;map[data.date]={...map[data.date],...(data.title!==undefined?{goal:data.title}:{}),...(data.status?{status:data.status}:{})};}return {state:{planning:p}};},async pendingDraft(){return null;},async readDashboard(){return {backlog_week:[{id:"note",kind:"task",title:"Actual backlog"}]};},toast(){}});
  try{
    await new Promise(resolve=>setTimeout(resolve,0));assert.deepEqual(calls,[]);assert.ok(root.innerHTML.includes('data-testid="work-constructor"'));assert.ok(root.innerHTML.includes('data-testid="day-goal"'));
    // Opening/closing an untouched cube is read-only. Goal edits cannot enter popup report logic.
    const cube={dataset:{ccube:"cube",tray:"selected"},getBoundingClientRect(){return {left:100,right:218,top:200,bottom:252};},closest(selector){return selector==="[data-ccube]"?this:null;}};
    handlers.get("click")[0]({target:cube,preventDefault(){}});handlers.get("keydown")[0]({key:"Escape",target:cube});await new Promise(resolve=>setTimeout(resolve,0));assert.deepEqual(calls,[]);
    const goalTitle={dataset:{goalTitle:"week",goalDate:monday(date)},value:"QA goal only",closest(){return null;}},goalPercent={dataset:{goalPercent:"week",goalDate:monday(date)},value:"70",closest(){return null;}};
    handlers.get("input")[0]({target:goalTitle});handlers.get("change")[0]({target:goalTitle});handlers.get("input")[0]({target:goalPercent});handlers.get("change")[0]({target:goalPercent});handlers.get("keydown")[0]({key:"Tab",target:goalPercent});await new Promise(resolve=>setTimeout(resolve,0));
    assert.deepEqual(calls,[{action:"constructor_goal",data:{scope:"week",date:monday(date),title:"QA goal only"}}]);assert.equal(p.completed_cubes.cube.percent,25);assert.ok(!root.innerHTML.includes("data-goal-percent"));
    const button={dataset:{action:"goal-close",scope:"week",goalDate:monday(date),status:"completed"},closest(selector){return selector==="[data-action]"?this:null;}};
    handlers.get("click")[0]({target:button,preventDefault(){}});await new Promise(resolve=>setTimeout(resolve,0));assert.deepEqual(calls.at(-1),{action:"constructor_goal_status",data:{scope:"week",date:monday(date),status:"completed"}});assert.equal(p.completed_cubes.cube.percent,25);assert.equal(p.constructor.weeks[monday(date)].status,"completed");
    const hover={dataset:{ctooltip:"Old generic tooltip"},closest(selector){return selector.includes("[data-ctooltip]")?this:null;}};
    handlers.get("pointermove")[0]({target:hover,clientX:300,clientY:240});assert.ok(!elements.some(el=>el.className?.includes('cc-hover-tooltip')));assert.equal(calls.length,2);
    p.result.constructor.goals=[{id:"day:"+date,scope:"day",date,title:"Closed day",status:"completed",percent:99.9,cube_ids:["cube"],award_variant:"orbit"}];app.render();assert.ok(root.innerHTML.includes('data-action="month-goal"'));assert.ok(root.innerHTML.includes('width:99.9%'));assert.ok(root.innerHTML.includes(formatReadiness(99.9)+'%'));assert.equal(p.completed_cubes.cube.percent,25);
    const earned={dataset:{action:"month-goal",id:"day:"+date},closest(selector){return selector==="[data-action]"?this:null;}};
    handlers.get("click")[0]({target:earned,preventDefault(){}});await new Promise(resolve=>setTimeout(resolve,0));assert.ok(root.innerHTML.includes('data-testid="month-history"'));assert.ok(root.innerHTML.includes("Saved"));assert.ok(root.innerHTML.includes("is-unfinished"));assert.equal(calls.length,2,"award drilldown is read-only");
  }finally{app.destroy();Object.assign(globalThis,globals);}
});
test("real popup rerender copies a DOM-like dataset to plain state across consecutive range changes",async()=>{
  const globals={document:globalThis.document,window:globalThis.window,innerWidth:globalThis.innerWidth,innerHeight:globalThis.innerHeight,matchMedia:globalThis.matchMedia,requestAnimationFrame:globalThis.requestAnimationFrame,cancelAnimationFrame:globalThis.cancelAnimationFrame};
  const handlers=new Map(),calls=[],date=todayIn(),p=fixture(date),root={innerHTML:"",querySelector(){return null;},querySelectorAll(){return [];},contains(){return true;},addEventListener(){},removeEventListener(){}};
  const document={activeElement:null,popup:null,hidden:false,querySelector(selector){return selector==="[data-constructor-popup]"?this.popup:null;},querySelectorAll(){return [];},addEventListener(name,fn){const entries=handlers.get(name)||[];entries.push(fn);handlers.set(name,entries);},removeEventListener(){},body:{append(el){if(Object.hasOwn(el.dataset,"constructorPopup"))document.popup=el;}},createElement(){
    const el={className:"",dataset:{},style:{},offsetWidth:332,offsetHeight:400,scrollTop:0,setAttribute(){},contains(node){return node?.owner===this;},querySelector(){return null;},querySelectorAll(selector){return selector==="input,textarea,select"?[this.range]:[];},remove(){if(document.popup===this)document.popup=null;}};
    el.range={owner:el,dataset:new Proxy({popupField:"minutes"},{}),value:"60",closest(selector){return selector==="[data-constructor-popup]"?el:null;},focus(){document.activeElement=this;}};return el;
  }};
  Object.assign(globalThis,{document,window:{addEventListener(){},removeEventListener(){}},innerWidth:1280,innerHeight:720,matchMedia:()=>({matches:true}),requestAnimationFrame:()=>1,cancelAnimationFrame(){}});
  const app=createWorkConstructor(root,{state:{planning:p},async action(action,data){calls.push({action,data});if(action==="constructor_cube"&&data.minutes!==undefined){p.constructor.cubes.cube.minutes=data.minutes;p.result.constructor.cubes[0].minutes=data.minutes;}return {state:{planning:p}};},async pendingDraft(){return null;},async readDashboard(){return {};},toast(){}});
  try{
    await new Promise(resolve=>setTimeout(resolve,0));
    const cube={dataset:{ccube:"cube",tray:"selected"},getBoundingClientRect(){return {left:100,right:218,top:200,bottom:252};},closest(selector){return selector==="[data-ccube]"?this:null;}};
    handlers.get("click")[0]({target:cube,preventDefault(){}});
    assert.throws(()=>structuredClone(document.popup.range.dataset),error=>error.name==="DataCloneError");
    for(const value of [30,25,20,15]){
      const old=document.popup,range=old.range;range.value=String(value);document.activeElement=range;
      handlers.get("input")[0]({target:range});handlers.get("change")[0]({target:range});await new Promise(resolve=>setTimeout(resolve,0));
      assert.notEqual(document.popup,old,"numeric change must finish rerendering instead of failing on DOMStringMap");
      assert.equal(document.activeElement,document.popup.range,"range focus survives the real popup replacement");
      assert.equal(p.constructor.cubes.cube.minutes,value,"each consecutive edit must be saved, not misidentified as remote conflict");
    }
    assert.equal(calls.filter(call=>call.action==="constructor_cube").length,4);assert.equal(p.completed_cubes.cube.percent,25);assert.equal(p.completed_cubes.cube.actual_minutes,17);
  }finally{app.destroy();Object.assign(globalThis,globals);}
});
