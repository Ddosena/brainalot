import { t } from "./i18n.js";
import { AUTO } from "./dictation.js";
export const POPUP_CONTEXT_KEY = "panel:popup-context";
export const DRAFT_KEY = "panel:capture-draft";
/** A worker from 0.1.37 or older answers an unknown command in Russian and without a code. */
export function isOutdatedWorker(error) {
  return error?.code === "unknown-command" || error?.message === "Неизвестная команда расширения.";
}

export function normalizeDesign(value) {
  return ["stream", "cabinet", "console", "glamour"].includes(value) ? value : "stream";
}

export function popupBounds(source, width = 560, height = 650) {
  const left = Number.isFinite(source?.left) ? source.left : 0;
  const top = Number.isFinite(source?.top) ? source.top : 0;
  const availableWidth = Math.max(320, Number.isFinite(source?.width) ? source.width : width);
  const availableHeight = Math.max(320, Number.isFinite(source?.height) ? source.height : height);
  const actualWidth = Math.min(width, availableWidth);
  const actualHeight = Math.min(height, availableHeight);
  return {
    width: actualWidth, height: actualHeight,
    left: Math.round(left + (availableWidth - actualWidth) / 2),
    top: Math.round(top + (availableHeight - actualHeight) / 2),
  };
}

export const DRAFT_VERSION = 2;

export function normalizeDraft(value) {
  const raw = value && typeof value === "object" ? value : {};
  // Before dictation a draft had no «Авто»: its untouched defaults (Входящие, без
  // срока, без привязки, обычная) mean «let the text decide» now.
  const legacy = raw.version !== DRAFT_VERSION;
  const choice = (name, legacyDefault) =>
    typeof raw[name] === "string" && !(legacy && raw[name] === legacyDefault) ? raw[name] : AUTO;
  return {
    version: DRAFT_VERSION,
    text: typeof raw.text === "string" ? raw.text.slice(0, 50000) : "",
    kind: choice("kind", "inbox"),
    schedule: choice("schedule", ""),
    exactDate: typeof raw.exactDate === "string" ? raw.exactDate : "",
    mediaType: typeof raw.mediaType === "string" ? raw.mediaType : "movie",
    contextId: choice("contextId", ""),
    topic: typeof raw.topic === "string" ? raw.topic.slice(0, 100) : "",
    importance: ["low", "normal", "high", "critical"].includes(raw.importance) && !(legacy && raw.importance === "normal")
      ? raw.importance : AUTO,
    route: raw.route === "note" ? "note" : AUTO,
    voice: raw.voice === true,
    showInUnscheduled: raw.showInUnscheduled === true,
    attachments: Array.isArray(raw.attachments) ? raw.attachments.filter(item =>
      typeof item?.localId === "string" && typeof item.name === "string" &&
      Number.isSafeInteger(item.size) && item.size > 0).slice(0, 10)
      .map(item => ({ localId:item.localId, name:item.name, type:String(item.type || ""), size:item.size })) : [],
    requestId: typeof raw.requestId === "string" && /^[0-9a-f-]{36}$/i.test(raw.requestId) ? raw.requestId : null,
    revision: Number.isSafeInteger(raw.revision) && raw.revision >= 0 ? raw.revision : 0,
    completed: raw.completed === true,
  };
}

export function matchesLibrary(note, query, contexts = []) {
  const q = String(query || "").trim().toLocaleLowerCase();
  if (!q) return true;
  const context = contexts.find(item => item.id === note.context_id);
  return [note.title, note.body, note.topic, context?.title, context?.name]
    .some(value => String(value || "").toLocaleLowerCase().includes(q));
}

export function createCaptureWindowController(chromeApi, popupUrl) {
  let opening = null;
  async function originalWindow(sender) {
    const saved = (await chromeApi.storage.local.get(POPUP_CONTEXT_KEY))[POPUP_CONTEXT_KEY];
    if (sender.url === popupUrl && saved?.windowId != null) {
      try { return await chromeApi.windows.get(saved.windowId); } catch { /* Original window has closed. */ }
    }
    if (sender.tab?.windowId != null) return chromeApi.windows.get(sender.tab.windowId);
    return chromeApi.windows.getLastFocused({ windowTypes: ["normal"] });
  }
  async function open(sender) {
    if (opening) return opening;
    opening = (async () => {
      const windows = await chromeApi.windows.getAll({ populate: true, windowTypes: ["popup"] });
      const existing = windows.find(win => win.tabs?.some(item => item.url === popupUrl));
      if (existing) {
        await chromeApi.windows.update(existing.id, { focused: true });
        return { windowId: existing.id, reused: true };
      }
      const origin = await originalWindow(sender);
      const [tab] = await chromeApi.tabs.query({ active: true, windowId: origin.id });
      await chromeApi.storage.local.set({ [POPUP_CONTEXT_KEY]: { windowId: origin.id, tabId: tab?.id ?? null } });
      const created = await chromeApi.windows.create({ url: popupUrl, type: "popup", focused: true, ...popupBounds(origin) });
      return { windowId: created.id, reused: false };
    })().finally(() => { opening = null; });
    return opening;
  }
  async function finish(sender) {
    if (sender.url !== popupUrl) throw new Error(t("Окно записи уже закрыто."));
    const saved = (await chromeApi.storage.local.get(POPUP_CONTEXT_KEY))[POPUP_CONTEXT_KEY];
    const windows = await chromeApi.windows.getAll({ populate: true, windowTypes: ["popup"] });
    const popup = windows.find(win => win.tabs?.some(item => item.url === popupUrl));
    if (popup) await chromeApi.windows.remove(popup.id);
    if (saved?.windowId != null) {
      try { await chromeApi.windows.update(saved.windowId, { focused: true }); } catch { /* Original window has closed. */ }
    }
    return { closed: true };
  }
  async function activeTab(sender) {
    const saved = (await chromeApi.storage.local.get(POPUP_CONTEXT_KEY))[POPUP_CONTEXT_KEY];
    let tab = null;
    if (sender.url === popupUrl && saved?.tabId != null) {
      try { tab = await chromeApi.tabs.get(saved.tabId); } catch { /* Tab has closed. */ }
    }
    if (!tab) [tab] = await chromeApi.tabs.query({ active: true, windowId: (await originalWindow(sender)).id });
    if (!tab?.url || !/^https?:\/\//i.test(tab.url)) throw new Error(t("Сохраняются обычные веб-страницы с адресом http или https."));
    return { url: tab.url, title: tab.title || tab.url };
  }
  return { open, finish, activeTab };
}

/** Ignore late drafts after a successful capture, including messages reordered at close. */
export async function writeDraft(storage, value, complete = false) {
  const incoming = normalizeDraft(value);
  const current = normalizeDraft((await storage.get(DRAFT_KEY))[DRAFT_KEY]);
  if (incoming.revision < current.revision) return current;
  if (current.completed && incoming.requestId === current.requestId && !complete) return current;
  const saved = complete ? { ...incoming, text: "", attachments: [], completed: true } : { ...incoming, completed: false };
  await storage.set({ [DRAFT_KEY]: saved });
  return saved;
}
