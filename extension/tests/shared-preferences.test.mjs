import test from "node:test";
import assert from "node:assert/strict";
import { syncPreferences } from "../shared-preferences.js";

const PENDING = "shared:preferences-pending", CONFLICT = "shared:preferences-conflict";
const clone = value => structuredClone(value);
const tick = () => new Promise(resolve => setImmediate(resolve));
async function until(predicate) {
  for (let i = 0; i < 100; i++) { if (predicate()) return; await tick(); }
  assert.fail("condition was not reached");
}
function fakeChrome(initial = {}) {
  const values = { ...initial }, listeners = new Set();
  const local = {
    async get(keys) {
      const names = typeof keys === "string" ? [keys] : keys;
      return Object.fromEntries(names.map(key => [key, clone(values[key])]));
    },
    async set(patch) {
      const changes = {};
      for (const [key, value] of Object.entries(patch)) {
        if (JSON.stringify(values[key]) !== JSON.stringify(value)) changes[key] = { oldValue: clone(values[key]), newValue: clone(value) };
        values[key] = clone(value);
      }
      // Chrome resolves the Promise before the event is dispatched.
      if (Object.keys(changes).length) setImmediate(() => { for (const listener of listeners) listener(changes, "local"); });
    },
  };
  return {
    values, userSet: patch => local.set(patch),
    storage: { local, onChanged: { addListener: fn => listeners.add(fn), removeListener: fn => listeners.delete(fn) } },
  };
}
function fakeServer(initial = { revision: 1, values: { "panel:theme": "dark" } }) {
  let state = clone(initial), throwAfterCommit = false;
  const calls = [], receipts = new Map();
  const api = async (path, method = "GET", body) => {
    assert.equal(path, "/api/ui/preferences");
    if (method === "GET") return clone(state);
    assert.equal(method, "POST");
    calls.push(clone(body));
    const prior = receipts.get(body.request_id);
    if (prior) { assert.deepEqual(body, prior.body); return clone(prior.result); }
    if (body.expected_revision !== state.revision) throw Object.assign(new Error("version conflict"), { status: 409 });
    state = { revision: state.revision + 1, values: { ...state.values, ...body.patch } };
    receipts.set(body.request_id, { body: clone(body), result: clone(state) });
    if (throwAfterCommit) { throwAfterCommit = false; throw Object.assign(new Error("response lost"), { status: 0 }); }
    return clone(state);
  };
  return {
    api, calls, get state() { return clone(state); },
    loseNextResponse: () => { throwAfterCommit = true; },
    externalChange: patch => { state = { revision: state.revision + 1, values: { ...state.values, ...patch } }; },
  };
}

test("an unknown POST result retries the exact request ID and expected revision", async () => {
  const chrome = fakeChrome(), server = fakeServer();
  const sync = syncPreferences(server.api, chrome);
  try {
    await until(() => chrome.values["panel:theme"] === "dark");
    await tick(); await tick();
    server.loseNextResponse();
    await chrome.userSet({ "panel:theme": "light" });
    await until(() => server.calls.length === 1);
    assert.equal(chrome.values[PENDING].length, 1);
    const first = clone(server.calls[0]);
    assert.equal(first.expected_revision, 1);
    assert.equal(server.state.values["panel:theme"], "light");
    await sync.pollNow();
    assert.deepEqual(server.calls[1], first);
    assert.deepEqual(chrome.values[PENDING], []);
    assert.equal(chrome.values["panel:theme"], "light");
  } finally { sync.stop(); }
});

test("409 keeps the local choice and explicit keep retries against latest revision", async () => {
  const chrome = fakeChrome(), server = fakeServer();
  const sync = syncPreferences(server.api, chrome);
  try {
    await until(() => chrome.values["panel:theme"] === "dark");
    await tick(); await tick();
    server.externalChange({ "panel:design": "new" });
    await chrome.userSet({ "panel:theme": "light" });
    await until(() => chrome.values[CONFLICT] === true);
    assert.equal(chrome.values["panel:theme"], "light");
    assert.equal(chrome.values[PENDING].length, 1);
    const failed = server.calls[0];
    await sync.resolve(true);
    assert.equal(server.calls[1].expected_revision, 2);
    assert.notEqual(server.calls[1].request_id, failed.request_id);
    assert.equal(server.state.values["panel:theme"], "light");
    assert.equal(chrome.values[CONFLICT], false);
    assert.deepEqual(chrome.values[PENDING], []);
  } finally { sync.stop(); }
});

test("discard applies remote choice without an asynchronous storage feedback POST", async () => {
  const chrome = fakeChrome(), server = fakeServer();
  const sync = syncPreferences(server.api, chrome);
  try {
    await until(() => chrome.values["panel:theme"] === "dark");
    await tick(); await tick();
    server.externalChange({ "panel:theme": "blue" });
    await chrome.userSet({ "panel:theme": "light" });
    await until(() => chrome.values[CONFLICT] === true);
    assert.equal(chrome.values["panel:theme"], "light");
    await sync.resolve(false);
    await tick(); await tick(); await tick();
    assert.equal(chrome.values["panel:theme"], "blue");
    assert.deepEqual(chrome.values[PENDING], []);
    assert.equal(server.calls.length, 1);
    assert.equal(chrome.values[CONFLICT], false);
  } finally { sync.stop(); }
});

test("initial remote apply emits storage.onChanged after set resolves without echoing to server", async () => {
  const chrome = fakeChrome(), server = fakeServer();
  const sync = syncPreferences(server.api, chrome);
  try {
    await until(() => chrome.values["panel:theme"] === "dark");
    await tick(); await tick(); await tick();
    assert.equal(server.calls.length, 0);
    assert.deepEqual(chrome.values[PENDING] || [], []);
  } finally { sync.stop(); }
});

test("a real local change racing the delayed remote event is still sent once", async () => {
  const chrome = fakeChrome(), server = fakeServer();
  const sync = syncPreferences(server.api, chrome);
  try {
    await until(() => chrome.values["panel:theme"] === "dark");
    await chrome.userSet({ "panel:theme": "light" });
    await until(() => server.state.values["panel:theme"] === "light");
    await tick(); await tick();
    assert.equal(server.calls.length, 1);
    assert.equal(server.calls[0].patch["panel:theme"], "light");
    assert.deepEqual(chrome.values[PENDING], []);
  } finally { sync.stop(); }
});

test("an existing Chrome color migrates once into an already initialized shared state", async () => {
  const chrome = fakeChrome({"panel:focus-overlay-color":"cyan"}), server = fakeServer();
  const sync = syncPreferences(server.api, chrome);
  try {
    await until(() => server.state.values["panel:focus-overlay-color"] === "cyan");
    await tick(); await tick();
    assert.equal(server.calls.length, 1);
    await sync.pollNow(); await tick();
    assert.equal(server.calls.length, 1);
  } finally { sync.stop(); }
});

test("existing remote timer color wins over stale local choice without feedback writes", async () => {
  const chrome = fakeChrome({"panel:focus-overlay-color":"pink"});
  const server = fakeServer({revision:4,values:{"panel:focus-overlay-color":"lime"}});
  const sync = syncPreferences(server.api, chrome);
  try {
    await until(() => chrome.values["panel:focus-overlay-color"] === "lime");
    await tick(); await tick();
    assert.equal(server.calls.length, 0);
  } finally { sync.stop(); }
});
