import {test} from 'node:test';
import assert from 'node:assert/strict';
import {confirmedCubeDelete,cubeDeletion,isDeletedCube,normalizeDeletionState} from '../work-deletions.js';
import {constructorModel} from '../work-constructor-model.js';
import {sourcePools,fullCubeInfo} from '../work-readable-cubes.js';
import {weekProposals} from '../work-constructor.js';
import {historyCubes} from '../work-constructor-history.js';
import {requestApi} from '../api.js';

test('a tombstone excludes only its exact cube/blueprint or verified library source, retaining neighbours and history',()=>{
  const date='2030-03-04',deleted={id:'deleted',kind:'task',task_id:'task',segment_id:'phase:with:colons',blueprint_ref:'forecast-a',title:'Same title',minutes:30,progress_percent:25,actual_minutes:17,report_date:date,planned_date:date},other={...deleted,id:'other',blueprint_ref:'forecast-b'},note={id:'saved-note',kind:'brainalot',source_ref:'note',title:'Same title',minutes:60};
  const planning={tasks:[{id:'task'}],constructor:{cubes:{deleted,'saved-note':note}},cube_deletions:{deleted:{cube_id:'deleted',blueprint_ref:'forecast-a'},'saved-note':{cube_id:'saved-note',blueprint_ref:null}},completed_cubes:{deleted:{task_id:'task',percent:25,actual_minutes:17,date,planned_minutes:30}},
    result:{cubes:[{id:'forecast-a',date,task_id:'task',minutes:30},{id:'forecast-b',date,task_id:'task',minutes:30}],constructor:{cubes:[deleted,other,note],goal_cubes:{deleted},days:{[date]:{initialized:true,selected_cube_ids:['deleted','other'],possible_cube_ids:['saved-note'],hidden_cube_ids:[]}}}}};
  const before=structuredClone(planning);
  assert.equal(cubeDeletion(planning,{id:'alias',blueprint_ref:'forecast-a'}),planning.cube_deletions.deleted);
  assert.equal(isDeletedCube(planning,{id:'brainalot-note',kind:'brainalot',source_ref:'note'}),true);
  assert.equal(isDeletedCube(planning,other),false);assert.equal(isDeletedCube(planning,{id:'unrelated',title:'Same title'}),false);
  const model=constructorModel(planning,date,{today:[{id:'note',kind:'task',title:'Same title'}]});
  assert.deepEqual(model.selected.map(cube=>cube.id),['other']);assert.deepEqual(model.possible,[]);
  assert.deepEqual(weekProposals(planning,date).map(cube=>cube.id),['other']);
  const pools=sourcePools({planning,date,taskIds:['task'],cubes:[deleted,other,note]});assert.deepEqual(pools.chains[0].cubes.map(cube=>cube.id),['other']);assert.deepEqual(pools.external,[]);
  const history=historyCubes(planning).find(cube=>cube.id==='deleted');assert.equal(history.actual_minutes,17);assert.equal(history.progress_percent,25);assert.deepEqual(planning,before);
});

test('delete waits for the draft and timer facts, uses captured note CAS, and requires a complete receipt',async()=>{
  const calls=[],cube={id:'cube',kind:'brainalot'},response={revision:8,state:{planning:{}},delete_result:{status:'complete',cube_id:'cube',note_id:'note',operation_id:'op'}};
  const dependencies={cubeId:'cube',noteVersion:'confirmed-version',readCurrent:()=>cube,async saveDraft(){calls.push('save');},async stopAndMeasure(){calls.push('stop-measure');},async commit(action,data){calls.push({action,data});return response;}};
  assert.equal(await confirmedCubeDelete(dependencies),response);
  assert.deepEqual(calls,['save','stop-measure',{action:'cube_delete',data:{cube_id:'cube',expected_note_version:'confirmed-version'}}]);
  for(const failed of ['saveDraft','stopAndMeasure']){
    calls.length=0;await assert.rejects(confirmedCubeDelete({...dependencies,[failed]:async()=>{calls.push('failed');throw new Error('cannot save');}}),/cannot save/);
    assert.ok(!calls.some(call=>call?.action==='cube_delete'));
  }
  await assert.rejects(confirmedCubeDelete({...dependencies,commit:async()=>({...response,delete_result:{status:'pending'}})}),/не подтверждено/);
  let reads=0;await assert.rejects(confirmedCubeDelete({...dependencies,readCurrent:()=>++reads===1?cube:null}),/уже удалён/);
});

test('pending/conflict operation metadata survives the API error boundary without claiming HTTP success',async()=>{
  for(const [status,operation] of [[503,'pending'],[409,'conflict']]){
    const row={request_id:'request',cube_id:'cube',note_id:'note',status:operation,error:'Retry later'},state={pending:operation==='pending'?[row]:[],conflicts:operation==='conflict'?[row]:[]};
    await assert.rejects(requestApi('/api/work/planning',{token:'synthetic-delete',method:'POST',body:{},fetcher:async()=>Response.json({detail:'Typed delete status',delete_result:row,deletion_state:state},{status})}),error=>{
      assert.equal(error.status,status);assert.deepEqual(error.delete_result,row);assert.deepEqual(error.deletion_state,state);return true;
    });
  }
  assert.deepEqual(normalizeDeletionState(),{pending:[],conflicts:[]});
  assert.ok(fullCubeInfo({title:'Stale',allocation_stale:true}).includes('Пересчитайте план'));
});
