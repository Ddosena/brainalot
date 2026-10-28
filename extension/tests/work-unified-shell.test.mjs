import test from 'node:test';
import assert from 'node:assert/strict';
import {createWorkShellRouter} from '../work-shell-router.js';

const origin = 'http://127.0.0.1:8765';
const tick = () => new Promise(resolve => setImmediate(resolve));

function harness(options = {}) {
  const calls = [], posts = [], sources = [], states = [], timers = new Map();
  const context = {date:'2026-10-28',week_start:'2026-10-26',window_start:'2026-10-26',mode:'week'};
  let revision = 17, sequence = 0, timerSequence = 0, currentSrc = '';
  const child = {postMessage:(data,targetOrigin) => posts.push({data,targetOrigin})};
  const frame = {
    contentWindow:child,
    get src() { return currentSrc; },
    set src(value) { currentSrc = value; sources.push(value); },
  };
  const listeners = new Map();
  const parentWindow = {
    location:{origin,href:`${origin}/work.html`},
    addEventListener:(type,callback) => listeners.set(type,callback),
    removeEventListener:(type,callback) => { if (listeners.get(type) === callback) listeners.delete(type); },
  };
  const api = async (path,method = 'GET',body) => {
    calls.push({path,method,body});
    assert.equal(path,'/api/work/period/context');
    if (method === 'GET') return {revision,context:{...context}};
    assert.equal(method,'POST');
    if (options.commit) await options.commit(body);
    Object.assign(context,body.patch);
    return {revision:++revision,context:{...context}};
  };
  const router = createWorkShellRouter({
    frame,api,parentWindow,origin,onState:state => states.push({...state}),
    requestId:() => `shell-test-${++sequence}`,
    setTimer:callback => { const id = ++timerSequence; timers.set(id,callback); return id; },
    clearTimer:id => timers.delete(id),
    ...options.router,
  });
  const url = () => new URL(frame.src,`${origin}/`);
  const instance = () => url().searchParams.get('work-instance');
  const send = (data,overrides = {}) => router.handleMessage({source:child,origin,data,...overrides});
  const ready = () => send({type:'brainalot-work-ready',instance:instance()});
  const ack = (overrides = {},eventOverrides = {}) => {
    const request = posts.at(-1)?.data;
    assert.equal(request?.type,'brainalot-work-prepare-leave');
    return send({type:'brainalot-work-prepare-leave-result',request_id:request.request_id,
      instance:request.instance,ok:true,date:context.date,...overrides},eventOverrides);
  };
  return {router,frame,child,calls,posts,sources,states,timers,listeners,url,instance,send,ready,ack,context};
}

async function started(t,options) {
  const h = harness(options);
  t.after(() => h.router.destroy());
  await h.router.start();
  h.ready();
  return h;
}

test('common Work starts in Day using the shared selected date without writing context',async t => {
  const h = await started(t);
  assert.match(h.url().pathname,/\/work-day\.html$/);
  assert.equal(h.url().searchParams.get('embedded-work'),'1');
  assert.equal(h.url().searchParams.get('mode'),'day');
  assert.equal(h.url().searchParams.get('date'),'2026-10-28');
  assert.ok(h.instance());
  assert.equal(h.router.state.mode,'day');
  assert.equal(h.router.state.ready,true);
  assert.deepEqual(h.calls.map(call => call.method),['GET']);
  assert.equal(h.sources.length,1);
});

test('selecting the active route does not flush, commit, or reload the child',async t => {
  const h = await started(t);
  await h.router.navigate('day');
  assert.equal(h.posts.length,0);
  assert.equal(h.calls.length,1);
  assert.equal(h.sources.length,1);
});

test('an empty UTC placeholder does not override the child local day before planning starts',async t => {
  const h=await started(t,{router:{api:async()=>({revision:0,planning_initialized:false,context:{date:'2026-10-27'}})}});
  assert.equal(h.url().searchParams.has('date'),false);
  assert.equal(h.router.state.date,null);
});

test('Day to Week waits for saved child date and a successful strict context commit',async t => {
  let releaseCommit;
  const h = await started(t,{commit:() => new Promise(resolve => { releaseCommit = resolve; })});
  const original = h.frame.src;
  const navigation = h.router.navigate('week');
  await tick();
  assert.equal(h.frame.src,original);
  assert.equal(h.calls.length,1);
  assert.equal(h.router.state.mode,'day');
  assert.equal(h.router.state.busy,true);
  assert.equal(h.posts.at(-1).targetOrigin,origin);
  h.ack({date:'2026-11-05'});
  await tick();
  assert.equal(h.frame.src,original);
  assert.deepEqual(h.calls.map(call => call.method),['GET','GET','POST']);
  const body = h.calls.at(-1).body;
  assert.deepEqual(Object.keys(body).sort(),['expected_revision','patch','request_id']);
  assert.equal(body.expected_revision,17);
  assert.ok(body.request_id);
  assert.deepEqual(body.patch,{date:'2026-11-05',week_start:'2026-11-02',window_start:'2026-11-02',mode:'week'});
  releaseCommit();
  await navigation;
  assert.match(h.url().pathname,/\/work-period\.html$/);
  assert.equal(h.url().searchParams.get('mode'),'week');
  assert.equal(h.url().searchParams.get('date'),'2026-11-05');
  assert.equal(h.sources.length,2);
  assert.equal(h.router.state.mode,'week');
});

test('negative leave acknowledgement preserves current child and draft',async t => {
  const h = await started(t);
  const original = h.frame.src;
  const navigation = h.router.navigate('week');
  await tick();
  h.ack({ok:false,error:'Draft could not be saved'});
  await navigation;
  assert.equal(h.frame.src,original);
  assert.equal(h.calls.length,1);
  assert.equal(h.router.state.mode,'day');
  assert.equal(h.router.state.busy,false);
  assert.ok(h.router.state.error);
});

test('forged source, wrong origin, stale instance and stale request cannot unload an editor',async t => {
  const h = await started(t);
  const original = h.frame.src;
  const navigation = h.router.navigate('week');
  await tick();
  h.ack({}, {source:{}});
  h.ack({}, {origin:'https://unrelated.example'});
  h.ack({instance:'old-child'});
  h.ack({request_id:'old-transition'});
  await tick();
  assert.equal(h.frame.src,original);
  assert.equal(h.calls.length,1);
  assert.equal(h.router.state.busy,true);
  h.ack();
  await navigation;
  assert.equal(h.sources.length,2);
});

test('a context CAS conflict preserves current editor and does not retry',async t => {
  const h = await started(t,{commit:async () => { throw Object.assign(new Error('Conflict'),{status:409}); }});
  const original = h.frame.src;
  const navigation = h.router.navigate('week');
  await tick();
  h.ack();
  await navigation;
  assert.equal(h.frame.src,original);
  assert.equal(h.router.state.mode,'day');
  assert.equal(h.router.state.busy,false);
  assert.ok(h.router.state.error);
  assert.equal(h.calls.filter(call => call.method === 'POST').length,1);
});

test('repeated mode selections during a pending transition cannot create concurrent swaps',async t => {
  const h = await started(t);
  const first = h.router.navigate('week');
  await tick();
  const repeated = [h.router.navigate('week'),h.router.navigate('history')];
  await tick();
  assert.equal(h.posts.length,1);
  assert.equal(h.sources.length,1);
  h.ack();
  await Promise.allSettled([first,...repeated]);
  assert.equal(h.calls.filter(call => call.method === 'POST').length,1);
  assert.equal(h.sources.length,2);
  assert.equal(h.router.state.mode,'week');
});

test('History remains a mode of the Day child and carries its selected date',async t => {
  const h = await started(t);
  const navigation = h.router.navigate('history');
  await tick();
  h.ack({date:'2026-10-30'});
  await navigation;
  assert.match(h.url().pathname,/\/work-day\.html$/);
  assert.equal(h.url().searchParams.get('mode'),'history');
  assert.equal(h.url().searchParams.get('date'),'2026-10-30');
  assert.equal(h.router.state.mode,'history');
  assert.equal(h.sources.length,2);
});
