export const SHARED_KEYS = ["panel:calendar-importance", "panel:theme", "panel:design", "panel:locale", "time:preset"];
const PENDING = "shared:preferences-pending", CONFLICT = "shared:preferences-conflict";
export function syncPreferences(api, chromeApi = globalThis.chrome) {
  let state = null, busy = false, conflict = false;
  let queue = [];
  const suppressed = new Map();
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  const ready = chromeApi.storage.local.get([PENDING,CONFLICT]).then(saved => { queue = saved[PENDING] || []; conflict = !!saved[CONFLICT]; });
  const persist = () => chromeApi.storage.local.set({[PENDING]:queue});
  async function apply(remote) {
    state = remote;
    const before = await chromeApi.storage.local.get(SHARED_KEYS);
    const marks = [];
    for (const [key, value] of Object.entries(remote.values)) {
      if (!SHARED_KEYS.includes(key) || same(before[key], value)) continue;
      const mark = { oldValue: before[key], newValue: value };
      if (!suppressed.has(key)) suppressed.set(key, []);
      suppressed.get(key).push(mark); marks.push([key, mark]);
    }
    try { await chromeApi.storage.local.set(remote.values); }
    catch (error) {
      for (const [key, mark] of marks) {
        const candidates = suppressed.get(key);
        const index = candidates.indexOf(mark);
        if (index >= 0) candidates.splice(index, 1);
      }
      throw error;
    }
  }
  async function poll() {
    await ready;
    if (busy || conflict) return; busy = true;
    try {
      if (!state) {
        state = await api("/api/ui/preferences");
        if (state.revision === 0 && !chromeApi.desktop && !queue.length) {
          const local = await chromeApi.storage.local.get(SHARED_KEYS);
          const patch = Object.fromEntries(Object.entries(local).filter(([,v]) => v !== undefined));
          if (Object.keys(patch).length) queue.push({patch,request_id:crypto.randomUUID(),expected_revision:0});
        }
      }
      while (queue.length) {
        const command = queue[0]; command.expected_revision ??= state.revision; await persist();
        try { state = await api("/api/ui/preferences", "POST", command); queue.shift(); await persist(); }
        catch (error) {
          if (error.status === 409) { conflict = true; await chromeApi.storage.local.set({[CONFLICT]:true}); }
          throw error;
        }
      }
      await apply(await api("/api/ui/preferences"));
    } catch { /* Durable request and local values survive unknown outcomes and conflicts. */ }
    finally { busy = false; }
  }
  const onChanged = (changes, area) => {
    if (area !== "local") return;
    const patch = {};
    for (const key of SHARED_KEYS) {
      const change = changes[key];
      if (!change) continue;
      const marks = suppressed.get(key) || [];
      const index = marks.findIndex(mark => same(mark.oldValue, change.oldValue) && same(mark.newValue, change.newValue));
      if (index >= 0) { marks.splice(index, 1); continue; }
      if (change.newValue !== undefined) patch[key] = change.newValue;
    }
    if (Object.keys(patch).length) { void ready.then(async()=>{queue.push({patch,request_id:crypto.randomUUID()});await persist();await poll();}); }
  };
  chromeApi.storage.onChanged.addListener(onChanged);
  void poll();
  const interval = setInterval(poll, 5000);
  interval.unref?.();
  return {
    pollNow: poll,
    stop() { clearInterval(interval); chromeApi.storage.onChanged.removeListener?.(onChanged); },
    async resolve(keepLocal) {
      await ready;
      const latest = await api("/api/ui/preferences");
      if (keepLocal) {
        const patch = Object.assign({}, ...queue.map(item=>item.patch));
        queue = [{patch,request_id:crypto.randomUUID(),expected_revision:latest.revision}]; state = latest;
      } else { queue = []; await apply(latest); }
      conflict = false; await persist(); await chromeApi.storage.local.set({[CONFLICT]:false}); await poll();
    },
  };
}
