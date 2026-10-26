import {test} from 'node:test';
import assert from 'node:assert/strict';
import {sourceNote,sourceImportanceTarget,sourceImportance,saveSourceImportance} from '../work-source-importance.js';
import {constructorModel} from '../work-constructor-model.js';
import {prioritizeSourcePools} from '../work-readable-cubes.js';

test('task and library importance are resolved from their live owner, never the cube copy',()=>{
  const planning={tasks:[{id:'task',importance_score:3},{id:'old'}]},note={id:'note',kind:'task',importance:'high',version:'v1'},dashboard={today:[note]},taskCube={id:'a',task_id:'task',importance_score:5},libraryCube={id:'b',kind:'brainalot',source_ref:'note',importance_score:1};
  assert.equal(sourceImportance(taskCube,planning,dashboard),3);
  assert.equal(sourceImportance({task_id:'old'},planning,dashboard),0);
  assert.equal(sourceNote(libraryCube,dashboard),note);assert.equal(sourceImportance(libraryCube,planning,dashboard),4);
  note.importance_score=0;note.version='v2';assert.equal(sourceImportance(libraryCube,planning,dashboard),0);
  planning.tasks[0].importance_score=5;assert.equal(sourceImportance(taskCube,planning,dashboard),5);
  for(const kind of ['calendar','prep','weekly_review'])assert.deepEqual(sourceImportanceTarget({kind,importance_score:5},planning,dashboard),{kind:null,id:null,score:0,editable:false});
  assert.equal(sourceImportanceTarget({kind:'brainalot',source_ref:'missing'},planning,dashboard).editable,false);
});

test('month and unscheduled Library cards resolve current metadata without Dashboard and prefer it over stale rows',()=>{
  const cube={id:'brainalot-note',kind:'brainalot',source_ref:'note',library_source:true,source_horizon:'month',library_note_version:'current',library_importance_score:4,library_importance:'high',importance_score:1};
  assert.deepEqual(sourceImportanceTarget(cube,{},{}),{kind:'note',id:'note',score:4,version:'current',editable:true});
  const stale={today:[{id:'note',kind:'task',version:'old',importance_score:1}]};
  assert.equal(sourceImportance(cube,{},stale),4);
  const planning={result:{constructor:{cubes:[{...cube,library_note_version:'newer',library_importance_score:5}]}}};assert.equal(sourceImportance(cube,planning,stale),5);
  cube.library_importance_score=0;assert.equal(sourceImportance(cube,{},stale),0);
  delete cube.library_importance_score;cube.library_importance='critical';cube.source_horizon='unscheduled';assert.equal(sourceImportance(cube,{},{}),5);
});

test('candidate, materialized selected and hidden library cubes inherit live dashboard score without mutation',()=>{
  const date='2030-03-04',cube={id:'saved',kind:'brainalot',source_ref:'note',title:'Saved title',minutes:60,importance_score:1,progress_percent:35,actual_minutes:17};
  const planning={tasks:[],constructor:{},result:{constructor:{cubes:[cube],days:{[date]:{initialized:true,selected_cube_ids:['saved'],possible_cube_ids:[],hidden_cube_ids:[]}}}}};
  const dashboard={backlog_week:[{id:'note',kind:'task',title:'Current note',importance_score:5,version:'v1'},{id:'candidate',kind:'task',title:'Candidate',importance:'normal',version:'candidate-v1'}]},before=structuredClone(planning);
  let model=constructorModel(planning,date,dashboard);
  assert.equal(model.selected[0].importance_score,5);assert.equal(model.all[0].importance_score,5);assert.equal(model.possible[0].importance_score,2);
  dashboard.backlog_week[0].importance_score=0;model=constructorModel(planning,date,dashboard);assert.equal(model.selected[0].importance_score,0);
  assert.equal(model.selected[0].progress_percent,35);assert.equal(model.selected[0].actual_minutes,17);assert.deepEqual(planning,before);
  planning.result.constructor.days[date].selected_cube_ids=[];planning.result.constructor.days[date].hidden_cube_ids=['saved'];
  assert.equal(constructorModel(planning,date,dashboard).hidden[0].importance_score,0);
});

test('group importance sorts tasks while preserving each logical chain and ties',()=>{
  const a=[{id:'a2'},{id:'a1'}],b=[{id:'b1'}],c=[{id:'c1'}],planning={tasks:[{id:'a',importance_score:1},{id:'b',importance_score:5},{id:'c',importance_score:5}]};
  const pools={chains:[{task_id:'a',cubes:a},{task_id:'b',cubes:b},{task_id:'c',cubes:c}],external:[{id:'zero'},{id:'note',kind:'brainalot',source_ref:'n'}],future:[]},before=structuredClone(pools),dashboard={week:[{id:'n',kind:'task',importance_score:4}]};
  const sorted=prioritizeSourcePools(pools,cube=>sourceImportance(cube,planning,dashboard));
  assert.deepEqual(sorted.chains.map(group=>group.task_id),['b','c','a']);assert.equal(sorted.chains[2].cubes,a);assert.deepEqual(a.map(cube=>cube.id),['a2','a1']);
  assert.deepEqual(sorted.external.map(cube=>cube.id),['note','zero']);assert.deepEqual(pools,before);
});

test('importance saves use typed Work action or exact library CAS patch and allow score5',async()=>{
  const calls=[],transports={action:async(...args)=>calls.push(['work',...args]),api:async(...args)=>calls.push(['api',...args])};
  await saveSourceImportance({kind:'task',id:'task',editable:true},5,transports);
  await saveSourceImportance({kind:'note',id:'a/b',version:'current-hash',editable:true},0,transports);
  assert.deepEqual(calls,[['work','task_importance',{task_id:'task',importance_score:5}],['api','/api/notes/a%2Fb','PATCH',{expected_version:'current-hash',patch:{importance_score:0}}]]);
  for(const bad of [-1,6,2.5,true,'4',NaN])await assert.rejects(saveSourceImportance({kind:'task',id:'task',editable:true},bad,transports),RangeError);
  await assert.rejects(saveSourceImportance({editable:false},4,transports),/недоступен/);assert.equal(calls.length,2);
});

test('library conflict is returned unchanged without fetching a newer version or replaying a write',async()=>{
  const calls=[],conflict=Object.assign(new Error('Conflict'),{status:409});
  await assert.rejects(saveSourceImportance({kind:'note',id:'note',version:'old',editable:true},4,{
    action(){assert.fail('library importance must not mutate Work state');},
    async api(...args){calls.push(args);throw conflict;},
  }),error=>error===conflict);
  assert.deepEqual(calls,[['/api/notes/note','PATCH',{expected_version:'old',patch:{importance_score:4}}]]);
});
