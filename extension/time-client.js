import { createConnection } from "./connection.js";
import { t } from "./i18n.js";

export const TIME_PRESET_KEY = "time:preset";
// sessionStorage survives reload but is separate for each tab/top-level WebView.
// Pending receipts must never share a storage key with another live client.
export function timeClientIdentity(session = globalThis.sessionStorage) {
  const key = "brainalot:time-client-id";
  try {
    let id = session?.getItem(key);
    if (!id) { id = crypto.randomUUID(); session?.setItem(key, id); }
    return id;
  } catch { return crypto.randomUUID(); }
}
const clientId = timeClientIdentity();
const definiteRejection = error => error.status >= 400 && error.status < 500 && ![408, 425, 429].includes(error.status);
const localStorageAdapter = {
  async get(key) { return await globalThis.chrome?.storage?.local?.get(key) || {}; },
  async set(value) { await globalThis.chrome?.storage?.local?.set(value); },
  async remove(key) { await globalThis.chrome?.storage?.local?.remove(key); },
};
export const taskKey = task => task ? JSON.stringify([task.source, task.id]) : "null";
// Classify new UI commands without changing the identity or past intervals.
export function classifiedTimeTask(task, kind = null) {
  if(!task)return null;
  const ref={source:task.source,id:task.id,title:task.title};
  if(task.source==='label'&&task.id==='rest')return ref;
  ref.activity_kind=kind==='work'||kind==='personal'?kind:task.activity_kind|| (task.source==='work'?'work':'personal');
  return ref;
}
/** Stop only the task proven by a fresh revision; a peer's replacement is safe. */
export async function stopMatchingTimeTask(client, tasks, {requestId=()=>crypto.randomUUID()}={}) {
  const keys=new Set(tasks.map(taskKey));let stopped=false;
  for(let attempt=0;attempt<2;attempt++){
    await client.refresh();
    const snapshot=client.state;
    if(!snapshot||!['running','paused','finished'].includes(snapshot.status)||!keys.has(taskKey(snapshot.task)))return stopped;
    if(client.pending)throw new Error(t('Ответ на прошлую команду не получен. Нажмите «Проверить повтор».'));
    const receipt={request_id:requestId(),expected_revision:snapshot.revision,action:'stop'};
    try{await client.commandWithReceipt(receipt);stopped=true;}
    catch(error){if(error.status===409)continue;throw error;}
  }
  if(['running','paused','finished'].includes(client.state?.status)&&keys.has(taskKey(client.state.task)))throw new Error(t('Учёт изменился в другом окне. Повторите завершение кубика.'));
  return stopped;
}
export function normalizeTimePreset(value) {
  const valid = n => Number.isInteger(n) && n >= 60000 && n <= 10800000;
  return {work_ms:valid(value?.work_ms) ? value.work_ms : 1500000,
    break_ms:valid(value?.break_ms) ? value.break_ms : 300000};
}
export function timerFields(mode, preset) {
  const value = normalizeTimePreset(preset);
  return {mode, ...(mode === "stopwatch" ? {} : {duration_ms:mode === "break" ? value.break_ms : value.work_ms})};
}
let preset = normalizeTimePreset(null), preferenceState = null;
const presetListeners = new Set();
const presetOutboxPrefix = "time:preset-outbox:";
let presetQueue = Promise.resolve(), presetSaveWait = null;
function publishPreset(value) {
  const next = normalizeTimePreset(value), changed = JSON.stringify(next) !== JSON.stringify(preset);
  preset = next;
  if (changed) presetListeners.forEach(fn=>fn({...preset}));
  return {...preset};
}
export async function getTimePreset({refresh = true, waitForSave = true} = {}) {
  while (waitForSave && presetSaveWait) {
    const waiting = presetSaveWait, result = await waiting;
    if (result.error) throw result.error;
    if (waiting === presetSaveWait) break;
  }
  if (waitForSave && globalThis.chrome?.storage?.local && (await outboxCommands(chrome.storage.local,presetOutboxPrefix)).length) {
    throw new Error(t("Настройки таймера не подтверждены. Сохраните пресет повторно."));
  }
  listenPreset();
  if (refresh) {
    preferenceState = await sharedApi("/api/ui/preferences");
    publishPreset(preferenceState.values[TIME_PRESET_KEY]);
  }
  return {...preset};
}
let listeningPreset = false;
function listenPreset() {
  if (listeningPreset || !globalThis.chrome?.storage?.onChanged) return;
  listeningPreset = true;
  chrome.storage.onChanged.addListener((changes,area) => {
    if (area === "local" && changes[TIME_PRESET_KEY]) publishPreset(changes[TIME_PRESET_KEY].newValue);
  });
}
export function subscribeTimePreset(listener) { listenPreset(); presetListeners.add(listener); return () => presetListeners.delete(listener); }
async function commitTimePreset(value) {
  const storage = globalThis.chrome?.storage?.local;
  async function deliver(command) {
    try {
      const result = await sharedApi("/api/ui/preferences", "POST", command);
      if(storage) await discardSaved(storage,presetOutboxPrefix+command.request_id);
      return result;
    } catch(error) {
      if(definiteRejection(error)) {if(storage)await discardSaved(storage,presetOutboxPrefix+command.request_id);if(error.status===409)await getTimePreset({waitForSave:false});}
      throw error;
    }
  }
  for (const pending of storage ? await outboxCommands(storage,presetOutboxPrefix) : []) {
    // Recover an uncertain write with its exact id/body before accepting another edit.
    await deliver(pending);
  }
  await getTimePreset({waitForSave:false});
  if (JSON.stringify(preset) === JSON.stringify(value)) return {...preset};
  const pending = {request_id:crypto.randomUUID(),expected_revision:preferenceState.revision,patch:{[TIME_PRESET_KEY]:value}};
  await storage?.set({[presetOutboxPrefix+pending.request_id]:pending});
  preferenceState = await deliver(pending);
  return publishPreset(preferenceState.values[TIME_PRESET_KEY]);
}
export function saveTimePreset(value) {
  // Serialize edits in one page; the server CAS still arbitrates competing pages.
  const operation = presetQueue.catch(()=>{}).then(()=>commitTimePreset(normalizeTimePreset(value)));
  presetQueue = operation;
  const waiting = operation.then(()=>({}),error=>({error}));
  presetSaveWait = waiting;
  void waiting.then(()=>{if(presetSaveWait===waiting)presetSaveWait=null;});
  return operation;
}
export function elapsedAt(state, now = Date.now()) {
  if (!state) return 0;
  const elapsed = state.elapsed_ms + (state.status === "running" ? Math.max(0, now - state.server_now) : 0);
  return state.mode === "stopwatch" ? elapsed : Math.min(state.duration_ms, elapsed);
}
export function formatTime(ms) {
  const seconds = Math.floor(Math.max(0, ms) / 1000);
  return `${Math.floor(seconds / 3600) ? Math.floor(seconds / 3600) + ":" : ""}${String(Math.floor(seconds / 60) % 60).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}
export function formatTotal(ms) {
  const minutes = Math.floor(Math.max(0, ms) / 60000);
  if (ms <= 0) return t("0 мин");
  if (minutes === 0) return t("< 1 мин");
  if (minutes < 60) return t("{minutes} мин", {minutes});
  return t("{hours} ч {minutes} мин", {hours:Math.floor(minutes / 60),minutes:minutes % 60});
}
export function focusSnapshot(state, selectedPreset = preset) {
  return { durationMs: state.mode === "pomodoro" ? state.duration_ms : normalizeTimePreset(selectedPreset).work_ms,
    elapsedMs: state.mode === "pomodoro" ? state.elapsed_ms : 0,
    startedAt: state.mode === "pomodoro" && state.status === "running" ? state.server_now : null,
    status: state.mode === "pomodoro" ? state.status : "idle" };
}
async function outboxCommands(storage, prefix) {
  const saved = await storage.get(null);
  return Object.entries(saved).filter(([key,value])=>value && typeof value.request_id === "string" && key === prefix+value.request_id)
    .sort(([left],[right])=>left.localeCompare(right)).map(([,command])=>command);
}
async function discardSaved(storage, key) {
  if (storage.remove) await storage.remove(key);
  else await storage.set({[key]:null});
}
export function createTimeClient(api, {storage = null, pendingKey = "time:pending", outboxPrefix = null} = {}) {
  let state = null, pending = null, busy = false, sequence = 0, reconcile = false;
  let ready, recovered = false, pendingSaved = false, inFlight = null, retrying = null;
  const listeners = new Set();
  async function restorePending({force = false} = {}) {
    if (!storage || pending && !pendingSaved || !force && (busy || inFlight)) return;
    const next = outboxPrefix ? (await outboxCommands(storage,outboxPrefix))[0] || null : (await storage.get(pendingKey))[pendingKey] || null;
    // A storage read can finish after a local delivery began. Its snapshot must
    // not replace that command, even if a peer just recovered the same receipt.
    if (!force && (busy || inFlight)) return;
    if (pending?.request_id !== next?.request_id) recovered = !!next;
    pending = next; pendingSaved = !!next;
    if(next) reconcile = true;
  }
  const ensureReady = () => ready ??= restorePending({force:true});
  const publish = next => {if (state && next.revision < state.revision) return state; state = next; listeners.forEach(fn => fn(state)); return state;};
  const persist = async command => {
    await storage?.set({[outboxPrefix ? outboxPrefix+command.request_id : pendingKey]:command});
    pendingSaved = true;
  };
  async function clearPending(command) {
    if(outboxPrefix) {
      await discardSaved(storage,outboxPrefix+command.request_id);
      pending=null;pendingSaved=false;recovered=false;
      await restorePending({force:true});
    } else {await storage?.set({[pendingKey]:null});pending=null;pendingSaved=false;recovered=false;}
  }
  async function refresh() {
    await ensureReady();
    if(outboxPrefix) await restorePending();
    const ticket = ++sequence, next = await api("/api/time/state");
    return ticket === sequence ? publish(next) : state;
  }
  async function deliverPending() {
    await ensureReady();
    if(outboxPrefix) await restorePending();
    if (!pending) return refresh();
    const ticket = ++sequence, command = pending;
    inFlight = command;
    try {
      await persist(command);
      const next = await api("/api/time/command", "POST", command);
      await clearPending(command);
      inFlight = null;
      if (reconcile || state && next.revision < state.revision) {reconcile=false; return refresh();}
      return ticket === sequence ? publish(next) : refresh();
    } catch (error) {
      if (!definiteRejection(error)) reconcile = true;
      if (definiteRejection(error)) {await clearPending(command);inFlight=null;if(error.status===409)await refresh();}
      throw error;
    } finally {inFlight=null;}
  }
  async function submit(action, fields = {}, receipt = null) {
    await ensureReady();
    if (busy || retrying) throw new Error(t("Дождитесь ответа сервиса."));
    busy = true;
    try {
      if(outboxPrefix) await restorePending({force:true});
      if (pending && (!receipt || JSON.stringify(pending) !== JSON.stringify(receipt))) throw new Error(t("Ответ на прошлую команду не получен. Нажмите «Проверить повтор»."));
      if (!state) await refresh();
      pending = receipt ? structuredClone(receipt) : {request_id:crypto.randomUUID(), expected_revision:state.revision, action, ...fields};
      pendingSaved=false;recovered=false;
      return await deliverPending();
    } finally { busy = false; }
  }
  return {
    get state() { return state; }, get pending() { return pending; }, get pendingRecovered() { return recovered; },
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    refresh,
    command: (action, fields = {}) => submit(action, fields),
    async commandWithReceipt(receipt) {
      if (!receipt || typeof receipt.request_id !== "string" || !receipt.request_id || !Number.isInteger(receipt.expected_revision) || typeof receipt.action !== "string") throw new Error(t("Некорректная сохранённая команда учёта."));
      await submit(null, {}, receipt);
      // The same receipt may have been recovered elsewhere, followed by a pause,
      // stop or task change. Never resurrect its historical running snapshot.
      return refresh();
    },
    retry() {
      if(busy) return Promise.reject(new Error(t("Дождитесь ответа сервиса.")));
      if(!retrying) retrying=deliverPending().finally(()=>{retrying=null;});
      return retrying;
    },
  };
}

let connection;
export const sharedApi = (...args) => (connection ??= createConnection(globalThis.chrome)).api(...args);
export const timeClient = createTimeClient(sharedApi, {storage:localStorageAdapter,
  pendingKey:`time:pending:${clientId}`,outboxPrefix:"time:command-outbox:"});
