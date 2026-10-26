import { test } from "node:test";
import assert from "node:assert/strict";
import { daySummary, builderDaySummary, taskRemaining, clampDate, preserveTimedCubeBeforeSegment } from "../work-planning.js";

test("day totals keep calendar, planned, credited and actual time distinct", () => {
  const day = { date: "2030-03-04", fixed_minutes: 105 };
  const cubes = [{ id: "one", date: day.date, minutes: 90 }, { id: "two", date: day.date, minutes: 60 }];
  const done = { one: { percent: 50, actual_minutes: 72 }, two: { percent: 100, actual_minutes: 80 } };
  assert.deepEqual(daySummary(day, cubes, done), {
    planned: 150, credited: 105, actual: 152, fixed: 105, total: 255, free: 45, unfinished: 45,
  });
  assert.equal(clampDate("2030-03-01", "2030-03-02", "2030-03-20"), "2030-03-02");
  assert.equal(clampDate("2030-03-21", "2030-03-02", "2030-03-20"), "2030-03-20");
  const reviewDay = { ...day, weekly_review_minutes: 60, total_work_minutes: 315 };
  assert.equal(daySummary(reviewDay, cubes, done).total, 315);
  assert.equal(daySummary(reviewDay, cubes, done).free, 0);
});

test("task remaining prefers explicit daily outcome without treating actual minutes as credit", () => {
  const task = { id: "t", estimate_minutes: { central: 240 } };
  const plan = { completed_cubes: { c: { task_id: "t", planned_minutes: 90, percent: 50, actual_minutes: 120 } }, day_outcomes: [] };
  assert.equal(taskRemaining(task, plan), 195);
  plan.day_outcomes.push({ task_id: "t", date: "2030-03-02", remaining_minutes: 80, done: false });
  assert.equal(taskRemaining(task, plan), 80);
  plan.day_outcomes.push({ task_id: "t", date: "2030-03-03", remaining_minutes: 0, done: true });
  assert.equal(taskRemaining(task, plan), 0);
  plan.result = { tasks: [{ id: "t", remaining_minutes: 37 }] };
  assert.equal(taskRemaining(task, plan), 37);
});

test("builder energy excludes unselected AUTO forecast and keeps a 45-minute opening", () => {
  const date = "2030-03-04";
  const forecast = [{ id: "auto", date, minutes: 90, progress_percent: 0 }];
  const day = { date, fixed_minutes: 255, weekly_review_minutes: 0, builder_selected_minutes: 0,
    builder_total_work_minutes: 255, builder_available_minutes: 45, total_work_minutes: 345 };
  const empty = builderDaySummary(day, forecast, {}, 300);
  assert.equal(empty.selected.length, 0);
  assert.equal(empty.total, 255);
  assert.equal(empty.free, 45);
  const chosen = { ...day, builder_selected_minutes: 45, builder_total_work_minutes: 300,
    builder_available_minutes: 0 };
  const manual = { id: "manual", date, manual: true, minutes: 45 };
  const filled = builderDaySummary(chosen, [...forecast, manual], {}, 300);
  assert.deepEqual(filled.selected.map(c => c.id), ["manual"]);
  assert.equal(filled.total, 300);
  assert.equal(filled.free, 0);
});

test("segment 100 saves measured cube minutes before releasing unstarted reservations", async () => {
  const planning = { result: { cubes: [{ id: "cube", segment_id: "stage", minutes: 90,
    progress_percent: 0, actual_minutes: 0, manual: true }] }, completed_cubes: {} };
  const calls = [];
  let running = true, reserved = true;
  const action = async (name, data) => {
    calls.push(name);
    if (name === "cube_progress") planning.completed_cubes.cube = { percent: data.percent, actual_minutes: data.actual_minutes };
    if (name === "segment_progress") {
      if (!planning.completed_cubes.cube?.actual_minutes) reserved = false;
    }
  };
  await preserveTimedCubeBeforeSegment(planning, "stage", {
    timerState: () => ({ status: running ? "running" : "idle" }),
    stopTimer: async () => { running = false; calls.push("stop"); },
    recordedMinutes: async () => 20,
    action,
  });
  await action("segment_progress", { segment_id: "stage", percent: 100 });
  assert.deepEqual(calls, ["stop", "cube_progress", "segment_progress"]);
  assert.deepEqual(planning.completed_cubes.cube, { percent: 0, actual_minutes: 20 });
  assert.equal(reserved, true);

  // An older or shorter timer result must never overwrite a larger saved fact.
  planning.completed_cubes.cube.actual_minutes = 25;
  running = true;
  calls.length = 0;
  await preserveTimedCubeBeforeSegment(planning, "stage", {
    timerState: () => ({ status: running ? "paused" : "idle" }),
    stopTimer: async () => { running = false; calls.push("stop"); },
    recordedMinutes: async () => 20,
    action,
  });
  assert.deepEqual(calls, ["stop"]);
  assert.equal(planning.completed_cubes.cube.actual_minutes, 25);

  running = true;
  calls.length = 0;
  await assert.rejects(preserveTimedCubeBeforeSegment(planning, "stage", {
    timerState: () => ({ status: running ? "running" : "idle" }),
    stopTimer: async () => { running = false; calls.push("stop"); },
    recordedMinutes: async () => 30,
    action: async name => { calls.push(name); throw new Error("CAS conflict"); },
  }), /CAS conflict/);
  assert.deepEqual(calls, ["stop", "cube_progress"]);
});

function browserStorage() {
  const stores = new Map([["kv", new Map()], ["images", new Map()]]);
  const database = {
    objectStoreNames: { contains: name => stores.has(name) },
    createObjectStore: name => stores.set(name, new Map()),
    transaction: () => {
      const tx = { pending: 0, done: false, oncomplete: null, onerror: null, onabort: null };
      const finish = () => {
        if (!tx.pending && !tx.done) setImmediate(() => {
          if (!tx.pending && !tx.done) { tx.done = true; tx.oncomplete?.(); }
        });
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
      finish(); return tx;
    },
  };
  globalThis.indexedDB = { open: () => {
    const r = { result: database };
    setImmediate(() => { r.onupgradeneeded?.(); r.onsuccess?.(); });
    return r;
  } };
  const local = new Map();
  globalThis.localStorage = { getItem: key => local.get(key) ?? null,
    setItem: (key, value) => local.set(key, value), removeItem: key => local.delete(key) };
  globalThis.document = { addEventListener() {}, visibilityState: "visible" };
  globalThis.addEventListener = () => {};
  globalThis.BroadcastChannel = class { postMessage() {} };
  return stores;
}

function planningServer() {
  let revision = 0, dropOnce = false, hold = null, periodChange = null;
  let state = { version: 1, projects: [{ id: "kept" }], items: [], boards: [], view: {},
    planning: { version: 1, settings: {}, result: { days: [], cubes: [] }, completed_cubes: {} } };
  const blobs = new Map(), chunks = new Map(), receipts = new Map();
  const snapshot = () => ({ revision, initialized: true, state: structuredClone(state), images: [] });
  const api = async (path, method = "GET", body) => {
    if (path === "/api/work/state") return snapshot();
    if (path.startsWith("/api/work/blobs/")) {
      const hash = path.split("/")[4];
      const parts = chunks.get(hash) || Array(body.total).fill(null);
      parts[body.index] = Buffer.from(body.content_base64, "base64"); chunks.set(hash, parts);
      if (parts.every(Boolean)) blobs.set(hash, Buffer.concat(parts));
      return { blob: hash, complete: blobs.has(hash), received: parts.filter(Boolean).length,
        total: body.total, size: blobs.get(hash)?.length || 0 };
    }
    if (path === "/api/work/commit") {
      if (body.expected_revision !== revision) throw Object.assign(new Error("conflict"), { status: 409 });
      state = JSON.parse(blobs.get(body.state_blob).toString("utf8")); revision++;
      return snapshot();
    }
    if (path === "/api/work/planning") {
      const old = receipts.get(body.request_id);
      if (old) { assert.deepEqual(body, old.body); return old.response; }
      if (body.expected_revision !== revision) throw Object.assign(new Error("conflict"), { status: 409 });
      if (body.action === "day_budget" && body.data.minutes === 15) {
        throw Object.assign(new Error("selected cubes do not fit"), { status: 422 });
      }
      if (hold) { hold.enter(); await hold.promise; hold = null; }
      if (body.action === 'constructor_timeline') { /* The period namespace carries this placement. */ }
      else if (body.action === "day_budget") state = { ...state, planning: { ...state.planning,
        settings: { ...state.planning.settings, budget_minutes: body.data.minutes } } };
      else state = { ...state, planning: { ...state.planning,
        completed_cubes: { ...state.planning.completed_cubes, [body.data.cube_id]: {
          task_id: "synthetic", date: "2030-03-04", planned_minutes: 90, percent: body.data.percent,
          actual_minutes: body.data.actual_minutes ?? 0,
        } } } };
      if (periodChange) {state.period_planner = structuredClone(periodChange);periodChange = null;}
      revision++;
      const response = snapshot(); receipts.set(body.request_id, { body, response });
      if (dropOnce) { dropOnce = false; throw new Error("reply lost after commit"); }
      return response;
    }
    throw new Error(`unexpected ${path}`);
  };
  return { api, readImage: async () => { throw new Error("no images"); },
    snapshot, dropNextReply: () => { dropOnce = true; },
    changePeriodOnReply: value => {periodChange = value;},
    externalChange: () => { state.projects.push({ id: "other-tab" }); revision++; },
    holdNextPlanning: () => {
      let release, entered;
      const promise = new Promise(resolve => { release = resolve; });
      const reached = new Promise(resolve => { entered = resolve; });
      hold = { promise, enter: entered }; return { reached, release };
    },
  };
}

test('concurrent board save retains the Day response period placement', async () => {
  browserStorage();
  const server = planningServer();
  const store = await import(`../work-store.js?period-board=${Date.now()}`);
  store.setWorkTransport(server);
  await store.load();
  const placement = {placements: {c: {date: '2030-03-05', start: '15:00'}}};
  server.changePeriodOnReply(placement);
  const gate = server.holdNextPlanning();
  const action = store.planningAction('constructor_timeline', {cube_id: 'c', date: '2030-03-05', start: '15:00'});
  await gate.reached;
  store.update(state => state.projects.push({id: 'local-board-edit'}));
  const flushed = store.flush();
  gate.release();
  await action;
  await flushed;
  assert.equal(server.snapshot().state.projects.at(-1).id, 'local-board-edit');
  assert.deepEqual(server.snapshot().state.period_planner, placement);
  assert.deepEqual(store.get().period_planner, placement);
});

test("planning CAS preserves intervening board edits and retries the same durable request", async () => {
  const stores = browserStorage(), server = planningServer();
  const store = await import(`../work-store.js?planning=${Date.now()}`);
  store.setWorkTransport(server);
  await store.load();
  const gate = server.holdNextPlanning();
  const action = store.planningAction("cube_progress", { cube_id: "c", percent: 50, actual_minutes: 72 });
  await gate.reached;
  store.update(s => { s.projects.push({ id: "during-request" }); });
  const flushed = store.flush();
  gate.release();
  await action; await flushed;
  assert.equal(server.snapshot().revision, 2);
  assert.equal(server.snapshot().state.projects.at(-1).id, "during-request");
  assert.equal(server.snapshot().state.planning.completed_cubes.c.percent, 50);
  assert.equal(await store.getPendingPlanningDraft(), null);
  server.dropNextReply();
  await assert.rejects(store.planningAction("cube_progress", { cube_id: "c", percent: 75 }), /reply lost/);
  const draft = await store.getPendingPlanningDraft();
  assert.equal(draft.action, "cube_progress");
  assert.equal([...stores.get("kv")].filter(([key]) => key.startsWith("planning-draft:")).length, 1);
  await store.retryPlanningDraft();
  assert.equal(store.getRevision(), 3);
  assert.equal(store.get().planning.completed_cubes.c.percent, 75);
  assert.equal(await store.getPendingPlanningDraft(), null);
  assert.equal(server.snapshot().state.projects.at(-1).id, "during-request");
  server.externalChange();
  await assert.rejects(store.planningAction("cube_progress", { cube_id: "c", percent: 100 }), { status: 409 });
  assert.equal((await store.getPendingPlanningDraft()).action, "cube_progress");
  await assert.rejects(store.retryPlanningDraft(), { status: 409 });
  assert.equal((await store.getPendingPlanningDraft()).action, "cube_progress");
  await store.discardPlanningDraft();
  assert.equal(await store.getPendingPlanningDraft(), null);
  assert.equal(store.get().projects.at(-1).id, "other-tab");
  await assert.rejects(store.planningAction("day_budget", { date: "2030-03-04", minutes: 15 }), { status: 422 });
  assert.equal(await store.getPendingPlanningDraft(), null);
  await store.planningAction("day_budget", { date: "2030-03-04", minutes: 360 });
  assert.equal(store.get().planning.settings.budget_minutes, 360);
});
