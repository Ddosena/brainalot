import { isDesktop } from "./desktop-platform.js";
import { capturePayload, currentWeekDueOptions, normalizeTheme, tabCapture } from "./panel-state.js";
import { DRAFT_KEY, DRAFT_VERSION, isOutdatedWorker, normalizeDesign, normalizeDraft } from "./capture-window.js";
import { checkAttachmentLimit, renderAttachmentList, storeAttachment } from "./attachments.js";
import { applyDocument, locale, localeChange, syncLocale, t, translateServerMessage } from "./i18n.js";
import { AUTO, describePlan, dictationOverrides, dictationPayload, scheduleOverride, usesDictation } from "./dictation.js";
import { attachDictation } from "./voice-recorder.js";

applyDocument();
const $ = id => document.getElementById(id);
const THEME_KEY = "panel:theme";
const DESIGN_KEY = "panel:design";
const CONFIG_KEY = "connection";
const PREVIEW_DELAY_MS = 350;
const AUTO_SELECTS = [["capture-kind", "kind"], ["capture-schedule", "schedule"], ["capture-context", "context"],
  ["capture-importance", "importance"]];
const pendingWrites = new Set();
const pendingImports = new Set();
let importTail = Promise.resolve();
let importFailure = null;
let lastPersist = Promise.resolve();
let busy = false;
let draft = normalizeDraft(null);
let previewTimer = null;
let previewSeq = 0;
let lastPreview = null;
let voicePreparing = null;
let voicePreparation = 0;
let disposed = false;
let completion = null;
let nativeVisible = true;

function send(type, extra = {}) {
  return chrome.runtime.sendMessage({ type, ...extra }).then(reply => {
    if (!reply?.ok) {
      throw Object.assign(new Error(reply?.error || t("Ошибка расширения.")), { status: reply?.status || 0, code: reply?.code || null });
    }
    return reply.data;
  });
}
function moscowToday() {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", {timeZone:"Europe/Moscow",year:"numeric",month:"2-digit",day:"2-digit"})
    .formatToParts(new Date()).filter(part => part.type !== "literal").map(part => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}
function applyAppearance(saved) {
  const theme = normalizeTheme(saved[THEME_KEY]);
  if (theme === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  document.documentElement.dataset.design = normalizeDesign(saved[DESIGN_KEY]);
}
function scheduleOptions(selected = AUTO) {
  const select = $("capture-schedule");
  const today = moscowToday();
  const items = [[AUTO, t("Авто")], ["", t("Без срока")], ["week", t("На неделе · без даты")], ["month", t("На месяц · без даты")],
    ...currentWeekDueOptions(today).map(item => [item.value, item.label]), ["custom", t("Другая дата…")]];
  if (/^\d{4}-\d{2}-\d{2}$/.test(selected) && !items.some(([value]) => value === selected)) {
    items.splice(items.length - 1, 0, [selected, selected]);
  }
  select.replaceChildren(...items.map(([value, label]) => {
    const option = document.createElement("option"); option.value = value; option.textContent = label; return option;
  }));
  select.value = selected;
  $("capture-date").min = today;
}
function updateFields() {
  const kind = $("capture-kind").value;
  const dated = kind === AUTO || kind === "task" || kind === "purchase";
  $("capture-schedule").disabled = !dated;
  $("capture-schedule").title = dated ? "" : t("Сначала выберите дело или покупку");
  if (!dated) $("capture-schedule").value = AUTO;
  $("capture-date-label").hidden = !dated || $("capture-schedule").value !== "custom";
  $("capture-media-label").hidden = kind !== "media";
  $("capture-date-row").hidden = $("capture-date-label").hidden && $("capture-media-label").hidden;
  // «Также в делах без срока» belongs to thoughts, chosen by hand or recognised in the text.
  const thought = kind === "thought" || (kind === AUTO && lastPreview?.plan?.kind === "thought");
  $("capture-unscheduled-label").hidden = !thought;
  if (kind !== AUTO && kind !== "thought") $("capture-unscheduled").checked = false;
}
function snapshot() {
  return {
    version: DRAFT_VERSION,
    text: $("capture-text").value, kind: $("capture-kind").value,
    schedule: $("capture-schedule").value, exactDate: $("capture-date").value,
    mediaType: $("capture-media").value, contextId: $("capture-context").value,
    topic: $("capture-topic").value, importance: $("capture-importance").value,
    showInUnscheduled: $("capture-unscheduled").checked, route: draft.route, voice: draft.voice,
    requestId: draft.requestId, attachments: draft.attachments,
    revision: draft.revision + 1,
  };
}
function persist() {
  draft = normalizeDraft(snapshot());
  const saved = draft;
  // Dispatch immediately so the service worker can finish writing even if Chrome closes this page.
  const writing = send("save-draft", { draft: saved });
  // Event handlers cannot await every keystroke; still surface storage errors
  // and mark the rejected promise handled. Submit/Close await the same promise.
  void writing.catch(failure => error(failure.message || t("Не удалось восстановить черновик.")));
  lastPersist = writing;
  pendingWrites.add(writing);
  void writing.finally(() => pendingWrites.delete(writing)).catch(() => {});
  return writing;
}
function error(message) { $("capture-error").textContent = message; $("capture-error").hidden = false; }
function clearError() { $("capture-error").hidden = true; $("capture-error").textContent = ""; }

/** Option texts of the form itself, so the chips use the page's own words. */
function formLabels() {
  const read = id => Object.fromEntries(Array.from($(id).options)
    .filter(option => option.value && option.value !== AUTO).map(option => [option.value, option.textContent]));
  return { kinds: read("capture-kind"), media: read("capture-media"), importance: read("capture-importance") };
}
function setAutoLabels(auto) {
  for (const [id, key] of AUTO_SELECTS) {
    const option = $(id).querySelector(`option[value="${AUTO}"]`);
    if (option) option.textContent = auto?.[key] ? t("Авто · {value}", { value: auto[key] }) : t("Авто");
  }
}
function listItem(text, data = {}) {
  const item = document.createElement("li");
  item.textContent = text;
  for (const [key, value] of Object.entries(data)) if (value) item.dataset[key] = value;
  return item;
}
/** «Понял так»: the chips of a preview, or one note when there is none. */
function renderUnderstanding(state) {
  const view = state?.view || null;
  $("capture-understanding").hidden = !state;
  $("capture-chips").replaceChildren(...(view?.chips || []).map(chip => listItem(chip.text, { kind: chip.kind, route: chip.route })));
  $("capture-plan-title").hidden = !view;
  $("capture-plan-title").textContent = view ? t("Название: {title}", { title: view.title }) : "";
  const notes = view ? view.notes : state?.note ? [{ text: state.note, tone: state.tone || "info" }] : [];
  $("capture-plan-notes").replaceChildren(...notes.map(note => listItem(note.text, { tone: note.tone })));
  const toggle = $("capture-route");
  toggle.hidden = !view?.toggle;
  toggle.textContent = view?.toggle?.text || "";
  toggle.dataset.next = view?.toggle?.next || "";
  setAutoLabels(view?.auto);
  updateFields();
}
function schedulePreview(delay = PREVIEW_DELAY_MS) {
  clearTimeout(previewTimer);
  ++previewSeq; // An earlier response is stale as soon as the text changes.
  previewTimer = setTimeout(() => { void runPreview(); }, delay);
}
async function runPreview() {
  const request = ++previewSeq;
  const text = $("capture-text").value;
  if (!usesDictation(text)) {
    lastPreview = null;
    renderUnderstanding(text.trim() ? { note: t("Длинный текст сохранится как есть, без разбора.") } : null);
    return;
  }
  try {
    const preview = await send("dictation-preview", { text, withAttachments: draft.attachments.length > 0,
      overrides: dictationOverrides(draft, { today: moscowToday() }) });
    if (request !== previewSeq) return;
    lastPreview = preview;
    renderUnderstanding({ view: describePlan(preview, { today: moscowToday(), labels: formLabels() }) });
  } catch (failure) {
    if (request !== previewSeq) return;
    lastPreview = null;
    renderUnderstanding(isOutdatedWorker(failure)
      ? { note: t("Обновите расширение на chrome://extensions, чтобы видеть разбор текста.") }
      // 404: the extension is new, the running service is not.
      : failure.status === 404 ? { note: t("Перезапустите локальный сервис Brainalot: запущенная версия не умеет разбирать текст. Запись дождётся его в очереди."), tone: "warning" }
        : failure.status === 422 ? { note: failure.message, tone: "warning" }
          : { note: t("Разбор появится, когда сервис будет доступен. Запись всё равно сохранится.") });
  }
}

/** Without the parser (empty or very long text) «Авто» means Входящие, as before dictation. */
function plainPayload(text) {
  const dated = draft.schedule !== AUTO && draft.schedule !== "";
  const kind = draft.kind !== AUTO ? draft.kind : dated ? "task" : "inbox";
  const task = kind === "task" || kind === "purchase";
  const schedule = task && draft.schedule !== AUTO ? scheduleOverride(draft, moscowToday()) : { due: null, planning_horizon: null };
  const importance = draft.importance === AUTO ? "normal" : draft.importance === "critical" && !task ? "high" : draft.importance;
  return { ...capturePayload({
    text, kind, due: schedule.due, planningHorizon: schedule.planning_horizon, mediaType: draft.mediaType,
    contextId: draft.contextId === AUTO ? "" : draft.contextId, topic: draft.topic, importance,
    showInUnscheduled: draft.showInUnscheduled,
  }), attachments: draft.attachments };
}
async function saveDictation(text) {
  const payload = dictationPayload({ text, overrides: dictationOverrides(draft, { today: moscowToday(), strict: true }),
    topic: draft.topic, showInUnscheduled: draft.showInUnscheduled, attachments: draft.attachments, voice: draft.voice });
  try {
    await send("dictate", { requestId: draft.requestId, payload });
  } catch (failure) {
    // A worker from before dictation still keeps the text, as a plain capture.
    if (!isOutdatedWorker(failure)) throw failure;
    await send("capture", { requestId: draft.requestId, payload: plainPayload(text) });
  }
}
async function finish() {
  let failure;
  // Native navigation is idempotent. Retry a transient bridge/state-save error
  // once; window.close() cannot close the embedded capture document.
  for (let attempt = 0; attempt < (isDesktop ? 2 : 1); attempt++) {
    try { await send("finish-capture"); return; } catch (error) { failure = error; }
  }
  if (isDesktop) throw failure;
  window.close(); // Compatibility with an older Chrome service worker.
}
function committed(type) {
  completion = { type, draft: { ...draft, attachments: [...draft.attachments] }, finalized: false,
    nextRequestId: type === "tab" ? crypto.randomUUID() : null };
  cancelVoice();
  clearTimeout(previewTimer);
  ++previewSeq;
}
async function finishCommitted() {
  if (!completion.finalized) {
    if (completion.type === "tab" && (completion.draft.text.trim() || completion.draft.attachments.length)) {
      // The tab capture is committed; preserve the independent thought under a
      // single new ID even if its first draft write was not acknowledged.
      draft.requestId = completion.nextRequestId;
      await persist();
    } else {
      draft = normalizeDraft(await send("clear-draft", { draft: {
        ...completion.draft, revision: completion.draft.revision + 1,
      } }));
    }
    completion.finalized = true;
  }
  await finish();
}
function recover(failure, fallback) {
  error(completion ? t("Запись уже сохранена. Повторите закрытие окна.") : failure.message || fallback);
  setBusy(false);
}
async function closeWindow() {
  if (busy) return;
  cancelVoice();
  setBusy(true);
  if (completion) {
    try { await finishCommitted(); } catch (failure) { recover(failure); }
    return;
  }
  await Promise.allSettled([...pendingImports]);
  if (importFailure) { error(importFailure.message || t("Файл не сохранился. Проверьте его и попробуйте снова.")); setBusy(false); return; }
  const writes = await Promise.allSettled([...pendingWrites, lastPersist]);
  const failure = writes.find(result => result.status === "rejected");
  if (failure) { error(t("Черновик не сохранился. Проверьте расширение и закройте окно ещё раз.")); setBusy(false); return; }
  try { await finish(); } catch (failure) { recover(failure, t("Не удалось закрыть окно. Повторите попытку.")); }
}
function setBusy(value) {
  busy = value;
  $("capture-form").inert = value;
  $("capture-submit").disabled = $("save-tab").disabled = $("capture-close").disabled = value;
  if (completion) {
    // After enqueue acknowledgement only finalization/navigation may be retried.
    // Editing the old textarea under a cleared/new request ID would duplicate it.
    for (const field of $("capture-form").elements) if (field !== $("capture-submit")) field.disabled = true;
    $("capture-submit").querySelector("span").textContent = t("Закрыть");
  }
}
async function submit() {
  if (busy) return;
  setBusy(true);
  clearError();
  try {
    if (completion) { await finishCommitted(); return; }
    // Ctrl+Enter while speaking: the words land in the text first.
    const voiceRequest = voicePreparation;
    await voice.settle();
    if (disposed || voiceRequest !== voicePreparation) throw new Error(t("Запись прервалась."));
    await importTail;
    if (importFailure) throw importFailure;
    const text = $("capture-text").value;
    if (!text.trim() && !draft.attachments.length) throw new Error(t("Введите текст записи или добавьте файл."));
    await persist();
    if (usesDictation(text)) await saveDictation(text);
    else await send("capture", { requestId: draft.requestId, payload: plainPayload(text) });
    committed("entry");
    await finishCommitted();
  } catch (failure) {
    recover(failure, t("Не удалось сохранить. Проверьте данные и попробуйте снова."));
  }
}
async function saveTab() {
  if (busy) return;
  setBusy(true);
  clearError();
  try {
    if (completion) { await finishCommitted(); return; }
    await importTail;
    if (importFailure) throw importFailure;
    await persist();
    const tab = await send("active-tab");
    await send("capture", { requestId: draft.requestId, payload: tabCapture(tab) });
    committed("tab");
    await finishCommitted();
  } catch (failure) {
    recover(failure, t("Не удалось сохранить вкладку."));
  }
}
function previousContext(id) {
  const option = document.createElement("option"); option.value = id; option.textContent = t("Ранее выбранный контекст");
  option.dataset.placeholder = "true";
  $("capture-context").append(option);
}
async function initialize() {
  const saved = await chrome.storage.local.get([DRAFT_KEY, THEME_KEY, DESIGN_KEY]);
  applyAppearance(saved);
  // The desktop host has no browser tab to capture. Its adapter returns to the
  // day through finish-capture instead of closing the application window.
  $("save-tab").hidden = isDesktop;
  document.querySelector(".capture-hint").hidden = isDesktop;
  draft = normalizeDraft(saved[DRAFT_KEY]);
  if (draft.completed) draft = normalizeDraft({ version: DRAFT_VERSION, revision: draft.revision });
  draft.requestId ||= crypto.randomUUID();
  scheduleOptions(draft.schedule);
  $("capture-text").value = draft.text;
  renderFiles();
  $("capture-kind").value = Array.from($("capture-kind").options).some(option => option.value === draft.kind) ? draft.kind : AUTO;
  $("capture-schedule").value = draft.schedule;
  $("capture-date").value = draft.exactDate;
  $("capture-media").value = draft.mediaType;
  $("capture-topic").value = draft.topic;
  $("capture-importance").value = draft.importance;
  $("capture-unscheduled").checked = draft.showInUnscheduled;
  updateFields();
  const chosenContext = draft.contextId !== AUTO && draft.contextId;
  if (chosenContext) previousContext(draft.contextId);
  $("capture-context").value = draft.contextId;
  $("capture-text").focus();
  await persist();
  schedulePreview(0);
  try {
    const dashboard = await send("dashboard");
    $("capture-context").querySelector("option[data-placeholder]")?.remove();
    for (const context of dashboard.contexts || []) {
      const option = document.createElement("option"); option.value = context.id; option.textContent = context.title || context.name || context.id;
      $("capture-context").append(option);
    }
    if (chosenContext && !Array.from($("capture-context").options).some(option => option.value === draft.contextId)) {
      previousContext(draft.contextId);
    }
    $("capture-context").value = draft.contextId;
  } catch { /* The local queue still accepts drafts while the service is offline. */ }
}

function renderFiles() {
  renderAttachmentList($("capture-attachments"), draft.attachments, { removable: true, onRemove(index) {
    draft.attachments.splice(index, 1);
    importFailure = null;
    renderFiles(); void persist(); schedulePreview(0);
  }});
}

async function addFiles(files) {
  const incoming = checkAttachmentLimit(draft.attachments, files);
  for (const file of incoming) {
    const item = await storeAttachment(file);
    draft.attachments.push(item);
    renderFiles();
    await persist();
    schedulePreview(0);
  }
}

function importFiles(files) {
  const selected = Array.from(files);
  const operation = importTail.then(() => addFiles(selected)).then(() => { importFailure = null; }).catch(failure => {
    importFailure = failure;
    error(failure.message || t("Не удалось сохранить файл в черновике."));
  });
  importTail = operation;
  pendingImports.add(operation);
  void operation.finally(() => pendingImports.delete(operation));
}

const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
/** The model downloads once in the background; until then a click explains the wait. Resolves true when ready. */
async function prepareVoice(status, request) {
  const button = $("capture-voice");
  button.dataset.state = "preparing";
  voice.report(t("Загружаю модель распознавания речи. Это нужно один раз и может занять несколько минут."));
  try {
    let current = status.state === "preparing" ? status : await send("voice-prepare");
    while (current.state === "preparing") {
      await wait(1500);
      if (request !== voicePreparation || disposed) return false;
      current = await send("voice-status");
    }
    if (request !== voicePreparation || disposed) return false;
    if (current.state === "ready") {
      voice.report(t("Голосовой ввод готов: нажмите микрофон и говорите."));
      return true;
    }
    voice.report(current.error ? translateServerMessage(current.error) : t("Не удалось подготовить распознавание речи."), true);
  } catch (failure) {
    if (request === voicePreparation && !disposed) voice.report(failure.message, true);
  } finally {
    if (request === voicePreparation && !disposed) button.dataset.state = "idle";
  }
}
async function voiceReady() {
  if (voicePreparing || busy || disposed || !nativeVisible) return false;
  const request = voicePreparation;
  let status;
  try {
    status = await send("voice-status");
  } catch (failure) {
    if (request !== voicePreparation || disposed) return false;
    voice.report(isOutdatedWorker(failure) ? t("Обновите расширение на chrome://extensions, чтобы включить голосовой ввод.")
      : failure.status === 404 ? t("Перезапустите локальный сервис Brainalot: запущенная версия не знает голосового ввода.")
        : failure.message, true);
    return false;
  }
  if (request !== voicePreparation || busy || disposed || !nativeVisible) return false;
  if (status.state === "ready") return true;
  if (status.state === "missing") {
    voice.report(t("Голосовой ввод не установлен: запустите Install-Voice.cmd в папке Brainalot и перезапустите сервис."), true);
    return false;
  }
  // The click meant «dictate»: once the model is ready, start listening if the window is still in use.
  const pending = prepareVoice(status, request).finally(() => { if (voicePreparing === pending) voicePreparing = null; })
    .then(ready => { if (ready && request === voicePreparation && !busy && !disposed && nativeVisible && document.hasFocus()) $("capture-voice").click(); });
  voicePreparing = pending;
  return false;
}
function cancelVoice() {
  ++voicePreparation;
  voicePreparing = null;
  voice.cancel();
}
const voice = attachDictation({
  button: $("capture-voice"), textarea: $("capture-text"), status: $("capture-voice-status"),
  getToken: async () => (await chrome.storage.local.get(CONFIG_KEY))[CONFIG_KEY]?.token,
  // The parser understands Russian; with an English interface Whisper detects the language.
  language: locale() === "ru" ? "ru" : "auto",
  beforeStart: voiceReady,
  onResult: result => { if (result.text) { draft.voice = true; void persist(); } },
});
window.addEventListener("pagehide", () => { disposed = true; cancelVoice(); }, { once: true });
document.addEventListener("brainalot:visibility", event => {
  nativeVisible = event.detail?.visible !== false;
  if (!nativeVisible) cancelVoice();
});

$("capture-add-files").addEventListener("click", () => $("capture-files").click());
$("capture-files").addEventListener("change", event => {
  importFiles(event.target.files);
  event.target.value = "";
});
$("capture-text").addEventListener("paste", event => {
  const images = Array.from(event.clipboardData?.files || []).filter(file => file.type.startsWith("image/"));
  if (!images.length) return;
  event.preventDefault();
  const pastedText = event.clipboardData.getData("text/plain");
  if (pastedText) {
    const field = event.currentTarget;
    field.setRangeText(pastedText, field.selectionStart, field.selectionEnd, "end");
    field.dispatchEvent(new Event("input", { bubbles: true }));
  }
  importFiles(images);
});
$("capture-route").addEventListener("click", () => {
  draft.route = $("capture-route").dataset.next === "note" ? "note" : AUTO;
  void persist();
  schedulePreview(0);
});

$("capture-form").addEventListener("submit", event => { event.preventDefault(); void submit(); });
$("save-tab").addEventListener("click", () => { void saveTab(); });
$("capture-close").addEventListener("click", () => { void closeWindow(); });
document.addEventListener("keydown", event => {
  if (event.key === "Escape") {
    event.preventDefault();
    // The first Escape stops the microphone; the next one closes the window.
    if (["starting", "recording"].includes(voice.recorder.state)) cancelVoice();
    else void closeWindow();
  }
  if (event.key === "Enter" && event.ctrlKey) { event.preventDefault(); void submit(); }
});
$("capture-form").addEventListener("input", () => { importFailure = null; clearError(); void persist(); schedulePreview(); });
$("capture-form").addEventListener("change", event => {
  if (event.target !== $("capture-files")) importFailure = null;
  if (event.target.id === "capture-kind" || event.target.id === "capture-schedule") updateFields();
  void persist();
  if (event.target !== $("capture-text")) schedulePreview(0);
});
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local") return;
  // The draft is saved on every change, so a reload in the new language keeps it.
  if (localeChange(changes)) { location.reload(); return; }
  if (changes[THEME_KEY] || changes[DESIGN_KEY]) {
    void chrome.storage.local.get([THEME_KEY, DESIGN_KEY]).then(applyAppearance);
  }
});
void syncLocale();
void initialize().catch(failure => error(failure.message || t("Не удалось восстановить черновик.")));
