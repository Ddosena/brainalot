import test from 'node:test';
import assert from 'node:assert/strict';
import {installWindowGestures,installRightButtonWindowDrag} from '../desktop-gestures.js';

const flush = async () => { for (let i=0; i<16; i++) await Promise.resolve(); };
function rightFixture(){
  const handlers=new Map(),calls=[],captures=new Set(),target={setPointerCapture:id=>captures.add(id),hasPointerCapture:id=>captures.has(id),releasePointerCapture:id=>captures.delete(id)};
  const surface={addEventListener:(name,fn)=>handlers.set(name,fn),removeEventListener:(name,fn)=>{if(handlers.get(name)===fn)handlers.delete(name);},defaultView:{addEventListener:(name,fn)=>handlers.set(name,fn),removeEventListener:name=>handlers.delete(name)}};
  const native={window_action:async(action,value)=>{calls.push([action,value]);return action==='gesture_probe'?{origin:[120,-30]}:{ok:true};}};
  const dispose=installRightButtonWindowDrag(native,{surface});
  const emit=(type,extra={})=>{const event={button:2,pointerId:9,clientX:20,clientY:20,target,prevented:false,stopped:false,preventDefault(){this.prevented=true;},stopImmediatePropagation(){this.stopped=true;},...extra};handlers.get(type)?.(event);return event;};
  return {calls,surface,native,dispose,emit,handlers};
}
test('right short click in a control preserves its context menu and never begins moving',async()=>{
 const f=rightFixture();f.emit('pointerdown');f.emit('pointermove',{clientX:23});f.emit('pointerup',{clientX:23});await flush();
 assert.deepEqual(f.calls.map(c=>c[0]),['gesture_probe']);assert.equal(f.emit('contextmenu').prevented,false);f.dispose();
});
test('right drag from any element uses physical down origin and consumes only its own context menu',async()=>{
 const f=rightFixture();f.emit('pointerdown');f.emit('pointermove',{clientX:70});f.emit('pointerup',{clientX:70});await new Promise(r=>setImmediate(r));
 assert.deepEqual(f.calls.map(c=>c[0]),['gesture_probe','gesture_begin','gesture_move','gesture_end']);
 assert.deepEqual(f.calls[1][1],{edge:'',right_button:true,origin:[120,-30]});assert.equal(f.emit('contextmenu').stopped,true);assert.equal(f.emit('contextmenu').prevented,false);
 f.emit('pointerdown');f.emit('pointerup');assert.equal(f.emit('contextmenu').prevented,false);f.dispose();
});
test('right cancellation and iframe disposal finish the native gesture once without swallowing left clicks',async()=>{
 const f=rightFixture();f.emit('pointerdown',{button:0});f.emit('pointermove',{button:0,clientX:80});await flush();assert.deepEqual(f.calls,[]);
 assert.equal(installRightButtonWindowDrag(f.native,{surface:f.surface}),f.dispose);
 f.emit('pointerdown');f.emit('pointermove',{clientX:80});f.emit('pointercancel');f.dispose();await new Promise(r=>setImmediate(r));
 assert.equal(f.calls.filter(c=>c[0]==='gesture_end').length,1);assert.equal(f.handlers.size,0);
});
function fixture(options={}) {
  const handlers = new Map(), calls = [], captures = new Set();
  const surface = {
    ownerDocument:{defaultView:{getSelection:()=>({toString:()=>options.selection?.() || ''})}},
    addEventListener(name, handler) { handlers.set(name, handler); },
    setPointerCapture(id) { captures.add(id); },
    hasPointerCapture(id) { return captures.has(id); },
    releasePointerCapture(id) { captures.delete(id); },
  };
  const target = {closest(selector) { return selector.includes('[data-window-drag]') ? this : null; }};
  const native = {async window_action(action,value) { calls.push([action,value]); return {ok:true}; }};
  installWindowGestures(native, {surface,...options});
  const emit = (name,extra={}) => {
    const event = {button:0,pointerId:1,clientX:20,clientY:20,timeStamp:100,target,isTrusted:true,
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

test('a drag never disables later native element.click() activation (compact widget)',async()=>{
  // Bug report 0.2.10: dragging the card while a menu was open closed the menu,
  // the page then rendered through the native canvas and received no further
  // pointerdown, and every native button click was swallowed for hours.
  const f=fixture({tab:true});
  f.emit('pointerdown',{timeStamp:100});
  f.emit('pointermove',{clientX:40,timeStamp:110});
  f.emit('pointerup',{clientX:40,timeStamp:120});
  assert.equal(f.emit('click').stopped,true,'the browser click that ends the drag is discarded');
  await flush(); await new Promise(resolve=>setTimeout(resolve));
  assert.equal(f.emit('click',{isTrusted:false}).stopped,false,'native element.click() runs');
  assert.equal(f.emit('click').stopped,false,'the drag flag expires with its own task');
  const unreleased=fixture({tab:true});
  unreleased.emit('pointerdown',{timeStamp:100});
  unreleased.emit('pointermove',{clientX:40,timeStamp:110});
  assert.equal(unreleased.emit('click',{isTrusted:false}).stopped,false,'release lost offscreen still keeps native clicks');
  unreleased.emit('pointerdown',{timeStamp:5000,target:{closest(){return {};}}});
  unreleased.emit('pointerup',{timeStamp:5010,target:{closest(){return {};}}});
  assert.equal(unreleased.emit('click').stopped,false,'the next real press in a reopened menu is not a stale drag');
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

test('full free body double click collapses without starting native drag; selected text and cards are excluded',async()=>{
  let collapsed=0,selection='';
  const f=fixture({freeDoubleClick:true,selection:()=>selection,onDoubleClick:()=>collapsed++});
  const body={closest(){return null;}};
  await f.click(0,{target:body});await f.click(150,{target:body});
  assert.equal(collapsed,1);assert.deepEqual(f.calls,[],'free body never begins native window dragging');
  selection='selected words';await f.click(300,{target:body});await f.click(400,{target:body});assert.equal(collapsed,1);
  selection='';const card={closest(selector){return selector.includes('.note')?this:null;}};
  await f.click(600,{target:card});await f.click(700,{target:card});assert.equal(collapsed,1);
  f.emit('pointerdown',{target:body,timeStamp:900});f.emit('pointermove',{target:body,clientX:40,timeStamp:910});f.emit('pointerup',{target:body,clientX:40,timeStamp:920});await flush();
  await f.click(1000,{target:body});assert.equal(collapsed,1,'text-selection drag does not complete a double click');
});
