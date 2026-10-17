// The service is Work's source of truth. This database retains legacy data and
// a durable draft; it is never deleted as part of migration.
import { createConnection } from "./connection.js";
import { prepareCommit, verifyCommit, sha256 } from "./work-sync.js";

export const uid = () => (crypto.randomUUID?.() || Math.random().toString(36).slice(2) + Date.now().toString(36)).replace(/-/g, "").slice(0, 10);
export const emptyState = () => ({
  version: 1, projects: [], items: [],
  boards: [{ id: "general", view: { x: 40, y: 28, z: 1 }, items: [] }],
  view: { board: "general", days: 7, calH: null },
});
const copy = value => structuredClone(value);
const pageId = crypto.randomUUID?.() || uid();
const lockKey = "work-canvas:migration-lock";
const draftPrefix = "draft:";
const channel = typeof document !== "undefined" && "BroadcastChannel" in globalThis ? new BroadcastChannel("work-canvas") : null;
let dbPromise;
function db() {
  dbPromise ??= new Promise((resolve, reject) => {
    const req = indexedDB.open("work-canvas", 2);
    let settled = false, blockedTimer;
    const fail = error => {
      if (settled) return;
      settled = true; clearTimeout(blockedTimer); reject(error);
    };
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains("kv")) req.result.createObjectStore("kv");
      if (!req.result.objectStoreNames.contains("images")) req.result.createObjectStore("images");
      if (!req.result.objectStoreNames.contains("canonical-images")) req.result.createObjectStore("canonical-images");
    };
    req.onblocked = () => {
      blockedTimer ??= setTimeout(() => fail(new Error("Close older Work tabs to upgrade its database, then retry")), 2000);
    };
    req.onsuccess = () => {
      if (settled) { req.result.close?.(); return; }
      settled = true; clearTimeout(blockedTimer); resolve(req.result);
    };
    req.onerror = () => fail(req.error || new Error("Work database cannot open"));
  }).catch(error => { dbPromise = null; throw error; });
  return dbPromise;
}
async function tx(stores, mode, work) {
  const database = await db();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(stores, mode);
    let result;
    try { result = work(transaction); } catch (error) { transaction.abort(); reject(error); return; }
    transaction.oncomplete = () => resolve(result?.result);
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error || new Error("Work database transaction aborted"));
  });
}
const read = key => tx(["kv"], "readonly", t => t.objectStore("kv").get(key));
const put = (key, value) => tx(["kv"], "readwrite", t => t.objectStore("kv").put(value, key));
const del = key => tx(["kv"], "readwrite", t => t.objectStore("kv").delete(key));
async function draftRecords() {
  const database = await db();
  return new Promise((resolve, reject) => {
    const t = database.transaction(["kv"], "readonly"), kv = t.objectStore("kv");
    const keys = kv.getAllKeys(), values = kv.getAll();
    t.oncomplete = () => resolve(keys.result.map((key, i) => [key, values.result[i]])
      .filter(([key]) => typeof key === "string" && key.startsWith(draftPrefix))
      .map(([key, value]) => ({ ...value, key }))
      .sort((a, b) => (b.created || 0) - (a.created || 0)));
    t.onerror = () => reject(t.error);
  });
}
const draftKey = (owner, requestId) => `${draftPrefix}${owner}:${requestId}`;
async function snapshotLegacy(saveBackup = false) {
  const database = await db();
  return new Promise((resolve, reject) => {
    const t = database.transaction(["kv", "images"], "readwrite");
    const kv = t.objectStore("kv"), img = t.objectStore("images");
    const state = kv.get("state"), backup = kv.get("legacy-backup"), ids = img.getAllKeys(), blobs = img.getAll();
    let snapshot;
    blobs.onsuccess = () => {
      snapshot = { state: copy(state.result || emptyState()), images: ids.result.map((id, i) => [id, blobs.result[i]]) };
      if (saveBackup && !backup.result) kv.put(snapshot, "legacy-backup");
    };
    t.oncomplete = () => resolve(snapshot);
    t.onerror = () => reject(t.error);
    t.onabort = () => reject(t.error || new Error("Work snapshot aborted"));
  });
}
async function snapshotCanonicalImages() {
  const database = await db();
  return new Promise((resolve, reject) => {
    const t = database.transaction(["canonical-images"], "readonly");
    const img = t.objectStore("canonical-images"), ids = img.getAllKeys(), blobs = img.getAll();
    t.oncomplete = () => resolve(new Map(ids.result.map((id, i) => [id, blobs.result[i]])));
    t.onerror = () => reject(t.error);
  });
}

let transport, connection;
export function setWorkTransport(value) { transport = value; }
function client() {
  if (transport) return transport;
  connection ??= createConnection(globalThis.chrome);
  return {
    api: (...args) => connection.api(...args),
    readImage: async id => {
      const { token, port } = await connection.current();
      const response = await fetch(`http://127.0.0.1:${port || 8765}/api/work/images/${encodeURIComponent(id)}`, {
        headers: { Authorization: `Bearer ${token}` }, credentials: "omit", redirect: "error", cache: "no-store",
      });
      if (!response.ok) { const error = new Error(`Work image HTTP ${response.status}`); error.status = response.status; throw error; }
      return response.arrayBuffer();
    },
  };
}

let state = null, revision = 0, initialized = false;
let images = new Map(), timer = null, loadPromise = null, pending = Promise.resolve(), dirty = 0, unresolved = false, committing = false, committingDraftKey = null;
let statusCb, remoteCb, migrationCb, pollTimer, migrationLease, remoteMigrationUntil = 0, migrationExpiryTimer;
let selectedDraftKey = null, supersedesDraftKey = null, lastStatus = null;
const subs = new Set();
const status = (kind, error) => { lastStatus = { kind, error }; statusCb?.(kind, error); };
const notify = meta => subs.forEach(fn => fn(meta));
const broadcast = () => channel?.postMessage({ from: pageId, revision });
export const get = () => state;
export const getRevision = () => revision;
export const isInitialized = () => initialized;
export const subscribe = fn => { subs.add(fn); return () => subs.delete(fn); };
export const onSaveStatus = fn => { statusCb = fn; if (lastStatus) fn(lastStatus.kind, lastStatus.error); };
export const onRemoteChange = fn => { remoteCb = fn; };
export const onMigrationChange = fn => { migrationCb = fn; fn(!!migrationLease || locked()); };
export async function listDrafts() {
  return (await draftRecords()).map(({ key, owner, request_id, expected_revision, created, images: pictures }) => ({
    key, owner, request_id, expected_revision, created, imageCount: pictures?.length || 0,
  }));
}

function foreignLeaseUntil() {
  try {
    const lock = JSON.parse(localStorage.getItem(lockKey) || "null");
    return lock?.owner !== pageId ? lock?.until || 0 : 0;
  } catch { return 0; }
}
function locked() {
  return remoteMigrationUntil > Date.now() || foreignLeaseUntil() > Date.now();
}
function scheduleMigrationExpiry() {
  clearTimeout(migrationExpiryTimer);
  const remaining = Math.max(remoteMigrationUntil, foreignLeaseUntil()) - Date.now();
  if (remaining > 0) {
    migrationExpiryTimer = setTimeout(scheduleMigrationExpiry, remaining + 5);
    migrationExpiryTimer.unref?.();
  } else {
    remoteMigrationUntil = 0;
    migrationCb?.(false);
    void checkRemote();
  }
}
export const isWritable = () => initialized && !unresolved && !locked() && !migrationLease;
function writable() {
  if (locked() || migrationLease) throw new Error("Work migration in progress");
  if (!initialized) throw new Error("Work is not initialized");
  if (unresolved) throw new Error("Resolve the unconfirmed Work draft first");
}
function migrationLock() {
  if (locked()) throw new Error("Another Work tab is migrating");
  const renew = () => localStorage.setItem(lockKey, JSON.stringify({ owner: pageId, until: Date.now() + 30000 }));
  renew(); migrationLease = setInterval(renew, 10000);
  channel?.postMessage({ from: pageId, migration: true, until: Date.now() + 30000 });
  migrationCb?.(true);
  return () => {
    clearInterval(migrationLease); migrationLease = null;
    try { if (JSON.parse(localStorage.getItem(lockKey) || "null")?.owner === pageId) localStorage.removeItem(lockKey); } catch {}
    channel?.postMessage({ from: pageId, migration: false });
    migrationCb?.(false);
  };
}
async function fetchImage(meta) {
  const bytes = new Uint8Array(await client().readImage(meta.id));
  if (bytes.byteLength !== meta.size || await sha256(bytes) !== meta.blob) throw new Error(`Work image corrupt: ${meta.id}`);
  return new Blob([bytes], { type: meta.mime || "application/octet-stream" });
}
async function accept(remote, announce = false) {
  if (!remote.initialized || !remote.state) throw new Error("Work server returned empty state");
  const fetched = new Map();
  for (const meta of remote.images || []) fetched.set(meta.id, await fetchImage(meta));
  await tx(["kv", "canonical-images"], "readwrite", t => {
    t.objectStore("kv").put({ revision: remote.revision, state: remote.state }, "canonical-cache");
    const img = t.objectStore("canonical-images"); img.clear();
    for (const [id, blob] of fetched) img.put(blob, id);
  });
  state = copy(remote.state); revision = remote.revision; images = fetched; initialized = true;
  unresolved = false; selectedDraftKey = null;
  if (announce) broadcast();
  notify({ from: "remote" }); remoteCb?.();
}
function nonempty(snapshot) {
  return snapshot.images.length > 0 || JSON.stringify(snapshot.state) !== JSON.stringify(emptyState());
}

async function commit(next, imageMap, expected, requestId = crypto.randomUUID?.() || uid(), beforeCommit, fixedKey) {
  const entries = [...imageMap];
  const selectedAtStart = fixedKey === undefined ? selectedDraftKey : fixedKey;
  const key = selectedAtStart && (await read(selectedAtStart))?.request_id === requestId
    ? selectedAtStart : draftKey(pageId, requestId);
  const oldKeyToDelete = supersedesDraftKey;
  if (committing) committingDraftKey = key;
  if (selectedDraftKey === selectedAtStart) selectedDraftKey = key;
  await put(key, { owner: pageId, state: copy(next), images: entries, expected_revision: expected, request_id: requestId, created: Date.now() });
  const { api, readImage } = client();
  const prepared = await prepareCommit(api, next, entries);
  if (beforeCommit) {
    try { await beforeCommit(); }
    catch (error) { await del(key); selectedDraftKey = null; throw error; }
  }
  const receipt = await api("/api/work/commit", "POST", { request_id: requestId, expected_revision: expected, ...prepared });
  if (receipt.revision !== expected + 1) throw new Error("Work commit revision receipt mismatch");
  const hashes = new Map(prepared.images.map(row => [row.id, row.blob]));
  await verifyCommit(api, readImage, receipt, next, entries, hashes);
  if (beforeCommit) await beforeCommit();
  revision = receipt.revision; initialized = true;
  await tx(["kv", "canonical-images"], "readwrite", t => {
    t.objectStore("kv").put({ revision, state: copy(next) }, "canonical-cache");
    const img = t.objectStore("canonical-images"); img.clear();
    for (const [id, blob] of entries) img.put(blob, id);
  });
  await del(key);
  if (oldKeyToDelete && oldKeyToDelete !== key) await del(oldKeyToDelete);
  if (selectedDraftKey === key) selectedDraftKey = null;
  if (supersedesDraftKey === oldKeyToDelete) supersedesDraftKey = null;
  unresolved = false; broadcast(); status("saved");
}
async function sameLegacy(a, b) {
  if (JSON.stringify(a.state) !== JSON.stringify(b.state) || a.images.length !== b.images.length) return false;
  for (let i = 0; i < a.images.length; i++) {
    if (a.images[i][0] !== b.images[i][0]) return false;
    if (await sha256(await a.images[i][1].arrayBuffer()) !== await sha256(await b.images[i][1].arrayBuffer())) return false;
  }
  return true;
}
const legacyChanged = () => Object.assign(new Error("Legacy Work changed during migration"), { status: 409, sourceChanged: true });
async function migrate() {
  const unlock = migrationLock();
  try {
    const snapshot = await snapshotLegacy(true);
    const remote = await client().api("/api/work/state");
    if (remote.initialized) { await accept(remote); return; }
    if (!nonempty(snapshot)) { state = snapshot.state; images = new Map(snapshot.images); return; }
    await commit(snapshot.state, new Map(snapshot.images), 0, undefined, async () => {
      if (!await sameLegacy(snapshot, await snapshotLegacy())) throw legacyChanged();
    });
    state = copy(snapshot.state); images = new Map(snapshot.images);
    notify({ from: "replace" });
  } finally { unlock(); }
}
export function load() {
  loadPromise ??= (async () => {
    const snapshot = await snapshotLegacy();
    const draft = locked() ? null : (await draftRecords())[0];
    const canonicalImages = await snapshotCanonicalImages();
    try {
      const remote = await client().api("/api/work/state");
      if (remote.initialized) {
        if (draft) {
          selectedDraftKey = draft.key;
          state = copy(draft.state); revision = draft.expected_revision;
          images = new Map(draft.images || ((await read("canonical-cache")) ? canonicalImages : snapshot.images));
          initialized = true; unresolved = true;
          status("conflict", new Error("Work has an unconfirmed local draft"));
        } else await accept(remote);
      } else if (draft) {
        selectedDraftKey = draft.key;
        state = copy(draft.state); revision = draft.expected_revision;
        images = new Map(draft.images || snapshot.images);
        initialized = unresolved = true;
        status("conflict", new Error("Work has an unconfirmed migration draft"));
      } else if (nonempty(snapshot)) await migrate();
      else { state = snapshot.state; images = new Map(snapshot.images); }
    } catch (error) {
      const cache = await read("canonical-cache");
      const pendingDraft = locked() ? null : draft || (await draftRecords())[0];
      const latest = cache ? snapshot : await snapshotLegacy();
      selectedDraftKey = pendingDraft?.key || null;
      state = copy(error?.sourceChanged ? latest.state : pendingDraft?.state || cache?.state || latest.state);
      revision = pendingDraft?.expected_revision ?? cache?.revision ?? 0;
      images = error?.sourceChanged ? new Map(latest.images)
        : new Map(pendingDraft?.images || (cache ? canonicalImages : latest.images));
      initialized = error?.sourceChanged ? false : !!(pendingDraft || cache);
      unresolved = !!(pendingDraft || error?.sourceChanged);
      status(error?.status === 409 ? "conflict" : "error", error);
    }
    if (typeof document !== "undefined" && !pollTimer) {
      pollTimer = setInterval(checkRemote, 5000);
      pollTimer.unref?.();
    }
    return state;
  })().catch(error => {
    state = emptyState(); initialized = false;
    status("error", error);
    return state;
  });
  return loadPromise;
}
export async function retryLoad() {
  loadPromise = null; lastStatus = null;
  const result = await load();
  if (!lastStatus) status("saved");
  return result;
}

export function update(fn, meta = {}) {
  writable(); fn?.(state); dirty++;
  timer ??= setTimeout(() => { void flush().catch(() => {}); }, 250);
  notify(meta);
}
export async function flush() {
  clearTimeout(timer); timer = null;
  await load();
  if (unresolved) throw new Error("Resolve the unconfirmed Work draft before saving");
  if (committing) {
    await pending;
    return dirty ? flush() : undefined;
  }
  if (!initialized || !dirty) return;
  const batch = dirty, next = copy(state), imgs = new Map(images), batchDraftKey = selectedDraftKey;
  committing = true; committingDraftKey = batchDraftKey;
  pending = Promise.resolve().then(async () => {
    try {
      const expected = revision;
      const draft = batchDraftKey ? await read(batchDraftKey) : null;
      if (draft && draft.expected_revision !== expected) throw Object.assign(new Error("Work draft revision conflict"), { status: 409 });
      await commit(next, imgs, expected, draft?.request_id, undefined, batchDraftKey);
      if (dirty === batch) dirty = 0;
      else { dirty -= batch; timer ??= setTimeout(() => { void flush().catch(() => {}); }, 0); }
    } catch (error) { unresolved = true; status(error?.status === 409 ? "conflict" : "error", error); throw error; }
  }).finally(() => { committing = false; committingDraftKey = null; });
  return pending;
}
export async function replace(next) {
  writable(); state = copy(next); dirty++; notify({ from: "replace" });
  return flush();
}
// JSON import replaces both halves of the Work snapshot in one server CAS commit.
export async function replaceWithImages(next, pairs) {
  await load(); writable();
  const replacement = new Map(pairs);
  if (replacement.size !== pairs.length) throw new Error("Duplicate Work image ID");
  state = copy(next); images = replacement; dirty++;
  notify({ from: "replace" });
  return flush();
}
export async function imgGet(id) { await load(); return images.get(id); }
export async function imgPut(id, blob) {
  await load(); writable();
  const had = images.has(id), previous = images.get(id);
  images.set(id, blob); dirty++;
  // imgPut historically resolved only after the Blob was durable. Keep that
  // property without writing into another tab's canonical image cache.
  try {
    const prior = selectedDraftKey && (!committing || selectedDraftKey !== committingDraftKey)
      ? await read(selectedDraftKey) : null;
    const requestId = prior?.request_id || crypto.randomUUID?.() || uid();
    const key = prior ? selectedDraftKey : draftKey(pageId, requestId);
    await put(key, {
      owner: pageId, state: copy(state), images: [...images],
      expected_revision: revision + (committing ? 1 : 0),
      request_id: requestId, created: prior?.created || Date.now(),
    });
    selectedDraftKey = key;
  } catch (error) {
    if (had) images.set(id, previous); else images.delete(id);
    dirty--; throw error;
  }
  timer ??= setTimeout(() => { void flush().catch(() => {}); }, 250);
}
export async function imgAll() { await load(); return [...images]; }

async function checkRemote() {
  if (dirty || migrationLease || locked()) return;
  if (selectedDraftKey) {
    if (await read(selectedDraftKey)) return;
    selectedDraftKey = null; unresolved = false;
  }
  try {
    const remote = await client().api("/api/work/state");
    if (remote.initialized && remote.revision > revision) await accept(remote);
  } catch { /* Retain the last verified state until the next poll. */ }
}
if (channel) channel.onmessage = event => {
  if (event.data?.from === pageId) return;
  if (typeof event.data?.migration === "boolean") {
    remoteMigrationUntil = event.data.migration ? Math.max(Date.now(), Number(event.data.until) || Date.now() + 30000) : 0;
    migrationCb?.(locked());
    if (event.data.migration) scheduleMigrationExpiry();
    else { clearTimeout(migrationExpiryTimer); void checkRemote(); }
  }
  if (event.data?.revision > revision) void checkRemote();
};

export async function initializeEmpty() {
  await load();
  if (unresolved) throw new Error("Resolve the Work migration conflict before initialization");
  if (initialized) throw new Error("Work already initialized");
  const remote = await client().api("/api/work/state");
  if (remote.initialized) { await accept(remote); throw new Error("Work initialized in another window"); }
  await commit(emptyState(), new Map(), 0);
  state = emptyState(); images = new Map(); notify({ from: "replace" });
}
export async function recoverDraft(key) {
  const saved = key ? await read(key) : null;
  const draft = key ? (saved && key.startsWith(draftPrefix) ? { ...saved, key } : null) : (await draftRecords())[0];
  if (!draft) return null;
  selectedDraftKey = draft.key;
  state = copy(draft.state); images = new Map(draft.images || []);
  revision = draft.expected_revision; dirty = 1; initialized = true; unresolved = true;
  notify({ from: "replace" }); return copy(draft);
}
export async function discardDraft(key = selectedDraftKey) {
  const remote = await client().api("/api/work/state");
  if (remote.initialized) await accept(remote);
  else {
    const legacy = await snapshotLegacy();
    state = legacy.state; images = new Map(legacy.images);
    revision = 0; initialized = false;
    notify({ from: "replace" }); remoteCb?.();
  }
  if (key) await del(key);
  selectedDraftKey = null; dirty = 0; unresolved = false;
  const remaining = (await draftRecords()).length;
  status(remaining ? "conflict" : "saved", remaining ? new Error("Other Work drafts remain available") : undefined);
}
export async function retryDraft({ expectedRevision, state: resolvedState } = {}) {
  const oldKey = selectedDraftKey || (await draftRecords())[0]?.key;
  const draft = oldKey ? await read(oldKey) : null;
  if (!draft) throw new Error("No Work draft");
  if (expectedRevision !== undefined && (!Number.isInteger(expectedRevision) || expectedRevision < 0)) throw new Error("Invalid revision");
  images = new Map(draft.images || []);
  state = copy(resolvedState ?? draft.state);
  revision = expectedRevision ?? draft.expected_revision; dirty = 1; initialized = true; unresolved = false;
  if (expectedRevision !== undefined || resolvedState !== undefined) {
    const requestId = crypto.randomUUID?.() || uid();
    selectedDraftKey = draftKey(pageId, requestId);
    supersedesDraftKey = oldKey;
    await put(selectedDraftKey, {
      owner: pageId, state: copy(state), images: [...images], expected_revision: revision,
      request_id: requestId, created: Date.now(),
    });
  } else {
    selectedDraftKey = oldKey;
  }
  return flush();
}
export const revisions = () => client().api("/api/work/revisions");
export async function restoreRevision(target) {
  await load(); writable();
  if (dirty || selectedDraftKey) throw new Error("Resolve Work draft before restore");
  const remote = await client().api("/api/work/restore", "POST", {
    request_id: crypto.randomUUID?.() || uid(), expected_revision: revision, revision: target,
  });
  if (remote.revision !== revision + 1) throw new Error("Work restore revision receipt mismatch");
  await accept(remote, true); return remote;
}
if (typeof document !== "undefined") {
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden" && timer) void flush().catch(() => {}); });
  addEventListener("pagehide", () => { if (timer) void flush().catch(() => {}); });
}
