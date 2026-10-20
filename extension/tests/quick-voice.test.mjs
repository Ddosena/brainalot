import test from 'node:test';
import assert from 'node:assert/strict';
import {createQuickVoiceOutbox,quickVoiceDestination,QUICK_VOICE_PREFIX} from '../quick-voice.js';
import {setLocale} from '../i18n.js';
setLocale('ru');
const baseline={captured_at:'2026-10-20T20:59:50.000Z',time_zone:'Europe/Moscow'};
function storage(){const values={};return{values,async get(){return structuredClone(values);},async set(patch){Object.assign(values,structuredClone(patch));},async remove(key){delete values[key];}};}
const note=(body)=>({route:'note',note:{id:'task-a',title:body.text,kind:'task',due:null,status:'active',version:'v1'}});
test('home voice persists immutable task-only parser input before delivery and returns only confirmed note',async()=>{
  const store=storage(),calls=[];
  const box=createQuickVoiceOutbox({storage:store,uuid:()=> 'one',api:async(path,method,body)=>{assert.ok(store.values[QUICK_VOICE_PREFIX+body.external_id]);calls.push({path,method,body});return note(body);}});
  const result=await box.capture('Сохранить задачу сегодня',baseline);
  assert.equal(result.note.id,'task-a');assert.equal((await box.list()).length,0);
  assert.equal(calls[0].path,'/api/dictation');assert.equal(calls[0].body.external_id,'home-voice:one');assert.deepEqual(calls[0].body.overrides,{kind:'task',route:'note',keep_context_in_title:true});assert.equal(calls[0].body.captured_at,baseline.captured_at);assert.equal(calls[0].body.time_zone,baseline.time_zone);
});
test('unknown commit result survives reload and retries the same external id/date/body once',async()=>{
  const store=storage(),calls=[],records=new Map();let fail=true;
  const api=async(_p,_m,body)=>{calls.push(structuredClone(body));if(!records.has(body.external_id))records.set(body.external_id,note(body));if(fail){fail=false;throw new Error('lost response');}return records.get(body.external_id);};
  const first=createQuickVoiceOutbox({storage:store,api,uuid:()=> 'stable'});await assert.rejects(first.capture('Завтра подготовить урок',baseline));assert.equal((await first.list()).length,1);
  const restarted=createQuickVoiceOutbox({storage:store,api,uuid:()=>assert.fail('retry must not allocate another id')});await restarted.retry();assert.deepEqual(calls[0],calls[1]);assert.equal(records.size,1);assert.equal((await restarted.list()).length,0);
});
test('failed local save never sends; retry persists the same in-memory record first',async()=>{
  const store=storage(),realSet=store.set;let fail=true,sends=0;
  store.set=async patch=>{if(fail){fail=false;throw new Error('storage unavailable');}return realSet(patch);};
  const box=createQuickVoiceOutbox({storage:store,uuid:()=> 'local',api:async(_p,_m,body)=>{sends++;assert.ok(store.values[QUICK_VOICE_PREFIX+body.external_id]);return note(body);}});
  await assert.rejects(box.capture('Задача без даты',baseline));assert.equal(sends,0);assert.equal((await box.list())[0].payload.external_id,'home-voice:local');await box.retry();assert.equal(sends,1);
});
test('concurrent capture is gated, and empty text or an unexpected calendar result cannot confirm success',async()=>{
  const store=storage();let release,sends=0;
  const box=createQuickVoiceOutbox({storage:store,uuid:()=> 'single',api:async(_p,_m,body)=>{sends++;await new Promise(resolve=>{release=resolve;});return note(body);}});
  await assert.rejects(box.capture(' ',baseline));assert.equal(sends,0);
  const first=box.capture('Одна задача',baseline);await new Promise(setImmediate);await assert.rejects(box.capture('Другая задача',baseline));release();await first;assert.equal(sends,1);
  const bad=createQuickVoiceOutbox({storage:store,uuid:()=> 'unexpected',api:async()=>({route:'calendar',event:{id:'bad'}})});await assert.rejects(bad.capture('Созвон сегодня в 10',baseline));assert.equal((await bad.list()).length,1);
});
test('confirmation destinations reflect explicit dates/horizons; no date stays undated',()=>{
  const clock={now:new Date('2026-10-20T12:00:00Z'),timeZone:'Europe/Moscow'};
  assert.equal(quickVoiceDestination({kind:'task',due:null},clock),'Без срока');assert.equal(quickVoiceDestination({kind:'task',due:'2026-10-20'},clock),'Сегодня');assert.equal(quickVoiceDestination({kind:'task',due:'2026-10-21'},clock),'Завтра');assert.equal(quickVoiceDestination({kind:'task',planning_horizon:'week'},clock),'На неделе');
});
