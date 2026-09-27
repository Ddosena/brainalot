import test from "node:test";
import assert from "node:assert/strict";
import { createCaptureWindowController, DRAFT_KEY, matchesLibrary, normalizeDesign, normalizeDraft, popupBounds, POPUP_CONTEXT_KEY, writeDraft } from "../capture-window.js";
import { CaptureQueue } from "../queue.js";

function storage() {
  const values = new Map();
  return {
    async get(key) {
      if (key === null) return Object.fromEntries(values);
      if (Array.isArray(key)) return Object.fromEntries(key.map(item => [item, values.get(item)]));
      return { [key]: values.get(key) };
    },
    async set(items) { for (const [key, value] of Object.entries(items)) values.set(key, value); },
    async remove(key) { values.delete(key); },
  };
}

function chromeMock() {
  const local = storage();
  const popupUrl = "chrome-extension://mmm/capture.html";
  const normal = { id: 7, left: -1600, top: 60, width: 1100, height: 800, type: "normal" };
  const windows = [normal];
  const tabs = [{ id: 42, windowId: 7, active: true, url: "https://example.org/a", title: "Origin" }];
  let failCreate = false;
  const calls = { create: 0, removed: [], focused: [] };
  const chrome = {
    storage: { local },
    tabs: {
      async query({ windowId }) { return tabs.filter(item => item.windowId === windowId && item.active); },
      async get(id) { const tab = tabs.find(item => item.id === id); if (!tab) throw new Error("No tab"); return tab; },
    },
    windows: {
      async getAll() { return windows.filter(item => item.type === "popup"); },
      async get(id) { const win = windows.find(item => item.id === id); if (!win) throw new Error("No window"); return win; },
      async getLastFocused() { return normal; },
      async create(options) {
        calls.create++;
        if (failCreate) throw new Error("Window failed");
        const win = { id: 9, type: "popup", tabs: [{ id: 91, url: options.url }], ...options };
        windows.push(win); return win;
      },
      async update(id) { calls.focused.push(id); },
      async remove(id) { calls.removed.push(id); windows.splice(windows.findIndex(item => item.id === id), 1); },
    },
  };
  return { chrome, popupUrl, local, tabs, calls, setFailCreate: value => { failCreate = value; } };
}

test("popup stays centered on a negative monitor and clamps to a small browser window", () => {
  assert.deepEqual(popupBounds({ left: -1600, top: 60, width: 1100, height: 800 }), { left: -1330, top: 135, width: 560, height: 650 });
  assert.deepEqual(popupBounds({ left: 20, top: 30, width: 360, height: 390 }), { left: 20, top: 30, width: 360, height: 390 });
});

test("design family is independent and old or invalid saved values use stream", () => {
  for (const value of ["stream", "cabinet", "console", "glamour"]) assert.equal(normalizeDesign(value), value);
  assert.equal(normalizeDesign("dark"), "stream");
  assert.equal(normalizeDesign(undefined), "stream");
});

test("popup reuses the existing window across controller restart and retains original tab", async () => {
  const mock = chromeMock();
  const first = createCaptureWindowController(mock.chrome, mock.popupUrl);
  assert.deepEqual(await first.open({ url: "chrome-extension://mmm/panel.html" }), { windowId: 9, reused: false });
  const original = (await mock.local.get(POPUP_CONTEXT_KEY))[POPUP_CONTEXT_KEY];
  assert.deepEqual(original, { windowId: 7, tabId: 42 });
  mock.tabs.push({ id: 43, windowId: 7, active: true, url: "https://example.org/new", title: "New" });
  mock.tabs[0].active = false;
  const restarted = createCaptureWindowController(mock.chrome, mock.popupUrl);
  assert.deepEqual(await restarted.open({ url: "chrome-extension://mmm/panel.html" }), { windowId: 9, reused: true });
  assert.deepEqual((await mock.local.get(POPUP_CONTEXT_KEY))[POPUP_CONTEXT_KEY], original);
  assert.deepEqual(await restarted.activeTab({ url: mock.popupUrl }), { url: "https://example.org/a", title: "Origin" });
  await restarted.finish({ url: mock.popupUrl });
  assert.deepEqual(mock.calls.removed, [9]);
  assert.equal(mock.calls.focused.at(-1), 7);
});

test("failed popup creation can be retried", async () => {
  const mock = chromeMock();
  const controller = createCaptureWindowController(mock.chrome, mock.popupUrl);
  mock.setFailCreate(true);
  await assert.rejects(controller.open({ url: "chrome-extension://mmm/panel.html" }), /Window failed/);
  mock.setFailCreate(false);
  assert.equal((await controller.open({ url: "chrome-extension://mmm/panel.html" })).reused, false);
  assert.equal(mock.calls.create, 2);
});

test("draft survives ordinary close and late writes cannot restore it after success", async () => {
  const local = storage();
  const input = normalizeDraft({ text: "Не забыть", kind: "task", schedule: "week", requestId: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", revision: 1 });
  await writeDraft(local, input);
  assert.equal(normalizeDraft((await local.get(DRAFT_KEY))[DRAFT_KEY]).text, "Не забыть");
  await writeDraft(local, { ...input, revision: 3 }, true);
  await writeDraft(local, { ...input, text: "Старая версия", revision: 2 });
  const saved = normalizeDraft((await local.get(DRAFT_KEY))[DRAFT_KEY]);
  assert.equal(saved.completed, true);
  assert.equal(saved.text, "");
  await writeDraft(local, { ...input, text: "Новая мысль", requestId: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", revision: 4 });
  assert.equal((await local.get(DRAFT_KEY))[DRAFT_KEY].text, "Новая мысль");
});

test("retry uses one queue record and rejects changed payload under the same request id", async () => {
  const local = storage();
  const queue = new CaptureQueue({ storage: local, send: async () => ({ id: "server-id" }) });
  const id = "cccccccc-cccc-cccc-cccc-cccccccccccc";
  await queue.enqueue({ text: "Первый", kind: "inbox" }, id);
  await queue.enqueue({ text: "Первый", kind: "inbox" }, id);
  assert.equal((await queue.list()).length, 1);
  await assert.rejects(queue.enqueue({ text: "Другой", kind: "inbox" }, id), /Черновик изменился/);
  assert.equal((await queue.list()).length, 1);
});

test("library search matches related project and person names", () => {
  const contexts = [{ id: "p1", title: "Мирон" }, { id: "p2", title: "Космическая игра" }];
  const note = { title: "Материалы", body: "", topic: "урок", context_id: "p1" };
  assert.equal(matchesLibrary(note, "Мирон", contexts), true);
  assert.equal(matchesLibrary(note, "космическая", contexts), false);
  assert.equal(matchesLibrary(note, "УРОК", contexts), true);
});

test("a new draft leaves every field to the text, and old defaults become «Авто»", () => {
  const fresh = normalizeDraft(null);
  assert.deepEqual([fresh.version, fresh.kind, fresh.schedule, fresh.contextId, fresh.importance, fresh.route, fresh.voice],
    [2, "auto", "auto", "auto", "auto", "auto", false]);
  const old = normalizeDraft({ text: "Купить хлеб", kind: "inbox", schedule: "", contextId: "", importance: "normal" });
  assert.deepEqual([old.kind, old.schedule, old.contextId, old.importance], ["auto", "auto", "auto", "auto"]);
  const chosen = normalizeDraft({ kind: "task", schedule: "week", contextId: "a".repeat(32), importance: "high" });
  assert.deepEqual([chosen.kind, chosen.schedule, chosen.contextId, chosen.importance], ["task", "week", "a".repeat(32), "high"]);
  // In a current draft «Входящие», «Без срока», «Без привязки» and «Обычная» are real choices.
  const manual = normalizeDraft({ version: 2, kind: "inbox", schedule: "", contextId: "", importance: "normal",
    route: "note", voice: true });
  assert.deepEqual([manual.kind, manual.schedule, manual.contextId, manual.importance, manual.route, manual.voice],
    ["inbox", "", "", "normal", "note", true]);
  assert.equal(normalizeDraft({ version: 2, importance: "critical" }).importance, "critical");
  assert.equal(normalizeDraft({ version: 2, route: "calendar" }).route, "auto");
});
