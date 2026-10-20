import {attachDictation} from "./voice-recorder.js";
import {sharedApi} from "./time-client.js";
import {createConnection} from "./connection.js";
import {locale, t} from "./i18n.js";
import {whenText} from "./dictation.js";

export const QUICK_VOICE_PREFIX = "quick-voice:pending:";

/** Home dictation keeps its immutable parser input until the API confirms a
 * real task. A queued write is never presented as a saved task. */
export function createQuickVoiceOutbox({storage, api, uuid = () => crypto.randomUUID()}) {
  let busy = false, unsaved = null;
  async function list() {
    const saved = await storage.get(null);
    const records = Object.entries(saved).filter(([key, value]) => key.startsWith(QUICK_VOICE_PREFIX) && value?.payload?.external_id && key === QUICK_VOICE_PREFIX + value.payload.external_id).map(([, value]) => value);
    if (unsaved && !records.some(record => record.payload.external_id === unsaved.payload.external_id)) records.push(unsaved);
    return records.sort((a, b) => a.payload.captured_at.localeCompare(b.payload.captured_at) || a.payload.external_id.localeCompare(b.payload.external_id));
  }
  async function deliver(record) {
    const result = await api("/api/dictation", "POST", record.payload);
    if (result?.route !== "note" || typeof result.note?.id !== "string" || result.note.kind !== "task") throw new Error(t("Сервис не подтвердил сохранение задачи."));
    await storage.remove(QUICK_VOICE_PREFIX + record.payload.external_id);
    return {note: result.note, record};
  }
  async function exclusive(operation) {
    if (busy) throw new Error(t("Дождитесь сохранения диктовки."));
    busy = true; try { return await operation(); } finally { busy = false; }
  }
  return {
    list,
    capture(text, baseline) {
      return exclusive(async () => {
        const value = String(text || "").trim();
        if (!value) throw new Error(t("Не удалось разобрать слова. Попробуйте ещё раз."));
        const payload = {text: value, external_id: `home-voice:${uuid()}`, source: "voice", captured_at: baseline.captured_at,
          time_zone: baseline.time_zone, overrides: {kind: "task", route: "note", keep_context_in_title: true}};
        const record = {payload};
        // The exact UUID, speech-date baseline and parser overrides exist before
        // any write request. Retries across windows/reloads reuse the same body.
        unsaved = record; await storage.set({[QUICK_VOICE_PREFIX + payload.external_id]: record}); unsaved = null;
        return deliver(record);
      });
    },
    retry() { return exclusive(async () => { const record = (await list())[0]; if (!record) return null; await storage.set({[QUICK_VOICE_PREFIX + record.payload.external_id]: record}); unsaved = null; return deliver(record); }); },
  };
}

function todayInZone(moment, timeZone) {
  const parts = new Intl.DateTimeFormat("en-CA", {timeZone, year: "numeric", month: "2-digit", day: "2-digit"}).formatToParts(moment);
  const part = kind => parts.find(item => item.type === kind)?.value;
  return `${part("year")}-${part("month")}-${part("day")}`;
}
export function quickVoiceDestination(note, {now = new Date(), timeZone = "Europe/Moscow"} = {}) {
  return whenText({kind: note.kind, due: note.due, planning_horizon: note.planning_horizon}, todayInZone(now, timeZone)) || t("Без срока");
}

export function mountQuickVoice(container, {onSaved = () => {}, onEdit = () => {}, api = sharedApi,
  storage = globalThis.chrome.storage.local, attach = attachDictation, recorderOptions = {},
  getToken = null, now = () => new Date(), toastMs = 5000} = {}) {
  const doc = container.ownerDocument;
  container.classList.add("quick-voice");
  container.innerHTML = `<button id="quick-voice-start" type="button" class="quick-voice-start" aria-pressed="false"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="12" rx="3"/><path d="M6 10v2a6 6 0 0 0 12 0v-2M12 18v3m-3 0h6"/></svg></button>
    <div class="quick-voice-progress" hidden><p id="quick-voice-status" role="status" aria-live="polite" hidden></p><div class="quick-voice-actions"><button id="quick-voice-cancel" type="button" hidden>${t("Отменить")}</button><button id="quick-voice-retry" type="button" hidden>${t("Повторить сохранение")}</button></div></div>
    <div id="quick-voice-toast" class="quick-voice-toast" role="status" aria-live="polite" hidden><div><strong id="quick-voice-toast-title"></strong><span id="quick-voice-toast-destination"></span></div><button id="quick-voice-edit" type="button">${t("Изменить")}</button></div>
    <textarea id="quick-voice-text" hidden tabindex="-1" aria-hidden="true"></textarea>`;
  const $ = id => container.querySelector(`#${id}`);
  const button = $("quick-voice-start"), status = $("quick-voice-status"), cancel = $("quick-voice-cancel"), retry = $("quick-voice-retry");
  const progress = container.querySelector(".quick-voice-progress"), toast = $("quick-voice-toast"), edit = $("quick-voice-edit"), textarea = $("quick-voice-text");
  const outbox = createQuickVoiceOutbox({storage, api}), connection = createConnection(globalThis.chrome);
  let voice, pending = null, saving = false, checking = false, preparing = false, disposed = false, generation = 0, loadTicket = 0;
  let baseline = null, accepting = false, toastTimer = null, toastNote = null, prepareTimer = null, prepareResolve = null, nativeVisible = true;
  function message(text, failed = false) { status.textContent = text; status.hidden = !text; status.dataset.error = String(failed); render(); }
  function render() {
    if (disposed) return;
    const state = button.dataset.state || "idle", active = ["starting", "recording", "stopping", "transcribing"].includes(state);
    button.disabled = saving || checking || preparing || Boolean(pending) && state !== "recording" || ["starting", "stopping", "transcribing"].includes(state);
    const label = saving ? t("Сохраняю задачу…") : checking ? t("Проверяю голосовой ввод…") : preparing ? t("Подготавливаю голосовой ввод…") : state === "recording" ? t("Остановить запись голоса") : state === "transcribing" ? t("Распознаю…") : t("Надиктовать задачу");
    button.setAttribute("aria-label", label); button.title = label;
    cancel.hidden = saving || !(checking || preparing || active); retry.hidden = !pending || saving;
    retry.disabled = saving;
    progress.hidden = !status.textContent && !active && !preparing && !pending;
    container.dataset.busy = String(saving || checking || preparing || active);
  }
  async function loadPending({announce = false} = {}) {
    const ticket = ++loadTicket, records = await outbox.list();
    if (!disposed && ticket === loadTicket) {
      pending = records[0] || null;
      if (announce && pending && !saving) message(t("Есть неподтверждённая диктовка. Повторите сохранение."), true);
      render();
    }
    return records;
  }
  function hideToast() { clearTimeout(toastTimer); toastTimer = null; toast.hidden = true; }
  function showToast(note, timeZone) {
    hideToast(); toastNote = note;
    $("quick-voice-toast-title").textContent = note.title;
    $("quick-voice-toast-destination").textContent = t("Добавлено в «{destination}»", {destination: quickVoiceDestination(note, {now: now(), timeZone})});
    toast.hidden = false; toastTimer = setTimeout(hideToast, toastMs);
  }
  async function saved(operation) {
    if (saving || disposed) return;
    saving = true; message(t("Сохраняю задачу…"));
    try {
      const result = await operation();
      if (disposed) return;
      await loadPending();
      if (result) {
        textarea.value = ""; message(""); showToast(result.note, result.record.payload.time_zone);
        void Promise.resolve(onSaved(result.note)).catch(() => {});
      } else message("");
    } catch (error) {
      if (!disposed) { await loadPending().catch(() => {}); message(error.message, true); }
    } finally { saving = false; render(); }
  }
  function cancelVoice() {
    ++generation; accepting = false; baseline = null; checking = false; preparing = false;
    clearTimeout(prepareTimer); prepareTimer = null;
    prepareResolve?.(); prepareResolve = null;
    voice?.cancel(); textarea.value = ""; message("");
  }
  async function prepareModel(current, run) {
    preparing = true; message(t("Подготавливаю голосовой ввод…"));
    try {
      let value = current.state === "preparing" ? current : await api("/api/voice/prepare", "POST");
      while (!disposed && run === generation && value.state === "preparing") {
        await new Promise(resolve => { prepareResolve = resolve; prepareTimer = setTimeout(resolve, 1500); }); prepareTimer = null; prepareResolve = null;
        if (disposed || run !== generation) return;
        value = await api("/api/voice/status");
      }
      if (disposed || run !== generation) return;
      message(value.state === "ready" ? t("Голосовой ввод готов. Нажмите микрофон и говорите.") : value.error || t("Не удалось подготовить распознавание речи."), value.state !== "ready");
    } catch (error) { if (!disposed && run === generation) message(error.message, true); }
    finally { if (run === generation) { preparing = false; render(); } }
  }
  voice = attach({button, textarea, status, recorderOptions, language: locale() === "ru" ? "ru" : "auto",
    getToken: getToken || (async () => (await connection.current()).token),
    beforeStart: async () => {
      if (disposed || !nativeVisible || saving || checking || preparing) return false;
      const run = ++generation;
      checking = true; message(t("Проверяю голосовой ввод…"));
      try {
        if ((await loadPending({announce: true})).length || run !== generation) return false;
        const current = await api("/api/voice/status");
        if (disposed || doc.hidden || !nativeVisible || run !== generation) return false;
        if (current.state === "missing") { message(t("Голосовой ввод не установлен: запустите Install-Voice.cmd в папке Brainalot и перезапустите сервис."), true); return false; }
        if (current.state !== "ready") { void prepareModel(current, run); return false; }
        hideToast(); textarea.value = ""; baseline = {captured_at: now().toISOString(), time_zone: Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/Moscow"}; accepting = true;
        return true;
      } catch (error) { if (!disposed && run === generation) message(error.message, true); return false; }
      finally { if (run === generation) { checking = false; render(); } }
    },
    onResult: result => {
      if (disposed || !accepting) return;
      accepting = false;
      if (!result.text?.trim()) { message(t("Не удалось разобрать слова. Попробуйте ещё раз."), true); return; }
      const captured = baseline; baseline = null;
      void saved(() => outbox.capture(result.text, captured));
    },
    onError: () => { accepting = false; render(); },
  });
  cancel.onclick = cancelVoice;
  retry.onclick = () => void saved(() => outbox.retry());
  edit.onclick = async () => {
    if (!toastNote || edit.disabled) return;
    clearTimeout(toastTimer); toastTimer = null; edit.disabled = true;
    try { await onEdit(toastNote); hideToast(); }
    catch (error) { message(error.message || t("Не удалось открыть редактор задачи."), true); }
    finally { edit.disabled = false; }
  };
  const observer = new MutationObserver(render); observer.observe(button, {attributes: true, attributeFilter: ["data-state"]}); observer.observe(status, {childList: true, subtree: true});
  const changed = (changes, area) => { if (area === "local" && Object.keys(changes).some(key => key.startsWith(QUICK_VOICE_PREFIX))) void loadPending({announce: !saving}).catch(error => message(error.message, true)); };
  globalThis.chrome.storage.onChanged.addListener(changed);
  const visibility = event => {
    if (event.type === "brainalot:visibility") nativeVisible = event.detail?.visible !== false;
    if (disposed || (!doc.hidden && nativeVisible) || saving) return;
    if (checking || preparing || accepting || ["starting", "recording", "stopping", "transcribing"].includes(button.dataset.state)) cancelVoice();
  };
  doc.addEventListener("visibilitychange", visibility);
  doc.addEventListener("brainalot:visibility", visibility);
  void loadPending({announce: true}).catch(error => message(error.message, true)); render();
  const destroy = () => { disposed = true; cancelVoice(); hideToast(); observer.disconnect(); doc.removeEventListener("visibilitychange", visibility); doc.removeEventListener("brainalot:visibility", visibility); globalThis.chrome.storage.onChanged.removeListener?.(changed); };
  window.addEventListener("pagehide", destroy, {once: true});
  return {voice, cancel: cancelVoice, destroy};
}
