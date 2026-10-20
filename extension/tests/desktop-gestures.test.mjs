import test from 'node:test';
import assert from 'node:assert/strict';
import {installWindowGestures} from '../desktop-gestures.js';

const flush = async () => { for (let i=0; i<16; i++) await Promise.resolve(); };
function fixture(options={}) {
  const handlers = new Map(), calls = [], captures = new Set();
  const surface = {
    addEventListener(name, handler) { handlers.set(name, handler); },
    setPointerCapture(id) { captures.add(id); },
    hasPointerCapture(id) { return captures.has(id); },
    releasePointerCapture(id) { captures.delete(id); },
  };
  const target = {closest(selector) { return selector.includes('[data-window-drag]') ? this : null; }};
  const native = {async window_action(action,value) { calls.push([action,value]); return {ok:true}; }};
  installWindowGestures(native, {surface,...options});
  const emit = (name,extra={}) => {
    const event = {button:0,pointerId:1,clientX:20,clientY:20,timeStamp:100,target,
      prevented:false,stopped:false,preventDefault(){this.prevented=true;},stopImmediatePropagation(){this.stopped=true;},...extra};
    handlers.get(name)?.(event); return event;
  };
  const click = async (time,extra={}) => { emit('pointerdown',{timeStamp:time,...extra}); emit('pointerup',{timeStamp:time+10,...extra}); await flush(); };
  return {calls,emit,click};
}

test('single tab click stays compact; double left toggles with Windows timing', async () => {
  const f=fixture({tab:true,doubleClick:{interval_ms:800,width:8,height:8}});
  await f.click(0);
  assert.equal(f.calls.filter(([a])=>a==='card').length,0);
  await f.click(650,{clientX:23,clientY:23});
  assert.equal(f.calls.filter(([a])=>a==='card').length,1);
});

test('double right opens full without beginning native drag, context menu suppressed',async()=>{
  const f=fixture({tab:true});
  await f.click(0,{button:2}); await f.click(200,{button:2});
  assert.deepEqual(f.calls,[['full',undefined]]);
  assert.equal(f.emit('contextmenu',{button:2}).prevented,true);
});

test('distance, timeout, different buttons and controls do not double click',async()=>{
  for (const second of [{time:900},{time:100,clientX:40},{time:100,button:2},
    {time:100,target:{closest(){return {};}}}]) {
    const f=fixture({tab:true}); await f.click(0);
    const {time,...extra}=second; await f.click(time,extra);
    assert.equal(f.calls.some(([a])=>['full','card','bar'].includes(a)),false);
  }
});

test('drag and cancelled pointer suppress clicks synchronously and reset double click pair',async()=>{
  const f=fixture({tab:true}); await f.click(0);
  f.emit('pointerdown',{timeStamp:100});
  f.emit('pointermove',{clientX:40,timeStamp:110});
  f.emit('pointerup',{clientX:40,timeStamp:120});
  assert.equal(f.emit('click').stopped,true);
  await flush(); await f.click(200);
  assert.equal(f.calls.some(([a])=>a==='card'),false);
  f.emit('pointerdown',{timeStamp:300}); f.emit('pointercancel',{timeStamp:320});
  assert.equal(f.emit('click').prevented,true);
  await flush();
  f.emit('pointerdown',{timeStamp:400,target:{closest(){return {};}}});
  assert.equal(f.emit('click').prevented,false, 'next control click must remain usable after drag/cancel');
});

test('full header callback and compact card default, ordinary body ignored',async()=>{
  let expanded=0;
  const f=fixture({onDoubleClick:()=>expanded++}); await f.click(0); await f.click(100);
  assert.equal(expanded,1);
  const card=fixture(); await card.click(0); await card.click(100);
  assert.equal(card.calls.filter(([a])=>a==='bar').length,1);
  const body=fixture(); await body.click(0,{target:{closest(){return null;}}});
  assert.deepEqual(body.calls,[]);
});
