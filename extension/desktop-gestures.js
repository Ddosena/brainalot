// Pointer handling for our own native windows. This never emulates OS input.
export function installWindowGestures(native, {surface = document.body, tab = false, onClick = () => {}} = {}) {
  let gesture = null, queue = Promise.resolve(), suppressClick = false, moving = false;
  const send = (action, value) => queue = queue.catch(() => {}).then(() => native.window_action(action, value));
  surface.addEventListener("pointerdown", event => {
    if (event.button !== 0 || gesture || event.target.closest("button, input, select, textarea, a, summary, [contenteditable], [data-no-drag]")) return;
    if (!tab && !event.target.closest("[data-window-drag], [data-resize]")) return;
    gesture = {id: event.pointerId, x:event.clientX, y:event.clientY, moved:false};
    surface.setPointerCapture(event.pointerId);
    void send("gesture_begin", {edge:event.target.closest("[data-resize]")?.dataset.resize || ""});
    event.preventDefault();
  });
  surface.addEventListener("pointermove", event => {
    if (!gesture || gesture.id !== event.pointerId) return;
    if (Math.abs(event.clientX-gesture.x)+Math.abs(event.clientY-gesture.y) >= 5) gesture.moved = true;
    if (gesture.moved && !moving) {
      moving = true;
      void send("gesture_move").finally(() => { moving = false; });
    }
  });
  const finish = async (event, cancelled = false) => {
    if (!gesture || gesture.id !== event.pointerId) return;
    const finished = gesture; gesture = null;
    suppressClick = finished.moved || cancelled;
    const result = await send("gesture_end");
    if (result.side) document.body.dataset.side = result.side;
    suppressClick = finished.moved || result.dragged || cancelled;
    if (surface.hasPointerCapture(event.pointerId)) surface.releasePointerCapture(event.pointerId);
    if (tab && !suppressClick) onClick();
    // pointerup's compatibility click must never activate a button after drag.
    setTimeout(() => { suppressClick = false; }, 0);
  };
  surface.addEventListener("pointerup", event => { void finish(event); });
  surface.addEventListener("pointercancel", event => { void finish(event, true); });
  surface.addEventListener("click", event => {
    if (suppressClick) {event.preventDefault(); event.stopImmediatePropagation();}
  }, true);
}
