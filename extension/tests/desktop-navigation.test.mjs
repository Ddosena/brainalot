import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";
import {normalizeLibrarySnapshot} from "../library-window.js";
import {createDesktopNavigation, resolvePanelView} from "../desktop-navigation.js";

const panel = readFileSync(new URL("../panel.js", import.meta.url), "utf8");
const applySnapshot = panel.slice(panel.indexOf("function applyLibrarySnapshot("), panel.indexOf("function renderLibrary()"));
function panelFixture({desktopView = "day", embedded = true, libraryWindow = false} = {}) {
  const elements = new Map();
  const context = vm.createContext({normalizeLibrarySnapshot, resolvePanelView, EMBEDDED_DESKTOP: embedded,
    DESKTOP_PANEL_VIEW: desktopView, IS_LIBRARY_WINDOW: libraryWindow,
    libraryRevision: 0, libraryRestoring: false, libraryDetached: false,
    libraryRoute: {}, libraryRenderKey: null, libraryRestoreScroll: null, expandedLibrary: new Set(),
    document: {body: {dataset: {}}},
    $: id => { if (!elements.has(id)) elements.set(id, {focus() { this.focused = true; }}); return elements.get(id); },
    openLibrary() { context.document.body.dataset.panelView = "library"; },
    returnToDay() { delete context.document.body.dataset.panelView; }, renderLibrary() {},
  });
  vm.runInContext(applySnapshot, context);
  return {context, elements, apply: snapshot => context.applyLibrarySnapshot(snapshot)};
}

test("desktop Day ignores saved open Library but retains query and filters", () => {
  const f = panelFixture();
  f.apply({open: true, revision: 1, query: "lesson", route: {category: "task"}, expanded: ["qa"]});
  assert.notEqual(f.context.document.body.dataset.panelView, "library");
  assert.equal(f.elements.get("library-search").value, "lesson");
  assert.equal(f.context.libraryRoute.category, "task");
  assert.deepEqual([...f.context.expandedLibrary], ["qa"]);
  // A later storage notification is just as incapable of changing modules.
  f.apply({open: true, revision: 2, query: "updated"});
  assert.notEqual(f.context.document.body.dataset.panelView, "library");
});

test("desktop Library remains Library even when another panel saves closed state", () => {
  const f = panelFixture({desktopView: "library"});
  f.apply({open: false, revision: 1});
  assert.equal(f.context.document.body.dataset.panelView, "library");
});

test("standalone Chrome panel still restores library open/closed state", () => {
  const f = panelFixture({embedded: false});
  f.apply({open: true, revision: 1});
  assert.equal(f.context.document.body.dataset.panelView, "library");
  f.apply({open: false, revision: 2});
  assert.notEqual(f.context.document.body.dataset.panelView, "library");
});

test("desktop header uses the brand for Day and has no duplicate module tabs", () => {
  const html = readFileSync(new URL("../desktop.html", import.meta.url), "utf8");
  assert.match(html, /<button[^>]*id="desktop-home"[^>]*data-view="day"[^>]*>Brainalot<\/button>/);
  assert.equal((html.match(/data-view="day"/g) || []).length, 1);
  assert.equal((html.match(/data-view="library"/g) || []).length, 1);
  assert.match(html, /id="desktop-search"[^>]*data-view="library"/);
  assert.doesNotMatch(html, /<button[^>]*data-view="library"[^>]*>Библиотека/);
  assert.doesNotMatch(html, /id="desktop-home"[^>]*data-window-drag/);
});

function shell(workEnabled = true) {
  const messages = [], views = [];
  const frame = new EventTarget();
  frame.src = "about:blank"; frame.dataset = {};
  frame.contentDocument = {readyState: "loading"};
  frame.contentWindow = {location: {href: "about:blank"}, postMessage: (...args) => messages.push(args)};
  frame.load = (url = frame.src) => {
    frame.contentWindow.location.href = url; frame.contentDocument.readyState = "complete";
    if (url === frame.src) frame.dataset.loading = "false";
    frame.dispatchEvent(new Event("load"));
  };
  const nav = createDesktopNavigation({frame, baseUrl: "http://localhost:43982/desktop.html",
    workEnabled, onView: view => views.push(view)});
  return {nav, frame, messages, views};
}

test("search has its own library route; brand always chooses Day after it", async () => {
  const f = shell();
  f.nav.show("day"); f.frame.load();
  for (let i = 0; i < 3; i++) {
    const search = f.nav.panelAction("search");
    assert.match(f.frame.src, /desktop-view=library/);
    assert.doesNotMatch(f.frame.src, /library-window/);
    f.frame.load(); assert.equal(await search, true);
    f.nav.show("day"); f.frame.load();
    assert.match(f.frame.src, /desktop-view=day/);
  }
  assert.equal(f.views.at(-1), "day");
  assert.deepEqual(f.messages.map(([message]) => message.action), ["search", "search", "search"]);
});

test("Work supersedes a pending search without receiving its delayed message", async () => {
  const f = shell();
  f.nav.show("work"); f.frame.load();
  const search = f.nav.panelAction("search");
  f.nav.show("work"); f.frame.load();
  assert.equal(await search, false);
  assert.match(f.frame.src, /\/work.html$/);
  assert.deepEqual(f.messages, []);
});

test("Day supersedes pending search and menu actions", async () => {
  for (const action of ["search", "appearance", "locale", "settings"]) {
    const f = shell();
    const pending = f.nav.panelAction(action);
    f.nav.show("day"); f.frame.load();
    assert.equal(await pending, false);
    assert.equal(f.views.at(-1), "day");
    assert.deepEqual(f.messages, []);
  }
});

test("repeated pending searches only execute the latest one", async () => {
  const f = shell();
  const first = f.nav.panelAction("search"), last = f.nav.panelAction("search");
  f.frame.load();
  assert.equal(await first, false); assert.equal(await last, true);
  assert.equal(f.messages.length, 1);
  assert.deepEqual(f.messages[0], [{type: "brainalot:panel-action", action: "search"}, "http://localhost:43982"]);
  assert.equal(await f.nav.panelAction("search"), true); // already loaded: focus the field
});

test("menu actions leave library and target the Day panel", async () => {
  const f = shell();
  const search = f.nav.panelAction("search"); f.frame.load(); await search;
  const settings = f.nav.panelAction("settings");
  assert.match(f.frame.src, /desktop-view=day/);
  f.frame.load(); assert.equal(await settings, true);
  assert.equal(f.messages.at(-1)[0].action, "settings");
});

test("Day can reset the reused panel without reloading; no-work routes stay valid", () => {
  const f = shell(false);
  for (const route of ["work", "home", "invalid", "__proto__"]) {
    f.nav.show(route);
    assert.match(f.frame.src, /desktop-view=day/);
    assert.equal(f.views.at(-1), "day");
    f.frame.load();
  }
  f.nav.show("day");
  assert.equal(f.messages.at(-1)[0].action, "day");
});

test("internal library/back links delegate to the desktop navigation owner", () => {
  const calls = [];
  const source = panel.slice(panel.indexOf("function openLibrary("), panel.indexOf('$("find-toggle").setAttribute("aria-controls"'));
  const context = vm.createContext({EMBEDDED_DESKTOP: true, DESKTOP_PANEL_VIEW: "day",
    window: {parent: {postMessage: (...args) => calls.push(args)}},
    location: {origin: "http://localhost:43982", href: "http://localhost:43982/panel.html?desktop-view=day"}});
  vm.runInContext(source, context);
  context.openLibrary();
  assert.equal(calls.at(-1)[0].view, "library");
  context.DESKTOP_PANEL_VIEW = "library";
  context.returnToDay();
  assert.equal(calls.at(-1)[0].view, "day");
  assert.ok(calls.every(([, origin]) => origin === "http://localhost:43982"));
});

test("library entry works without any saved snapshot before async restore completes", async () => {
  const f = panelFixture({desktopView: "library"});
  f.context.send = async () => ({});
  f.context.chrome = {storage: {local: {get: async () => ({})}}};
  f.context.LIBRARY_SNAPSHOT_KEY = "snapshot";
  const initialize = panel.slice(panel.indexOf("async function initializeLibraryView()"), panel.indexOf("function stepRotation("));
  vm.runInContext(initialize, f.context);
  const initialized = f.context.initializeLibraryView();
  assert.equal(f.context.document.body.dataset.panelView, "library");
  await initialized;
  assert.equal(f.elements.get("library-search").focused, true);
  assert.equal(f.elements.get("library-window-toggle").hidden, true);
});

test("a stale document load cannot deliver an action to the new URL", async () => {
  const f = shell();
  f.nav.show("day");
  const day = f.frame.src, pending = f.nav.panelAction("search");
  f.frame.load(day); // old document finishes after src was changed to library
  await Promise.resolve();
  assert.equal(f.messages.length, 0);
  f.frame.load();
  assert.equal(await pending, true);
  assert.equal(f.messages.at(-1)[0].action, "search");
});

test("a late panel navigation request cannot override a newer Work selection", () => {
  const f = shell();
  f.nav.show("day"); f.frame.load();
  const day = f.frame.src;
  f.nav.show("work");
  assert.equal(f.nav.fromPanel({type: "brainalot:desktop-view", view: "library", href: day}), false);
  f.frame.load();
  assert.equal(f.nav.fromPanel({type: "brainalot:desktop-view", view: "library", href: f.frame.src}), false);
  assert.equal(f.views.at(-1), "work");
  f.nav.show("day"); f.frame.load();
  assert.equal(f.nav.fromPanel({type: "brainalot:desktop-view", view: "library", href: f.frame.src}), true);
  f.frame.load();
  assert.equal(f.nav.fromPanel({type: "brainalot:desktop-view", view: "day", href: f.frame.src}), true);
  assert.equal(f.views.at(-1), "day");
});
