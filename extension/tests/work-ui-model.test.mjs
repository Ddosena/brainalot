import test from 'node:test';
import assert from 'node:assert/strict';
import {adaptModel, currentMonday, iso, fromIso} from '../work-ui/model-adapter.js';
test('adapts dated plan to prototype shape while preserving undated cube and calendar points', () => {
 const base=currentMonday(new Date(2026,9,28));
 const s=adaptModel({revision:2,today:'2026-10-28',projects:[{id:'p',title:'P',color:'#123456',visible:false,calendar_points:[{date:'2026-11-01',start:'12:30'}]}],tasks:[{id:'t',project_id:'p',start:'2026-10-26',end:'2026-11-01',minutes:90,progress_percent:40}],cubes:[{id:'c',task_id:'t',minutes:30,selected_date:null,start:null,links:[{id:'a',url:'https://example.com'}]}]}, {baseDate:base});
 assert.equal(s.projects[0].hidden,true); assert.equal(s.projects[0].calendarPoints[0].day,6); assert.equal(s.goals[0].h,1.5); assert.equal(s.cubes[0].day,null); assert.equal(s.cubes[0].links.length,1); assert.equal(iso(-1,base),'2026-10-25'); assert.equal(fromIso('2026-11-02',base),7); assert.equal(fromIso('2026-02-30',base),null);
});
test('maps a reduced isolated API fixture including forecast and optional cubes',()=>{
 const week={revision:1,today:'2026-10-28',projects:[{id:'primary',title:'A',color:'teal',visible:true,calendar_points:[]}],tasks:[{id:'a',project_id:'primary',title:'Material A',start:'2026-10-26',end:'2026-10-31',deadline:'2026-11-03',minutes:900,progress_percent:0,owned:true}],cubes:[{id:'planned-1',candidate:true,kind:'task',task_id:'a',minutes:60,selected_date:null},{id:'optional',kind:'calendar_optional',task_id:null,minutes:30,selected_date:null}],events:[{id:'lesson',date:'2026-10-26',start:'11:00',duration_minutes:120}]};
 const s=adaptModel(week,{baseDate:Date.UTC(2026,9,26)});assert.equal(s.goals[0].h,15);assert.equal(s.goals[0].s,0);assert.equal(s.cubes.find(c=>c.id==='planned-1').candidate,true);assert.equal(s.cubes.find(c=>c.id==='optional').src,'Календарь');assert.equal(s.events[0].min,120);assert.equal(s.projects[0].c,'teal');
});
test('explicit empty sources suppress old links, absent sources retain old links',()=>{
 const legacy={id:'link1',url:'https://example.org',title:'old'};const base={revision:1,projects:[],tasks:[],cubes:[{id:'c',minutes:30,links:[legacy]}]};
 assert.deepEqual(adaptModel(base).cubes[0].links,[legacy]);
 assert.deepEqual(adaptModel({...base,cubes:[{...base.cubes[0],sources:[]}]}).cubes[0].links,[]);
 const note={id:'n',url:'',text:'new note'};assert.deepEqual(adaptModel({...base,cubes:[{...base.cubes[0],sources:[legacy,note]}]}).cubes[0].links,[legacy,note]);
});
test('zero-minute prep ghosts are hidden, positive preparation and undated optional offers remain',()=>{
 const s=adaptModel({projects:[],tasks:[],cubes:[{id:'ghost',kind:'prep',minutes:0},{id:'prep',kind:'prep',minutes:25},{id:'unknown',kind:'calendar_optional',minutes:0,duration_unknown:true}]});assert.deepEqual(s.cubes.map(x=>x.id),['prep','unknown']);
});
test('unknown calendar duration renders provisionally without asserting source duration',()=>{const s=adaptModel({projects:[],tasks:[],cubes:[{id:'o',kind:'calendar_optional',minutes:0,duration_unknown:true,source_duration_minutes:null,source_minutes:0}]});assert.equal(s.cubes[0].min,30);assert.equal(s.cubes[0].source_minutes,null);assert.equal(s.cubes[0].duration_unknown,true);});
test('preserves planning initialization state for both empty and legacy workspaces',()=>{assert.equal(adaptModel({revision:0,planning_initialized:false}).planning_initialized,false);assert.equal(adaptModel({revision:2,planning_initialized:true}).planning_initialized,true);});
test('legacy projects with no plan still require onboarding',()=>{assert.equal(adaptModel({revision:1,planning_initialized:false,projects:[{id:'legacy',title:'Legacy'}]}).started,false);});
test('explicit projectless repeatable instances remain visible after reload without autostarting from a default template',()=>{assert.equal(adaptModel({revision:1,planning_initialized:true,repeatables:[{id:'prep-universal',origin:'system'}],cubes:[{id:'i',kind:'repeatable',repeatable_instance:true,minutes:25}]}).started,true);assert.equal(adaptModel({revision:1,planning_initialized:true,repeatables:[{id:'prep-universal',origin:'system'}],cubes:[]}).started,false);});

test('readiness is integer for goal and cube even with legacy decimal responses',()=>{
 const model=adaptModel({revision:1,projects:[],tasks:[{id:'g',progress_percent:53.3}],
 cubes:[{id:'c',task_id:'g',progress_percent:66.7}]});
 assert.equal(model.goals[0].pct,53);assert.equal(model.cubes[0].pct,67);
 const nearDone=adaptModel({tasks:[{id:'g',progress_percent:99.9}],cubes:[{id:'c',progress_percent:100}]});
 assert.equal(nearDone.goals[0].pct,99);assert.equal(nearDone.cubes[0].pct,100);
});
