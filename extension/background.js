import { CaptureQueue, DICTATION, queueStatus } from "./queue.js";
import { requestApi } from "./api.js";
import { createConnection, nativeRequest } from "./connection.js";
import { createCaptureWindowController, writeDraft } from "./capture-window.js";
import { createLibraryWindowController } from "./library-window.js";
import { FOCUS_KEY, FOCUS_OVERLAY_COLOR_KEY, FOCUS_OVERLAY_KEY, normalizeNeonColor, normalizeTimer, resetTimerForExtensionUpdate, timerTransition } from "./focus-timer.js";
import { checkAttachmentLimit, localAttachments, uploadAttachments } from "./attachments.js";
import { initI18n, localeChange, setLocale, t } from "./i18n.js";
import { CALENDAR_CHANGED_KEY, checkDictationPayload, DICTATION_LIMIT } from "./dictation.js";

const CONFIG_KEY = "connection";
// Pages compare this code, not the message text, to spot a worker without a newer command.
const UNKNOWN_COMMAND = "unknown-command";
const RETRY_ALARM = "megamozg-retry";
const POPUP_URL = chrome.runtime.getURL("capture.html");
const LIBRARY_POPUP_URL = chrome.runtime.getURL("panel.html?library-window=1");
let lastError = null;
let overlayPanelWidthDip = null;
let overlayPanelMeasuredAt = 0;
const captureWindow = createCaptureWindowController(chrome, POPUP_URL);
const libraryWindow = createLibraryWindowController(chrome, LIBRARY_POPUP_URL);
const queue = new CaptureQueue({ storage: chrome.storage.local, send: async (payload, record) => {
  if (record?.type === DICTATION) return sendDictation(payload, record);
  if (!Array.isArray(payload.attachments)) return api("/api/captures", "POST", payload);
  const attachments = await resolveForServer(payload.attachments);
  return api("/api/captures", "POST", { ...payload, attachments });
} });

/** A dictation becomes a Google Calendar event or a Brainalot record; the panel reloads the calendar for an event. */
async function sendDictation(payload, record) {
  const attachments = await resolveForServer(payload.attachments || []);
  const result = await api("/api/dictation", "POST", { ...payload, attachments, captured_at: record.captured_at })
    .catch(error => {
      // A service older than dictation: keep the record and say what to do.
      if (error.status === 404) error.message = t("Перезапустите локальный сервис Brainalot: запущенная версия не принимает диктовку.");
      throw error;
    });
  if (result?.route === "calendar") await chrome.storage.local.set({ [CALENDAR_CHANGED_KEY]: Date.now() }).catch(() => {});
  return { ...result, id: result?.note?.id ?? result?.event?.google_event_id };
}

async function resolveForServer(items) {
  if (!items.length) return [];
  const saved = await chrome.storage.local.get(CONFIG_KEY);
  return uploadAttachments(items, saved[CONFIG_KEY]?.token);
}

const connection = createConnection(chrome);
const api = (path, method = "GET", body, options) => connection.api(path, method, body, options);

/** The settings buttons: start or restart the local service through the native host. */
async function serviceControl(action) {
  if (!["status", "start", "restart"].includes(action)) throw new Error(t("Неизвестное действие сервиса."));
  const answer = await nativeRequest(chrome, action);
  if (!answer?.ok) throw new Error(answer?.error || t("Сервис не запустился. Журнал открывается из меню значка Brainalot в трее."));
  // The service may have come back on another port: pick up the port and token again.
  await connection.connect(true);
  return answer;
}

async function syncFocusOverlay() {
  const saved = await chrome.storage.local.get([CONFIG_KEY, FOCUS_KEY, FOCUS_OVERLAY_KEY, FOCUS_OVERLAY_COLOR_KEY]);
  const state = normalizeTimer(saved[FOCUS_KEY]);
  if (overlayPanelWidthDip != null && Date.now() - overlayPanelMeasuredAt < 7000) {
    state.panelWidthDip = overlayPanelWidthDip;
    state.panelMeasuredAt = overlayPanelMeasuredAt;
  }
  const body = { state, visible: saved[FOCUS_OVERLAY_KEY] !== false, color: normalizeNeonColor(saved[FOCUS_OVERLAY_COLOR_KEY]) };
  const post = payload => requestApi("/api/focus/overlay", { token: saved[CONFIG_KEY]?.token, port: saved[CONFIG_KEY]?.port, method: "POST", body: payload, timeoutMs: 1500 });
  // A service started before the neon colors rejects the unknown field: draw without it.
  const result = await post(body).catch(error => {
    if (error.status !== 422) throw error;
    const { color: _, ...plain } = body;
    return post(plain);
  });
  if (result?.rendererVersion !== 2) {
    throw new Error(t("Перезапустите локальный сервис Brainalot: он работает со старой версией полосы."));
  }
  return result;
}

async function badge() {
  const records = await queue.list();
  await chrome.action.setBadgeText({ text: records.length ? String(records.length) : "" });
  await chrome.action.setBadgeBackgroundColor({ color: "#95703b" });
}

async function retry() {
  try {
    const result = await queue.flush();
    lastError = result.error;
    return result;
  } catch (error) {
    lastError = error.message;
    return { error: lastError };
  } finally {
    await badge().catch(() => {});
  }
}

async function initialize() {
  // The worker has no localStorage: its messages follow the choice kept in chrome.storage.
  await initI18n();
  await chrome.storage.local.setAccessLevel({ accessLevel: "TRUSTED_CONTEXTS" });
  await chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
  if (!(await chrome.alarms.get(RETRY_ALARM))) {
    await chrome.alarms.create(RETRY_ALARM, { periodInMinutes: 1 });
  }
  await badge();
}

const ready = initialize().catch((error) => { lastError = error.message; });
chrome.runtime.onInstalled.addListener(details => {
  void ready.then(async () => {
    if (details.reason === "update") {
      await queue.serial(async () => {
        await chrome.storage.local.set({ [FOCUS_KEY]: resetTimerForExtensionUpdate() });
        await syncFocusOverlay().catch(() => {});
      });
    } else {
      await queue.serial(syncFocusOverlay).catch(() => {});
    }
    await retry();
  }).catch(() => {});
});
chrome.runtime.onStartup.addListener(() => {
  void ready.then(retry);
  void ready.then(() => queue.serial(syncFocusOverlay)).catch(() => {});
});
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === RETRY_ALARM) {
    void ready.then(retry);
    void ready.then(() => queue.serial(syncFocusOverlay)).catch(() => {});
  }
});
chrome.windows.onRemoved.addListener(windowId => { void libraryWindow.onRemoved(windowId).catch(() => {}); });
chrome.storage.onChanged.addListener((changes, area) => {
  const next = area === "local" ? localeChange(changes) : null;
  if (next) setLocale(next);
});

function notePath(id) {
  if (typeof id !== "string" || !id || id.length > 150) throw new Error(t("Неверный ID записи."));
  return `/api/notes/${encodeURIComponent(id)}`;
}


async function handle(message, sender) {
  await ready;
  switch (message.type) {
    case "status": {
      // No token yet: ask the native host once (the installed program answers with port and token).
      const config = await connection.current();
      return { configured: Boolean(config.token), automatic: config.auto === true, hostMissing: connection.hostMissing, ...queueStatus(await queue.list()), error: lastError };
    }
    case "reconnect": return Boolean(await connection.connect(true));
    case "configure": {
      if (typeof message.token !== "string" || !message.token.trim()) throw new Error(t("Введите токен."));
      // A token typed by hand keeps the port learned from the native host.
      await queue.serial(async () => {
        const saved = (await chrome.storage.local.get(CONFIG_KEY))[CONFIG_KEY] || {};
        await chrome.storage.local.set({ [CONFIG_KEY]: { port: saved.port, token: message.token.trim() } });
      });
      void retry();
      return { configured: true };
    }
    case "capture": {
      if (typeof message.payload?.text !== "string" || (!message.payload.text.trim() && !localAttachments(message.payload.attachments).length)) throw new Error(t("Введите текст мысли или добавьте файл."));
      checkAttachmentLimit([], message.payload.attachments || []);
      const requestId = typeof message.requestId === "string" && /^[0-9a-f-]{36}$/i.test(message.requestId) ? message.requestId : undefined;
      const record = await queue.enqueue(message.payload, requestId);
      await badge().catch(() => {});
      void retry();
      return { queued: true, id: record.id };
    }
    case "dictate": {
      const payload = checkDictationPayload(message.payload);
      checkAttachmentLimit([], payload.attachments);
      const requestId = typeof message.requestId === "string" && /^[0-9a-f-]{36}$/i.test(message.requestId) ? message.requestId : undefined;
      const record = await queue.enqueue(payload, requestId, DICTATION);
      await badge().catch(() => {});
      void retry();
      return { queued: true, id: record.id };
    }
    case "dictation-preview": {
      if (typeof message.text !== "string" || !message.text.trim() || message.text.trim().length > DICTATION_LIMIT) {
        throw new Error(t("Текст для разбора: от 1 до {limit} символов.", { limit: DICTATION_LIMIT }));
      }
      return api("/api/dictation/parse", "POST", { text: message.text, overrides: message.overrides || undefined,
        with_attachments: message.withAttachments === true });
    }
    case "service-control": return serviceControl(message.action);
    case "voice-status": return api("/api/voice/status");
    case "voice-prepare": return api("/api/voice/prepare", "POST");
    case "open-capture": {
      const source = sender.url === LIBRARY_POPUP_URL
        ? {...sender, tab:{...sender.tab, windowId:(await libraryWindow.context(sender)).originWindowId}}
        : sender;
      return captureWindow.open(source);
    }
    case "finish-capture": return captureWindow.finish(sender);
    case "library-window-open": return libraryWindow.open(sender);
    case "library-window-context": return libraryWindow.context(sender);
    case "library-window-return": return libraryWindow.finish(sender);
    case "library-window-status": return libraryWindow.status();
    case "save-draft": return queue.serial(() => writeDraft(chrome.storage.local, message.draft));
    case "clear-draft": return queue.serial(() => writeDraft(chrome.storage.local, message.draft, true));
    case "timer-action": return queue.serial(async () => {
      const current = (await chrome.storage.local.get(FOCUS_KEY))[FOCUS_KEY];
      const next = timerTransition(current, message.action, Date.now(), message.minutes);
      await chrome.storage.local.set({ [FOCUS_KEY]: next });
      const overlayError = await syncFocusOverlay().then(() => null, error => error.message);
      return { ...normalizeTimer(next), overlayError };
    });
    case "timer-overlay-visibility": return queue.serial(async () => {
      if (typeof message.visible !== "boolean") throw new Error(t("Укажите видимость полосы."));
      await chrome.storage.local.set({ [FOCUS_OVERLAY_KEY]: message.visible });
      const overlayError = await syncFocusOverlay().then(() => null, error => error.message);
      return { visible: message.visible, overlayError };
    });
    case "timer-overlay-color": return queue.serial(async () => {
      if (normalizeNeonColor(message.color) !== message.color) throw new Error(t("Неизвестный цвет полосы."));
      await chrome.storage.local.set({ [FOCUS_OVERLAY_COLOR_KEY]: message.color });
      const overlayError = await syncFocusOverlay().then(() => null, error => error.message);
      return { color: message.color, overlayError };
    });
    case "timer-overlay-layout": return queue.serial(async () => {
      if (!sender.url?.startsWith(chrome.runtime.getURL("panel.html")) ||
          typeof message.width !== "number" || !Number.isFinite(message.width) ||
          message.width < 200 || message.width > 1200) {
        throw new Error(t("Неверная ширина панели таймера."));
      }
      overlayPanelWidthDip = message.width;
      overlayPanelMeasuredAt = Date.now();
      const saved = await chrome.storage.local.get([FOCUS_KEY, FOCUS_OVERLAY_KEY]);
      if (saved[FOCUS_OVERLAY_KEY] !== false &&
          ["running", "paused"].includes(normalizeTimer(saved[FOCUS_KEY]).status)) {
        await syncFocusOverlay().catch(() => {});
      }
      return {ok:true};
    });
    case "timer-state": return (await chrome.storage.local.get(FOCUS_KEY))[FOCUS_KEY] || null;
    case "retry": return retry();
    case "dashboard": return api("/api/dashboard");
    case "calendar-config": return api("/api/calendar/config");
    case "calendar-configure": return api("/api/calendar/config", "POST", { ical_url: message.icalUrl, name: message.name });
    case "calendar-remove": {
      if (typeof message.calendarId !== "string" || !/^[a-zA-Z0-9_-]{1,80}$/.test(message.calendarId)) {
        throw new Error(t("Неверный ID календаря."));
      }
      return api(`/api/calendar/config/${encodeURIComponent(message.calendarId)}`, "DELETE");
    }
    case "calendar-oauth-client":
      return api("/api/calendar/oauth/client", "POST", { credentials: message.credentials });
    case "calendar-oauth-status": return api("/api/calendar/oauth/status");
    case "calendar-oauth-start": {
      const result = await api("/api/calendar/oauth/start", "POST");
      if (!/^https:\/\/accounts\.google\.com\//.test(result.authorization_url || "")) {
        throw new Error(t("Сервис вернул неверную ссылку Google."));
      }
      await chrome.tabs.create({url:result.authorization_url});
      return {opened:true};
    }
    case "calendar-oauth-disconnect": return api("/api/calendar/oauth", "DELETE");
    case "calendar-event-move":
      return api(`/api/calendar/events/${encodeURIComponent(message.eventId)}`, "PATCH", {
        calendar_id: message.calendarId, start: message.start, new_date: message.newDate,
      });
    case "calendar-event-delete":
      return api(`/api/calendar/events/${encodeURIComponent(message.eventId)}`, "DELETE", {
        calendar_id: message.calendarId, start: message.start,
      });
    case "calendar-events": {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(message.start || "") || !/^\d{4}-\d{2}-\d{2}$/.test(message.end || "")) {
        throw new Error(t("Укажите даты календаря."));
      }
      return api(`/api/calendar/events?start=${message.start}&end=${message.end}${message.force === true ? "&force=1" : ""}`);
    }
    case "notes": return api("/api/notes");
    case "update": {
      const patch = { ...message.patch };
      if (Array.isArray(patch.attachments)) {
        checkAttachmentLimit([], patch.attachments);
        patch.attachments = await resolveForServer(patch.attachments);
      }
      return api(notePath(message.id), "PATCH", { expected_version: message.version, patch });
    }
    case "reorder-tasks": return api("/api/tasks/reorder", "POST", {
      due: message.due,
      ordered_ids: message.orderedIds,
      expected_versions: message.expectedVersions,
    });
    case "delete": return api(notePath(message.id), "DELETE", { expected_version: message.version });
    case "feedback": return api(notePath(message.id) + "/feedback", "POST", { expected_version: message.version, action: message.action });
    case "active-tab": {
      const source = sender.url === LIBRARY_POPUP_URL
        ? {...sender, tab:{...sender.tab, windowId:(await libraryWindow.context(sender)).originWindowId}}
        : sender;
      return captureWindow.activeTab(source);
    }
    default: throw Object.assign(new Error(t("Неизвестная команда расширения.")), { code: UNKNOWN_COMMAND });
  }
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  const extensionPage = sender.url?.startsWith(chrome.runtime.getURL(""));
  if (sender.id !== chrome.runtime.id || !extensionPage) return false;
  handle(message, sender).then(
    (data) => respond({ ok: true, data }),
    (error) => respond({ ok: false, error: error.message || t("Не удалось выполнить действие."), status: error.status || 0,
      code: error.code || null }),
  );
  return true;
});
