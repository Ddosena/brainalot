import { popupBounds } from "./capture-window.js";
import { t } from "./i18n.js";

export const LIBRARY_WINDOW_CONTEXT_KEY = "panel:library-window-context";
export const LIBRARY_SNAPSHOT_KEY = "panel:library-snapshot";
export function mayWriteLibrarySnapshot(currentOwner, nextOwner, claim = false) {
  return currentOwner === nextOwner || claim;
}

/** The side panel open call is started in the click handler before this async handoff. */
export async function finishLibraryReturn(opening, saveSnapshot, closePopup, rollbackSnapshot) {
  try {
    await opening;
    await saveSnapshot();
    return await closePopup();
  } catch (error) {
    try { await rollbackSnapshot(); } catch { /* Preserve the actionable return error. */ }
    throw error;
  }
}

export function normalizeLibrarySnapshot(value) {
  const raw = value && typeof value === "object" ? value : {};
  const route = raw.route && typeof raw.route === "object" ? raw.route : {};
  return {
    owner: raw.owner === "window" ? "window" : "panel",
    open: raw.open === true,
    route: {
      category: typeof route.category === "string" ? route.category : "all",
      projectId: typeof route.projectId === "string" ? route.projectId : null,
      section: typeof route.section === "string" ? route.section : "all",
    },
    query: typeof raw.query === "string" ? raw.query.slice(0, 500) : "",
    expanded: Array.isArray(raw.expanded) ? [...new Set(raw.expanded.filter(id => typeof id === "string"))].slice(0, 500) : [],
    listScroll: Number.isFinite(raw.listScroll) ? Math.max(0, raw.listScroll) : 0,
    navScroll: Number.isFinite(raw.navScroll) ? Math.max(0, raw.navScroll) : 0,
    revision: Number.isSafeInteger(raw.revision) && raw.revision >= 0 ? raw.revision : 0,
  };
}

export function createLibraryWindowController(chromeApi, popupUrl) {
  let opening = null;
  const local = chromeApi.storage.local;
  const readContext = async () => (await local.get(LIBRARY_WINDOW_CONTEXT_KEY))[LIBRARY_WINDOW_CONTEXT_KEY] || {};
  const findPopup = async () => (await chromeApi.windows.getAll({populate:true, windowTypes:["popup"]}))
    .find(win => win.tabs?.some(tab => tab.url === popupUrl));

  async function normalWindow(id) {
    if (!Number.isInteger(id)) return null;
    try {
      const win = await chromeApi.windows.get(id);
      return win.type === "normal" ? win : null;
    } catch { return null; }
  }

  async function originFor(sender, saved) {
    return await normalWindow(saved?.originWindowId)
      || await normalWindow(sender.tab?.windowId)
      || await chromeApi.windows.getLastFocused({windowTypes:["normal"]}).catch(() => null);
  }

  async function open(sender) {
    if (opening) return opening;
    opening = (async () => {
      const saved = await readContext();
      const existing = await findPopup();
      if (existing) {
        const origin = await originFor(sender, saved);
        await local.set({[LIBRARY_WINDOW_CONTEXT_KEY]: {originWindowId:origin?.id ?? null, popupWindowId:existing.id}});
        await chromeApi.windows.update(existing.id, {focused:true});
        return {windowId:existing.id, reused:true, originWindowId:origin?.id ?? null};
      }
      const origin = await originFor(sender, {});
      if (!origin || origin.type !== "normal") throw new Error(t("Откройте обычное окно Chrome и повторите попытку."));
      await local.set({[LIBRARY_WINDOW_CONTEXT_KEY]: {originWindowId:origin.id, popupWindowId:null}});
      const win = await chromeApi.windows.create({url:popupUrl, type:"popup", focused:true, ...popupBounds(origin, 1100, 800)});
      if (!win?.id) throw new Error(t("Не удалось открыть окно библиотеки."));
      await local.set({[LIBRARY_WINDOW_CONTEXT_KEY]: {originWindowId:origin.id, popupWindowId:win.id}});
      return {windowId:win.id, reused:false, originWindowId:origin.id};
    })().finally(() => { opening = null; });
    return opening;
  }

  async function context(sender) {
    if (sender.url !== popupUrl) throw new Error(t("Контекст библиотеки доступен только её окну."));
    const saved = await readContext();
    const popup = await findPopup();
    if (!popup || (sender.tab?.windowId != null && popup.id !== sender.tab.windowId)) throw new Error(t("Окно библиотеки уже закрыто."));
    const origin = await originFor(sender, saved);
    if (saved.originWindowId !== origin?.id || saved.popupWindowId !== popup.id) {
      await local.set({[LIBRARY_WINDOW_CONTEXT_KEY]: {originWindowId:origin?.id ?? null, popupWindowId:popup.id}});
    }
    return {originWindowId:origin?.id ?? null, popupWindowId:popup.id};
  }

  async function finish(sender) {
    if (sender.url !== popupUrl) throw new Error(t("Закрыть окно библиотеки можно только из него."));
    const saved = await readContext();
    const popup = await findPopup();
    if (!popup || (sender.tab?.windowId != null && popup.id !== sender.tab.windowId) || popup.id !== saved.popupWindowId) throw new Error(t("Окно библиотеки уже закрыто."));
    await chromeApi.windows.remove(popup.id);
    if (await normalWindow(saved.originWindowId)) {
      try { await chromeApi.windows.update(saved.originWindowId, {focused:true}); } catch { /* Origin closed after validation. */ }
    }
    return {closed:true};
  }

  async function onRemoved(windowId) {
    if (opening) await opening.catch(() => {});
    const saved = await readContext();
    if (saved.popupWindowId !== windowId) return;
    if ((await readContext()).popupWindowId !== windowId) return;
    await local.set({[LIBRARY_WINDOW_CONTEXT_KEY]: {...saved, popupWindowId:null}});
    const snapshot = normalizeLibrarySnapshot((await local.get(LIBRARY_SNAPSHOT_KEY))[LIBRARY_SNAPSHOT_KEY]);
    if (snapshot.owner === "window" && (await readContext()).popupWindowId === null) {
      await local.set({[LIBRARY_SNAPSHOT_KEY]: {...snapshot, owner:"panel", revision:snapshot.revision+1}});
    }
  }

  async function status() {
    const popup = await findPopup();
    if (popup) return {open:true, windowId:popup.id};
    const saved = await readContext();
    if (saved.popupWindowId != null) await onRemoved(saved.popupWindowId);
    else {
      const snapshot = normalizeLibrarySnapshot((await local.get(LIBRARY_SNAPSHOT_KEY))[LIBRARY_SNAPSHOT_KEY]);
      if (snapshot.owner === "window") {
        await local.set({[LIBRARY_SNAPSHOT_KEY]: {...snapshot, owner:"panel", revision:snapshot.revision+1}});
      }
    }
    return {open:false, windowId:null};
  }

  return {open, context, finish, onRemoved, status};
}
