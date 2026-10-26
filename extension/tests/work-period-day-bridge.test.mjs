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

test('late older reads cannot replace a newer Day projection', async () => {
  const reads = [];
  const f = fixture(() => new Promise(resolve => reads.push(resolve)));
  const first = f.bridge.ready();
  const second = f.bridge.remoteChanged();
  reads[1]({revision: 3, planning: {title: 'Newest'}});
  await second;
  reads[0]({revision: 2, planning: {title: 'Old'}});
  await first;
  assert.equal(f.ctx().state.planning.title, 'Newest');
});

test('failed initial projection never exposes an editable raw schedule', async () => {
  const f = fixture(async () => {throw new Error('Unavailable');});
  await assert.rejects(f.bridge.ready(),/Unavailable/);
  assert.equal(f.ctx(), undefined);
  assert.equal(f.root.textContent, 'Unavailable');
});
