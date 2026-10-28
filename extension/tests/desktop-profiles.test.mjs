import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createProfileController} from '../desktop-profiles.js';

test('rapid navigation serializes changes and discards stale results', async () => {
  const calls=[], renders=[]; let release;
  const controller=createProfileController({action: async (name,value) => {
    calls.push([name,value]);
    if (calls.length===1) await new Promise(resolve => {release=resolve;});
    return {ok:true,size_default:{width:value==='work'?1600:410,height:800,custom:false}};
  },render:(size,profile)=>renders.push([size,profile]),onError:assert.fail});
  const first=controller.select('work'); await new Promise(resolve=>setImmediate(resolve));
  const second=controller.select('brainalot'); release(); await Promise.all([first,second]);
  assert.deepEqual(calls,[['view_profile','work'],['view_profile','brainalot']]);
  assert.equal(renders.filter(([size])=>size).length,1);
  assert.equal(renders.at(-1)[1],'brainalot');
});

test('capture and reset apply to the active queued profile before later navigation', async () => {
  let active; const calls=[], defaults={work:{width:1600,height:1000,custom:false},brainalot:{width:410,height:800,custom:false}};
  const controller=createProfileController({action:async(name,value)=>{
    calls.push([name,value,active]);
    if(name==='view_profile') active=value;
    else defaults[active]=value?{width:900,height:700,custom:true}:(active==='work'?{width:1600,height:1000,custom:false}:{width:410,height:800,custom:false});
    return {ok:true,profile:active,size_default:defaults[active]};
  },render:()=>{},onError:assert.fail});
  await controller.select('work'); await controller.capture(true);
  const reset=controller.capture(false); const other=controller.select('brainalot'); await Promise.all([reset,other]);
  assert.deepEqual(calls.map(c=>c.slice(0,2)),[['view_profile','work'],['view_size_default',true],['view_size_default',false],['view_profile','brainalot']]);
  assert.equal(calls[2][2],'work'); assert.equal(defaults.work.custom,false); assert.equal(defaults.brainalot.width,410);
});

test('failure does not poison subsequent commands', async () => {
  let count=0; const errors=[]; const results=[];
  const controller=createProfileController({action:async()=>++count===1?{ok:false,error:'failed'}:{ok:true,size_default:{width:410}},render:(s)=>results.push(s),onError:e=>errors.push(e.message)});
  await controller.select('work'); await controller.select('brainalot');
  assert.deepEqual(errors,['failed']); assert.equal(results.at(-1).width,410);
});

test('fullscreen and route changes share the queue', async () => {
  const calls=[]; let release;
  const controller=createProfileController({action:async(name,value)=>{
    calls.push([name,value]);
    if(name==='work_expand') await new Promise(resolve=>{release=resolve});
    return {ok:true,expanded:value,size_default:{width:410}};
  },render:()=>{},onError:assert.fail,beforeProfile:async profile=>calls.push(['before',profile])});
  await controller.select('work'); const expanded=controller.workExpand(true);
  await new Promise(resolve=>setImmediate(resolve)); const select=controller.select('brainalot'); release();
  await expanded; await select;
  assert.deepEqual(calls.map(c=>c[0]),['before','view_profile','work_expand','before','view_profile']);
  await assert.rejects(controller.workExpand(true),/no longer active/);
});
