import test from 'node:test';
import assert from 'node:assert/strict';
import {activeTask, compactTasks, resolveActiveTask} from '../widget-model.js';
const a={source:'library',id:'a',title:'Hot'}, b={source:'calendar',id:'b',title:'Current'};
test('compact tasks keep selection, deduplicate and deterministically recover',()=>{
  const model=compactTasks([a,b,a],'library:a',{status:'paused',task:b});
  assert.equal(model.tasks.length,2); assert.equal(model.hot,a); assert.equal(model.current,b); assert.equal(model.same,false);
  assert.equal(compactTasks([a,b],'missing',null).selected,'calendar:b');
});
test('stopped/completed task does not retain card, pause does',()=>{
  assert.equal(activeTask({status:'paused',task:b}),b);
  for(const status of ['idle','stopped','completed']) assert.equal(compactTasks([],null,{status,task:b}).hasTask,false);
});
test('same stable source/id has one title and taskless timer is honest',()=>{
  assert.equal(compactTasks([a],'library:a',{status:'running',task:{...a,title:'New title'}}).same,true);
  assert.equal(compactTasks([],null,{status:'running',task:null}).hasTask,false);
});
test('paused completed/deleted tasks do not hold a card; live title follows stable id',()=>{
  const paused={status:'paused',task:a};
  assert.equal(resolveActiveTask(paused,[{...a,status:'done'}]),null);
  assert.equal(resolveActiveTask(paused,[]),null);
  assert.equal(resolveActiveTask(paused,[{...a,status:'active',title:'Renamed'}]).title,'Renamed');
  assert.equal(resolveActiveTask({...paused,status:'running'},[]).id,a.id);
});
