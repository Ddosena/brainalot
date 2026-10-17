import { browserCommand } from "./browser-bridge.js";
import { WORK_ENABLED, WORK_PAUSED_MESSAGE } from "./work-availability.js";
import { t } from "./i18n.js";
// A Work task's "workspace" is a set of Chrome tab groups. It opens as its own
// window; the window is remembered in chrome.storage.session so a second click
// focuses it instead of opening a copy. When that window closes, the background
// worker leaves a note and the Work page asks where to pick up next time.

export const GROUP_COLORS = ["blue", "cyan", "green", "yellow", "orange", "red", "pink", "purple", "grey"];
export const WORK_PAGE = "work.html";
export const WS_KEY = "work:windows";
export const PENDING_STOP_KEY = "work:pending-stop";

export function normUrl(u) {
  u = String(u || "").trim();
  if (!u) return "";
  return /^[a-z][\w+.-]*:/i.test(u) ? u : `https://${u}`;
}

export function hasTabGroups(chromeApi = globalThis.chrome) {
  return !!chromeApi?.tabGroups;
}

export function faviconUrl(pageUrl, chromeApi = globalThis.chrome) {
  if (!hasTabGroups(chromeApi) || !pageUrl) return "";
  const url = new URL(chromeApi.runtime.getURL("/_favicon/"));
  url.searchParams.set("pageUrl", pageUrl);
  url.searchParams.set("size", "32");
  return url.toString();
}

/** Groups that have at least one link, links without URLs dropped. */
export function launchableGroups(groups) {
  return (groups || [])
    .map(g => ({ ...g, links: (g.links || []).filter(l => l.url) }))
    .filter(g => g.links.length);
}

async function windowMap(chromeApi) {
  return (await chromeApi.storage.session.get(WS_KEY))[WS_KEY] || {};
}

async function liveWindow(chromeApi, itemId) {
  const map = await windowMap(chromeApi);
  if (map[itemId] == null) return null;
  try {
    return await chromeApi.windows.get(map[itemId]);
  } catch {
    delete map[itemId];
    await chromeApi.storage.session.set({ [WS_KEY]: map });
    return null;
  }
}

/** onlyGroup opens a single group without treating it as the task's workspace. */
export async function openWorkspace(item, onlyGroup = null, chromeApi = globalThis.chrome) {
  if (chromeApi?.desktop) return browserCommand("open-workspace", {item, onlyGroup});
  const groups = launchableGroups(onlyGroup ? [onlyGroup] : item.groups);
  if (!groups.length) return { ok: false, reason: "empty" };

  if (!hasTabGroups(chromeApi)) {
    globalThis.open?.(groups[0].links[0].url, "_blank", "noopener");
    return { ok: true, preview: true };
  }

  if (!onlyGroup) {
    const live = await liveWindow(chromeApi, item.id);
    if (live) {
      await chromeApi.windows.update(live.id, { focused: true });
      return { ok: true, focused: true };
    }
  }

  const win = await chromeApi.windows.create({ url: groups[0].links[0].url, focused: true });
  let tabs = 0;
  for (const [gi, g] of groups.entries()) {
    const ids = [];
    for (const [li, l] of g.links.entries()) {
      if (gi === 0 && li === 0) ids.push(win.tabs[0].id);
      else ids.push((await chromeApi.tabs.create({ windowId: win.id, url: l.url, active: false })).id);
      tabs++;
    }
    const groupId = await chromeApi.tabs.group({ tabIds: ids, createProperties: { windowId: win.id } });
    await chromeApi.tabGroups.update(groupId, {
      title: g.title || "",
      color: GROUP_COLORS.includes(g.color) ? g.color : "grey",
    });
  }

  if (!onlyGroup) {
    const map = await windowMap(chromeApi);
    map[item.id] = win.id;
    await chromeApi.storage.session.set({ [WS_KEY]: map });
  }
  return { ok: true, tabs, groups: groups.length };
}

/**
 * Tabs of the task's workspace window, or of the Work page's own window when the
 * workspace is not open. Extension pages and empty new tabs are skipped.
 * Returns raw groups; an ungrouped tab gets group title null.
 */
export async function captureWorkspace(item, chromeApi = globalThis.chrome) {
  if (chromeApi?.desktop) return browserCommand("capture-workspace", {item});
  if (!hasTabGroups(chromeApi)) return { ok: false, reason: "preview" };
  const live = await liveWindow(chromeApi, item.id);
  const win = live || await chromeApi.windows.getCurrent();
  const tabs = await chromeApi.tabs.query({ windowId: win.id });
  const info = new Map((await chromeApi.tabGroups.query({ windowId: win.id })).map(g => [g.id, g]));
  const own = chromeApi.runtime.getURL("");
  const groups = new Map();

  for (const tab of tabs) {
    const url = tab.url || tab.pendingUrl || "";
    if (!url || url.startsWith(own) || /^chrome:\/\/(newtab|new-tab-page)/.test(url)) continue;
    const key = tab.groupId >= 0 ? tab.groupId : "loose";
    if (!groups.has(key)) {
      const g = info.get(tab.groupId);
      groups.set(key, g ? { title: g.title || null, color: g.color, links: [] } : { title: null, color: "grey", links: [] });
    }
    groups.get(key).links.push({ title: tab.title || url, url });
  }
  return { ok: true, groups: [...groups.values()], fromWorkspace: !!live };
}

/** Background worker: a workspace window closed → leave a note for the Work page. */
export async function workWindowClosed(chromeApi, windowId) {
  const map = await windowMap(chromeApi);
  const itemId = Object.keys(map).find(id => map[id] === windowId);
  if (!itemId) return false;
  delete map[itemId];
  await chromeApi.storage.session.set({ [WS_KEY]: map });
  await chromeApi.storage.local.set({ [PENDING_STOP_KEY]: { itemId, at: Date.now() } });
  return true;
}

/** Side panel button: focus the Work tab if it is open, otherwise open it. */
export async function openWorkPage(chromeApi = globalThis.chrome) {
  if (!WORK_ENABLED) throw new Error(t(WORK_PAUSED_MESSAGE));
  const url = chromeApi.runtime.getURL(WORK_PAGE);
  const existing = (await chromeApi.tabs.query({})).find(tab => (tab.url || "").startsWith(url));
  if (existing) {
    await chromeApi.tabs.update(existing.id, { active: true });
    await chromeApi.windows.update(existing.windowId, { focused: true });
    return { reused: true, tabId: existing.id };
  }
  const tab = await chromeApi.tabs.create({ url });
  return { reused: false, tabId: tab.id };
}
