// Pointer handling for our own native windows. This never emulates OS input.
const rightDragSurfaces=new WeakMap();

// Every native document, including child iframes, forwards a real right drag.
// A short press is left to the document's own context menu.
export function installRightButtonWindowDrag(native,{surface=globalThis.document,threshold=5}={}) {
  if(!surface||typeof native?.window_action!=='function')return ()=>{};
  if(rightDragSurfaces.has(surface))return rightDragSurfaces.get(surface);
  const doc=surface.ownerDocument||surface,win=doc.defaultView||globalThis.window,listeners=[];
  let drag=null,queue=Promise.resolve(),suppressContext=false,disposed=false;
  const send=(action,value)=>queue=queue.catch(()=>{}).then(()=>native.window_action(action,value));
  const listen=(target,type,callback)=>{if(!target?.addEventListener)return;target.addEventListener(type,callback,true);listeners.push(()=>target.removeEventListener(type,callback,true));};
  function finish(event){
    if(!drag||(event?.pointerId!=null&&event.pointerId!==drag.id))return;
    const ended=drag;drag=null;
    if(ended.target.hasPointerCapture?.(ended.id))ended.target.releasePointerCapture(ended.id);
    if(ended.moved){suppressContext=true;void ended.begin.then(()=>send('gesture_end')).catch(()=>{});}
  }
  listen(surface,'pointerdown',event=>{
    if(event.button!==2||disposed)return;
    finish();suppressContext=false;
    drag={id:event.pointerId,x:event.clientX,y:event.clientY,target:event.target,moved:false,sending:false,
      probe:send('gesture_probe').then(value=>value?.origin,()=>null)};
  });
  listen(surface,'pointermove',event=>{
    const current=drag;if(!current||current.id!==event.pointerId)return;
    if(!current.moved&&Math.abs(event.clientX-current.x)+Math.abs(event.clientY-current.y)<threshold)return;
    if(!current.moved){
      current.moved=true;suppressContext=true;
      current.begin=current.probe.then(origin=>send('gesture_begin',{edge:'',right_button:true,...(origin?{origin}:{})})).then(result=>{
        if(result?.restored_work&&win?.top?.dispatchEvent&&win.CustomEvent)win.top.dispatchEvent(new win.CustomEvent('brainalot-work-restored'));
        return result;
      });
      try{current.target.setPointerCapture?.(current.id);}catch{}
    }
    event.preventDefault();
    if(!current.sending){current.sending=true;void current.begin.then(()=>send('gesture_move')).catch(()=>{}).finally(()=>{current.sending=false;});}
  });
  for(const type of['pointerup','pointercancel','lostpointercapture'])listen(surface,type,finish);
  listen(win,'blur',()=>finish());
  listen(surface,'contextmenu',event=>{
    if(!suppressContext||event.button!==2)return;
    suppressContext=false;event.preventDefault();event.stopImmediatePropagation();
  });
  const dispose=()=>{if(disposed)return;finish();disposed=true;listeners.forEach(fn=>fn());rightDragSurfaces.delete(surface);};
  rightDragSurfaces.set(surface,dispose);return dispose;
}

export function installWindowGestures(native, {
  surface = document.body, tab = false, doubleClick = {}, freeDoubleClick = false,
  onDoubleClick = () => native.window_action(tab ? "card" : "bar"),
  onRightDoubleClick = () => native.window_action("full"),
} = {}) {
  const controls = "button, input, select, textarea, a, summary, [contenteditable], [data-no-drag], [role=button], [role=checkbox]";
  const interactiveCards = ".note,.calendar-event,.stack-sheet,#rotation-card,[role=dialog],dialog,details";
  const ownerDocument = surface.ownerDocument || globalThis.document;
  const selectedText = () => Boolean(ownerDocument?.defaultView?.getSelection?.()?.toString().trim());
  const interval = doubleClick.interval_ms || 500;
  const distanceX = Math.max(1, (doubleClick.width || 4) / 2);
  const distanceY = Math.max(1, (doubleClick.height || 4) / 2);
  let gesture = null, queue = Promise.resolve(), suppressClick = false, moving = false, last = null;
  let pointerEpoch = 0, interruption = 0;
  const send = (action, value) => queue = queue.catch(() => {}).then(() => native.window_action(action, value));
  const eligible = event => !event.target.closest(controls) &&
    (!freeDoubleClick || !selectedText() && !event.target.closest(interactiveCards)) &&
    (tab || event.target.closest("[data-window-drag], [data-resize]") || freeDoubleClick && event.button === 0);
  const stamp = event => Number.isFinite(event.timeStamp) ? event.timeStamp : performance.now();
  surface.addEventListener("pointerdown", event => {
    pointerEpoch++;
    suppressClick = false;
    // A new press of the same pointer means its previous release never reached
    // the page (the compact widget hid the WebView mid-drag). Drop that gesture
    // instead of letting the new release finish it as a stale drag.
    if (gesture?.id === event.pointerId) gesture = null;
    if (gesture || ![0, 2].includes(event.button) || !eligible(event)) { last = null; interruption++; return; }
    gesture = {id: event.pointerId, button:event.button, x:event.clientX, y:event.clientY,
      moved:false, resize:!!event.target.closest("[data-resize]"), nativeGesture:tab || !!event.target.closest("[data-window-drag], [data-resize]"), epoch:pointerEpoch, interruption};
    if (gesture.nativeGesture) {
      surface.setPointerCapture(event.pointerId);
      if (event.button === 0) void send("gesture_begin", {edge:event.target.closest("[data-resize]")?.dataset.resize || ""});
      event.preventDefault();
    }
  });
  surface.addEventListener("pointermove", event => {
    if (!gesture || gesture.id !== event.pointerId) return;
    if (Math.abs(event.clientX-gesture.x)+Math.abs(event.clientY-gesture.y) >= 5) {
      gesture.moved = true; suppressClick = true; last = null;
    }
    if (gesture.nativeGesture && gesture.button === 0 && gesture.moved && !moving) {
      moving = true;
      void send("gesture_move").finally(() => { moving = false; });
    }
  });
  const finish = async (event, cancelled = false) => {
    if (!gesture || gesture.id !== event.pointerId) return;
    const finished = gesture; gesture = null;
    const click = {button:finished.button, x:event.clientX, y:event.clientY, time:stamp(event)};
    // The browser's compatibility click follows pointerup in this same task,
    // before any native reply. The flag must not outlive that task: a compact
    // widget gets no further pointerdown, so a leaked flag swallowed every
    // native button click until the next real press reached the page.
    suppressClick = finished.moved || cancelled;
    if (suppressClick) setTimeout(() => { if (pointerEpoch === finished.epoch) suppressClick = false; });
    if (surface.hasPointerCapture(event.pointerId)) surface.releasePointerCapture(event.pointerId);
    const result = finished.nativeGesture && finished.button === 0 ? await send("gesture_end") : {};
    if (result.side && typeof document !== "undefined") document.body.dataset.side = result.side;
    const discarded = finished.moved || result.dragged || cancelled || freeDoubleClick && selectedText();
    if (discarded || finished.resize || finished.interruption !== interruption) { last = null; return; }
    const paired = last && last.button === click.button && click.time-last.time >= 0 &&
      click.time-last.time <= interval && Math.abs(click.x-last.x) <= distanceX && Math.abs(click.y-last.y) <= distanceY;
    last = paired ? null : click;
    if (paired) await (click.button === 2 ? onRightDoubleClick() : onDoubleClick());
  };
  surface.addEventListener("pointerup", event => { void finish(event); });
  surface.addEventListener("pointercancel", event => { void finish(event, true); });
  surface.addEventListener("click", event => {
    // Only the browser's own click after a drag is discarded, once. Native
    // compact controls call element.click() (untrusted); those always run.
    if (!suppressClick || !event.isTrusted) return;
    suppressClick = false;
    event.preventDefault(); event.stopImmediatePropagation();
  }, true);
  // Implement both buttons with Windows preferences; browser dblclick has its own timing.
  surface.addEventListener("dblclick", event => { if (eligible(event)) event.preventDefault(); });
  surface.addEventListener("contextmenu", event => { if (eligible(event)) event.preventDefault(); });
}
