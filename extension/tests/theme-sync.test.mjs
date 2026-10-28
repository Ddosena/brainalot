import test from 'node:test';
import assert from 'node:assert/strict';
import {createThemeStore, legacyTheme, applyTheme} from '../theme.js';
const clone = x => structuredClone(x);
function server(values = {}) {
  let state = {revision:1, values:clone(values)}, lose = false, race = null;
  const requests = [], receipts = new Map();
  return {requests, get state() {return clone(state);},
    change(patch) {state = {revision:state.revision+1,values:{...state.values,...patch}};},
    race(patch) {race = patch;}, lose() {lose = true;},
    async api(path, method = 'GET', body) {
      if (method === 'GET') return clone(state);
      requests.push(clone(body));
      if (receipts.has(body.request_id)) return clone(receipts.get(body.request_id));
      if (race) {this.change(race); race = null;}
      if (body.expected_revision !== state.revision) throw Object.assign(new Error('conflict'),{status:409});
      state = {revision:state.revision+1, values:{...state.values,...body.patch}};
      receipts.set(body.request_id,clone(state));
      if (lose) {lose = false; throw Object.assign(new Error('offline'),{status:0});}
      return clone(state);
    }};
}
const storage = values => ({get:async () => clone(values)});
const session = () => {const m = new Map(); return {getItem:k=>m.get(k), setItem:(k,v)=>m.set(k,v)};};
const ids = () => {let n = 0; return () => `id-${++n}`;};
test('legacy mapping includes desktop shim and current system mode', async () => {
  assert.deepEqual(['stream','console','cabinet','glamour'].map(design =>
    legacyTheme({'panel:design':design,'panel:theme':'system'}, true)),
  ['midnight','graphite','contrast','glamour'].map(theme => ({theme,mode:'dark'})));
  const api=server(); const s=createThemeStore(api,{storage:storage({'panel:design':'cabinet','panel:theme':'light'}), requestId:ids()});
  assert.deepEqual(await s.refresh(),{theme:'contrast',mode:'light'});
  assert.deepEqual(api.state.values['work:theme'],{theme:'contrast',mode:'light'});
});
test('existing Work selection beats both local and shared legacy', async () => {
  const chosen={theme:'glamour',mode:'dark'}, api=server({'work:theme':chosen,'panel:design':'console'});
  const s=createThemeStore(api,{storage:storage({'panel:design':'cabinet','panel:theme':'light'})});
  assert.deepEqual(await s.refresh(),chosen); assert.equal(api.requests.length,0);
});
test('two independent stores racing legacy migration and a Work choice converge', async () => {
  const api=server({'panel:design':'glamour','panel:theme':'light'});
  let n=0;const requestId=()=>`concurrent-${++n}`;
  const a=createThemeStore(api,{storage:storage({}),session:session(),requestId});
  const b=createThemeStore(api,{storage:storage({}),session:session(),requestId});
  await Promise.all([a.refresh(),b.refresh()]);
  assert.deepEqual(api.state.values['work:theme'],{theme:'glamour',mode:'light'});
  await Promise.all([a.refresh(),b.set({theme:'graphite',mode:'dark'})]);
  await Promise.all([a.refresh(),b.refresh()]);
  assert.deepEqual(a.current,{theme:'graphite',mode:'dark'});
  assert.deepEqual(b.current,a.current);
  const reloaded=createThemeStore(api,{storage:storage({'panel:design':'cabinet','panel:theme':'light'}),session:session(),requestId});
  assert.deepEqual(await reloaded.refresh(),a.current);
});
test('a concurrent Work choice wins the migration conflict', async () => {
  const api=server({'panel:design':'stream','panel:theme':'light'}), choice={theme:'glamour',mode:'dark'};
  api.race({'work:theme':choice}); const s=createThemeStore(api,{storage:storage({}),requestId:ids()});
  assert.deepEqual(await s.refresh(),choice); assert.deepEqual(api.state.values['work:theme'],choice);
  assert.equal(api.requests.length,1);
});
test('lost response replays exact receipt after page reload without clobbering later choice', async () => {
  const api=server({'work:theme':{theme:'midnight',mode:'dark'}}), saved=session(), nextId=ids();
  let s=createThemeStore(api,{session:saved,requestId:nextId}); await s.refresh(); api.lose();
  await assert.rejects(s.set({theme:'graphite'})); const first=clone(api.requests[0]);
  api.change({'work:theme':{theme:'glamour',mode:'light'}});
  s=createThemeStore(api,{session:saved,requestId:nextId});
  assert.deepEqual(await s.refresh(),{theme:'glamour',mode:'light'});
  assert.deepEqual(api.requests[1],first); assert.equal(saved.getItem('brainalot:theme-pending:default'),'[]');
});
test('unrelated revision conflict rebases only user patch', async () => {
  const api=server({'work:theme':{theme:'midnight',mode:'dark'}}), s=createThemeStore(api,{requestId:ids()});
  await s.refresh(); api.race({'work:theme':{theme:'contrast',mode:'light'}});
  assert.deepEqual(await s.set({theme:'glamour'}),{theme:'glamour',mode:'light'});
});
test('apply replaces all theme classes and sets shared DOM attributes', () => {
  const classes=new Set(['contrast','dark']); const el={dataset:{},classList:{remove:(...x)=>x.forEach(k=>classes.delete(k)),add:(...x)=>x.forEach(k=>classes.add(k))}};
  applyTheme(el,{theme:'glamour',mode:'light'});
  assert.deepEqual([...classes],['glamour','light']); assert.deepEqual(el.dataset,{theme:'light',design:'glamour'});
});

test('later arriving shared legacy is migrated even when first read was empty', async () => {
  const api=server(), store=createThemeStore(api,{storage:storage({}),requestId:ids()});
  assert.deepEqual(await store.refresh(),{theme:'midnight',mode:'dark'});
  api.change({'panel:design':'glamour','panel:theme':'light'});
  assert.deepEqual(await store.refresh(),{theme:'glamour',mode:'light'});
});

test('choice made offline before first read survives reload and merges with server mode', async () => {
  const api=server({'work:theme':{theme:'contrast',mode:'light'}}), saved=session(), nextId=ids();
  let offline=true; const connection={api:(...args)=>offline?Promise.reject(Object.assign(new Error('offline'),{status:0})):api.api(...args)};
  let s=createThemeStore(connection,{session:saved,requestId:nextId});
  await assert.rejects(s.set({theme:'glamour'}));
  offline=false; s=createThemeStore(connection,{session:saved,requestId:nextId});
  assert.deepEqual(await s.refresh(),{theme:'glamour',mode:'light'});
});
