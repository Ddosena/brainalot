import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import { WORK_ENABLED, WORK_PAUSED_MESSAGE, browserActionAllowed, workCommandDisabled } from "../work-availability.js";

const source = file => readFile(new URL(`../${file}`, import.meta.url), "utf8");

test("Work entry cannot load its old app or migrate IndexedDB while paused", async () => {
  assert.equal(WORK_ENABLED, false);
  const page = await source("work.html");
  assert.match(page, /<script type="module" src="work-paused\.js"><\/script>/);
  assert.doesNotMatch(page, /<script[^>]+src="work-app\.js"/);
  assert.match(page, /Ваши задачи, доски и изображения сохранены/);
  const prior = { chrome: globalThis.chrome, document: globalThis.document, indexedDB: globalThis.indexedDB };
  try {
    globalThis.chrome = { storage: { local: { get: async () => ({}) } } };
    globalThis.document = { documentElement: { dataset: {} }, querySelectorAll: () => [] };
    Object.defineProperty(globalThis, "indexedDB", { configurable: true, get() { throw new Error("Work data was opened"); } });
    await import(`../work-paused.js?gate=${Date.now()}`);
  } finally {
    globalThis.chrome = prior.chrome;
    globalThis.document = prior.document;
    Object.defineProperty(globalThis, "indexedDB", { configurable: true, value: prior.indexedDB, writable: true });
  }
});

test("old Work and chat commands are blocked before they reach the service", async () => {
  assert.match(WORK_PAUSED_MESSAGE, /сохранены/);
  for (const type of ["work-snapshot", "work-proposals", "work-proposal-resolve", "chats-status",
    "chats-sync", "chats-remove", "chat-setup", "chat-connect"]) {
    assert.equal(workCommandDisabled(type), true, type);
  }
  assert.equal(workCommandDisabled("capture"), false);
  const worker = await source("background.js");
  assert.match(worker, /if \(workCommandDisabled\(message\.type\)\) throw/);
  assert.match(worker, /startBrowserBridge\(api, async \(action, payload\) => \{\s+if \(!browserActionAllowed\(action\)\) throw/);
  assert.match(worker, /unregisterContentScripts/);
  assert.match(worker, /brainalot-chat-stop/);
  assert.equal(browserActionAllowed("active-tab"), true);
  for (const action of ["open-workspace", "stop-workspace", "capture-workspace", "chat-setup", "chat-connect"]) {
    assert.equal(browserActionAllowed(action), false, action);
  }
  const desktop = await source("desktop.html");
  assert.match(desktop, /data-view="work" hidden/);
  assert.match(await source("desktop.js"), /view === "work" && !WORK_ENABLED/);
  assert.match(await source("desktop.js"), /\[data-view="work"\]'\)\.hidden = !WORK_ENABLED/);
  assert.match(await source("time-ui.js"), /task\.source !== "work"/);
});

test("an injected reader stops observing and sending after the pause command", async () => {
  const listeners = new Set(), sent = [], events = new Map();
  let observer;
  class Observer {
    constructor(callback) { this.callback = callback; this.disconnected = false; observer = this; }
    observe() {}
    disconnect() { this.disconnected = true; }
  }
  const chrome = { runtime: { id: "brainalot", onMessage: {
    addListener: fn => listeners.add(fn), removeListener: fn => listeners.delete(fn),
  }, sendMessage: async message => { sent.push(message); return { ok: true, data: { watched: true } }; } } };
  const context = { chrome, document: { documentElement: {} }, location: { href: "https://vk.com/im" },
    BrainalotChatParser: { peer: () => null, title: () => "", messages: () => [], skeleton: () => ({}) },
    MutationObserver: Observer, addEventListener: (name, fn) => events.set(name, fn),
    removeEventListener: name => events.delete(name), clearTimeout, setTimeout, Date, Set,
  };
  context.globalThis = context;
  vm.runInNewContext(await source("chat-reader.js"), context);
  assert.equal(listeners.size, 1);
  assert.equal(context.__brainalotChatReader, true);
  for (const listener of [...listeners]) listener({ type: "brainalot-chat-stop" }, { id: "brainalot" }, () => {});
  assert.equal(observer.disconnected, true);
  assert.equal(listeners.size, 0);
  assert.equal(events.has("hashchange"), false);
  assert.equal(context.__brainalotChatReader, false);
  observer.callback();
  assert.equal(sent.length, 0);
});
