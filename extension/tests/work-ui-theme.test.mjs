import test from 'node:test'; import assert from 'node:assert/strict'; import {createThemeStore,normalizeTheme} from '../work-ui/theme.js';
test('uses shared preferences revision and validates theme defaults',async()=>{const calls=[];const conn={api:async(path,method,body)=>{calls.push({path,method,body});return method==='POST'?{revision:2,values:body.patch}:{revision:1,values:{}};}};const s=createThemeStore(conn,{requestId:()=> 'r'});assert.deepEqual(await s.refresh(),{theme:'midnight',mode:'dark'});await s.set({theme:'contrast',mode:'light'});assert.equal(calls[1].body.expected_revision,1);assert.deepEqual(calls[1].body.patch['work:theme'],{theme:'contrast',mode:'light'});assert.deepEqual(normalizeTheme({theme:'bad'}),{theme:'midnight',mode:'dark'});});

test('rapid theme and mode choices preserve both user intentions', async () => {
  let state = {revision:1, values:{'work:theme':{theme:'midnight', mode:'dark'}}};
  const writes = [];
  const store = createThemeStore({api:async (path, method, body) => {
    if (method !== 'POST') return structuredClone(state);
    assert.equal(body.expected_revision, state.revision);
    writes.push(body.patch['work:theme']);
    state = {revision:state.revision + 1, values:body.patch};
    return structuredClone(state);
  }});
  await store.refresh();
  await Promise.all([store.set({theme:'contrast'}), store.set({mode:'light'})]);
  assert.deepEqual(writes, [{theme:'contrast', mode:'dark'}, {theme:'contrast', mode:'light'}]);
  assert.deepEqual(store.current, {theme:'contrast', mode:'light'});
});

test('a preference poll cannot cross a pending theme write', async () => {
  let finishWrite;
  let state = {revision:1, values:{'work:theme':{theme:'midnight', mode:'dark'}}};
  const calls = [];
  const store = createThemeStore({api:async (path, method, body) => {
    calls.push(method || 'GET');
    if (method !== 'POST') return structuredClone(state);
    await new Promise(resolve => { finishWrite = resolve; });
    state = {revision:state.revision + 1, values:body.patch};
    return structuredClone(state);
  }});
  await store.refresh();
  const write = store.set({theme:'graphite'});
  await Promise.resolve();
  const poll = store.refresh();
  await Promise.resolve();
  assert.deepEqual(calls, ['GET', 'POST']);
  finishWrite();
  await Promise.all([write, poll]);
  assert.deepEqual(store.current, {theme:'graphite', mode:'dark'});
});
