import { test } from "node:test";
import assert from "node:assert/strict";
import { CHUNK_BYTES, bytesOf, sha256, uploadBlob, prepareCommit, verifyCommit } from "../work-sync.js";

function fakeServer() {
  let revision = 0, state = null, imageRows = [], interrupts = 0, hold = null, commitHook = null, rejectCommits = false;
  const chunks = new Map(), blobs = new Map(), receipts = new Map();
  const api = async (path, method = "GET", body) => {
    if (path.startsWith("/api/work/blobs/")) {
      const hash = path.split("/")[4], part = Buffer.from(body.content_base64, "base64");
      assert.ok(part.length <= CHUNK_BYTES);
      const slots = chunks.get(hash) || Array(body.total).fill(null);
      assert.equal(slots.length, body.total);
      if (slots[body.index]) assert.deepEqual(slots[body.index], part);
      slots[body.index] = part; chunks.set(hash, slots);
      const complete = slots.every(Boolean);
      const combined = complete ? Buffer.concat(slots) : null;
      if (combined) {
        assert.equal(await sha256(combined), hash);
        blobs.set(hash, combined);
      }
      if (hold) { const wait = hold; hold = null; wait.enter(); await wait.promise; }
      if (interrupts-- > 0) throw new Error("connection interrupted after receiving chunk");
      return { blob: hash, complete, received: slots.filter(Boolean).length, total: slots.length, size: combined?.length || 0 };
    }
    if (path === "/api/work/state") return { revision, initialized: !!state, state, images: imageRows };
    if (path === "/api/work/commit") {
      if (rejectCommits) throw Object.assign(new Error("service unavailable"), { status: 0 });
      const prior = receipts.get(body.request_id);
      if (prior) { assert.deepEqual(prior.body, body); return prior.response; }
      if (body.expected_revision !== revision) throw Object.assign(new Error("conflict"), { status: 409 });
      assert.ok(blobs.has(body.state_blob));
      for (const image of body.images) assert.ok(blobs.has(image.blob));
      state = JSON.parse(blobs.get(body.state_blob).toString("utf8"));
      imageRows = body.images.map(row => ({ ...row, size: blobs.get(row.blob).length }));
      revision++;
      const response = { revision, initialized: true, state, images: imageRows };
      receipts.set(body.request_id, { body, response });
      commitHook?.();
      return response;
    }
    throw new Error(path);
  };
  return {
    api,
    readImage: async id => {
      const row = imageRows.find(row => row.id === id);
      return blobs.get(row.blob);
    },
    interruptOnce: () => { interrupts = 1; },
    onCommit: fn => { commitHook = fn; },
    rejectCommits: value => { rejectCommits = value; },
    holdNextUpload: () => {
      let release, entered;
      const promise = new Promise(resolve => { release = resolve; });
      const reached = new Promise(resolve => { entered = resolve; });
      hold = { promise, enter: entered };
      return { reached, release };
    },
    tamperImage: id => { const row = imageRows.find(row => row.id === id); blobs.set(row.blob, Buffer.from("tampered")); },
  };
}

test("128 KiB transfer resumes after an acknowledged chunk is interrupted", async () => {
  const server = fakeServer();
  const bytes = bytesOf("ё".repeat(90000));
  server.interruptOnce();
  await assert.rejects(uploadBlob(server.api, bytes), /interrupted/);
  assert.equal(await uploadBlob(server.api, bytes), await sha256(bytes));
});

test("full state including unknown properties and orphan images survives commit and receipt retry", async () => {
  const server = fakeServer();
  const state = {
    version: 1, projects: [{ id: "p", unusual: { nested: [1, null, "ё"] } }],
    items: [{ id: "task", custom: true }],
    boards: [{ id: "general", items: [{ id: "image-item", type: "image", imageId: "used" }] }],
    view: { days: 7 }, futureProperty: { keep: "verbatim" },
  };
  const images = [
    ["used", new Blob([bytesOf("pictured")], { type: "image/png" })],
    ["orphan", new Blob([bytesOf("not referenced")], { type: "image/webp" })],
  ];
  const prepared = await prepareCommit(server.api, state, images);
  const body = { request_id: "same-id", expected_revision: 0, ...prepared };
  const receipt = await server.api("/api/work/commit", "POST", body);
  assert.deepEqual(await server.api("/api/work/commit", "POST", body), receipt);
  const hashes = new Map(prepared.images.map(row => [row.id, row.blob]));
  assert.equal((await verifyCommit(server.api, server.readImage, receipt, state, images, hashes)).revision, 1);
  assert.equal((await server.api("/api/work/state")).images.length, 2);
});

test("version conflict does not overwrite canonical state, and image corruption fails verification", async () => {
  const server = fakeServer(), state = { projects: [], items: [], extra: 42 };
  const images = [["orphan", new Blob(["raw"], { type: "image/png" })]];
  const prepared = await prepareCommit(server.api, state, images);
  const body = { request_id: "first", expected_revision: 0, ...prepared };
  const receipt = await server.api("/api/work/commit", "POST", body);
  await assert.rejects(server.api("/api/work/commit", "POST", { ...body, request_id: "other" }), { status: 409 });
  assert.deepEqual((await server.api("/api/work/state")).state, state);
  server.tamperImage("orphan");
  await assert.rejects(
    verifyCommit(server.api, server.readImage, receipt, state, images, new Map(prepared.images.map(row => [row.id, row.blob]))),
    /image bytes verification failed/,
  );
});

function installBrowserStorage(legacy, initialImages = [["used", new Blob(["pictured"], { type: "image/png" })], ["orphan", new Blob(["orphan"], { type: "image/webp" })]]) {
  const stores = new Map([
    ["kv", new Map([["state", legacy]])],
    ["images", new Map(initialImages)],
  ]);
  const database = {
    objectStoreNames: { contains: name => stores.has(name) },
    createObjectStore: name => { stores.set(name, new Map()); },
    transaction: names => {
      const tx = { pending: 0, done: false, oncomplete: null, onerror: null, onabort: null };
      const finish = () => {
        if (tx.pending || tx.done) return;
        setImmediate(() => { if (!tx.pending && !tx.done) { tx.done = true; tx.oncomplete?.(); } });
      };
      tx.objectStore = name => {
        const map = stores.get(name);
        const request = fn => {
          const r = { result: undefined, onsuccess: null };
          tx.pending++;
          setImmediate(() => {
            try { r.result = fn(); r.onsuccess?.(); }
            catch (error) { tx.error = error; tx.onerror?.(); }
            tx.pending--; finish();
          });
          return r;
        };
        return {
          get: key => request(() => map.get(key)),
          getAllKeys: () => request(() => [...map.keys()]),
          getAll: () => request(() => [...map.values()]),
          put: (value, key) => request(() => { map.set(key, value); return key; }),
          delete: key => request(() => map.delete(key)),
          clear: () => request(() => map.clear()),
        };
      };
      tx.abort = () => { tx.done = true; tx.onabort?.(); };
      finish();
      return tx;
    },
  };
  globalThis.indexedDB = {
    open: () => {
      const req = { result: database };
      setImmediate(() => { req.onupgradeneeded?.(); req.onsuccess?.(); });
      return req;
    },
  };
  const local = new Map();
  globalThis.localStorage = {
    getItem: key => local.get(key) ?? null,
    setItem: (key, value) => local.set(key, value),
    removeItem: key => local.delete(key),
  };
  globalThis.document = { addEventListener() {}, visibilityState: "visible" };
  globalThis.addEventListener = () => {};
  const channels = new Set(), messages = [];
  globalThis.BroadcastChannel = class {
    constructor() { channels.add(this); }
    postMessage(data) { messages.push([this, data]); }
  };
  const deliver = () => {
    for (const [source, data] of messages.splice(0)) for (const target of channels) if (source !== target) target.onmessage?.({ data });
  };
  const emit = data => { for (const target of channels) target.onmessage?.({ data }); };
  return { stores, deliver, emit };
}
const savedDrafts = browser => [...browser.stores.get("kv")].filter(([key]) => key.startsWith("draft:")).map(([, draft]) => draft);

test("two Work tabs migrate atomically, preserve legacy backup, and keep a conflicting draft", async () => {
  const legacy = {
    version: 1, projects: [{ id: "p", custom: { keep: true } }], items: [],
    boards: [{ id: "general", items: [{ id: "image-item", type: "image", imageId: "used" }] }],
    view: { days: 7 }, unknown: { nested: ["intact"] },
  };
  const browser = installBrowserStorage(legacy), server = fakeServer();
  const a = await import(`../work-store.js?tab=a-${Date.now()}`);
  a.setWorkTransport(server);
  await a.load();
  assert.equal(a.getRevision(), 1);
  assert.deepEqual(browser.stores.get("kv").get("state"), legacy);
  assert.deepEqual(browser.stores.get("kv").get("legacy-backup").state, legacy);
  assert.equal(browser.stores.get("images").size, 2);
  assert.equal((await server.api("/api/work/state")).images.length, 2);

  const b = await import(`../work-store.js?tab=b-${Date.now()}`);
  b.setWorkTransport(server);
  await b.load();
  a.update(state => { state.projects[0].title = "A"; });
  const firstFlush = a.flush();
  await a.flush(); // Export or pagehide may call flush while a save is in flight.
  await firstFlush;
  assert.equal((await server.api("/api/work/state")).revision, 2);
  let conflict;
  b.onSaveStatus((kind, error) => { if (kind === "conflict") conflict = error; });
  b.update(state => { state.projects[0].title = "B"; });
  await assert.rejects(b.flush(), { status: 409 });
  await assert.rejects(b.flush(), /Resolve the unconfirmed Work draft/);
  assert.ok(conflict);
  assert.equal(b.get().projects[0].title, "B");
  assert.equal(savedDrafts(browser)[0].state.projects[0].title, "B");
  assert.equal((await server.api("/api/work/state")).state.projects[0].title, "A");
  await b.retryDraft({ expectedRevision: 2, state: b.get() });
  assert.equal((await server.api("/api/work/state")).state.projects[0].title, "B");
  assert.deepEqual(browser.stores.get("kv").get("legacy-backup").state, legacy);
  browser.deliver();
});

test("a second already open tab stays read-only during migration and adopts the verified revision", async () => {
  const legacy = {
    version: 1, projects: [{ id: "locked" }], items: [],
    boards: [{ id: "general", items: [] }], view: { days: 7 },
  };
  const browser = installBrowserStorage(legacy), server = fakeServer();
  const gate = server.holdNextUpload();
  const a = await import(`../work-store.js?tab=lock-a-${Date.now()}`);
  a.setWorkTransport(server);
  const migrating = a.load();
  await gate.reached;
  const b = await import(`../work-store.js?tab=lock-b-${Date.now()}`);
  b.setWorkTransport(server);
  await b.load();
  assert.equal(b.isWritable(), false);
  assert.throws(() => b.update(s => s.projects.push({ id: "forbidden" })), /initialized|migration/);
  assert.deepEqual(browser.stores.get("kv").get("state"), legacy);
  gate.release();
  await migrating;
  browser.deliver();
  for (let i = 0; i < 30 && b.getRevision() === 0; i++) await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(b.getRevision(), 1);
  assert.equal(b.isWritable(), true);
});

test("migration aborts when a legacy tab changes source after snapshot and before commit", async () => {
  const legacy = {
    version: 1, projects: [{ id: "original" }], items: [],
    boards: [{ id: "general", items: [] }], view: { days: 7 },
  };
  const browser = installBrowserStorage(legacy), server = fakeServer();
  const gate = server.holdNextUpload();
  const a = await import(`../work-store.js?tab=source-change-${Date.now()}`);
  a.setWorkTransport(server);
  let failure;
  a.onSaveStatus((kind, error) => { if (kind === "conflict") failure = error; });
  const migrating = a.load();
  await gate.reached;
  const changed = structuredClone(legacy);
  changed.projects.push({ id: "late-edit" });
  browser.stores.get("kv").set("state", changed); // Old tab without the new lock protocol.
  gate.release();
  await migrating;
  assert.match(failure.message, /changed during migration/);
  assert.equal((await server.api("/api/work/state")).initialized, false);
  assert.deepEqual(a.get(), changed);
  assert.deepEqual(browser.stores.get("kv").get("legacy-backup").state, legacy);
  assert.equal(savedDrafts(browser).length, 0);
  await a.discardDraft();
  await a.retryLoad();
  assert.deepEqual((await server.api("/api/work/state")).state, changed);
});

test("late legacy write after server receipt prevents source switch and retains recoverable draft", async () => {
  const legacy = {
    version: 1, projects: [{ id: "before" }], items: [],
    boards: [{ id: "general", items: [] }], view: { days: 7 },
  };
  const browser = installBrowserStorage(legacy), server = fakeServer();
  const changed = structuredClone(legacy);
  changed.projects.push({ id: "after" });
  server.onCommit(() => browser.stores.get("kv").set("state", changed));
  const a = await import(`../work-store.js?tab=late-write-${Date.now()}`);
  a.setWorkTransport(server);
  let conflict;
  a.onSaveStatus((kind, error) => { if (kind === "conflict") conflict = error; });
  await a.load();
  assert.match(conflict.message, /changed during migration/);
  assert.equal((await server.api("/api/work/state")).initialized, true);
  assert.deepEqual(a.get(), changed);
  assert.equal(a.isWritable(), false);
  assert.deepEqual(savedDrafts(browser)[0].state, legacy);
});

test("JSON replacement commits complete new state and image set while legacy remains intact", async () => {
  const legacy = {
    version: 1, projects: [{ id: "old" }], items: [],
    boards: [{ id: "general", items: [] }], view: { days: 7 },
  };
  const browser = installBrowserStorage(legacy), server = fakeServer();
  const a = await import(`../work-store.js?tab=import-${Date.now()}`);
  a.setWorkTransport(server);
  await a.load();
  const imported = {
    version: 1, projects: [{ id: "new", arbitrary: { future: [1, "yes"] } }], items: [],
    boards: [{ id: "general", items: [{ id: "picture", type: "image", imageId: "new-image" }] }],
    view: { days: 14 }, extensionField: { untouched: true },
  };
  await a.replaceWithImages(imported, [["new-image", new Blob(["new pixels"], { type: "image/png" })]]);
  const remote = await server.api("/api/work/state");
  assert.deepEqual(remote.state, imported);
  assert.deepEqual(remote.images.map(row => row.id), ["new-image"]);
  assert.deepEqual((await a.imgAll()).map(([id]) => id), ["new-image"]);
  assert.deepEqual(browser.stores.get("kv").get("state"), legacy);
  assert.equal(browser.stores.get("images").size, 2);
});

test("empty desktop storage stays read-only until explicit initializeEmpty", async () => {
  const state = {
    version: 1, projects: [], items: [],
    boards: [{ id: "general", view: { x: 40, y: 28, z: 1 }, items: [] }],
    view: { board: "general", days: 7, calH: null },
  };
  installBrowserStorage(state, []);
  const server = fakeServer();
  const desktop = await import(`../work-store.js?tab=desktop-empty-${Date.now()}`);
  desktop.setWorkTransport(server);
  await desktop.load();
  assert.equal(desktop.isInitialized(), false);
  assert.throws(() => desktop.update(s => s.projects.push({ id: "bad" })), /not initialized/);
  assert.equal((await server.api("/api/work/state")).initialized, false);
  await desktop.initializeEmpty();
  assert.equal(desktop.isInitialized(), true);
  assert.equal((await server.api("/api/work/state")).revision, 1);
});

test("two failed tabs retain independent state and image drafts after shared cache replacement", async () => {
  const legacy = {
    version: 1, projects: [{ id: "p" }], items: [],
    boards: [{ id: "general", items: [] }], view: { days: 7 },
  };
  const browser = installBrowserStorage(legacy, []), server = fakeServer();
  const a = await import(`../work-store.js?tab=draft-a-${Date.now()}`);
  a.setWorkTransport(server); await a.load();
  const b = await import(`../work-store.js?tab=draft-b-${Date.now()}`);
  b.setWorkTransport(server); await b.load();
  server.rejectCommits(true);
  const stateA = structuredClone(legacy), stateB = structuredClone(legacy);
  stateA.projects[0].title = "draft A";
  stateA.boards[0].items.push({ id: "card-a", type: "image", imageId: "image-a" });
  stateB.projects[0].title = "draft B";
  stateB.boards[0].items.push({ id: "card-b", type: "image", imageId: "image-b" });
  await assert.rejects(a.replaceWithImages(stateA, [["image-a", new Blob(["alpha pixels"], { type: "image/png" })]]), { status: 0 });
  await assert.rejects(b.replaceWithImages(stateB, [["image-b", new Blob(["beta pixels"], { type: "image/webp" })]]), { status: 0 });
  const drafts = savedDrafts(browser);
  assert.equal(drafts.length, 2);
  assert.deepEqual(new Set(drafts.map(d => d.state.projects[0].title)), new Set(["draft A", "draft B"]));
  assert.deepEqual(new Set(drafts.map(d => d.images[0][0])), new Set(["image-a", "image-b"]));
  assert.equal(browser.stores.get("canonical-images").size, 0);

  const recovery = await import(`../work-store.js?tab=draft-recovery-${Date.now()}`);
  recovery.setWorkTransport(server); await recovery.load();
  const keys = await recovery.listDrafts();
  assert.equal(keys.length, 2);
  for (const entry of keys) {
    const record = await recovery.recoverDraft(entry.key);
    const [[id, blob]] = await recovery.imgAll();
    assert.equal(id, record.images[0][0]);
    assert.equal(await blob.text(), await record.images[0][1].text());
  }
  server.rejectCommits(false);
  const chosen = await recovery.recoverDraft(keys[0].key);
  await recovery.retryDraft({ expectedRevision: 1, state: chosen.state });
  const remaining = await recovery.listDrafts();
  assert.equal(remaining.length, 1);
  assert.equal(remaining[0].key, keys[1].key);
  const leftover = await recovery.recoverDraft(remaining[0].key);
  assert.equal((await recovery.imgAll())[0][0], leftover.images[0][0]);
});

test("blocked IndexedDB v2 upgrade reports an error and retryLoad succeeds after old tab closes", async () => {
  const empty = {
    version: 1, projects: [], items: [],
    boards: [{ id: "general", view: { x: 40, y: 28, z: 1 }, items: [] }],
    view: { board: "general", days: 7, calH: null },
  };
  installBrowserStorage(empty, []);
  const normalOpen = indexedDB.open;
  let first = true;
  indexedDB.open = (...args) => {
    if (!first) return normalOpen(...args);
    first = false;
    const blocked = {};
    setImmediate(() => blocked.onblocked?.());
    return blocked;
  };
  const server = fakeServer();
  const tab = await import(`../work-store.js?tab=upgrade-${Date.now()}`);
  tab.setWorkTransport(server);
  let error;
  tab.onSaveStatus((kind, detail) => { if (kind === "error") error = detail; });
  await tab.load();
  assert.match(error.message, /Close older Work tabs/);
  assert.equal(tab.isWritable(), false);
  await tab.retryLoad();
  assert.deepEqual(tab.get(), empty);
  assert.equal(tab.isInitialized(), false);
});

test("migration broadcast lock expires after owner crash and unlocks the tab", async () => {
  const legacy = {
    version: 1, projects: [{ id: "p" }], items: [],
    boards: [{ id: "general", items: [] }], view: { days: 7 },
  };
  const browser = installBrowserStorage(legacy, []), server = fakeServer();
  const tab = await import(`../work-store.js?tab=expired-lock-${Date.now()}`);
  tab.setWorkTransport(server); await tab.load();
  const changes = [];
  tab.onMigrationChange(value => changes.push(value));
  const until = Date.now() + 30;
  localStorage.setItem("work-canvas:migration-lock", JSON.stringify({ owner: "dead-tab", until }));
  browser.emit({ from: "dead-tab", migration: true, until });
  assert.equal(tab.isWritable(), false);
  await new Promise(resolve => setTimeout(resolve, 65));
  assert.equal(tab.isWritable(), true);
  assert.deepEqual(changes.slice(-2), [true, false]);
});

test("imgPut during another commit keeps its own durable draft and commits next revision", async () => {
  const legacy = {
    version: 1, projects: [{ id: "p" }], items: [],
    boards: [{ id: "general", items: [] }], view: { days: 7 },
  };
  const browser = installBrowserStorage(legacy, []), server = fakeServer();
  const tab = await import(`../work-store.js?tab=overlap-${Date.now()}`);
  tab.setWorkTransport(server); await tab.load();
  const gate = server.holdNextUpload();
  tab.update(s => { s.projects[0].title = "first"; });
  const first = tab.flush();
  await gate.reached;
  await tab.imgPut("late", new Blob(["late pixels"], { type: "image/png" }));
  tab.update(s => { s.boards[0].items.push({ id: "late-card", type: "image", imageId: "late" }); });
  assert.equal(savedDrafts(browser).length, 2);
  gate.release();
  await first;
  await tab.flush();
  const remote = await server.api("/api/work/state");
  assert.equal(remote.revision, 3);
  assert.equal(remote.images[0].id, "late");
  assert.equal(remote.state.boards[0].items[0].imageId, "late");
  assert.equal(savedDrafts(browser).length, 0);
});
