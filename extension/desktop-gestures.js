// Pointer handling for our own native windows. This never emulates OS input.
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
    // Set this synchronously: the compatibility click arrives before the native reply.
    suppressClick = finished.moved || cancelled;
    if (surface.hasPointerCapture(event.pointerId)) surface.releasePointerCapture(event.pointerId);
    const result = finished.nativeGesture && finished.button === 0 ? await send("gesture_end") : {};
    if (result.side && typeof document !== "undefined") document.body.dataset.side = result.side;
    const discarded = finished.moved || result.dragged || cancelled || freeDoubleClick && selectedText();
    if (pointerEpoch === finished.epoch) suppressClick = discarded;
    if (discarded || finished.resize || finished.interruption !== interruption) { last = null; return; }
    const paired = last && last.button === click.button && click.time-last.time >= 0 &&
      click.time-last.time <= interval && Math.abs(click.x-last.x) <= distanceX && Math.abs(click.y-last.y) <= distanceY;
    last = paired ? null : click;
    if (paired) {
      if (pointerEpoch === finished.epoch) suppressClick = true;
      await (click.button === 2 ? onRightDoubleClick() : onDoubleClick());
    }
  };
  surface.addEventListener("pointerup", event => { void finish(event); });
  surface.addEventListener("pointercancel", event => { void finish(event, true); });
  surface.addEventListener("click", event => {
    if (suppressClick) {event.preventDefault(); event.stopImmediatePropagation();}
  }, true);
  // Implement both buttons with Windows preferences; browser dblclick has its own timing.
  surface.addEventListener("dblclick", event => { if (eligible(event)) event.preventDefault(); });
  surface.addEventListener("contextmenu", event => { if (eligible(event)) event.preventDefault(); });
}
