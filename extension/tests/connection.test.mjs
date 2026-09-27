import test from "node:test";
import assert from "node:assert/strict";
import { ApiError } from "../api.js";
import { createConnection } from "../connection.js";

function fakeChrome({ answer = { ok: true, token: "host-token", port: 8770 }, lastError = null, stored = {} } = {}) {
  const store = { ...stored };
  const calls = [];
  return {
    calls, store,
    runtime: {
      lastError: null,
      sendNativeMessage(host, message, callback) {
        calls.push(message.action);
        this.lastError = lastError;
        callback(lastError ? undefined : answer);
        this.lastError = null;
      },
    },
    storage: { local: {
      async get(key) { return { [key]: store[key] }; },
      async set(values) { Object.assign(store, values); },
    } },
  };
}

test("without a token the host's port and token are saved and used", async () => {
  const chrome = fakeChrome();
  const seen = [];
  const connection = createConnection(chrome, { request: async (path, options) => { seen.push(options); return { ok: 1 }; } });
  assert.deepEqual(await connection.api("/api/dashboard"), { ok: 1 });
  assert.deepEqual(chrome.calls, ["hello"]);
  assert.equal(seen[0].token, "host-token");
  assert.equal(seen[0].port, 8770);
  assert.equal(chrome.store.connection.auto, true);
});

test("a rejected token is replaced from the host and the request repeated", async () => {
  const chrome = fakeChrome({ stored: { connection: { token: "old", port: 8765 } } });
  const tokens = [];
  const connection = createConnection(chrome, { request: async (path, { token }) => {
    tokens.push(token);
    if (token === "old") throw new ApiError("no", 401);
    return "fine";
  } });
  assert.equal(await connection.api("/api/notes/x", "PATCH", {}), "fine");
  assert.deepEqual(tokens, ["old", "host-token"]);
});

test("a write that may have reached the same service is not repeated", async () => {
  const chrome = fakeChrome({ answer: { ok: true, token: "same", port: 8765 }, stored: { connection: { token: "same", port: 8765 } } });
  let attempts = 0;
  const connection = createConnection(chrome, { request: async () => { attempts += 1; throw new ApiError("timeout", 0); } });
  await assert.rejects(connection.api("/api/captures", "POST", {}), /timeout/);
  assert.equal(attempts, 1);
});

test("a missing host leaves the manual token path working", async () => {
  const chrome = fakeChrome({ lastError: { message: "Specified native messaging host not found." } });
  const connection = createConnection(chrome, { request: async (path, { token }) => { if (!token) throw new ApiError("no token", 401); } });
  await assert.rejects(connection.api("/api/dashboard"), /no token/);
  assert.equal((await connection.current()).token, undefined);
});

test("the host is not asked again and again while the service is down", async () => {
  let clock = 0;
  const chrome = fakeChrome({ stored: { connection: { token: "t", port: 8765 } }, answer: { ok: false } });
  const connection = createConnection(chrome, { now: () => clock, request: async () => { throw new ApiError("down", 0); } });
  await assert.rejects(connection.api("/api/dashboard"));
  await assert.rejects(connection.api("/api/dashboard"));
  assert.deepEqual(chrome.calls, ["hello"]);
  clock = 20000;
  await assert.rejects(connection.api("/api/dashboard"));
  assert.deepEqual(chrome.calls, ["hello", "hello"]);
});

test("a missing installation is detectable and automatic probes are bounded until explicit retry",async()=>{
  const chrome=fakeChrome({lastError:{message:"Specified native messaging host not found."}});
  const connection=createConnection(chrome,{now:()=>0});
  await connection.current();
  await connection.current();
  assert.equal(connection.hostMissing,true);
  assert.equal(chrome.calls.length,1);
  await connection.connect(true);
  assert.equal(chrome.calls.length,2);
});
