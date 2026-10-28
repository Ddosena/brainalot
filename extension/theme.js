const THEMES = new Set(['graphite', 'midnight', 'contrast', 'glamour']);
const MODES = new Set(['dark', 'light']);
export const DEFAULT_THEME = Object.freeze({theme:'midnight', mode:'dark'});
export const THEME_KEY = 'work:theme';
export function normalizeTheme(value) {
  return {theme:THEMES.has(value?.theme) ? value.theme : DEFAULT_THEME.theme,
    mode:MODES.has(value?.mode) ? value.mode : DEFAULT_THEME.mode};
}
export function applyTheme(element, value) {
  const next = normalizeTheme(value);
  element.classList.remove(...THEMES, ...MODES);
  element.classList.add(next.theme, next.mode);
  element.dataset.theme = next.mode;
  element.dataset.design = next.theme;
  return next;
}
export function legacyTheme(values, prefersDark = false) {
  const designs = {stream:'midnight', console:'graphite', cabinet:'contrast', glamour:'glamour'};
  const design = values?.['panel:design'], mode = values?.['panel:theme'];
  if (!Object.hasOwn(designs, design) && !['system','light','dark'].includes(mode)) return null;
  return {theme:designs[design] || DEFAULT_THEME.theme,
    mode:mode === 'system' ? (prefersDark ? 'dark' : 'light') : MODES.has(mode) ? mode : DEFAULT_THEME.mode};
}
export function createThemeStore(connection, {onChange = () => {}, requestId = () => crypto.randomUUID(),
  storage = globalThis.chrome?.storage?.local, session = globalThis.sessionStorage,
  sessionKey = `brainalot:theme-pending:${globalThis.location?.pathname || 'default'}`,
  prefersDark = () => globalThis.matchMedia?.('(prefers-color-scheme: dark)')?.matches === true} = {}) {
  let state = null, current = null, queue = Promise.resolve(), pending = [];
  try {const saved = JSON.parse(session?.getItem(sessionKey) || '[]'); if (Array.isArray(saved)) pending = saved;} catch {}
  function persist() {try {session?.setItem(sessionKey, JSON.stringify(pending));} catch {}}
  function enqueue(task) {const result = queue.then(task, task); queue = result.catch(() => {}); return result;}
  function notify(value) {
    const next = normalizeTheme(value);
    if (!current || next.theme !== current.theme || next.mode !== current.mode) {current = next; onChange(next);}
    return next;
  }
  async function read() {state = await connection.api('/api/ui/preferences'); return state;}
  async function migrate() {
    if (state.values?.[THEME_KEY] !== undefined || pending.some(item => item.migration)) return;
    let local = {};
    try {local = await storage?.get?.(['panel:design', 'panel:theme']) || {};} catch {}
    const value = legacyTheme({...local, ...state.values}, prefersDark());
    if (value) {pending.unshift({migration:true, patch:value, receipt:null}); persist();}
  }
  async function flush() {
    let conflicts = 0, wrote = false;
    while (pending.length) {
      const item = pending[0];
      if (!state) await read();
      if (item.migration && state.values?.[THEME_KEY] !== undefined && !item.receipt) {pending.shift(); persist(); continue;}
      const next = item.migration ? item.patch : normalizeTheme({...normalizeTheme(state.values?.[THEME_KEY]), ...item.patch});
      item.receipt ||= {request_id:requestId(), expected_revision:state.revision, patch:{[THEME_KEY]:next}};
      persist();
      try {state = await connection.api('/api/ui/preferences', 'POST', item.receipt); pending.shift(); persist(); conflicts = 0; wrote = true;}
      catch (error) {
        if (error.status !== 409 || ++conflicts > 3) throw error;
        item.receipt = null; persist(); await read();
      }
    }
    return wrote;
  }
  async function refreshInner() {
    const first = !state;
    if (first) {await read(); await migrate();}
    const wrote = await flush();
    if (!first || wrote) {
      await read();
      if (state.values?.[THEME_KEY] === undefined) {
        await migrate();
        if (pending.length) {await flush(); await read();}
      }
    }
    return notify(state.values?.[THEME_KEY]);
  }
  function refresh() {return enqueue(refreshInner);}
  function set(patch) {
    return enqueue(async () => {
      pending.push({patch:{...patch}, receipt:null}); persist();
      notify({...current, ...patch});
      if (!state) {await read(); await migrate();}
      await flush(); await read(); return notify(state.values?.[THEME_KEY]);
    });
  }
  return {refresh, set, get current() {return current || DEFAULT_THEME;}};
}
export function mountTheme(connection, {onChange = () => {}, document:doc = globalThis.document,
  window:win = globalThis.window, interval = 750} = {}) {
  const store = createThemeStore(connection, {onChange});
  let polling = false;
  const poll = () => {
    if (doc?.hidden || polling) return;
    polling = true; void store.refresh().catch(() => {}).finally(() => {polling = false;});
  };
  poll(); const timer = setInterval(poll, interval);
  doc?.addEventListener('visibilitychange', poll);
  win?.addEventListener('pagehide', () => {clearInterval(timer); doc?.removeEventListener('visibilitychange', poll);}, {once:true});
  return store;
}
