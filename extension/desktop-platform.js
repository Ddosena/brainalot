// Desktop platform adapter. Data stays behind the common authenticated API.
export const isDesktop = typeof location !== "undefined" && location.pathname.startsWith("/desktop/");
const event = () => {
  const listeners = new Set();
  return { addListener: fn => listeners.add(fn), removeListener: fn => listeners.delete(fn), emit: (...args) => listeners.forEach(fn => fn(...args)), listeners };
};
export let native = null;
if (isDesktop) {
  if (window !== window.top && window.top.BrainalotPlatform) {
    const parent = window.top.BrainalotPlatform;
    const listeners = new Set();
    globalThis.chrome = {...parent, storage:{...parent.storage,onChanged:{
      addListener(fn){listeners.add(fn);parent.storage.onChanged.addListener(fn);},
      removeListener(fn){listeners.delete(fn);parent.storage.onChanged.removeListener(fn);}
    }}};
    window.addEventListener("pagehide",()=>{for(const fn of listeners)parent.storage.onChanged.removeListener(fn);listeners.clear();},{once:true});
    native = window.top.pywebview.api;
  } else {
    if (!window.pywebview?.api) await new Promise(resolve => window.addEventListener("pywebviewready", resolve, { once: true }));
    native = window.pywebview.api;
    const config = await native.bootstrap();
    let cache = await native.get_storage(null);
    cache.connection = { port: config.port, token: config.token, auto: true };
    const changed = event();
    const notify = next => {
      next.connection = cache.connection;
      const changes = {};
      for (const key of new Set([...Object.keys(cache), ...Object.keys(next)])) {
        if (JSON.stringify(cache[key]) !== JSON.stringify(next[key])) changes[key] = { oldValue: cache[key], newValue: next[key] };
      }
      cache = next;
      if (Object.keys(changes).length) changed.emit(changes, "local");
    };
    const local = {
      async get(keys) {
        if (keys == null) return structuredClone(cache);
        const names = typeof keys === "string" ? [keys] : Array.isArray(keys) ? keys : Object.keys(keys);
        return Object.fromEntries(names.map(k => [k, structuredClone(cache[k] ?? (typeof keys === "object" && !Array.isArray(keys) ? keys[k] : undefined))]));
      },
      async set(items) { await native.set_storage(items); notify({ ...cache, ...items }); },
      async remove(keys) { const names = typeof keys === "string" ? [keys] : keys; await native.remove_storage(names); const next = { ...cache }; for (const key of names) delete next[key]; notify(next); },
      async setAccessLevel() {},
    };
    const messages = event();
    const base = new URL("./", location.href).href;
    const navigate = async url => {
      const target = new URL(url, base);
      if (target.href.startsWith(base)) await native.open_page(target.href.slice(base.length));
      else await native.external_url(target.href);
      return { id: 1, windowId: 1, url: target.href };
    };
    const noop = async () => {};
    const chromeApi = {
      webview: globalThis.chrome?.webview,
      desktop: true, storage: { local, session: local, onChanged: changed },
      i18n: { getUILanguage: () => cache["panel:locale"] || "ru" },
      runtime: {
        id: "brainalot-desktop", getURL: path => new URL(path.replace(/^\//, ""), base).href,
        onInstalled: event(), onStartup: event(), onMessage: messages, getManifest: () => ({ version: "desktop" }),
        sendNativeMessage: (_host, _message, callback) => callback({ ok: true, ...config }),
        async sendMessage(message) {
          if (message.type === "open-capture") { await native.open_page("capture.html"); return { ok: true, data: {} }; }
          if (message.type === "library-window-open") { await native.open_page("panel.html?library-window=1"); return { ok: true, data: {} }; }
          if (["finish-capture", "library-window-return"].includes(message.type)) { await native.open_page("panel.html"); return { ok: true, data: {} }; }
          if (message.type === "library-window-status") return { ok: true, data: { open: false } };
          if (message.type === "library-window-context") return { ok: true, data: { originWindowId: 1 } };
          return new Promise(resolve => {
            const sender = { id: chromeApi.runtime.id, url: base + "panel.html", tab: { id: 1, windowId: 1 } };
            let handled = false;
            for (const fn of messages.listeners) if (fn(message, sender, resolve)) handled = true;
            if (!handled) resolve({ ok: false, error: "Desktop is not ready" });
          });
        },
      },
      tabs: { create: ({ url }) => navigate(url), getCurrent: async () => ({ id: 1, windowId: 1 }), query: async () => [], update: async () => ({}), onUpdated: event(), onRemoved: event() },
      windows: { onRemoved: event(), getCurrent: async () => ({ id: 1, width: innerWidth, height: innerHeight }), getLastFocused: async () => ({ id: 1 }), update: noop, create: ({ url }) => navigate(url), remove: noop },
      sidePanel: { setPanelBehavior: noop, open: async () => native.open_page("panel.html") },
      action: { setBadgeText: noop, setBadgeBackgroundColor: noop },
      alarms: { get: async () => null, create: noop, onAlarm: event() },
      permissions: { contains: async () => false, request: async () => false },
    };
    globalThis.chrome = chromeApi;
    window.BrainalotPlatform = chromeApi;
    setInterval(() => native.get_storage(null).then(notify).catch(() => {}), 2000);
    setInterval(() => chromeApi.alarms.onAlarm.emit({ name: "megamozg-retry" }), 60_000);
  }
  document.addEventListener("click", event => {
    const link = event.target.closest?.("a[href]");
    if (!link || !/^https?:/.test(link.href)) return;
    const url = new URL(link.href);
    if (url.origin === location.origin && url.pathname.startsWith("/desktop/")) return;
    event.preventDefault(); void native.external_url(link.href);
  });
}
