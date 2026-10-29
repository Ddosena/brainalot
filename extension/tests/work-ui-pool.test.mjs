import test from 'node:test';import assert from 'node:assert/strict';
import {possibleOn,installPool} from '../work-ui/pool.js';
import {installRepeatables} from '../work-ui/repeatables.js';
const day='2026-10-28';
const calendar=(id,date,extra={})=>({id,kind:'calendar_optional',title:'Same title',source_date:date,available:true,source_event_id:id,day:null,pct:0,...extra});
test('Calendar candidates and pinned evergreen rows appear only on a confirmed occurrence day',()=>{const today=calendar('today',day),future=calendar('future','2026-11-02',{availability:'any_day',pinned:true});assert.deepEqual(possibleOn([today,future],day).map(c=>c.id),['today']);assert.deepEqual(possibleOn([today,future],'2026-11-02').map(c=>c.id),['future']);assert.equal(possibleOn([calendar('unknown',null,{availability:'any_day'})],day).length,0);});
test('only proven occurrence IDs deduplicate, never matching titles',()=>{const a=calendar('a',day,{source_event_id:'event-a'}),b=calendar('b',day,{source_event_id:'event-b'}),alias=calendar('alias',day,{source_occurrences:[{event_id:'event-a',date:day,identity_match:'exact'}]});assert.deepEqual(possibleOn([a,b,alias],day).map(c=>c.id),['a','b']);});
test('all-day spans respect exclusive source end and do not invent later occurrences',()=>{const c=calendar('span','2026-10-27',{all_day:true,source_end_date:'2026-10-30'});assert.equal(possibleOn([c],day).length,1);assert.equal(possibleOn([c],'2026-10-30').length,0);});
test('selected and factual history is not deduplicated or mutated by possible projection',()=>{const original=[calendar('placed',day,{day:2,selected_date:day,pct:20}),calendar('available',day),calendar('deleted',day,{source_deleted:true})],before=JSON.stringify(original);assert.deepEqual(possibleOn(original,day).map(c=>c.id),['available']);assert.equal(JSON.stringify(original),before);});
test('pinned Calendar rows cannot leak a future occurrence into today',()=>{const S={cubes:[calendar('future','2026-11-02',{pinned:true})],goals:[],projects:[],repeatables:[]},ctx={S,U:{day:2},iso:()=>day,esc:String,isDone:()=>false,visible:()=>true,goalColor:()=> '#123',keycap:k=>`<div>${k.id}</div>`};installPool(ctx);installRepeatables(ctx);assert.equal(ctx.pinnedPanel(),'');});
test('goal chips: nearest goals first, day goals preselected, click toggles a multi-selection down to none',()=>{
 // Owner 2026-10-29: variant B with goals instead of projects; several goals at once; clicking a chosen goal removes it.
 const S={cubes:[],projects:[{id:'p',name:'Project',c:'#f09300'}],goals:[{id:'far',p:'p',title:'November',s:12,e:15,pct:0},{id:'now',p:'p',title:'Today',s:1,e:3,pct:20},{id:'soon',p:'p',title:'Soon',s:4,e:5,pct:0}]};
 const U={screen:'day',day:2,weekStart:0},ctx={S,U,esc:String,isDone:g=>g.pct>=100,visible:()=>true,goalColor:()=> '#f09300',iso:()=>day,ddmm:i=>String(i)};installPool(ctx);
 assert.deepEqual(ctx.poolGoals(2).map(g=>g.id),['now']);
 const order=[...ctx.goalChips().matchAll(/data-arg="([^"]+)"/g)].map(m=>m[1]);assert.deepEqual(order,['now','soon','far']);
 assert.match(ctx.goalChips(),/title="November · 12–15 · 0%"/);assert.match(ctx.goalChips(),/class="dot"/);
 ctx.togglePoolGoal('far');assert.deepEqual(ctx.poolGoals(2).map(g=>g.id),['now','far']);
 ctx.togglePoolGoal('now');ctx.togglePoolGoal('far');assert.deepEqual(ctx.poolGoals(2),[]);
 U.screen='week';U.poolGoal='far';assert.equal(ctx.poolGoals(2)[0].id,'far');
});
