import test from "node:test";
import assert from "node:assert/strict";
import { createLibraryWindowController, finishLibraryReturn, LIBRARY_SNAPSHOT_KEY, LIBRARY_WINDOW_CONTEXT_KEY, mayWriteLibrarySnapshot, normalizeLibrarySnapshot } from "../library-window.js";

function fixture() {
  const url = "chrome-extension://mmm/panel.html?library-window=1";
  const values = new Map();
  const windows = [{id:7, type:"normal", left:-1600, top:40, width:1200, height:850}];
  const calls = {created:[], focused:[], removed:[]};
  let nextId = 9;
  let failCreate = false;
  const chrome = {
    storage:{local:{
      async get(key) { return {[key]:values.get(key)}; },
      async set(items) { for (const [key,value] of Object.entries(items)) values.set(key,value); },
    }},
    windows:{
      async getAll() { return windows.filter(win => win.type === "popup"); },
      async get(id) { const win = windows.find(item => item.id === id); if (!win) throw new Error("Window closed"); return win; },
      async getLastFocused() { return windows.find(win => win.type === "normal") || null; },
      async create(options) {
        calls.created.push(options);
        if (failCreate) throw new Error("Create failed");
        const win = {id:nextId++, tabs:[{url:options.url}], ...options};
        windows.push(win);
        return win;
      },
      async update(id) { calls.focused.push(id); },
      async remove(id) { calls.removed.push(id); windows.splice(windows.findIndex(win => win.id === id),1); },
    },
  };
  const panelSender = {url:"chrome-extension://mmm/panel.html"};
  const popupSender = windowId => ({url,tab:{windowId}});
  return {chrome,url,values,windows,calls,panelSender,popupSender,setFailCreate(value){failCreate=value;}};
}

test("library state normalization retains Explorer route, query, expanded and scroll without trusting invalid fields", () => {
  const state = normalizeLibrarySnapshot({owner:"window",open:true,route:{category:"projects",projectId:"p-1",section:"attachments"},query:"рисунки",expanded:["n1","n1","n2"],listScroll:203,navScroll:14,revision:8});
  assert.deepEqual(state,{owner:"window",open:true,route:{category:"projects",projectId:"p-1",section:"attachments"},query:"рисунки",expanded:["n1","n2"],listScroll:203,navScroll:14,revision:8});
  assert.deepEqual(normalizeLibrarySnapshot({expanded:[12,null],listScroll:-5,route:{category:4}}),{owner:"panel",open:false,route:{category:"all",projectId:null,section:"all"},query:"",expanded:[],listScroll:0,navScroll:0,revision:0});
});

test("handoff owns snapshot writes; an explicit rollback can reclaim authority after either failure", () => {
  assert.equal(mayWriteLibrarySnapshot("panel","window"),false);
  assert.equal(mayWriteLibrarySnapshot("panel","window",true),true);
  assert.equal(mayWriteLibrarySnapshot("window","panel"),false);
  assert.equal(mayWriteLibrarySnapshot("window","panel",true),true);
  assert.equal(mayWriteLibrarySnapshot("window","window"),true);
});

test("failed side-panel return leaves the popup open and restores window authority", async () => {
  const calls = [];
  await assert.rejects(finishLibraryReturn(Promise.reject(new Error("Origin closed")),
    async () => calls.push("save-panel"),
    async () => calls.push("close-popup"),
    async () => calls.push("rollback-window")),/Origin closed/);
  assert.deepEqual(calls,["rollback-window"]);
  await finishLibraryReturn(Promise.resolve(),
    async () => calls.push("save-panel"),
    async () => calls.push("close-popup"),
    async () => calls.push("rollback-window"));
  assert.deepEqual(calls.slice(1),["save-panel","close-popup"]);
});

test("concurrent opens create one centered popup; restarted controller discovers and reuses it", async () => {
  const f = fixture();
  const controller = createLibraryWindowController(f.chrome,f.url);
  const [a,b] = await Promise.all([controller.open(f.panelSender),controller.open(f.panelSender)]);
  assert.deepEqual(a,{windowId:9,reused:false,originWindowId:7});
  assert.deepEqual(b,a);
  assert.equal(f.calls.created.length,1);
  assert.deepEqual({left:f.calls.created[0].left,top:f.calls.created[0].top,width:f.calls.created[0].width,height:f.calls.created[0].height},{left:-1550,top:65,width:1100,height:800});
  const restarted = createLibraryWindowController(f.chrome,f.url);
  assert.deepEqual(await restarted.open(f.panelSender),{windowId:9,reused:true,originWindowId:7});
  assert.equal(f.calls.created.length,1);
  assert.deepEqual(await restarted.context(f.popupSender(9)),{originWindowId:7,popupWindowId:9});
  assert.deepEqual(await restarted.context({url:f.url}),{originWindowId:7,popupWindowId:9});
});

test("window X restores library authority and snapshot; late old close cannot replace a new popup", async () => {
  const f = fixture();
  const controller = createLibraryWindowController(f.chrome,f.url);
  const snapshot = normalizeLibrarySnapshot({owner:"window",open:true,route:{category:"projects",projectId:"p1",section:"tasks"},query:"",expanded:["a"],listScroll:422,navScroll:15,revision:4});
  await f.chrome.storage.local.set({[LIBRARY_SNAPSHOT_KEY]:snapshot});
  await controller.open(f.panelSender);
  f.windows.splice(f.windows.findIndex(win => win.id === 9),1);
  await controller.onRemoved(9);
  const restored = (await f.chrome.storage.local.get(LIBRARY_SNAPSHOT_KEY))[LIBRARY_SNAPSHOT_KEY];
  assert.deepEqual(restored,{...snapshot,owner:"panel",revision:5});
  await controller.open(f.panelSender);
  assert.equal((await f.chrome.storage.local.get(LIBRARY_WINDOW_CONTEXT_KEY))[LIBRARY_WINDOW_CONTEXT_KEY].popupWindowId,10);
  await controller.onRemoved(9);
  assert.equal((await f.chrome.storage.local.get(LIBRARY_WINDOW_CONTEXT_KEY))[LIBRARY_WINDOW_CONTEXT_KEY].popupWindowId,10);
});

test("closed origin selects another normal window, while return closes only the requesting library popup", async () => {
  const f = fixture();
  const controller = createLibraryWindowController(f.chrome,f.url);
  await controller.open(f.panelSender);
  f.windows.splice(f.windows.findIndex(win => win.id === 7),1);
  f.windows.push({id:15,type:"normal",left:0,top:0,width:600,height:500});
  assert.deepEqual(await controller.context(f.popupSender(9)),{originWindowId:15,popupWindowId:9});
  await assert.rejects(controller.finish(f.popupSender(123)),/закрыто/);
  assert.deepEqual(f.calls.removed,[]);
  assert.deepEqual(await controller.finish(f.popupSender(9)),{closed:true});
  assert.deepEqual(f.calls.removed,[9]);
  assert.equal(f.calls.focused.at(-1),15);
});

test("failed creation remains retryable; status reconciles stale detached snapshot after restart", async () => {
  const f = fixture();
  const controller = createLibraryWindowController(f.chrome,f.url);
  f.setFailCreate(true);
  await assert.rejects(controller.open(f.panelSender),/Create failed/);
  f.setFailCreate(false);
  assert.equal((await controller.open(f.panelSender)).windowId,9);
  f.windows.splice(f.windows.findIndex(win => win.id === 9),1);
  await f.chrome.storage.local.set({[LIBRARY_SNAPSHOT_KEY]:normalizeLibrarySnapshot({owner:"window",open:true,revision:2})});
  const restarted = createLibraryWindowController(f.chrome,f.url);
  assert.deepEqual(await restarted.status(),{open:false,windowId:null});
  assert.equal((await f.chrome.storage.local.get(LIBRARY_SNAPSHOT_KEY))[LIBRARY_SNAPSHOT_KEY].owner,"panel");
});
