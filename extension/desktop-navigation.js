/** Desktop routes are explicit; a shared library snapshot stores content state. */
export function resolvePanelView(snapshot, {embedded = false, view = "day", libraryWindow = false} = {}) {
  if (embedded) return view === "library" ? "library" : "day";
  return libraryWindow || snapshot.open ? "library" : "day";
}

export function desktopFrameReady(frame, url = frame.src) {
  try { return frame.contentWindow.location.href === url && frame.contentDocument.readyState === "complete"; }
  catch { return false; }
}

export function createDesktopNavigation({frame, baseUrl, workEnabled, onView = () => {}}) {
  const origin = new URL(baseUrl).origin;
  const paths = {
    day: "panel.html?embedded-desktop=1&desktop-view=day",
    library: "panel.html?embedded-desktop=1&desktop-view=library",
    work: "work.html", capture: "capture.html",
  };
  let generation = 0;
  function normalize(view) {
    if (!Object.hasOwn(paths, view) || view === "work" && !workEnabled) return "day";
    return view;
  }
  function target(view) { return new URL(paths[normalize(view)], baseUrl).href; }
  function post(action) {
    frame.contentWindow.postMessage({type: "brainalot:panel-action", action}, origin);
  }
  function show(requested) {
    const view = normalize(requested), next = target(view), ticket = ++generation;
    const changed = frame.src !== next;
    if (changed) { frame.dataset.loading = "true"; frame.src = next; }
    onView(view);
    if (view === "day" && !changed && frame.dataset.loading !== "true" && desktopFrameReady(frame)) post("day");
    return ticket;
  }
  async function panelAction(action) {
    const view = action === "search" ? "library" : "day", next = target(view);
    const loading = frame.src !== next || frame.dataset.loading === "true" || !desktopFrameReady(frame, next);
    // Subscribe before changing src, and discard work superseded by a later click.
    const ticket = generation + 1;
    const loaded = loading ? new Promise(resolve => {
      const ready = () => {
        if (ticket === generation && frame.src === next && !desktopFrameReady(frame, next)) return;
        frame.removeEventListener("load", ready);
        resolve();
      };
      frame.addEventListener("load", ready);
    }) : null;
    show(view);
    if (loaded) await loaded;
    if (ticket !== generation || frame.src !== next || !desktopFrameReady(frame, next)) return false;
    post(action);
    return true;
  }
  function fromPanel(request) {
    if (request?.type !== "brainalot:desktop-view" || !["day", "library"].includes(request.view) ||
        request.href !== frame.src || !desktopFrameReady(frame) ||
        !new URL(frame.src).pathname.endsWith("/panel.html")) return false;
    show(request.view);
    return true;
  }
  return {show, panelAction, fromPanel};
}
