import {test} from 'node:test';
import assert from 'node:assert/strict';
import {taskPoolChange,isProtectedTaskCube,uniqueTaskCubes,readTaskChoice,saveTaskChoice} from '../work-constructor-selection.js';
import {previousWeekAward,currentWeekProgress,pedestalShelf,awardsPanel,weightedReadiness} from '../work-constructor-history.js';
import {dayLoadBreakdown,dayTimelineEntries} from '../work-constructor-model.js';
import {shouldCelebrateCompletion,createCompletionClaims,createKeyedSaveQueue,transientOrbitModel,goalPreviewLabel} from '../work-constructor-motion.js';

test('per-cube saves serialize and read later edits instead of dropping them',async()=>{
  const enqueue=createKeyedSaveQueue(),saved=[];let draft=5,release,started;
  const ready=new Promise(resolve=>started=resolve),gate=new Promise(resolve=>release=resolve),first=enqueue('cube',async()=>{const snapshot=draft;started();await gate;saved.push(snapshot);});
  await ready;draft=6;const second=enqueue('cube',async()=>saved.push(draft));release();await Promise.all([first,second]);assert.deepEqual(saved,[5,6]);
  await assert.rejects(enqueue('cube',()=>Promise.reject(new Error('network'))),/network/);
  await enqueue('cube',async()=>saved.push(7));assert.deepEqual(saved,[5,6,7]);
});
test('celebration claim has one owner and releases for retry only by its owner',()=>{
  const claims=createCompletionClaims(),before={progress_percent:0},next={progress_percent:100};
  const owner=claims.reserve('cube',before,next);assert.ok(owner);assert.equal(claims.reserve('cube',before,next),null);
  assert.equal(claims.release('cube',{}),false);assert.equal(claims.reserve('cube',before,next),null);
  assert.equal(claims.release('cube',owner),true);const retry=claims.reserve('cube',before,next);assert.ok(retry);
  assert.equal(claims.release('cube',owner),false);assert.equal(claims.release('cube',retry),true);
  assert.equal(claims.reserve('cube',{...before,award_variant:'cup'},next),null);
});

test('completion celebrates once and snap preview never mutates canonical selections',()=>{
  assert.equal(shouldCelebrateCompletion({progress_percent:95},{progress_percent:100}),true);
  assert.equal(shouldCelebrateCompletion({progress_percent:95,award_variant:'cup'},{progress_percent:100}),false);
  assert.equal(shouldCelebrateCompletion({progress_percent:100},{progress_percent:100}),false);
  const cube={id:'a',minutes:45,start:'09:00'},model={selected:[cube,{id:'b',start:'10:00'}],summary:{total:90}};
  const preview=transientOrbitModel(model,cube,600);assert.equal(preview.selected.find(c=>c.id==='a').start,'10:00');assert.equal(preview.selected.length,2);assert.equal(cube.start,'09:00');assert.equal(model.selected[0],cube);assert.equal(preview.summary,model.summary);
  assert.equal(dayTimelineEntries(preview).cubes.find(c=>c.id==='a').start,600);
  assert.equal(transientOrbitModel(model,{id:'possible',minutes:20},660).selected.length,3);assert.equal(transientOrbitModel(model,cube,null),model);
  assert.equal(goalPreviewLabel({},'day'),'Пример медали');assert.equal(goalPreviewLabel({},'week'),'Пример кубка');assert.equal(goalPreviewLabel({award_variant:'relic'},'week'),'Кубок за цель');
});

test('load breakdown preserves canonical actual-versus-plan charging and adds to total',()=>{
  const selected=[{id:'done',minutes:60,progress_percent:100,actual_minutes:45},{id:'active',minutes:90,progress_percent:25,actual_minutes:20}];
  const m={date:'2026-10-23',selected,all:selected,summary:{fixed:120,selected:150,actual:65,total:255}};
  const parts=dayLoadBreakdown(m);assert.equal(parts.completedActual,45);assert.equal(parts.unfinished,90);assert.equal(parts.calendar,120);assert.equal(parts.other,0);assert.equal(parts.plannedSelected,150);
  const sum=parts=>['calendar','unfinished','completedActual','completedEstimate','returnedActual','returnedEstimate','other'].reduce((n,key)=>n+parts[key],0);
  assert.equal(sum(parts),m.summary.total);
  const returned={id:'returned',minutes:30,progress_percent:50,actual_minutes:10,report_date:m.date};
  const mixed=dayLoadBreakdown({...m,selected:[...selected,{id:'overrun',minutes:30,actual_minutes:40,progress_percent:20},{id:'without-fact',minutes:15,actual_minutes:0,progress_percent:100}],all:[...selected,returned],summary:{...m.summary,total:320}});
  assert.equal(mixed.returnedActual,10);assert.equal(mixed.completedEstimate,15);assert.equal(mixed.unfinished,130);assert.equal(mixed.other,0);assert.equal(sum(mixed),320);
});

test('removing one task excludes only untouched cubes and deduplicates common pool',()=>{
  const cubes=[{id:'a',task_id:'A'},{id:'b',task_id:'B'},{id:'b',task_id:'B'},{id:'p',task_id:'B',progress_percent:5},{id:'t',task_id:'B',actual_minutes:1},{id:'done',task_id:'B',progress_percent:100},{id:'timer',task_id:'B'},{id:'historical',task_id:'B'},{id:'calendar',kind:'calendar'}];
  const result=taskPoolChange(cubes,['A'],{timerIds:new Set(['timer']),historyIds:new Set(['historical'])});
  assert.deepEqual(result.removed.map(c=>c.id),['b']);
  assert.deepEqual(result.retained.map(c=>c.id),['p','t','done','timer','historical']);
  assert.equal(result.included.length,7);
  assert.deepEqual(taskPoolChange(cubes,['A','B']).removed,[]);
  assert.equal(uniqueTaskCubes([{id:'x',blueprint_ref:'source'},{id:'y',canonical_id:'source'}]).length,1);
  assert.equal(isProtectedTaskCube({id:'x',started_at:'2026-10-23'}),true);
});
test('local task selection survives reload, empty selection and duplicate ids',()=>{
  const values=new Map(),storage={getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value)};
  assert.deepEqual(readTaskChoice(storage,'day',['A','B','A']),{taskIds:['A','B'],collapsed:false});
  saveTaskChoice(storage,'day',{taskIds:['B','B'],collapsed:true});
  assert.deepEqual(readTaskChoice(storage,'day',['A']),{taskIds:['B'],collapsed:true});
  saveTaskChoice(storage,'day',{taskIds:[],collapsed:false});assert.deepEqual(readTaskChoice(storage,'day',['A']).taskIds,[]);
  values.set('day','{"version":1,"taskIds":7}');assert.deepEqual(readTaskChoice(storage,'day',['A']).taskIds,['A']);
  saveTaskChoice(storage,'day',{taskIds:['A'],collapsed:true,protectedCubeIds:['timed','timed'],restoredCubeIds:['restored']});
  const reload=readTaskChoice(storage,'day',[]);assert.deepEqual(reload.protectedCubeIds,['timed']);assert.deepEqual(reload.restoredCubeIds,['restored']);
  const afterReload=taskPoolChange([{id:'timed',task_id:'B'},{id:'untouched',task_id:'B'}],reload.taskIds,{timerIds:new Set(reload.protectedCubeIds)});
  assert.deepEqual(afterReload.retained.map(c=>c.id),['timed']);assert.deepEqual(afterReload.removed.map(c=>c.id),['untouched']);
});
test('awards use exactly previous calendar week and current readiness independently',()=>{
  const previous={id:'week:old',date:'2026-10-12',scope:'week',status:'completed',percent:37,award_variant:'cup'},current={id:'week:current',date:'2026-10-19',scope:'week',percent:63,status:'active'},future={id:'week:future',date:'2026-10-26',scope:'week',status:'completed',award_variant:'relic'};
  const planning={result:{constructor:{goals:[future,current,previous]}}};
  assert.equal(previousWeekAward(planning,'2026-10-25'),previous);assert.equal(previousWeekAward(planning,'2026-10-26'),null);
  assert.deepEqual(currentWeekProgress(planning,'2026-10-23'),{start:'2026-10-19',end:'2026-10-25',goal:current,percent:63});
  assert.equal(previousWeekAward({result:{constructor:{goals:[{...previous,status:'cancelled'}]}}},'2026-10-23'),null);
  const html=awardsPanel(planning,'2026-10-23');assert.ok(html.includes('week:old'));assert.ok(!html.includes('week:future'));
});
test('completed shelf includes only selected day finished cubes; readiness remains weighted',()=>{
  const cubes=[{id:'done',title:'Done',minutes:60,progress_percent:100,selected_date:'2026-10-23'},{id:'other',title:'Other day',minutes:20,progress_percent:100,selected_date:'2026-10-22'},{id:'open',title:'Open',minutes:30,progress_percent:25,selected_date:'2026-10-23'}];
  const planning={tasks:[],result:{constructor:{cubes,goals:[{id:'g',date:'2026-10-23',scope:'day',status:'completed',award_variant:'enamel'}]}}};
  const html=pedestalShelf(planning,'2026-10-23');assert.ok(html.includes('data-id="done"'));assert.ok(!html.includes('data-id="other"'));assert.ok(!html.includes('data-id="open"'));assert.ok(!html.includes('data-testid="earned-goal"'));
  assert.equal(weightedReadiness(cubes),80);assert.equal(weightedReadiness([{minutes:60,progress_percent:99.99}]),99.9);
});
