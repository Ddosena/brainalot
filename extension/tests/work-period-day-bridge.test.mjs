import test from 'node:test';
import assert from 'node:assert/strict';
import {createDayBridge} from '../work-period/day-bridge.js';

function fixture(api, action = async () => ({revision: 2})) {
  const raw = {planning: {raw: true}, boards: ['preserved']};
  const root = {textContent: ''};
  const calls = [];
  let projected;
  const bridge = createDayBridge(root, {state: raw, action}, {
    api, createConstructor(_root, ctx) {
      projected = ctx;
      return {render() {calls.push('render');}, remoteChanged() {calls.push('remote');}, destroy() {calls.push('destroy');}};
    },
  });
  return {raw, root, bridge, calls, ctx: () => projected};
}

test('Day renders effective planning without changing the raw board cache', async () => {
  const f = fixture(async () => ({revision: 1, planning: {effective: true}}));
  await f.bridge.ready();
  assert.deepEqual(f.ctx().state, {planning: {effective: true}, boards: ['preserved']});
  assert.deepEqual(f.raw.planning, {raw: true});
  f.bridge.destroy();
  assert.equal(f.calls.at(-1), 'destroy');
});

test('Day actions refresh the effective schedule before returning to its editor', async () => {
  let revision = 1;
  const f = fixture(async () => ({revision, planning: {title: revision === 1 ? 'Before' : 'After'}}), async () => {revision = 2; return {revision};});
  await f.bridge.ready();
  await f.ctx().action('constructor_cube', {title: 'After'});
  assert.equal(f.ctx().state.planning.title, 'After');
  assert.equal(f.raw.planning.raw, true);
});

test('concurrent Day reads coalesce rather than racing older projections', async () => {
  const reads=[];const f=fixture(()=>new Promise(resolve=>reads.push(resolve)));
  const first=f.bridge.ready(),second=f.bridge.remoteChanged();assert.equal(reads.length,1);
  reads[0]({revision:3,planning:{title:'Newest'}});await Promise.all([first,second]);
  assert.equal(f.ctx().state.planning.title,'Newest');f.bridge.destroy();
});

test('failed initial projection never exposes an editable raw schedule', async () => {
  const f = fixture(async () => {throw new Error('Unavailable');});
  await assert.rejects(f.bridge.ready(),/Unavailable/);
  assert.equal(f.ctx(), undefined);
  assert.equal(f.root.textContent, 'Unavailable');
});


test('Day live poll sees source fingerprint changes at an unchanged Work revision and stops on destroy',async()=>{
  let poll,cleared=false,source='one',rawReads=0;const calls=[];
  const bridge=createDayBridge({textContent:''},{state:{planning:{}},liveRefresh:true,async refreshWork(){rawReads++;}}, {
    async api(){return {revision:4,fingerprint:source,planning:{title:source}};},
    createConstructor(){return {render(){calls.push('render');},remoteChanged(){calls.push('remote');},destroy(){calls.push('destroy');}};},
    setPoll(callback,interval){assert.equal(interval,750);poll=callback;return 77;},clearPoll(id){assert.equal(id,77);cleared=true;},
  });
  await bridge.ready();source='two';await poll();await new Promise(resolve=>setImmediate(resolve));
  assert.ok(calls.includes('remote'));const before=calls.length;await poll();await new Promise(resolve=>setImmediate(resolve));assert.equal(calls.length,before);assert.ok(rawReads>=2);
  bridge.destroy();assert.equal(cleared,true);
});
