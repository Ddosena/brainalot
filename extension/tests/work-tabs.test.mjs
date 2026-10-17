import test from "node:test";
import assert from "node:assert/strict";
import { captureWorkspace, launchableGroups, normUrl, openWorkPage, openWorkspace, PENDING_STOP_KEY, workWindowClosed, WS_KEY } from "../work-tabs.js";

function fixture() {
  const session = new Map(), local = new Map();
  const windows = new Map([[1, { id: 1, type: "normal" }]]);
  const tabs = [{ id: 100, windowId: 1, url: "chrome-extension://mmm/panel.html", groupId: -1 }];
  const groups = [];
  const calls = { focused: [], created: [], grouped: [], updatedGroups: [] };
  let nextWin = 2, nextTab = 101, nextGroup = 500;
  const store = map => ({
    async get(key) { return { [key]: map.get(key) }; },
    async set(items) { for (const [k, v] of Object.entries(items)) map.set(k, v); },
    async remove(key) { map.delete(key); },
  });
  const chrome = {
    runtime: { getURL: path => `chrome-extension://mmm/${path.replace(/^\//, "")}` },
    storage: { session: store(session), local: store(local) },
    windows: {
      async create({ url }) {
        const win = { id: nextWin++, type: "normal" };
        windows.set(win.id, win);
        const tab = { id: nextTab++, windowId: win.id, url, groupId: -1 };
        tabs.push(tab);
        calls.created.push(url);
        return { ...win, tabs: [tab] };
      },
      async get(id) { if (!windows.has(id)) throw new Error("No window"); return windows.get(id); },
      async getCurrent() { return windows.get(1); },
      async update(id) { calls.focused.push(id); },
    },
    tabs: {
      async create({ windowId, url }) {
        const tab = { id: nextTab++, windowId: windowId ?? 1, url, groupId: -1 };
        tabs.push(tab);
        calls.created.push(url);
        return tab;
      },
      async query({ windowId } = {}) { return tabs.filter(tab => windowId == null || tab.windowId === windowId); },
      async update(id) { calls.focused.push(`tab:${id}`); },
      async group({ tabIds, createProperties }) {
        const id = nextGroup++;
        groups.push({ id, windowId: createProperties.windowId, title: "", color: "grey" });
        for (const tab of tabs) if (tabIds.includes(tab.id)) tab.groupId = id;
        calls.grouped.push(tabIds.length);
        return id;
      },
    },
    tabGroups: {
      async update(id, props) { Object.assign(groups.find(g => g.id === id), props); calls.updatedGroups.push(props); },
      async query({ windowId }) { return groups.filter(g => g.windowId === windowId); },
    },
  };
  return { chrome, session, local, windows, tabs, calls };
}

const task = {
  id: "t1",
  groups: [
    { id: "g1", title: "ТЗ", color: "cyan", links: [{ url: "https://a.test/1" }, { url: "https://a.test/2" }] },
    { id: "g2", title: "Пусто", color: "red", links: [{ url: "" }] },
    { id: "g3", title: "Видео", color: "violet", links: [{ url: "https://b.test/v" }] },
  ],
};

test("links get a scheme and groups without usable links are skipped", () => {
  assert.equal(normUrl(" docs.google.com/x "), "https://docs.google.com/x");
  assert.equal(normUrl("http://127.0.0.1:5217/"), "http://127.0.0.1:5217/");
  assert.equal(normUrl(""), "");
  assert.deepEqual(launchableGroups(task.groups).map(g => g.id), ["g1", "g3"]);
});

test("a workspace opens as one window with named, colored groups and is reused on the next click", async () => {
  const f = fixture();
  const result = await openWorkspace(task, null, f.chrome);
  assert.deepEqual(result, { ok: true, tabs: 3, groups: 2 });
  assert.deepEqual(f.calls.created, ["https://a.test/1", "https://a.test/2", "https://b.test/v"]);
  assert.deepEqual(f.calls.grouped, [2, 1]);
  assert.deepEqual(f.calls.updatedGroups, [{ title: "ТЗ", color: "cyan" }, { title: "Видео", color: "grey" }]);
  const winId = f.session.get(WS_KEY).t1;
  assert.equal(winId, 2);

  const again = await openWorkspace(task, null, f.chrome);
  assert.deepEqual(again, { ok: true, focused: true });
  assert.deepEqual(f.calls.focused, [2]);
  assert.equal(f.calls.created.length, 3);
});

test("an empty workspace reports it instead of opening a window", async () => {
  const f = fixture();
  assert.deepEqual(await openWorkspace({ id: "t2", groups: [] }, null, f.chrome), { ok: false, reason: "empty" });
  assert.equal(f.calls.created.length, 0);
});

test("saving tabs reads the workspace window, keeps group names and skips extension pages", async () => {
  const f = fixture();
  await openWorkspace(task, null, f.chrome);
  f.tabs.push({ id: 900, windowId: 2, url: "https://c.test/loose", title: "Loose", groupId: -1 });
  f.tabs.push({ id: 901, windowId: 2, url: "chrome-extension://mmm/work.html", groupId: -1 });
  const result = await captureWorkspace(task, f.chrome);
  assert.equal(result.fromWorkspace, true);
  assert.deepEqual(result.groups.map(g => [g.title, g.color, g.links.length]), [["ТЗ", "cyan", 2], ["Видео", "grey", 1], [null, "grey", 1]]);
});

test("closing a workspace window leaves a pick-up note once; other windows are ignored", async () => {
  const f = fixture();
  await openWorkspace(task, null, f.chrome);
  assert.equal(await workWindowClosed(f.chrome, 99), false);
  assert.equal(f.local.get(PENDING_STOP_KEY), undefined);
  assert.equal(await workWindowClosed(f.chrome, 2), true);
  assert.equal(f.local.get(PENDING_STOP_KEY).itemId, "t1");
  assert.deepEqual(f.session.get(WS_KEY), {});
});

test("the Work button cannot open or focus Work while the release gate is closed", async () => {
  const f = fixture();
  await assert.rejects(openWorkPage(f.chrome), /Work временно отключён/);
  assert.equal(f.tabs.length, 1);
  assert.deepEqual(f.calls.created, []);
});
