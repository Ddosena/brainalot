import {addDays, assignmentPatch, calendarEventPhase, calendarEventTime, calendarEventsForDate, chooseResurface, compareCalendarEvents, createRefreshGate, currentWeekDueOptions, dayTasks, editableNoteText, editNotePatch, imageFit, isVisibleTask, mixPose, normalizeTheme, overdueTasks, posterHue, reorderedIds, RESURFACE_HOLD_MS, resurfaceCandidates, stackDragCommits, stackEase, stackPose, stepResurface, thoughtRecords, visibleWeekTasks, weekDates, weekStart} from "./panel-state.js";
import { isOutdatedWorker, normalizeDesign } from "./capture-window.js";
import { FOCUS_KEY, FOCUS_OVERLAY_COLOR_KEY, FOCUS_OVERLAY_KEY, NEON_COLORS, normalizeNeonColor, normalizeTimer, timerView } from "./focus-timer.js";
import { cachedImageColor, checkAttachmentLimit, paintImageBackdrop, previewUrl, rasterKey, renderAttachmentList, renderRasterGallery, safeRaster, storeAttachment, stripManagedAttachments } from "./attachments.js";
import { DOSSIER_SECTIONS, LIBRARY_CATEGORIES, dossierCounts, groupLibraryNotes, libraryCategoryCounts, linkedProjectNotes, resolveLibraryRoute, selectLibraryNotes } from "./library-view.js";
import { finishLibraryReturn, LIBRARY_SNAPSHOT_KEY, mayWriteLibrarySnapshot, normalizeLibrarySnapshot } from "./library-window.js";
import { createCalendarOverlay, createNoteDeletionOverlay } from "./optimistic.js";
import { CALENDAR_IMPORTANCE_KEY, FIRE_LIMIT, calendarImportanceKey, calendarImportanceLevel, fireGroupOfTask, importanceStep, normalizeCalendarImportance, withCalendarImportance } from "./importance-state.js";
import { createFireLayer, IGNITION_MS } from "./fire-layer.js";
import { CALENDAR_CHANGED_KEY } from "./dictation.js";
import { applyDocument, LOCALE_KEY, locale, localeChange, localeTag, saveLocale, setLocale, syncLocale, t } from "./i18n.js";
import { TIMER_CHIME_KEY, normalizeTimerChime, playTimerChime } from "./timer-chime.js";
import { DEFAULT_IGNITION_SOUND, IGNITION_SOUNDS, normalizeIgnitionSound, playIgnitionSound } from "./comic-fire-sound.js";

applyDocument();
const $ = (id) => document.getElementById(id);
const IS_LIBRARY_WINDOW = new URLSearchParams(location.search).has("library-window");
const kinds = {inbox:t("Входящие"),task:t("Дело"),purchase:t("Покупка"),media:t("Медиа"),idea:t("Идея"),thought:t("Мысль"),link:t("Ссылка"),project:t("Проект"),person:t("Человек")};
const media = {movie:t("Фильм"),series:t("Сериал"),book:t("Книга"),game:t("Игра"),podcast:t("Подкаст"),article:t("Статья")};
const importance = {low:t("Низкая"),normal:t("Обычная"),high:t("Высокая"),critical:t("Очень высокая")};
const statuses = {inbox:t("Входящие"),active:t("Активно"),done:t("Выполнено"),cancelled:t("Отменено"),archived:t("В архиве")};
let dashboard = null;
let allNotes = [];
let rawDashboard = null;
let rawAllNotes = [];
let noteReadSeq = 0;
const noteDeletions = createNoteDeletionOverlay();
let rotationRenderKey = null;
let rotationPendingKey = null;
let rotationMotion = null;
let rotationMotionToken = 0;
let rotationAnimating = false;
let viewerState = null;
let resurfaceId = null;
let resurfaceHoldUntil = 0;
let dayScrollBeforeLibrary = 0;
let lastLibraryQuery = "";
let lastLibraryRouteKey = "";
let libraryRenderKey = null;
let libraryRoute = {category:"all", projectId:null, section:"all"};
let libraryDetached = false;
let libraryRestoring = false;
let libraryRevision = 0;
let libraryWrite = Promise.resolve();
let libraryScrollTimer = null;
let libraryRestoreScroll = null;
let originWindowId = null;
let suppressRotationClickUntil = 0;
let refreshTimer = null;
const mutating = new Set();
const deleteConfirmations = new Map();
const expandedTasks = new Set();
const expandedLibrary = new Set();
const expandedManagement = new Set();
let selectedDate = null;
let selectedWeekStart = null;
let backlogView = "unscheduled";
let swipeStartX = null;
let swipeStartY = null;
let plannerCompact = false;
let draggedTaskId = null;
let editingNote = null;
let editingAttachments = [];
let editImports = Promise.resolve();
let editImportFailure = null;
let editSaving = false;
let editClosing = false;
let calendar = {configured:false, calendar_count:0, calendars:[], events:[], last_updated:null, stale:false, error:null, write_access:{client_configured:false,authorized:false}};
let calendarRequest = 0;
let rawCalendarEvents = [];
const calendarOverlay = createCalendarOverlay();
let focusState = normalizeTimer(null);
let timerChime = true;
let lastFocusStatus = null;
let focusOverlayVisible = true;
let focusOverlayColor = NEON_COLORS[0].id;
let selectedFocusMinutes = "25";
let focusPending = false;
const THEME_KEY = "panel:theme";
const DESIGN_KEY = "panel:design";
const BACKLOG_VIEW_KEY = "panel:backlog-view";
const IMPORTANCE_CONTROL_KEY = "panel:show-importance-control";
const IGNITION_SOUND_KEY = "panel:ignition-sound";
let showImportanceControl = true;
let ignitionSound = DEFAULT_IGNITION_SOUND;
let calendarImportance = {};
const importancePending = new Set();
// «Огонь» (очень важное): each block with burning rows owns one flame layer. The
// schedule gets its own, because it is a sticky layer above the day's tasks.
const fireLayers = {today:createFireLayer($("today-card")), calendar:createFireLayer($("calendar-section")), backlog:createFireLayer($("unscheduled-card"))};
const noteFireKey = note => "note:" + note.id;
const calendarFireKey = event => "event:" + calendarImportanceKey(event);
function applyDesign(value) {
  const design = normalizeDesign(value);
  document.documentElement.dataset.design = design;
  if ($("design-select")) $("design-select").value = design;
}

function send(type, extra = {}) {
  return chrome.runtime.sendMessage({type, ...extra}).then(reply => {
    if (!reply?.ok) throw Object.assign(new Error(reply?.error || t("Ошибка расширения")), {code:reply?.code || null});
    return reply.data;
  });
}

function node(tag, cls, value) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (value !== undefined) el.textContent = value;
  return el;
}
function empty(container, message) { container.replaceChildren(node("p", "empty", message)); }
let noticeTimer = null;
function notice(message, error = false, source = "action") {
  clearTimeout(noticeTimer);
  const el = $("notice");
  el.textContent = message;
  el.classList.toggle("error", error);
  el.dataset.source = source;
  el.hidden = false;
  if (!error && source === "action") noticeTimer = setTimeout(() => { el.hidden = true; }, 2200);
}
function applyTheme(value) {
  const theme = normalizeTheme(value);
  document.documentElement.toggleAttribute("data-theme", theme !== "system");
  if (theme === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  if ($("theme-select")) $("theme-select").value = theme;
}
function focusTime(ms, remaining = false) {
  const seconds = remaining ? Math.ceil(ms / 1000) : Math.floor(ms / 1000);
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor(seconds % 3600 / 60);
  const rest = String(seconds % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}` : `${minutes}:${rest}`;
}
function renderFocus(syncControls = false) {
  const view = timerView(focusState);
  const remaining = focusTime(view.remainingMs, true);
  $("focus-remaining").textContent = remaining;
  $("focus-elapsed").textContent = t("Прошло {focusTime}", {focusTime: focusTime(view.elapsedMs)});
  const statusText = { idle:t("Готов к началу"), running:t("Идёт"), paused:t("На паузе"), finished:t("Завершено") }[view.status];
  if ($("focus-status").textContent !== statusText) $("focus-status").textContent = statusText;
  $("focus-ring-value").style.strokeDashoffset = String(100 - view.progress);
  $("focus-progress").setAttribute("aria-valuenow", String(Math.round(view.progress)));
  $("focus-progress").setAttribute("aria-valuetext", t("Прошло {focusTime} из {focusTime2}", {focusTime: focusTime(view.elapsedMs), focusTime2: focusTime(view.durationMs)}));
  const toggleLabel = t("Таймер фокуса: {remaining}, {statusText}. Открыть настройки", {remaining, statusText: statusText.toLocaleLowerCase(localeTag())});
  $("focus-toggle").setAttribute("aria-label", toggleLabel);
  $("focus-toggle").title = toggleLabel;
  const quickLabel = { idle:t("Начать фокус"), running:t("Поставить фокус на паузу"), paused:t("Продолжить фокус"), finished:t("Начать фокус заново") }[view.status];
  $("focus-quick-action").setAttribute("aria-label", quickLabel);
  $("focus-quick-action").title = quickLabel;
  $("focus-quick-icon").setAttribute("d", view.status === "running" ? "M8 5v14M16 5v14" : "M8 5v14l11-7z");
  $("focus-action").hidden = view.status === "running" || view.status === "paused";
  $("focus-action").textContent = view.status === "finished" ? t("Начать заново") : t("Начать");
  $("focus-reset").disabled = focusPending || view.status === "idle";
  for (const button of $("focus-presets").querySelectorAll("button")) button.disabled = focusPending || view.status === "running" || view.status === "paused";
  $("focus-custom").disabled = focusPending || view.status === "running" || view.status === "paused";
  if (syncControls) {
    const minutes = view.durationMs / 60000;
    selectedFocusMinutes = [20, 25, 30, 50].includes(minutes) ? String(minutes) : "custom";
    $("focus-custom").value = String(minutes);
  }
  for (const button of $("focus-presets").querySelectorAll("button")) {
    button.setAttribute("aria-pressed", String(button.dataset.focusMinutes === selectedFocusMinutes));
  }
  $("focus-custom-label").classList.toggle("selected", selectedFocusMinutes === "custom");
}
function applyPlannerSize() {
  $("today-list").closest(".planner").classList.toggle("planner-compact", plannerCompact);
  const button = $("planner-size-toggle");
  button.title = plannerCompact ? t("Показать все дела без внутреннего скролла") : t("Свернуть список дел");
  button.setAttribute("aria-label", button.title);
  button.setAttribute("aria-pressed", String(plannerCompact));
  if (dashboard) renderCalendar();
}
function renderBody(el, note, {compact = false} = {}) {
  let body = stripManagedAttachments(note.body);
  const generatedHeading = body?.match(/^\s*# ([^\r\n]+)(?:\r?\n(?:\r?\n)?|$)/);
  if (generatedHeading?.[1] === note.title) {
    body = body.slice(generatedHeading[0].length);
    if (body.trim() === note.title) return;
  }
  if (!body) return;
  if (compact) {
    const plain = body.trim().replace(/\s+/g, " ");
    const excerpt = plain.slice(0, 96).trimEnd();
    if (plain.length <= 96 && !body.trim().includes("\n")) {
      el.append(node("p", "carousel-excerpt", excerpt));
    } else {
      const details = node("details", "carousel-body-details");
      const summary = node("summary", "carousel-excerpt", excerpt + (plain.length > 96 ? "…" : ""));
      summary.title = t("Развернуть запись");
      details.append(summary, node("p", "note-body", body));
      el.append(details);
    }
    return;
  }
  if (body.length <= 240) { el.append(node("p", "note-body", body)); return; }
  const preview = node("p", "note-preview", body.slice(0, 220).trimEnd() + "…");
  const details = node("details", "body-details");
  details.append(node("summary", "", t("Показать полный текст")), node("p", "note-body", body));
  details.addEventListener("toggle", () => { preview.hidden = details.open; });
  el.append(preview, details);
}
function recallExcerpt(note) {
  const plain = editableNoteText(stripManagedAttachments(note.body || "")).replace(/\s+/g, " ").trim();
  if (!plain || plain === note.title) return "";
  const limit = 176;
  return plain.length > limit ? `${plain.slice(0, limit).trimEnd()}…` : plain;
}
function showAttachments(container, note) {
  if (!Array.isArray(note.attachments) || !note.attachments.length) return;
  const list = node("div", "attachment-list note-attachments");
  renderAttachmentList(list, note.attachments, { preview:false, onError: error => notice(error.message || t("Не удалось скачать файл."), true) });
  container.append(list);
}
function makeDeleteButton(note, card, label = t("Удалить запись")) {
  const remove = node("button", "note-delete", label);
  remove.type = "button";
  remove.title = t("Удалить из панели и Obsidian");
  const setConfirmation = confirmed => {
    remove.dataset.confirm = String(confirmed);
    remove.textContent = confirmed ? t("Удалить из Obsidian?") : label;
    remove.setAttribute("aria-label", (confirmed ? t("Подтвердить удаление из Obsidian: ") : t("Удалить запись: ")) + note.title);
  };
  setConfirmation((deleteConfirmations.get(note.id) || 0) > Date.now());
  remove.addEventListener("click", () => {
    if (remove.dataset.confirm !== "true") {
      const expires = Date.now() + 8000;
      deleteConfirmations.set(note.id, expires);
      setConfirmation(true);
      window.setTimeout(() => {
        if (deleteConfirmations.get(note.id) === expires) {
          deleteConfirmations.delete(note.id);
          if (remove.isConnected) setConfirmation(false);
        }
      }, 8000);
      return;
    }
    deleteConfirmations.delete(note.id);
    void changeNote(note, "delete", {}, card);
  });
  return remove;
}
function makeUnscheduledToggle(note, card) {
  const button = node("button", "feedback-button unscheduled-toggle", note.show_in_unscheduled ? t("Убрать из дел без срока") : t("Показать в делах без срока"));
  button.type = "button";
  button.addEventListener("click", () => mutate(note, {show_in_unscheduled: !note.show_in_unscheduled}, card));
  return button;
}
function makeEditButton(note) {
  const button = node("button", "note-edit", t("Изменить"));
  button.type = "button";
  button.title = t("Изменить запись");
  button.setAttribute("aria-label", t("Изменить запись: {title}", {title: note.title}));
  button.addEventListener("click", event => {
    event.stopPropagation();
    openEditor(note);
  });
  return button;
}
function makeDraggableRow(note, card, row) {
  row.draggable = true;
  row.title = row.title || t("Нажмите, чтобы раскрыть; перетащите в другой раздел");
  row.addEventListener("dragstart", event => {
    if (event.target.closest("button, input, select, textarea, a")) {
      event.preventDefault();
      return;
    }
    draggedTaskId = note.id;
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", note.id);
    card.classList.add("task-dragging");
  });
  row.addEventListener("dragend", () => { draggedTaskId = null; clearTaskDropMarks(); });
}
function clearTaskDropMarks() {
  document.querySelectorAll(".task-drop-before,.task-drop-after,.task-dragging,.drop-target,.record-drop-target").forEach(el => {
    el.classList.remove("task-drop-before", "task-drop-after", "task-dragging", "drop-target", "record-drop-target");
  });
}
function exactPlannerTasks() {
  if (!dashboard || !selectedDate || !selectedWeekStart) return [];
  return dayTasks(visibleWeekTasks(dashboard, weekDates(selectedWeekStart)), selectedDate);
}
async function reorderPlannerTask(draggedId, targetId, after) {
  const tasks = exactPlannerTasks();
  const current = tasks.map(note => note.id);
  const orderedIds = reorderedIds(current, draggedId, targetId, after);
  if (orderedIds === current || orderedIds.every((id, index) => id === current[index])) return;
  const expectedVersions = Object.fromEntries(tasks.map(note => [note.id, note.version]));
  const list = $("today-list");
  list.classList.add("tasks-saving-order");
  try {
    await send("reorder-tasks", {due:selectedDate, orderedIds, expectedVersions});
    await refresh();
    notice(t("Порядок дел сохранён."));
  } catch (error) {
    notice(error.message, true);
    await refresh();
  } finally {
    list.classList.remove("tasks-saving-order");
  }
}
function renderNote(note, {task = false, movable = false, compact = movable, library = false} = {}) {
  const el = node("article", "note");
  el.dataset.noteId = note.id;
  if (movable) {
    el.classList.add("reorderable-task");
    el.addEventListener("dragover", event => {
      const draggedId = draggedTaskId || event.dataTransfer.getData("text/plain");
      if (!draggedId || draggedId === note.id || !exactPlannerTasks().some(item => item.id === draggedId)) return;
      event.preventDefault();
      const after = event.clientY >= el.getBoundingClientRect().top + el.getBoundingClientRect().height / 2;
      el.classList.toggle("task-drop-before", !after);
      el.classList.toggle("task-drop-after", after);
    });
    el.addEventListener("dragleave", event => { if (!el.contains(event.relatedTarget)) el.classList.remove("task-drop-before", "task-drop-after"); });
    el.addEventListener("drop", event => {
      const draggedId = draggedTaskId || event.dataTransfer.getData("text/plain");
      if (!draggedId || draggedId === note.id || !exactPlannerTasks().some(item => item.id === draggedId)) return;
      event.preventDefault(); event.stopPropagation();
      const after = event.clientY >= el.getBoundingClientRect().top + el.getBoundingClientRect().height / 2;
      clearTaskDropMarks();
      void reorderPlannerTask(draggedId, note.id, after);
    });
  }
  const heading = node("div", "note-heading");
  if (task) {
    const done = node("button", "complete", compact ? "" : "✓");
    done.type = "button";
    done.title = t("Отметить выполненным");
    done.setAttribute("aria-label", t("Отметить выполненным: {title}", {title: note.title}));
    done.addEventListener("click", () => mutate(note, {status:"done"}, el));
    heading.append(done);
  }
  const title = node("strong", "note-title", note.title);
  let taskDisclosureTimer = null;
  let content = el;
  if (compact || library) {
    el.classList.add(library ? "compact-library" : "compact-task");
    const details = node("details", library ? "library-details" : "task-details");
    const expanded = library ? expandedLibrary : expandedTasks;
    details.open = expanded.has(note.id);
    details.addEventListener("toggle", () => {
      if (!details.isConnected) return;
      if (details.open) expanded.add(note.id);
      else expanded.delete(note.id);
      if (library) void persistLibraryState();
    });
    const summary = node("summary", library ? "library-summary" : "task-summary");
    summary.title = note.title;
    summary.addEventListener("click", event => {
      event.preventDefault();
      // A keyboard-generated click has detail 0 and must remain immediate. A
      // pointer click waits briefly so a double click can change importance
      // without flashing this disclosure open first.
      if (!task || !compact || event.detail === 0) {
        details.open = !details.open;
        return;
      }
      if (event.detail !== 1) return;
      clearTimeout(taskDisclosureTimer);
      taskDisclosureTimer = setTimeout(() => {
        if (details.isConnected) details.open = !details.open;
      }, 240);
    });
    summary.append(title);
    if (library) summary.append(node("span", "library-kind", kinds[note.kind] || note.kind));
    details.append(summary);
    heading.append(details);
    content = details;
  } else heading.append(title);
  if (task && compact) {
    const level = note.importance || "normal";
    el.classList.toggle("is-important", level === "high" || level === "critical");
    el.classList.toggle("is-critical", level === "critical");
    if (level === "critical") el.dataset.fireKey = noteFireKey(note);
    const toggle = node("button", "importance-toggle");
    toggle.type = "button";
    toggle.hidden = !showImportanceControl;
    toggle.disabled = importancePending.has(note.id);
    toggle.dataset.level = level;
    labelImportanceToggle(toggle, level, note.title);
    toggle.addEventListener("click", event => { event.stopPropagation(); void toggleNoteImportance(note, el, firePoint(event, toggle)); });
    heading.append(toggle);
    if (level === "high" || level === "critical") {
      const clear = node("button", "importance-clear", "−");
      clear.type = "button";
      clear.hidden = !showImportanceControl;
      clear.disabled = importancePending.has(note.id);
      clear.setAttribute("aria-label", t("Снять выделение с дела «{title}»", {title: note.title}));
      clear.title = t("Снять выделение");
      clear.addEventListener("click", event => {
        event.preventDefault();
        event.stopPropagation();
        void toggleNoteImportance(note, el, null, "normal");
      });
      heading.append(clear);
    }
    el.addEventListener("dblclick", event => {
      if (event.target.closest("button,a,input,textarea,select,label,.move-details,.note-management,.body-details")) return;
      clearTimeout(taskDisclosureTimer);
      // Prevent the browser's second click from applying a native summary
      // toggle in browsers where it has not already been cancelled above.
      event.preventDefault();
      void toggleNoteImportance(note, el, firePoint(event, toggle));
    });
  }
  if (note.id) makeDraggableRow(note, el, heading);
  el.append(heading);
  const context = dashboard?.contexts.find(n=>n.id===note.context_id);
  const due = note.due && note.due !== selectedDate ? formatDay(note.due) : null;
  const horizon = note.planning_horizon === "week" ? t("На неделе") : note.planning_horizon === "month" ? t("На месяц") : null;
  const meta = [due, horizon, kinds[note.kind] || note.kind, statuses[note.status] || note.status, context?.title, note.topic, importance[note.importance || "normal"], note.show_in_carousel === false ? t("Без карусели") : null, note.show_in_unscheduled ? t("Также в делах без срока") : null];
  content.append(node("div", "note-meta", meta.filter(Boolean).join(" · ")));
  renderBody(content, note);
  renderRasterGallery(library ? content : el, note.attachments, {onOpen:(item, image) => openImageViewer(item, image, note)});
  showAttachments(content, note);
  if (compact && note.kind === "thought") content.append(makeUnscheduledToggle(note, el));
  if (compact && ["task", "purchase"].includes(note.kind)) {
    const controls = node("details", "move-details");
    const fields = node("div", "move-controls");
    const earliestDate = [dashboard?.date, moscowToday()].filter(Boolean).sort().at(-1);
    let chosenTarget;
    if (!note.due) {
      const label = node("label", "", t("Переместить в"));
      const quick = node("select");
      for (const [value, text] of [["bucket:none", t("Без срока")], ["bucket:week", t("На неделе")], ["bucket:month", t("На месяц")], ...currentWeekDueOptions(earliestDate).map(item => [item.value, item.label]), ["custom", t("Другая дата…")]]) {
        const option = node("option", "", text); option.value = value; quick.append(option);
      }
      quick.value = "bucket:" + (note.planning_horizon || "none");
      const customLabel = node("label", "", t("Другая дата"));
      const customDate = node("input"); customDate.type = "date"; customDate.min = earliestDate;
      customLabel.append(customDate); customLabel.hidden = true;
      quick.addEventListener("change", () => {
        customLabel.hidden = quick.value !== "custom";
        if (!customLabel.hidden) customDate.focus();
      });
      label.append(quick); fields.append(label, customLabel);
      chosenTarget = () => quick.value === "custom" ? customDate.value : quick.value;
    } else {
      const label = node("label", "", t("Новая дата"));
      const date = node("input"); date.type = "date"; date.value = note.due; date.min = earliestDate;
      label.append(date); fields.append(label);
      chosenTarget = () => date.value;
    }
    const button = node("button", "secondary", t("Перенести"));
    button.type = "button";
    button.addEventListener("click", () => {
      const target = chosenTarget();
      if (!target || target === "custom") { notice(t("Выберите, куда перенести дело."), true); return; }
      if (target.startsWith("bucket:")) {
        const planning_horizon = target.slice(7);
        const nextHorizon = planning_horizon === "none" ? null : planning_horizon;
        if (note.due !== null || note.planning_horizon !== nextHorizon) mutate(note, {due:null, planning_horizon:nextHorizon}, el);
        return;
      }
      if (target < earliestDate) { notice(t("Выберите сегодняшний или будущий день."), true); return; }
      if (target !== note.due) mutate(note, {due:target, planning_horizon:null}, el);
    });
    fields.append(button);
    controls.append(node("summary", "", t("Перенести…")), fields); content.append(controls);
  }
  if (note.url?.startsWith("https://") || note.url?.startsWith("http://")) {
    const link = node("a", "", t("Открыть ссылку ↗"));
    link.href = note.url; link.target = "_blank"; link.rel = "noopener noreferrer"; content.append(link);
  }
  if (note.show_in_carousel === false && note.kind !== "project") {
    const show = node("button", "feedback-button carousel-restore", t("Вернуть в карусель"));
    show.type = "button";
    show.addEventListener("click", () => mutate(note, {show_in_carousel:true}, el));
    content.append(show);
  }
  const management = node("details", "note-management");
  management.open = expandedManagement.has(note.id);
  management.addEventListener("toggle", () => {
    if (!management.isConnected) return;
    if (management.open) expandedManagement.add(note.id);
    else expandedManagement.delete(note.id);
  });
  management.append(node("summary", "", t("Настройки")));
  management.append(makeEditButton(note));
  if (note.kind === "thought") management.append(makeUnscheduledToggle(note, el));
  management.append(makeDeleteButton(note, el));
  (library || compact ? content : el).append(management);
  return el;
}
function rasterAttachments(note) {
  return Array.isArray(note.attachments) ? note.attachments.filter(item => safeRaster(item?.mime || item?.type)) : [];
}
/** Poster colours: a stable hue for text cards, the sampled image colour for pictures. */
function applyStackLook(el, note) {
  el.style.setProperty("--poster-hue", String(posterHue(note.id)));
  const color = rasterAttachments(note).map(cachedImageColor).find(Boolean);
  if (color) setStackGlow(el, color);
}
function setStackGlow(el, color) {
  el.style.setProperty("--glow-rgb", color.join(" "));
  el.classList.add("has-image-color");
}
function stackImageReady(item, image, figure) {
  figure.dataset.fit = imageFit(image.naturalWidth, image.naturalHeight, figure.clientWidth, figure.clientHeight);
  let backdrop = figure.querySelector(".note-image-backdrop");
  if (!backdrop) {
    backdrop = document.createElement("canvas");
    backdrop.className = "note-image-backdrop";
    backdrop.width = 24; backdrop.height = 24;
    backdrop.setAttribute("aria-hidden", "true");
    figure.prepend(backdrop);
  }
  const color = paintImageBackdrop(image, backdrop, rasterKey(item));
  const card = figure.closest(".stack-note");
  if (color && card) setStackGlow(card, color);
}
/**
 * One Recall card. The same anatomy is used for the interactive reading card
 * and for passive copies in the stack, so a card can move between the two
 * without a visible change.
 */
function renderRecallNote(note, {passive = false} = {}) {
  const el = node("article", "note stack-note");
  el.dataset.noteId = note.id;
  applyStackLook(el, note);
  const images = rasterAttachments(note);
  el.classList.toggle("has-stack-image", images.length > 0);
  if (images.length) {
    const gallery = renderRasterGallery(el, images, {draggable:false, limit:1, onReady:stackImageReady,
      onOpen: passive ? null : (item, image) => openImageViewer(item, image, note)});
    if (images.length > 1) gallery.append(node("span", "stack-image-count", `+${images.length - 1}`));
  }
  const heading = node("div", "note-heading");
  const disclosure = node(passive ? "div" : "button", "carousel-disclosure");
  if (!passive) disclosure.type = "button";
  disclosure.append(node("div", "note-meta", kinds[note.kind] || note.kind), node("strong", "note-title", note.title));
  if (!images.length) {
    const excerpt = recallExcerpt(note);
    if (excerpt) disclosure.append(node("span", "stack-excerpt", excerpt));
  }
  heading.append(disclosure);
  el.append(heading);
  const context = dashboard?.contexts.find(item => item.id === note.context_id);
  const stackContext = [context?.title, note.topic].filter(Boolean).join(" · ");
  if (stackContext) el.append(node("span", "stack-context", stackContext));
  if (passive) return el;

  const more = node("div", "carousel-more");
  more.id = `carousel-details-${note.id}`;
  more.hidden = true;
  disclosure.setAttribute("aria-controls", more.id);
  disclosure.setAttribute("aria-expanded", "false");
  disclosure.setAttribute("aria-label", t("Открыть запись: {title}", {title: note.title}));
  const toggleMore = () => {
    if (rotationAnimating || Date.now() < suppressRotationClickUntil) return;
    more.hidden = !more.hidden;
    $("rotation-stage").classList.toggle("stack-expanded", !more.hidden);
    disclosure.setAttribute("aria-expanded", String(!more.hidden));
    disclosure.setAttribute("aria-label", (more.hidden ? t("Открыть запись: ") : t("Свернуть запись: ")) + note.title);
    resurfaceHoldUntil = Date.now() + RESURFACE_HOLD_MS;
  };
  disclosure.addEventListener("click", toggleMore);
  el.addEventListener("click", event => {
    if (more.contains(event.target)) return;
    if (event.target.closest("button,a,input,textarea,select,summary,details,label")) return;
    toggleMore();
  });
  renderBody(more, note);
  if (images.length > 1) renderRasterGallery(more, images.slice(1), {draggable:false, onOpen:(item, image) => openImageViewer(item, image, note)});
  showAttachments(more, note);
  if (note.url?.startsWith("https://") || note.url?.startsWith("http://")) {
    const link = node("a", "", t("Открыть ссылку ↗"));
    link.href = note.url; link.target = "_blank"; link.rel = "noopener noreferrer"; more.append(link);
  }
  more.append(node("p", "reason", note.reason || t("Запись снова появилась в библиотеке.")));
  // «Чаще» stops at «Высокая»: the fire is a day mark, set from the day list.
  const levels = ["low", "normal", "high", "critical"];
  const level = Math.max(0, levels.indexOf(note.importance || "normal"));
  const preferences = node("div", "carousel-preferences");
  for (const [label, next, disabled] of [[t("Чаще"), levels[level + 1], level >= 2], [t("Реже"), levels[level - 1], level <= 0]]) {
    const button = node("button", "feedback-button", label);
    button.type = "button";
    button.disabled = disabled;
    if (!disabled) button.addEventListener("click", () => mutate(note, {importance:next}, el));
    preferences.append(button);
  }
  const hide = node("button", "feedback-button carousel-hide", t("Не показывать"));
  hide.type = "button";
  hide.addEventListener("click", () => mutate(note, {show_in_carousel:false}, el));
  preferences.append(hide);
  more.append(preferences);
  const actions = node("div", "feedback-actions");
  for (const [action, label] of [["soon",t("Скоро")],["later",t("Позже")],["archive",t("В архив")]]) {
    const button = node("button", "feedback-button", label);
    button.addEventListener("click", () => feedback(note, action, el));
    actions.append(button);
  }
  if (note.kind === "thought") actions.append(makeUnscheduledToggle(note, el));
  actions.append(makeEditButton(note), makeDeleteButton(note, el));
  more.append(actions);
  el.append(more);
  return el;
}
async function changeNote(note, type, extra, card) {
  if (mutating.has(note.id)) return;
  mutating.add(note.id);
  const buttons = [...card.querySelectorAll("button")];
  const disabled = buttons.map(button => button.disabled);
  buttons.forEach(button => button.disabled = true);
  if (type === "delete") {
    const token = noteDeletions.begin(note.id);
    renderEffectiveNotes(true);
    try {
      await send(type, {id:note.id, version:note.version, ...extra});
      noteDeletions.succeed(token,noteReadSeq);
      notice(t("Запись удалена из панели и Obsidian."));
    } catch (error) {
      noteDeletions.fail(token);
      renderEffectiveNotes(true);
      notice(error.message, true);
    } finally {
      mutating.delete(note.id);
      buttons.forEach((button, index) => button.disabled = disabled[index]);
      void refresh();
    }
    return;
  }
  try {
    await send(type, {id:note.id, version:note.version, ...extra});
    await refresh();
    if (type === "delete") notice(t("Запись удалена из панели и Obsidian."));
  }
  catch (error) { notice(error.message, true); await refresh(); }
  finally { mutating.delete(note.id); buttons.forEach((button, index) => button.disabled = disabled[index]); }
}
function mutate(note, patch, card) { return changeNote(note, "update", {patch}, card); }
function setLocalNoteImportance(id, value) {
  const changed = new Map();
  const visit = source => {
    if (!source || typeof source !== "object") return;
    if (Array.isArray(source)) { source.forEach(visit); return; }
    if (source.id === id && !changed.has(source)) { changed.set(source, source.importance); source.importance = value; }
    Object.values(source).forEach(child => { if (child && typeof child === "object") visit(child); });
  };
  visit(rawDashboard); visit(rawAllNotes);
  return changed;
}
function labelImportanceToggle(toggle, level, title) {
  const burning = level === "critical";
  toggle.setAttribute("aria-pressed", String(burning || level === "high"));
  toggle.setAttribute("aria-label", (burning ? t("Очень важное, снять важность: ") : level === "high" ? t("Сделать очень важным: ") : t("Сделать важным: ")) + title);
  toggle.title = burning ? t("Очень важное — нажмите, чтобы погасить") : level === "high" ? t("Сделать очень важным") : t("Сделать важным");
}
/** Where the fire starts: the pointer, or the dot itself for a keyboard press. */
function firePoint(event, fallback) {
  if (event?.detail > 0 && Number.isFinite(event.clientX)) return {x:event.clientX, y:event.clientY};
  const rect = fallback?.getBoundingClientRect();
  return rect?.width ? {x:rect.left + rect.width / 2, y:rect.top + rect.height / 2} : null;
}
/** Play the chosen ignition sound, leaning a little towards where the fire starts; it lasts as long as the ignition. */
function playIgnition(point = null) {
  if (ignitionSound === "none") return;
  const pan = point && innerWidth ? Math.max(-1, Math.min(1, point.x / innerWidth * 2 - 1)) / 2 : 0;
  playIgnitionSound(ignitionSound, {pan, length:IGNITION_MS / 1000});
}
/** Light a fire with its sound. Every caller is a click or a submit: Chrome plays audio only after a user gesture. */
function igniteFire(layer, key, point = null) {
  layer.ignite(key, point);
  playIgnition(point);
}
/** Burning rows in one group: a day (tasks and calendar events together) or the dateless backlog. */
function burningCount(group, exceptKey = "") {
  if (!dashboard) return 0;
  const tasks = allDashboardNotes().filter(note => isVisibleTask(note) && note.importance === "critical"
    && noteFireKey(note) !== exceptKey && fireGroupOfTask(note, dashboard.date) === group).length;
  if (group === "backlog") return tasks;
  const now = Date.now();
  return tasks + calendarEventsForDate(calendar.events, group).filter(event => calendarFireKey(event) !== exceptKey
    && calendarImportanceLevel(calendarImportance, event) === "critical" && calendarEventPhase(event, now) !== "finished").length;
}
function denyFire(row, group) {
  const layers = group === "backlog" ? [fireLayers.backlog] : [fireLayers.calendar, fireLayers.today];
  const own = row?.closest("#calendar-section") ? fireLayers.calendar : layers.at(-1);
  own.deny(row, t("Только одно очень важное на день"), t("Сначала погасите текущую отметку."));
  for (const layer of layers) if (layer !== own) layer.flare();
}
async function toggleNoteImportance(note, row = null, point = null, forcedNext = null) {
  if (!note?.id || importancePending.has(note.id)) return;
  const current = note.importance || "normal";
  const group = fireGroupOfTask(note, dashboard?.date);
  const {next, blocked} = forcedNext === "normal"
    ? {next:"normal", blocked:false}
    : importanceStep(current, burningCount(group, noteFireKey(note)));
  if (blocked) { denyFire(row, group); return; }
  const fire = group === "backlog" ? fireLayers.backlog : fireLayers.today;
  if (next === "critical") igniteFire(fire, noteFireKey(note), point);
  if (current === "critical") fire.extinguish(noteFireKey(note));
  const changed = setLocalNoteImportance(note.id, next);
  importancePending.add(note.id);
  renderEffectiveNotes(true);
  try {
    await send("update", {id:note.id, version:note.version, patch:{importance:next}});
    await refresh();
  } catch (error) {
    for (const [record, previous] of changed) record.importance = previous;
    notice(error.message || t("Не удалось изменить важность."), true);
  } finally {
    importancePending.delete(note.id);
    renderEffectiveNotes(true);
  }
}
function feedback(note, action, card) { return changeNote(note, "feedback", {action}, card); }
function renderList(id, notes, message, options = {}) {
  const target = $(id); target.replaceChildren(...notes.map(n=>renderNote(n, options)));
  if (!notes.length) empty(target,message);
}
function renderEffectiveNotes(instant = false) {
  const projected = noteDeletions.project(rawDashboard,rawAllNotes);
  dashboard = projected.dashboard;
  allNotes = projected.notes;
  if (!dashboard) return;
  renderPlanner();
  renderBacklog();
  $("inbox-count").textContent = String(dashboard.inbox.length);
  renderList("inbox-list",dashboard.inbox,t("Пока пусто."));
  $("library-count").textContent = String(allNotes.length);
  renderLibrary();
  renderRotation(1, instant);
}
function formatDay(date) {
  const [year, month, day] = date.split("-").map(Number);
  return new Intl.DateTimeFormat(localeTag(), {weekday:"short", day:"numeric", month:"short", timeZone:"UTC"}).format(new Date(Date.UTC(year, month - 1, day)));
}
function headingDay(date, today) {
  if (date === today) return t("Сегодня");
  const [year, month, day] = date.split("-").map(Number);
  const label = new Intl.DateTimeFormat(localeTag(), {weekday:"long", timeZone:"UTC"}).format(new Date(Date.UTC(year, month - 1, day)));
  return label.charAt(0).toUpperCase() + label.slice(1);
}
function fullDayDate(date) {
  const [year, month, day] = date.split("-").map(Number);
  return new Intl.DateTimeFormat(localeTag(), {day:"numeric", month:"long", year:"numeric", timeZone:"UTC"}).format(new Date(Date.UTC(year, month - 1, day)));
}
function moscowToday() {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", {timeZone:"Europe/Moscow",year:"numeric",month:"2-digit",day:"2-digit"})
    .formatToParts(new Date()).filter(part => part.type !== "literal").map(part => [part.type,part.value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}
function renderCalendarConnections() {
  const container = $("calendar-connections");
  const calendars = Array.isArray(calendar.calendars) ? calendar.calendars : [];
  container.replaceChildren();
  $("calendar-disconnect").hidden = calendars.length === 0 && !calendar.configured;
  for (const connected of calendars) {
    const row = node("div", "calendar-connection");
    const info = node("div", "calendar-connection-info");
    info.append(node("strong", "calendar-connection-name", connected.name || t("Google Календарь")));
    info.append(node("span", "calendar-connection-updated", connected.last_updated
      ? t("Обновлён: {last_updated}", {last_updated: new Date(connected.last_updated).toLocaleString(localeTag())})
      : t("Ещё не загружен")));
    const remove = node("button", "secondary calendar-remove", t("Удалить"));
    remove.type = "button";
    remove.setAttribute("aria-label", t("Отключить календарь: {name}", {name: (connected.name || t("Google Календарь"))}));
    remove.addEventListener("click", async () => {
      if (remove.disabled) return;
      remove.disabled = true;
      try {
        await send("calendar-remove", {calendarId:connected.id});
        notice(t("Календарь отключён."));
        await loadCalendarWeek();
      } catch (error) {
        notice(error.message, true);
        remove.disabled = false;
      }
    });
    row.append(info, remove);
    container.append(row);
  }
}
function renderCalendarWriteStatus() {
  const access = calendar.write_access || {};
  $("calendar-oauth-status").textContent = access.authorized
    ? t("Управление подключено: события можно переносить и удалять.")
    : access.client_configured
      ? t("OAuth-клиент сохранён. Нажмите «Подключить Google API» и подтвердите доступ.")
      : t("Управление событиями не подключено.");
  $("calendar-oauth-disconnect").hidden = !access.authorized;
}
function mutateCalendarOccurrence(event, kind, newDate = null) {
  const token = kind === "move" ? calendarOverlay.beginMove(event,newDate) : calendarOverlay.beginDelete(event);
  if (!token) return;
  calendar.events = calendarOverlay.project(rawCalendarEvents);
  renderCalendar();
  void (async () => {
    try {
      await send(kind === "move" ? "calendar-event-move" : "calendar-event-delete", {
        eventId:event.id, calendarId:event.calendar_id, start:event.start,
        ...(kind === "move" ? {newDate} : {}),
      });
      calendarOverlay.succeed(token,calendarRequest);
      notice(kind === "move" ? t("Событие перенесено в Google Календаре.") : t("Удалён только выбранный экземпляр события."));
      void loadCalendarWeek(true);
    } catch (error) {
      calendarOverlay.fail(token);
      calendar.events = calendarOverlay.project(rawCalendarEvents);
      renderCalendar();
      notice(error.message || t("Не удалось изменить событие."), true);
    }
  })();
}
async function toggleCalendarImportance(event, row = null, point = null, forcedNext = null) {
  const previous = calendarImportance;
  const current = calendarImportanceLevel(previous, event);
  const key = calendarFireKey(event);
  const {next, blocked} = forcedNext === "normal"
    ? {next:"normal", blocked:false}
    : importanceStep(current, burningCount(selectedDate, key));
  if (blocked) { denyFire(row, selectedDate); return; }
  if (next === "critical") igniteFire(fireLayers.calendar, key, point);
  if (current === "critical") fireLayers.calendar.extinguish(key);
  calendarImportance = withCalendarImportance(previous, event, next);
  renderCalendar();
  try {
    await chrome.storage.local.set({[CALENDAR_IMPORTANCE_KEY]:calendarImportance});
  } catch (error) {
    calendarImportance = previous;
    renderCalendar();
    notice(error.message || t("Не удалось сохранить важность события."), true);
  }
}
// The day list ends with the calendar, so the day's fires follow every day render.
function renderCalendar() {
  renderCalendarRows();
  fireLayers.calendar.sync();
  fireLayers.today.sync();
}
function renderCalendarRows() {
  const status = $("calendar-status");
  const warning = $("calendar-warning");
  const list = $("calendar-list"); const previousScroll = list.scrollTop; list.replaceChildren();
  const count = Number.isInteger(calendar.calendar_count) ? calendar.calendar_count : calendar.calendars.length;
  $("calendar-config-status").textContent = !calendar.configured ? t("Календари не подключены.")
    : calendar.last_updated ? t("Подключено календарей: {count}. Последнее обновление: {last_updated}", {count, last_updated: new Date(calendar.last_updated).toLocaleString(localeTag())})
    : t("Подключено календарей: {count}. Ещё не загружены из Google.", {count});
  renderCalendarConnections();
  renderCalendarWriteStatus();
  if (!calendar.configured && !calendar.error) { $("calendar-section").hidden = true; return; }
  const cachedWarning = Boolean(calendar.stale && calendar.events.length);
  const warningMessage = calendar.error || t("Расписание временно показывается из сохранённой копии.");
  warning.hidden = !cachedWarning;
  warning.title = cachedWarning ? warningMessage : "";
  warning.setAttribute("aria-label", cachedWarning ? warningMessage : "");
  const now = Date.now();
  const events = calendarEventsForDate(calendar.events, selectedDate)
    .map(event => ({event, phase:calendarEventPhase(event, now)}))
    .filter(({phase}) => phase !== "finished");
  events.sort(compareCalendarEvents);
  const section = $("calendar-section");
  const tasksHeading = $("today-list").previousElementSibling;
  if (section.nextElementSibling !== tasksHeading) tasksHeading.before(section);
  section.hidden = !events.length && !calendar.error;
  status.hidden = cachedWarning || (!calendar.error && !calendar.stale);
  status.textContent = status.hidden ? "" : calendar.error || t("Расписание может быть устаревшим.");
  status.classList.toggle("calendar-stale", !status.hidden);
  for (const {event, phase} of events) {
    const writable = Boolean(calendar.write_access?.authorized);
    const item = node(writable ? "details" : "div", "calendar-event" + (phase === "normal" ? "" : " calendar-event--" + phase));
    const eventLevel = calendarImportanceLevel(calendarImportance, event);
    item.classList.toggle("is-important", eventLevel !== "normal");
    item.classList.toggle("is-critical", eventLevel === "critical");
    if (eventLevel === "critical") item.dataset.fireKey = calendarFireKey(event);
    const row = writable ? node("summary", "calendar-event-row") : item;
    let calendarDisclosureTimer = null;
    const description = node("span", "calendar-event-description");
    description.append(node("span", "calendar-event-title", event.title || t("Событие без названия")));
    if (count > 1 && event.calendar_name) description.append(node("span", "calendar-event-source", event.calendar_name));
    const check = node("button", "calendar-event-check");
    check.type = "button";
    check.setAttribute("role", "checkbox");
    check.setAttribute("aria-checked", "false");
    check.setAttribute("aria-label", t("Удалить из Google Календаря только событие «{title}» за {selectedDate}", {title: event.title || t("Без названия"), selectedDate}));
    check.title = writable ? t("Удалить только этот экземпляр из Google Календаря") : t("Для удаления подключите управление Google API в настройках");
    check.disabled = !writable || Boolean(event.optimistic_pending);
    check.addEventListener("click", eventClick => {
      eventClick.preventDefault();
      eventClick.stopPropagation();
      if (!check.disabled) mutateCalendarOccurrence(event, "delete");
    });
    row.append(check, node("span", "calendar-event-time", calendarEventTime(event)), description);
    const importanceToggle = node("button", "importance-toggle");
    importanceToggle.type = "button";
    importanceToggle.hidden = !showImportanceControl;
    importanceToggle.dataset.level = eventLevel;
    labelImportanceToggle(importanceToggle, eventLevel, t("событие «{title}»", {title: (event.title || t("Событие без названия"))}));
    importanceToggle.addEventListener("click", eventClick => { eventClick.preventDefault(); eventClick.stopPropagation(); void toggleCalendarImportance(event, item, firePoint(eventClick, importanceToggle)); });
    row.append(importanceToggle);
    if (eventLevel === "high" || eventLevel === "critical") {
      const clear = node("button", "importance-clear", "−");
      clear.type = "button";
      clear.hidden = !showImportanceControl;
      clear.setAttribute("aria-label", t("Снять выделение с события «{title}»", {title: (event.title || t("Событие без названия"))}));
      clear.title = t("Снять выделение");
      clear.addEventListener("click", eventClick => {
        eventClick.preventDefault();
        eventClick.stopPropagation();
        void toggleCalendarImportance(event, item, null, "normal");
      });
      row.append(clear);
    }
    item.addEventListener("dblclick", eventClick => {
      if (eventClick.target.closest("button,a,input,textarea,select,label,.calendar-event-actions")) return;
      clearTimeout(calendarDisclosureTimer);
      eventClick.preventDefault();
      void toggleCalendarImportance(event, item, firePoint(eventClick, importanceToggle));
    });
    if (writable) {
      row.addEventListener("click", eventClick => {
        eventClick.preventDefault();
        // Keep keyboard disclosure immediate while a pointer double click
        // changes importance without an intermediate visible open state.
        if (eventClick.detail === 0) { item.open = !item.open; return; }
        if (eventClick.detail !== 1) return;
        clearTimeout(calendarDisclosureTimer);
        calendarDisclosureTimer = setTimeout(() => {
          if (item.isConnected) item.open = !item.open;
        }, 240);
      });
      item.append(row);
      const actions = node("div", "calendar-event-actions");
      const date = node("input"); date.type = "date"; date.value = event.start.slice(0, 10);
      date.disabled = Boolean(event.optimistic_pending);
      date.setAttribute("aria-label", t("Новая дата события: {title}", {title: event.title}));
      const move = node("button", "secondary", t("Перенести")); move.type = "button";
      move.disabled = Boolean(event.optimistic_pending);
      move.addEventListener("click", () => {
        if (!date.value || move.disabled || date.value === event.start.slice(0,10)) return;
        mutateCalendarOccurrence(event,"move",date.value);
      });
      actions.append(date, move);
      item.append(actions);
    }
    list.append(item);
  }
  list.scrollTop = previousScroll;
}
async function loadCalendarWeek(force = false) {
  if (!selectedWeekStart) return;
  const request = ++calendarRequest;
  const dates = weekDates(selectedWeekStart);
  try {
    const [config, response] = await Promise.all([
      send("calendar-config"),
      send("calendar-events", {start:dates[0], end:dates.at(-1), force}),
    ]);
    if (request !== calendarRequest) return;
    rawCalendarEvents = Array.isArray(response.events) ? response.events : [];
    calendarOverlay.reconcile(rawCalendarEvents, {start:dates[0], end:dates.at(-1), stale:Boolean(response.stale || response.error), readSeq:request});
    calendar = {
      configured:Boolean(config.configured ?? response.configured),
      calendar_count:Number.isInteger(config.calendar_count) ? config.calendar_count : (Array.isArray(config.calendars) ? config.calendars.length : 0),
      calendars:Array.isArray(config.calendars) ? config.calendars : [],
      write_access:config.write_access || {client_configured:false,authorized:false},
      events:calendarOverlay.project(rawCalendarEvents),
      last_updated:config.last_updated || response.last_updated || null,
      stale:Boolean(response.stale),
      error:response.error || null,
    };
  } catch (error) {
    if (request !== calendarRequest) return;
    const message = isOutdatedWorker(error)
      ? t("Обновите расширение на chrome://extensions и откройте панель заново.")
      : error.message || t("Ошибка подключения");
    calendar = {...calendar, error:message, stale:Boolean(calendar.events.length)};
  }
  renderCalendar();
}
function renderPlanner() {
  if (!dashboard || !selectedDate || !selectedWeekStart) return;
  const dates = weekDates(selectedWeekStart);
  const tasks = visibleWeekTasks(dashboard, dates);
  const overdue = selectedDate === dashboard.date ? overdueTasks(dashboard, dashboard.date) : [];
  $("planner-heading").textContent = headingDay(selectedDate, dashboard.date);
  $("planner-date").dateTime = selectedDate;
  $("planner-date").textContent = fullDayDate(selectedDate);
  $("today-jump").hidden = selectedDate === dashboard.date;
  const list = $("today-list"); const previousScroll = list.scrollTop; list.replaceChildren();
  const exact = dayTasks(tasks, selectedDate);
  if (selectedDate === dashboard.date) {
    if (exact.length) list.append(...exact.map(note => renderNote(note, {task:true, movable:true})));
    if (overdue.length) list.append(node("h3", "", t("Просрочено")), ...overdue.map(note => renderNote(note, {task:true, movable:true})));
  } else {
    if (exact.length) list.append(...exact.map(note => renderNote(note, {task:true, movable:true})));
  }
  if (!list.children.length) empty(list, selectedDate === dashboard.date ? t("Сегодня дел нет.") : t("На этот день дел нет."));
  list.scrollTop = previousScroll;
  renderCompleted();
  renderCalendar();
}
function renderCompleted() {
  const records = (dashboard?.completed_tasks || []).filter(note => note.completed_at?.slice(0, 10) === selectedDate);
  const section = $("completed-section");
  const list = $("completed-list");
  const previousScroll = list.scrollTop;
  list.replaceChildren();
  $("completed-count").textContent = String(records.length);
  section.hidden = records.length === 0;
  for (const note of records) {
    const row = node("div", "completed-item");
    row.dataset.noteId = note.id;
    const title = node("span", "completed-title", note.title);
    title.title = note.title;
    const restore = node("button", "completed-restore", t("Вернуть"));
    restore.type = "button";
    restore.title = t("Вернуть в дела: {title}", {title: note.title});
    restore.setAttribute("aria-label", restore.title);
    restore.addEventListener("click", () => mutate(note, {status:"active"}, row));
    const edit = makeEditButton(note);
    edit.textContent = "⋯";
    edit.classList.add("completed-edit");
    const remove = makeDeleteButton(note, row, "×");
    remove.classList.add("completed-delete");
    row.append(node("span", "completed-check", "✓"), title, restore, edit, remove);
    makeDraggableRow(note, row, row);
    list.append(row);
  }
  list.scrollTop = previousScroll;
}
function selectDay(date) {
  const newWeek = weekStart(date);
  const changedWeek = newWeek !== selectedWeekStart;
  selectedDate = date;
  selectedWeekStart = newWeek;
  if (changedWeek) {
    rawCalendarEvents = [];
    calendar = {...calendar, events:calendarOverlay.project(rawCalendarEvents), stale:false, error:null};
  }
  renderPlanner();
  if (changedWeek) void loadCalendarWeek();
}
function shiftDay(amount) { if (selectedDate) selectDay(addDays(selectedDate, amount)); }
function allDashboardNotes() {
  if (!dashboard) return [];
  const buckets = [dashboard.today, dashboard.week, dashboard.upcoming, dashboard.unscheduled, dashboard.backlog_week, dashboard.backlog_month, dashboard.inbox, dashboard.library, dashboard.completed_tasks, dashboard.overdue_yesterday, dashboard.overdue_older];
  const notes = buckets.flatMap(records => Array.isArray(records) ? records : []);
  if (dashboard.resurface) notes.push(dashboard.resurface);
  return notes.filter((note, index, all) => note?.id && all.findIndex(item => item?.id === note.id) === index);
}
function draggedNote(event) {
  const id = draggedTaskId || event.dataTransfer?.getData("text/plain");
  return allDashboardNotes().find(note => note.id === id) || null;
}
function patchChangesNote(note, patch) {
  return Boolean(patch && Object.entries(patch).some(([key, value]) => (note[key] ?? null) !== value));
}
function assignNoteTo(note, target, card) {
  const patch = assignmentPatch(note, target);
  if (!patchChangesNote(note, patch)) return false;
  void mutate(note, patch, card);
  return true;
}
function setupAssignmentDropZone(zone, targetFactory) {
  zone.addEventListener("dragover", event => {
    const note = draggedNote(event);
    const patch = note && assignmentPatch(note, targetFactory());
    if (!patchChangesNote(note, patch)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    zone.classList.add("record-drop-target");
  });
  zone.addEventListener("dragleave", event => {
    if (!zone.contains(event.relatedTarget)) zone.classList.remove("record-drop-target");
  });
  zone.addEventListener("drop", event => {
    const note = draggedNote(event);
    const target = targetFactory();
    if (!patchChangesNote(note, assignmentPatch(note, target))) return;
    event.preventDefault();
    zone.classList.remove("record-drop-target");
    clearTaskDropMarks();
    assignNoteTo(note, target, zone);
  });
}
function dropOnDay(event, amount) {
  event.preventDefault();
  event.currentTarget.classList.remove("drop-target");
  const note = draggedNote(event);
  if (note) assignNoteTo(note, {area:"today", date:addDays(selectedDate, amount)}, $("today-card"));
}
function populateContexts(select, selected = "") {
  select.replaceChildren();
  const emptyOption = node("option", "", t("Без проекта или человека")); emptyOption.value=""; select.append(emptyOption);
  for (const note of dashboard?.contexts || []) {
    const option=node("option","",note.title + (note.status === "archived" ? t(" (в архиве)") : "")); option.value=note.id; select.append(option);
  }
  select.value=selected || "";
}
function backlogRecords(key = backlogView) {
  if (!dashboard) return [];
  return key === "thoughts" ? thoughtRecords(allNotes) : key === "week" ? dashboard.backlog_week || [] : key === "month" ? dashboard.backlog_month || [] : dashboard.unscheduled || [];
}
function renderBacklog() {
  const labels = {unscheduled:t("без срока"), week:t("на неделе"), month:t("на месяц"), thoughts:t("в этом разделе")};
  const records = backlogRecords();
  document.querySelectorAll("[data-backlog]").forEach(button => {
    const selected = button.dataset.backlog === backlogView;
    button.classList.toggle("selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  renderList("unscheduled-list", records, backlogView === "thoughts" ? t("Пока нет активных мыслей и идей.") : t("Дел {labels} нет.", {labels: labels[backlogView]}), {task:backlogView !== "thoughts", compact:true});
  $("unscheduled-count").textContent = String((dashboard?.unscheduled || []).length);
  $("backlog-week-count").textContent = String((dashboard?.backlog_week || []).length);
  $("backlog-month-count").textContent = String((dashboard?.backlog_month || []).length);
  $("thought-count").textContent = String(thoughtRecords(allNotes).length);
  fireLayers.backlog.sync();
}
function moveTaskToBacklog(noteId, targetKey, card) {
  const note = allDashboardNotes().find(item => item.id === noteId);
  if (!note) return;
  backlogView = targetKey;
  if (!assignNoteTo(note, {area:"backlog", bucket:targetKey}, card)) renderBacklog();
}
// ——— Recall stack: a real card stack after the approved Dribbble reference ———
// The front card leaves to the left while the next one grows into its place;
// cards behind it are smaller and washed out. A drag scrubs the same motion.
const STACK_DURATION = 420;
let stackDrag = null;
function reducedMotion() { return window.matchMedia("(prefers-reduced-motion: reduce)").matches; }
function stackGeometry() {
  const stage = $("rotation-stage");
  const style = getComputedStyle(stage);
  return {
    width:$("rotation-content").offsetWidth || stage.clientWidth,
    peek:parseFloat(style.getPropertyValue("--stack-peek")) || 30,
    peek2:parseFloat(style.getPropertyValue("--stack-peek-2")) || 24,
  };
}
function stackPoseAt(slot, geometry) { return stackPose(slot, geometry.width, geometry.peek, geometry.peek2); }
function applyStackPose(el, pose) {
  el.style.transform = `translate3d(${pose.x.toFixed(2)}px,0,0) scale(${pose.s.toFixed(4)})`;
  el.style.opacity = pose.opacity >= .999 ? "" : pose.opacity.toFixed(3);
  const wash = el.querySelector(":scope > .stack-wash");
  if (wash) wash.style.opacity = pose.wash.toFixed(3);
  const spine = el.querySelector(":scope > .stack-spine");
  if (spine) spine.style.opacity = pose.spine.toFixed(3);
}
function resetFrontPose(front = $("rotation-content")) {
  front.querySelectorAll(":scope > .stack-wash, :scope > .stack-spine").forEach(el => el.remove());
  for (const property of ["transform", "opacity", "visibility", "z-index"]) front.style.removeProperty(property);
}
function stackSheets() { return [...$("rotation-stage").querySelectorAll(":scope > .stack-sheet")]; }
function sheetAt(slot) { return stackSheets().find(sheet => sheet.dataset.slot === String(slot) && !sheet.dataset.cover) || null; }
function stackSpine(note) {
  const spine = node("span", "stack-spine");
  spine.setAttribute("aria-hidden", "true");
  spine.append(node("span", "stack-spine-title", note.title));
  return spine;
}
function makeStackSheet(note) {
  const sheet = node("button", "stack-sheet");
  sheet.type = "button";
  sheet.dataset.noteId = note.id;
  sheet.dataset.key = `${note.id}:${note.version}`;
  sheet.setAttribute("aria-label", t("Следующая запись: {title}", {title: note.title}));
  const face = node("div", "stack-face");
  face.inert = true;
  face.append(renderRecallNote(note, {passive:true}));
  sheet.append(face, node("span", "stack-wash"), stackSpine(note));
  sheet.addEventListener("click", event => {
    event.preventDefault();
    event.stopPropagation();
    if (Date.now() < suppressRotationClickUntil || sheet.dataset.cover) return;
    stepRotation(1);
  });
  return sheet;
}
/** Put the next records into slots 1–2, reusing sheets that already show them. */
function syncStackSheets(candidates, selected, geometry = stackGeometry()) {
  const stage = $("rotation-stage");
  const index = candidates.findIndex(note => note.id === selected?.id);
  const wanted = index < 0 ? [] : [1, 2].slice(0, Math.max(0, Math.min(2, candidates.length - 1)))
    .map(step => candidates[(index + step) % candidates.length]);
  const pool = stackSheets().filter(sheet => !sheet.dataset.cover);
  const used = new Set();
  wanted.forEach((note, offset) => {
    const key = `${note.id}:${note.version}`;
    let sheet = pool.find(item => !used.has(item) && item.dataset.key === key);
    if (!sheet) { sheet = makeStackSheet(note); stage.append(sheet); }
    used.add(sheet);
    delete sheet.dataset.transient;
    delete sheet.dataset.leaving;
    sheet.dataset.slot = String(offset + 1);
    sheet.style.removeProperty("z-index");
    applyStackPose(sheet, stackPoseAt(offset + 1, geometry));
  });
  pool.forEach(sheet => { if (!used.has(sheet)) sheet.remove(); });
}
function stackImagesReady(elements, timeout = 320) {
  const figures = elements.flatMap(el => [...el.querySelectorAll(".note-image-preview")]);
  if (!figures.length) return Promise.resolve();
  const started = performance.now();
  return new Promise(resolve => {
    const check = () => {
      const done = figures.every(figure => figure.dataset.loaded === "true" || !figure.isConnected || !figure.querySelector("img[src]") && figure.querySelector(".secondary:not([hidden])"));
      if (done || performance.now() - started > timeout) resolve();
      else requestAnimationFrame(check);
    };
    check();
  });
}
/** Build the moving cards for one step. Nothing is committed until it settles. */
function prepareStackStep(direction, target, candidates) {
  const stage = $("rotation-stage");
  const front = $("rotation-content");
  const geometry = stackGeometry();
  const count = candidates.length;
  const index = candidates.findIndex(note => note.id === target.id);
  const motion = {direction, target, candidates, geometry, cards:[], progress:0, frame:0, cover:null, commit:false};
  const pose = slot => stackPoseAt(slot, geometry);
  const move = (el, from, to, z) => {
    el.style.zIndex = String(z);
    motion.cards.push({el, from:typeof from === "number" ? pose(from) : from, to:typeof to === "number" ? pose(to) : to});
  };
  const fresh = (note, slot) => {
    const sheet = makeStackSheet(note);
    sheet.dataset.transient = "true";
    applyStackPose(sheet, pose(slot));
    stage.append(sheet);
    return sheet;
  };
  const leave = (sheet, to, z) => {
    const slot = Number(sheet.dataset.slot);
    sheet.dataset.leaving = "true";
    move(sheet, slot, to === "fade" ? {...pose(slot), opacity:0} : to, z);
  };
  const next1 = sheetAt(1);
  const next2 = sheetAt(2);
  if (direction > 0) {
    const after = count > 1 ? candidates[(index + 1) % count] : null;
    const later = count > 2 ? candidates[(index + 2) % count] : null;
    const incoming = next1?.dataset.noteId === target.id ? next1 : fresh(target, 1);
    motion.cover = incoming;
    move(front, 0, -1, 9);
    move(incoming, 1, 0, 8);
    let slotOne = null;
    if (after) {
      slotOne = next2?.dataset.noteId === after.id ? next2 : null;
      if (slotOne) move(slotOne, 2, 1, 7);
      else { slotOne = fresh(after, 3); move(slotOne, 3, 1, 7); }
    }
    if (later) move(fresh(later, 3), 3, 2, 6);
    for (const sheet of [next1, next2]) if (sheet && sheet !== incoming && sheet !== slotOne) leave(sheet, "fade", 5);
  } else {
    const later = count > 2 ? candidates[(index + 2) % count] : null;
    const entering = fresh(target, -1);
    motion.cover = entering;
    move(entering, -1, 0, 9);
    const current = candidates.find(note => note.id === front.querySelector(".note")?.dataset.noteId);
    front.append(node("span", "stack-wash"));
    if (current) front.append(stackSpine(current));
    move(front, 0, 1, 8);
    let slotTwo = null;
    if (later) {
      slotTwo = next1?.dataset.noteId === later.id ? next1 : null;
      if (slotTwo) move(slotTwo, 1, 2, 7);
      else { slotTwo = fresh(later, 3); move(slotTwo, 3, 2, 7); }
    }
    for (const sheet of [next1, next2]) if (sheet && sheet !== slotTwo) leave(sheet, 3, 6);
  }
  return motion;
}
function setStackProgress(motion, progress) {
  motion.progress = progress;
  for (const card of motion.cards) applyStackPose(card.el, mixPose(card.from, card.to, progress));
}
function runStackMotion(motion, to, duration) {
  return new Promise(resolve => {
    const from = motion.progress;
    const span = reducedMotion() ? 0 : Math.max(120, duration * Math.abs(to - from));
    const started = performance.now();
    let watchdog = 0;
    const done = () => {
      clearTimeout(watchdog);
      cancelAnimationFrame(motion.frame);
      motion.frame = 0;
      motion.resolve = null;
      resolve();
    };
    const frame = now => {
      const t = span ? Math.min(1, (now - started) / span) : 1;
      setStackProgress(motion, from + (to - from) * stackEase(t));
      if (t < 1) motion.frame = requestAnimationFrame(frame);
      else done();
    };
    // Frames stop while the panel is not painted; finish the step anyway.
    watchdog = setTimeout(() => { setStackProgress(motion, to); done(); }, span + 500);
    motion.resolve = done;
    motion.frame = requestAnimationFrame(frame);
  });
}
/** Return a released-but-uncommitted drag to the resting stack. */
function revertStackMotion(motion) {
  setStackProgress(motion, 0);
  $("rotation-stage").querySelectorAll(":scope > .stack-sheet[data-transient]").forEach(el => el.remove());
  resetFrontPose();
  for (const sheet of stackSheets()) {
    delete sheet.dataset.leaving;
    sheet.style.removeProperty("z-index");
    applyStackPose(sheet, stackPoseAt(Number(sheet.dataset.slot), motion.geometry));
  }
}
/**
 * Replace the moving copies with the resting stack. The copy that arrived in
 * front stays on top until the real, interactive card has decoded its image.
 */
async function settleStack(motion, immediate = false) {
  const stage = $("rotation-stage");
  const front = $("rotation-content");
  const token = rotationMotionToken;
  stage.querySelectorAll(":scope > .stack-sheet[data-leaving]").forEach(el => el.remove());
  motion.cover.dataset.cover = "true";
  const before = new Set(stackSheets());
  syncStackSheets(motion.candidates, motion.target, motion.geometry);
  if (!immediate) {
    await stackImagesReady(stackSheets().filter(sheet => !before.has(sheet)));
    if (token !== rotationMotionToken) return;
  }
  resetFrontPose(front);
  if (front.firstElementChild !== motion.content[0]) front.replaceChildren(...motion.content);
  rotationRenderKey = motion.key;
  if (!immediate) {
    front.style.visibility = "hidden";
    await stackImagesReady([front]);
    front.style.removeProperty("visibility");
    if (token !== rotationMotionToken) return;
  }
  motion.cover.remove();
  if (motion.restoreFocus) front.querySelector(".carousel-disclosure")?.focus({preventScroll:true});
  clearRotationMotion();
}
function clearRotationMotion() {
  rotationMotion = null;
  rotationPendingKey = null;
  rotationAnimating = false;
  $("rotation-stage").classList.remove("stack-animating");
}
/** Finish any motion at once: a committed step settles, a drag returns. */
function cancelRotationMotion() {
  const motion = rotationMotion;
  rotationMotionToken++;
  if (motion) {
    cancelAnimationFrame(motion.frame);
    motion.resolve?.();
    if (motion.commit) { setStackProgress(motion, 1); void settleStack(motion, true); }
    else revertStackMotion(motion);
  }
  clearRotationMotion();
}
function installRotation(content, key, keepExpanded = false, restoreFocus = false, candidates = [], selected = null) {
  const front = $("rotation-content");
  const stage = $("rotation-stage");
  resetFrontPose(front);
  front.replaceChildren(...content);
  syncStackSheets(candidates, selected);
  rotationRenderKey = key;
  const disclosure = front.querySelector(".carousel-disclosure");
  if (keepExpanded && disclosure) {
    const more = front.querySelector(".carousel-more");
    more.hidden = false;
    disclosure.setAttribute("aria-expanded", "true");
    disclosure.setAttribute("aria-label", t("Свернуть запись: {textContent}", {textContent: disclosure.querySelector(".note-title")?.textContent}));
  }
  stage.classList.toggle("stack-expanded", keepExpanded);
  if (restoreFocus) disclosure?.focus({preventScroll:true});
}
async function animateRotation(content, key, direction, restoreFocus, candidates, selected, prepared = null) {
  if (!prepared) cancelRotationMotion();
  const token = ++rotationMotionToken;
  const motion = prepared || prepareStackStep(direction, selected, candidates);
  Object.assign(motion, {commit:true, content, key, restoreFocus});
  rotationMotion = motion;
  rotationPendingKey = key;
  rotationAnimating = true;
  $("rotation-stage").classList.add("stack-animating");
  await runStackMotion(motion, 1, STACK_DURATION);
  if (token !== rotationMotionToken) return;
  await settleStack(motion);
}
function recallContent(selected) {
  if (!selected) return [node("p", "empty", t("Пока нет записей для этой карточки."))];
  const reason = selected.id === dashboard.resurface?.id ? dashboard.resurface.reason || t("Выбрано автоматически")
    : Date.now() < resurfaceHoldUntil ? t("Выбрано вручную") : t("Показано из библиотеки");
  return [renderRecallNote({...selected, reason})];
}
function renderRotation(direction = 1, instant = false) {
  // An open image or a finger on the card pauses the automatic change.
  if (!dashboard || viewerState || stackDrag) return;
  const front = $("rotation-content");
  const stage = $("rotation-stage");
  const currentId = front.querySelector(".note")?.dataset.noteId || null;
  if (editingNote?.id === currentId && editDialog.open) return;
  const candidates = resurfaceCandidates(dashboard);
  const openDetails = Boolean(front.querySelector(".carousel-more:not([hidden])"));
  const selected = chooseResurface(candidates, dashboard.resurface, resurfaceId, resurfaceHoldUntil, Date.now(), openDetails);
  resurfaceId = selected?.id || null;
  if (instant) cancelRotationMotion();
  const nextKey = selected ? `${selected.id}:${selected.version}` : "empty";
  const wasMultiple = stage.dataset.fanCount === "multiple";
  stage.dataset.fanCount = candidates.length > 1 ? "multiple" : selected ? "single" : "empty";
  if (nextKey === rotationPendingKey) return;
  if (nextKey === rotationRenderKey && front.children.length) {
    if (rotationPendingKey) cancelRotationMotion();
    // The front record has not changed, but refreshes may have replaced its
    // neighbors. Keep an open detail untouched while updating the stack.
    if (!rotationMotion) syncStackSheets(candidates, selected);
    return;
  }
  const content = recallContent(selected);
  const restoreFocus = front.contains(document.activeElement);
  const sameId = currentId && currentId === selected?.id;
  const animate = !instant && !sameId && Boolean(currentId) && Boolean(selected) && wasMultiple && candidates.length > 1 && !openDetails && !document.hidden && !reducedMotion();
  if (animate) void animateRotation(content, nextKey, direction, restoreFocus, candidates, selected);
  else {
    cancelRotationMotion();
    installRotation(content, nextKey, Boolean(sameId && openDetails), restoreFocus, candidates, selected);
  }
}
// ——— Full-size image view ("immersed view" of the reference) ———
// The picture grows out of its card over every block; any click or Escape
// returns it. While it is open, the Recall stack does not change.
const imageViewer = $("image-viewer");
function viewerFlip(image, source, opening) {
  const to = image.getBoundingClientRect();
  const box = source.getBoundingClientRect();
  const fit = source.closest(".note-image-preview")?.dataset.fit || getComputedStyle(source).objectFit;
  const naturalWidth = source.naturalWidth || to.width;
  const naturalHeight = source.naturalHeight || to.height;
  const scale = fit === "cover" ? Math.max(box.width / naturalWidth, box.height / naturalHeight)
    : Math.min(box.width / naturalWidth, box.height / naturalHeight);
  const content = {width:naturalWidth * scale, height:naturalHeight * scale};
  content.left = box.left + (box.width - content.width) / 2;
  content.top = box.top + (box.height - content.height) / 2;
  const k = content.width / to.width;
  const inset = [box.top - content.top, content.left + content.width - box.right,
    content.top + content.height - box.bottom, box.left - content.left].map(value => `${Math.max(0, value / k).toFixed(1)}px`);
  const radius = parseFloat(getComputedStyle(source.closest(".stack-note") || source).borderTopLeftRadius) || 8;
  const small = {transform:`translate(${(content.left - to.left).toFixed(1)}px,${(content.top - to.top).toFixed(1)}px) scale(${k.toFixed(4)})`,
    clipPath:`inset(${inset.join(" ")} round ${(radius / k).toFixed(1)}px)`};
  const large = {transform:"translate(0px,0px) scale(1)", clipPath:"inset(0px 0px 0px 0px round 12px)"};
  return image.animate(opening ? [small, large] : [large, small],
    {duration:opening ? 340 : 260, easing:opening ? "cubic-bezier(.2,.85,.25,1)" : "cubic-bezier(.45,0,.7,.4)", fill:"both"});
}
function sourceVisible(source) {
  if (!source?.isConnected || source.closest("[inert],[hidden]")) return false;
  const rect = source.getBoundingClientRect();
  return rect.width > 0 && rect.bottom > 0 && rect.top < window.innerHeight && rect.right > 0 && rect.left < window.innerWidth;
}
async function openImageViewer(item, source, note = null) {
  if (viewerState || rotationAnimating || Date.now() < suppressRotationClickUntil) return;
  viewerState = {item, source, closing:false, url:null};
  const state = viewerState;
  const loaded = await previewUrl(item).catch(() => null);
  if (viewerState !== state) { if (loaded && !loaded.shared) URL.revokeObjectURL(loaded.url); return; }
  if (!loaded || !source.isConnected) { viewerState = null; if (loaded && !loaded.shared) URL.revokeObjectURL(loaded.url); return; }
  state.url = loaded;
  state.restore = source.closest("button") || document.activeElement;
  const image = $("image-viewer-image");
  const width = source.naturalWidth || 1;
  const height = source.naturalHeight || 1;
  const scale = Math.min((window.innerWidth - 24) / width, (window.innerHeight - 64) / height, 2);
  image.style.width = `${Math.max(1, Math.round(width * scale))}px`;
  image.style.height = `${Math.max(1, Math.round(height * scale))}px`;
  image.alt = note?.title && note.title !== item.name ? `${note.title} — ${item.name || t("изображение")}` : item.name || t("Изображение");
  image.src = loaded.url;
  // Keep the layer invisible until its first frame is the small card image.
  imageViewer.dataset.state = "opening";
  imageViewer.showModal();
  imageViewer.focus({preventScroll:true});
  await image.decode().catch(() => {});
  if (viewerState !== state || state.closing) return;
  source.style.visibility = "hidden";
  if (!reducedMotion()) {
    imageViewer.querySelector(".image-viewer-scrim")?.animate([{opacity:0}, {opacity:1}], {duration:260, easing:"ease-out"});
    imageViewer.querySelector(".image-viewer-hint")?.animate([{opacity:0}, {opacity:0, offset:.6}, {opacity:1}], {duration:700});
    if (sourceVisible(source)) state.flip = viewerFlip(image, source, true);
    else image.animate([{opacity:0, transform:"scale(.96)"}, {opacity:1, transform:"scale(1)"}], {duration:220, easing:"ease-out"});
  }
  delete imageViewer.dataset.state;
}
async function closeImageViewer() {
  const state = viewerState;
  if (!state || state.closing || !imageViewer.open) return;
  state.closing = true;
  const image = $("image-viewer-image");
  const scrim = imageViewer.querySelector(".image-viewer-scrim");
  state.flip?.cancel();
  const motions = reducedMotion() ? [] : [
    sourceVisible(state.source) ? viewerFlip(image, state.source, false)
      : image.animate([{opacity:1, transform:"scale(1)"}, {opacity:0, transform:"scale(.96)"}], {duration:180, fill:"both"}),
    scrim?.animate([{opacity:1}, {opacity:0}], {duration:240, easing:"ease-in", fill:"both"}),
  ].filter(Boolean);
  // A hidden or throttled page may never finish an animation; never keep the
  // picture open because of that.
  await Promise.race([Promise.allSettled(motions.map(motion => motion.finished)), new Promise(resolve => setTimeout(resolve, 450))]);
  state.source.style.removeProperty("visibility");
  imageViewer.close();
  delete imageViewer.dataset.state;
  motions.forEach(motion => motion.cancel());
  image.removeAttribute("src");
  image.style.removeProperty("width");
  image.style.removeProperty("height");
  if (state.url && !state.url.shared) URL.revokeObjectURL(state.url.url);
  viewerState = null;
  // Resume the automatic change with a fresh minute on the same card.
  resurfaceHoldUntil = Math.max(resurfaceHoldUntil, Date.now() + RESURFACE_HOLD_MS);
  if (state.restore?.isConnected) state.restore.focus({preventScroll:true});
  if (dashboard) renderRotation();
}
imageViewer.addEventListener("click", () => { void closeImageViewer(); });
imageViewer.addEventListener("cancel", event => { event.preventDefault(); void closeImageViewer(); });
imageViewer.addEventListener("keydown", event => {
  if (event.key === "Enter" || event.key === " ") { event.preventDefault(); void closeImageViewer(); }
});
let lastSuccessAt = null;
function renderLastSuccess(online) {
  if (!lastSuccessAt) return;
  const time = lastSuccessAt.toLocaleString(localeTag());
  $("last-success").textContent = online ? t("Списки обновлены: {time}", {time}) : t("Списки последний раз обновлены: {time}", {time});
}
async function refreshOnce() {
  const readSeq = ++noteReadSeq;
  try {
    const status = await send("status");
    $("token-status").textContent = status.automatic
      ? t("Подключено автоматически: токен вводить не нужно.")
      : status.configured
        ? t("Токен уже сохранён. Пустое поле выше не означает, что его нужно вводить снова.")
        : t("Токен хранится только в этом профиле Chrome.");
    $("queue-section").hidden = !status.queue_count;
    $("more-section").hidden = !status.queue_count;
    $("queue-count").textContent = String(status.queue_count);
    $("more-queue-notice").hidden = !status.queue_count;
    $("more-queue-count").textContent = String(status.queue_count);
    const queueList = $("queue-list"); queueList.replaceChildren();
    for (const item of status.queue) {
      const el = node("div", "queue-item", item.payload.text.slice(0,150));
      if (item.last_error) el.append(node("div", "queue-error", item.last_error));
      queueList.append(el);
    }
    $("setup-guide").hidden = status.configured && !status.hostMissing;
    if (!status.configured) throw new Error(t("Установите программу Brainalot для Windows и нажмите «Подключиться снова»."));
    const wasViewingToday = !selectedDate || selectedDate === dashboard?.date;
    const selectedDateAtRead = selectedDate;
    const [nextDashboard, noteResponse] = await Promise.all([send("dashboard"), send("notes")]);
    if (!Array.isArray(noteResponse?.notes)) throw new Error(t("Сервис не вернул полный список записей."));
    rawDashboard = nextDashboard;
    rawAllNotes = noteResponse.notes;
    noteDeletions.reconcile(rawDashboard,rawAllNotes,readSeq);
    $("connection").textContent = t("Сервис подключён");
    $("connection").dataset.online = "true";
    $("connection").title = t("Сервис подключён");
    $("connection").setAttribute("aria-label", t("Сервис подключён"));
    lastSuccessAt = new Date();
    renderLastSuccess(true);
    if ($("notice").dataset.source === "connection") $("notice").hidden = true;
    $("setup-guide").hidden = true;
    // A response that started on Today must not undo a later user navigation.
    if (wasViewingToday && selectedDate === selectedDateAtRead) selectedDate = rawDashboard.date;
    if (!selectedWeekStart || !weekDates(selectedWeekStart).includes(selectedDate)) selectedWeekStart = weekStart(selectedDate);
    renderEffectiveNotes();
    void loadCalendarWeek();
  } catch (error) {
    cancelRotationMotion();
    dashboard = null;
    allNotes = [];
    $("connection").textContent=t("Сервис недоступен");
    $("connection").dataset.online = "false";
    $("connection").title = t("Сервис недоступен");
    $("connection").setAttribute("aria-label", t("Сервис недоступен"));
    renderLastSuccess(false);
    $("planner-heading").textContent = t("Сегодня");
    $("planner-date").textContent = "";
    for (const id of ["today-list","unscheduled-list","inbox-list","library-list","rotation-content"]) empty($(id),t("Данные недоступны. Откройте настройки (шестерёнка) и нажмите «Запустить сервис»."));
    rotationRenderKey = null;
    stackSheets().forEach(sheet => sheet.remove());
    $("rotation-stage").dataset.fanCount = "empty";
    $("rotation-stage").classList.remove("stack-expanded");
    libraryRenderKey = null;
    $("library-nav").replaceChildren();
    $("library-breadcrumb").hidden = true;
    $("completed-list").replaceChildren();
    $("completed-section").hidden = true;
    calendar = {configured:false, calendar_count:0, calendars:[], events:[], last_updated:null, stale:false, error:null, write_access:{client_configured:false,authorized:false}}; renderCalendar();
    for (const id of ["unscheduled-count","backlog-week-count","backlog-month-count","thought-count","inbox-count","library-count"]) $(id).textContent = "—";
    notice(error.message || t("Не удалось обновить панель"),true,"connection");
  }
}
const refresh = createRefreshGate(refreshOnce);
$("setup-retry").addEventListener("click", async () => {
  const button = $("setup-retry");
  button.disabled = true;
  try { await send("reconnect"); await refresh(); }
  catch (error) { notice(error.message, true, "connection"); }
  finally { button.disabled = false; }
});
function scheduleRefresh() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => { void refresh(); }, 150);
}
function renderLibraryRecord(note) {
  if (note.kind !== "project") return renderNote(note, {library:true});
  const button = node("button", "library-project-row");
  button.type = "button";
  button.dataset.projectId = note.id;
  button.setAttribute("aria-label", t("Открыть проект {title}", {title: note.title}));
  const linked = linkedProjectNotes(allNotes, note.id);
  button.append(node("strong", "library-project-title", note.title), node("small", "library-project-meta", t("Записей: {length}{status}", {length: linked.length, status: note.status === "archived" ? t(" · В архиве") : ""})));
  return button;
}
function librarySnapshot(owner = IS_LIBRARY_WINDOW ? "window" : "panel") {
  return {
    owner, open:document.body.dataset.panelView === "library", route:{...libraryRoute},
    query:$("library-search").value, expanded:[...expandedLibrary],
    listScroll:$("library-list").scrollTop, navScroll:$("library-nav").scrollTop,
    revision:libraryRevision,
  };
}
function persistLibraryState(owner = IS_LIBRARY_WINDOW ? "window" : "panel", {claim = false} = {}) {
  if (libraryRestoring || (!IS_LIBRARY_WINDOW && libraryDetached && !claim)) return libraryWrite;
  const snapshot = librarySnapshot(owner);
  libraryWrite = libraryWrite.catch(() => {}).then(async () => {
    const current = normalizeLibrarySnapshot((await chrome.storage.local.get(LIBRARY_SNAPSHOT_KEY))[LIBRARY_SNAPSHOT_KEY]);
    if (!mayWriteLibrarySnapshot(current.owner, owner, claim)) return current;
    snapshot.revision = Math.max(current.revision, libraryRevision) + 1;
    libraryRevision = snapshot.revision;
    await chrome.storage.local.set({[LIBRARY_SNAPSHOT_KEY]:snapshot});
    return snapshot;
  });
  return libraryWrite;
}
function scheduleLibraryScrollSave() {
  if (libraryRestoring || document.body.dataset.panelView !== "library" || (!IS_LIBRARY_WINDOW && libraryDetached)) return;
  clearTimeout(libraryScrollTimer);
  libraryScrollTimer = setTimeout(() => { void persistLibraryState(); }, 120);
}
function applyLibrarySnapshot(value) {
  const snapshot = normalizeLibrarySnapshot(value);
  if ((libraryRevision > 0 && snapshot.revision <= libraryRevision) || (IS_LIBRARY_WINDOW && snapshot.owner !== "window")) return;
  libraryRestoring = true;
  libraryRevision = snapshot.revision;
  libraryDetached = !IS_LIBRARY_WINDOW && snapshot.owner === "window";
  libraryRoute = snapshot.route;
  $("library-search").value = snapshot.query;
  expandedLibrary.clear();
  for (const id of snapshot.expanded) expandedLibrary.add(id);
  libraryRenderKey = null;
  libraryRestoreScroll = {list:snapshot.listScroll, nav:snapshot.navScroll};
  if (snapshot.open || IS_LIBRARY_WINDOW) openLibrary({focus:false, resetScroll:false, persist:false});
  else if (document.body.dataset.panelView === "library") returnToDay(false, false);
  renderLibrary();
  libraryRestoring = false;
}
function renderLibrary() {
  if (!dashboard) return;
  const query = $("library-search").value.trim().toLocaleLowerCase();
  libraryRoute = resolveLibraryRoute(libraryRoute, allNotes);
  const contexts = allNotes.filter(note => note.kind === "project" || note.kind === "person");
  const routeKey = `${libraryRoute.category}:${libraryRoute.projectId || ""}:${libraryRoute.section}`;
  const sameRoute = routeKey === lastLibraryRouteKey;
  const renderKey = `${routeKey}:${query}:${allNotes.map(note => `${note.id}:${note.version}`).join("|")}`;
  if (renderKey === libraryRenderKey) return;
  const previousScroll = query === lastLibraryQuery && sameRoute ? $("library-list").scrollTop : 0;
  const focusedNav = $("library-nav").contains(document.activeElement)
    ? {category:document.activeElement.dataset.libraryCategory, section:document.activeElement.dataset.dossierSection} : null;
  const project = libraryRoute.projectId ? allNotes.find(note => note.id === libraryRoute.projectId) : null;
  const counts = project && !query ? dossierCounts(allNotes, project.id) : libraryCategoryCounts(allNotes);
  const options = project && !query ? DOSSIER_SECTIONS : LIBRARY_CATEGORIES;
  const nav = $("library-nav");
  nav.setAttribute("aria-label", project && !query ? t("Разделы проекта") : t("Разделы библиотеки"));
  nav.replaceChildren(...options.map(option => {
    const button = node("button", "library-nav-item");
    button.type = "button";
    button.dataset[project && !query ? "dossierSection" : "libraryCategory"] = option.id;
    button.setAttribute("aria-current", String(!query && (project ? libraryRoute.section : libraryRoute.category) === option.id));
    button.setAttribute("aria-label", t("{label}, записей: {counts}", {label: option.label, counts: counts[option.id]}));
    button.append(node("span", "library-nav-label", option.label), node("small", "library-nav-count", String(counts[option.id])));
    return button;
  }));
  const crumb = $("library-breadcrumb");
  crumb.replaceChildren();
  crumb.hidden = false;
  if (query) crumb.append(node("span", "library-crumb-current", t("Результаты поиска")));
  else if (project) {
    const back = node("button", "library-crumb-back", t("Проекты"));
    back.type = "button";
    back.dataset.libraryBackProjects = "true";
    crumb.append(back, node("span", "library-crumb-separator", "›"), node("span", "library-crumb-current", project.title));
  } else crumb.append(node("span", "library-crumb-current", options.find(item => item.id === libraryRoute.category)?.label || t("Все")));
  const notes = selectLibraryNotes(allNotes, libraryRoute, query, contexts);
  if (project && !query && libraryRoute.section === "attachments" && !sameRoute) {
    for (const note of notes) expandedLibrary.add(note.id);
  }
  const list = $("library-list");
  list.replaceChildren();
  if (query) {
    list.append(node("p", "library-result-count", t("Найдено: {length}", {length: notes.length})));
  } else if (project) {
    list.append(node("h3", "library-group-title", project.title));
    if (libraryRoute.section === "all") {
      list.append(node("p", "library-group-label", t("О проекте")), renderNote(project, {library:true}));
    }
    list.append(node("p", "library-group-label", libraryRoute.section === "all" ? t("Связанные записи") : DOSSIER_SECTIONS.find(item => item.id === libraryRoute.section)?.label || t("Записи")));
  }
  if (!query && libraryRoute.category === "projects" && !project) {
    list.append(...notes.map(renderLibraryRecord));
  } else if (project || query) {
    list.append(...notes.map(note => project && note.id === project.id ? renderNote(note, {library:true}) : renderLibraryRecord(note)));
  } else {
    for (const group of groupLibraryNotes(notes, contexts)) {
      list.append(node("h3", "library-group-title", group.title), ...group.notes.map(renderLibraryRecord));
    }
  }
  if (!notes.length) list.append(node("p", "empty", query ? t("Совпадений нет. Измените запрос или очистите поиск.") : project ? t("В этом разделе пока нет записей.") : t("В этом разделе пока нет записей.")));
  $("library-list").scrollTop = libraryRestoreScroll?.list ?? previousScroll;
  if (libraryRestoreScroll) $("library-nav").scrollTop = libraryRestoreScroll.nav;
  libraryRestoreScroll = null;
  lastLibraryQuery = query;
  lastLibraryRouteKey = routeKey;
  libraryRenderKey = renderKey;
  $("library-clear").hidden = !query;
  if (focusedNav && sameRoute) {
    const selector = focusedNav.section ? `[data-dossier-section="${focusedNav.section}"]` : `[data-library-category="${focusedNav.category}"]`;
    nav.querySelector(selector)?.focus({preventScroll:true});
  }
}
for (const [key,label] of Object.entries(kinds)) { const option=node("option","",label); option.value=key; $("edit-kind").append(option); }
for (const [key,label] of Object.entries(media)) { const option=node("option","",label); option.value=key; $("edit-media").append(option); }
for (const [key,label] of Object.entries(importance)) { const option=node("option","",label); option.value=key; $("edit-importance").append(option); }
const editDialog = $("edit-dialog");
function clearEditError() { $("edit-error").hidden = true; $("edit-error").textContent = ""; }
function showEditError(message) { $("edit-error").textContent = message; $("edit-error").hidden = false; }
function syncEditFields() {
  const kind = $("edit-kind").value;
  const task = ["task", "purchase"].includes(kind);
  const mediaKind = kind === "media";
  const thought = kind === "thought";
  $("edit-schedule").disabled = !task;
  if (!task) { $("edit-schedule").value = "none"; $("edit-date").value = ""; }
  $("edit-date-label").hidden = !task || $("edit-schedule").value !== "date";
  $("edit-media-label").hidden = !mediaKind;
  if (!mediaKind) $("edit-media").value = "movie";
  $("edit-show-in-unscheduled-label").hidden = !thought;
  if (!thought) $("edit-show-in-unscheduled").checked = false;
  // The fire marks only tasks and purchases, the rows that can burn in a list.
  const critical = $("edit-importance").querySelector('option[value="critical"]');
  critical.hidden = !task;
  critical.disabled = !task;
  if (!task && $("edit-importance").value === "critical") $("edit-importance").value = "high";
}
/** The group whose fire the edited task would take, or null when it will not burn. */
function editFireGroup() {
  if ($("edit-importance").value !== "critical" || !["task", "purchase"].includes($("edit-kind").value)) return null;
  const due = $("edit-schedule").value === "date" ? $("edit-date").value || null : null;
  return fireGroupOfTask({due}, dashboard?.date);
}
function openEditor(note) {
  editingNote = note;
  editClosing = false;
  editingAttachments = Array.isArray(note.attachments) ? note.attachments.map(item => ({ ...item })) : [];
  editImportFailure = null;
  renderEditAttachments();
  clearEditError();
  $("edit-note-title").value = note.title || "";
  $("edit-note-text").value = editableNoteText(note.body);
  $("edit-kind").value = note.kind;
  $("edit-schedule").value = note.due ? "date" : ["week", "month"].includes(note.planning_horizon) ? note.planning_horizon : "none";
  $("edit-date").value = note.due || "";
  $("edit-media").value = note.media_type || "movie";
  populateContexts($("edit-context"), note.context_id);
  $("edit-topic").value = note.topic || "";
  $("edit-importance").value = note.importance || "normal";
  $("edit-url").value = note.url || "";
  $("edit-show-in-carousel").checked = note.show_in_carousel !== false;
  $("edit-show-in-unscheduled").checked = Boolean(note.show_in_unscheduled);
  syncEditFields();
  editDialog.showModal();
  $("edit-note-title").focus();
}
async function closeEditor() {
  if (editSaving || editClosing) return;
  editClosing = true;
  $("edit-form").inert = true;
  $("edit-close").disabled = true;
  await editImports;
  editDialog.close();
  $("edit-form").inert = false;
  $("edit-close").disabled = false;
  editClosing = false;
}
$("edit-close").addEventListener("click", () => { void closeEditor(); });
editDialog.addEventListener("cancel", event => { event.preventDefault(); void closeEditor(); });
editDialog.addEventListener("close", () => { editingNote = null; clearEditError(); if (dashboard) renderRotation(); });
$("edit-form").addEventListener("input", event => { if (event.target !== $("edit-files")) editImportFailure = null; });
$("edit-form").addEventListener("change", event => { if (event.target !== $("edit-files")) editImportFailure = null; });
function renderEditAttachments() {
  renderAttachmentList($("edit-attachments"), editingAttachments, { removable:true, onRemove(index) {
    editingAttachments.splice(index, 1);
    editImportFailure = null;
    renderEditAttachments();
  }});
}
$("edit-add-files").addEventListener("click", () => $("edit-files").click());
function importEditFiles(files) {
  const selected = Array.from(files);
  editImports = editImports.then(async () => {
    checkAttachmentLimit(editingAttachments, selected);
    for (const file of selected) {
      editingAttachments.push(await storeAttachment(file));
      renderEditAttachments();
    }
    editImportFailure = null;
  }).catch(error => { editImportFailure = error; showEditError(error.message || t("Не удалось добавить файл.")); });
}
$("edit-files").addEventListener("change", event => {
  importEditFiles(event.target.files);
  event.target.value = "";
});
$("edit-note-text").addEventListener("paste", event => {
  const images = Array.from(event.clipboardData?.files || []).filter(file => file.type.startsWith("image/"));
  if (!images.length) return;
  event.preventDefault();
  const text = event.clipboardData.getData("text/plain");
  if (text) {
    const field = event.currentTarget;
    field.setRangeText(text, field.selectionStart, field.selectionEnd, "end");
  }
  importEditFiles(images);
});
$("edit-kind").addEventListener("change", syncEditFields);
$("edit-schedule").addEventListener("change", syncEditFields);
$("edit-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (!editingNote || editClosing) return;
  const button = $("edit-submit");
  if (button.disabled) return;
  const fireGroup = editFireGroup();
  if (fireGroup && burningCount(fireGroup, noteFireKey(editingNote)) >= FIRE_LIMIT) {
    showEditError(fireGroup === "backlog"
      ? t("Среди дел без срока уже есть очень важное. Сначала погасите его.")
      : t("На этот день уже есть очень важное дело или событие. Сначала погасите его."));
    const select = $("edit-importance");
    select.classList.remove("fire-denied");
    void select.offsetWidth;
    select.classList.add("fire-denied");
    setTimeout(() => select.classList.remove("fire-denied"), 600);
    return;
  }
  if (fireGroup && editingNote.importance !== "critical") igniteFire(fireGroup === "backlog" ? fireLayers.backlog : fireLayers.today, noteFireKey(editingNote));
  button.disabled = true;
  editSaving = true;
  $("edit-form").inert = true;
  $("edit-close").disabled = true;
  try {
    await editImports;
    if (editImportFailure) throw editImportFailure;
    const patch = editNotePatch({
      title:$("edit-note-title").value,
      text:$("edit-note-text").value,
      kind:$("edit-kind").value,
      schedule:$("edit-schedule").value,
      exactDate:$("edit-date").value || null,
      mediaType:$("edit-media").value,
      contextId:$("edit-context").value,
      topic:$("edit-topic").value,
      importance:$("edit-importance").value,
      url:$("edit-url").value,
      showInCarousel:$("edit-show-in-carousel").checked,
      showInUnscheduled:$("edit-show-in-unscheduled").checked,
    });
    patch.attachments = editingAttachments;
    await send("update", {id:editingNote.id, version:editingNote.version, patch});
    editDialog.close();
    notice(t("Запись изменена."));
    await refresh();
  } catch (error) {
    showEditError(error.message || t("Не удалось изменить запись."));
    notice(error.message || t("Не удалось изменить запись."), true);
    await refresh();
  } finally {
    editSaving = false;
    $("edit-form").inert = false;
    $("edit-close").disabled = false;
    button.disabled = false;
  }
});
$("capture-open").addEventListener("click", async () => {
  try { await send("open-capture"); } catch (error) { notice(error.message, true); }
});
function closeAppearancePanel(restoreFocus = false) {
  if ($("appearance-panel").hidden) return;
  $("appearance-panel").hidden = true;
  $("appearance-toggle").setAttribute("aria-expanded", "false");
  if (restoreFocus) $("appearance-toggle").focus();
}
function openAppearancePanel() {
  closeFocusPanel(false);
  $("settings").hidden = true;
  $("settings-toggle").setAttribute("aria-expanded", "false");
  $("appearance-panel").hidden = false;
  $("appearance-toggle").setAttribute("aria-expanded", "true");
  $("design-select").focus();
}
function updateLocaleToggle() {
  const label = locale() === "ru" ? t("Переключить на английский") : t("Переключить на русский");
  $("locale-toggle").setAttribute("aria-label", label);
  $("locale-toggle").title = label;
}
$("appearance-toggle").addEventListener("click", () => {
  if ($("appearance-panel").hidden) openAppearancePanel();
  else closeAppearancePanel(true);
});
$("appearance-close").addEventListener("click", () => closeAppearancePanel(true));
document.addEventListener("pointerdown", event => {
  if (!$("appearance-panel").hidden && !$("appearance-host").contains(event.target)) closeAppearancePanel(false);
});
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && !$("appearance-panel").hidden) {
    event.preventDefault();
    event.stopPropagation();
    closeAppearancePanel(true);
  }
});
$("locale-toggle").addEventListener("click", async () => {
  const previous = locale();
  $("locale-toggle").disabled = true;
  try {
    await saveLocale(previous === "ru" ? "en" : "ru");
    location.reload();
  } catch (error) {
    setLocale(previous);
    try { localStorage.setItem(LOCALE_KEY, previous); } catch { /* Chrome storage remains authoritative. */ }
    updateLocaleToggle();
    $("locale-toggle").disabled = false;
    notice(error.message || t("Не удалось сохранить язык."), true);
  }
});
$("settings-toggle").addEventListener("click",()=>{ closeFocusPanel(false); closeAppearancePanel(false); if (document.body.dataset.panelView === "library") returnToDay(false); const el=$("settings");el.hidden=!el.hidden;$("settings-toggle").setAttribute("aria-expanded",String(!el.hidden)); if (!el.hidden) $("daily-scroll").scrollTop = 0; });
/** Refresh, start or restart the local service from the settings; start/restart go through the native host. */
async function serviceButton(button, action, working, done) {
  const status = $("service-status");
  const buttons = [...document.querySelectorAll(".service-controls button")];
  buttons.forEach(item => { item.disabled = true; });
  status.hidden = false;
  status.textContent = working;
  try {
    if (action) await send("service-control", { action });
    await refresh();
    status.textContent = done;
  } catch (error) {
    status.textContent = error.message;
  } finally {
    buttons.forEach(item => { item.disabled = false; });
  }
}
$("service-refresh").addEventListener("click", () => void serviceButton($("service-refresh"), null, t("Обновляем…"), t("Обновлено.")));
$("service-start").addEventListener("click", () => void serviceButton($("service-start"), "start", t("Запускаем сервис…"), t("Сервис запущен.")));
$("service-restart").addEventListener("click", () => void serviceButton($("service-restart"), "restart", t("Перезапускаем сервис…"), t("Сервис перезапущен.")));
$("settings-form").addEventListener("submit",async event=>{event.preventDefault();try{await send("configure",{token:$("token").value});$("token").value="";await refresh();}catch(error){notice(error.message,true);}});
$("theme-select").addEventListener("change", async event => {
  const theme = normalizeTheme(event.target.value);
  applyTheme(theme);
  await chrome.storage.local.set({[THEME_KEY]:theme});
});
$("design-select").addEventListener("change", async event => {
  applyDesign(event.target.value);
  await chrome.storage.local.set({[DESIGN_KEY]:document.documentElement.dataset.design});
});
$("importance-control-visible").addEventListener("change", async event => {
  const previous = showImportanceControl;
  showImportanceControl = event.target.checked;
  renderEffectiveNotes(true); renderCalendar();
  try { await chrome.storage.local.set({[IMPORTANCE_CONTROL_KEY]:showImportanceControl}); }
  catch (error) {
    showImportanceControl = previous;
    event.target.checked = previous;
    renderEffectiveNotes(true); renderCalendar();
    notice(error.message || t("Не удалось сохранить настройку важности."), true);
  }
});
/** «Дзинь» when a running timer reaches zero. Runs while the panel is hidden too; a timer that ended while the panel was closed stays quiet. */
function watchTimerEnd() {
  const status = timerView(focusState).status;
  if (lastFocusStatus === "running" && status === "finished" && timerChime && !IS_LIBRARY_WINDOW) playTimerChime();
  lastFocusStatus = status;
}
setInterval(watchTimerEnd, 500);
$("focus-chime").addEventListener("change", async event => {
  const checkbox = event.currentTarget;
  timerChime = checkbox.checked;
  if (timerChime) playTimerChime();
  try { await chrome.storage.local.set({[TIMER_CHIME_KEY]:timerChime}); }
  catch (error) {
    timerChime = !timerChime;
    checkbox.checked = timerChime;
    showFocusError(error.message);
  }
});
for (const [value, label] of Object.entries(IGNITION_SOUNDS)) $("ignition-sound").append(new Option(label, value));
$("ignition-sound").value = ignitionSound;
$("ignition-sound").addEventListener("change", async event => {
  const previous = ignitionSound;
  ignitionSound = normalizeIgnitionSound(event.target.value);
  playIgnition();
  try { await chrome.storage.local.set({[IGNITION_SOUND_KEY]:ignitionSound}); }
  catch (error) {
    ignitionSound = previous;
    event.target.value = previous;
    notice(error.message || t("Не удалось сохранить звук розжига."), true);
  }
});
$("calendar-settings-form").addEventListener("submit", async event => {
  event.preventDefault();
  const input = $("calendar-url"); const icalUrl = input.value.trim();
  const nameInput = $("calendar-name"); const name = nameInput.value.trim();
  if (!icalUrl) { notice(t("Вставьте iCal-ссылку календаря."), true); return; }
  const button = $("calendar-connect"); button.disabled = true;
  try {
    await send("calendar-configure", {icalUrl, name:name || undefined});
    input.value = "";
    nameInput.value = "";
    notice(t("Календарь добавлен."));
    await loadCalendarWeek();
  }
  catch (error) { notice(error.message, true); }
  finally { button.disabled = false; }
});
$("calendar-disconnect").addEventListener("click", async () => {
  const button = $("calendar-disconnect"); if (button.disabled) return; button.disabled = true;
  try { await send("calendar-configure", {icalUrl:null}); calendar = {...calendar, configured:false, calendar_count:0, calendars:[], events:[], last_updated:null, stale:false, error:null}; renderCalendar(); notice(t("Все календари отключены.")); }
  catch (error) { notice(error.message, true); }
  finally { button.disabled = false; }
});
$("calendar-oauth-connect").addEventListener("click", async () => {
  const button = $("calendar-oauth-connect");
  if (button.disabled) return;
  button.disabled = true;
  try {
    const file = $("calendar-oauth-file").files?.[0];
    if (file) {
      let credentials;
      try { credentials = JSON.parse(await file.text()); }
      catch { throw new Error(t("Не удалось прочитать OAuth JSON Google.")); }
      calendar.write_access = await send("calendar-oauth-client", {credentials});
      $("calendar-oauth-file").value = "";
    } else if (!calendar.write_access?.client_configured) {
      throw new Error(t("Сначала выберите OAuth JSON типа Desktop app."));
    }
    await send("calendar-oauth-start");
    notice(t("В новой вкладке подтвердите доступ Google. Панель подключится автоматически."));
    renderCalendarWriteStatus();
  } catch (error) { notice(error.message, true); }
  finally { button.disabled = false; }
});
$("calendar-oauth-disconnect").addEventListener("click", async () => {
  const button = $("calendar-oauth-disconnect");
  if (button.disabled) return;
  button.disabled = true;
  try {
    calendar.write_access = await send("calendar-oauth-disconnect");
    renderCalendarWriteStatus();
    notice(t("Управление Google Календарём отключено. iCal-чтение осталось подключено."));
  } catch (error) { notice(error.message, true); }
  finally { button.disabled = false; }
});
for (const [id, direction] of [["previous-day", -1], ["next-day", 1]]) {
  const button = $(id);
  button.addEventListener("click", () => shiftDay(direction));
  button.addEventListener("dragover", event => { event.preventDefault(); event.dataTransfer.dropEffect = "move"; button.classList.add("drop-target"); });
  button.addEventListener("dragleave", () => button.classList.remove("drop-target"));
  button.addEventListener("drop", event => dropOnDay(event, direction));
}
$("today-jump").addEventListener("click", () => { if (dashboard) selectDay(dashboard.date); });
document.querySelectorAll("[data-backlog]").forEach(button => {
  button.addEventListener("click", () => { backlogView = button.dataset.backlog; renderBacklog(); void chrome.storage.local.set({[BACKLOG_VIEW_KEY]:backlogView}); });
  button.addEventListener("dragover", event => {
    if (button.dataset.backlog === "thoughts") return;
    const note = draggedNote(event);
    if (!patchChangesNote(note, assignmentPatch(note, {area:"backlog", bucket:button.dataset.backlog}))) return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = "move";
    button.classList.add("drop-target");
  });
  button.addEventListener("dragleave", () => button.classList.remove("drop-target"));
  button.addEventListener("drop", event => {
    if (button.dataset.backlog === "thoughts") return;
    event.preventDefault();
    event.stopPropagation();
    const noteId = draggedTaskId || event.dataTransfer.getData("text/plain");
    clearTaskDropMarks();
    if (noteId) moveTaskToBacklog(noteId, button.dataset.backlog, $("unscheduled-card"));
  });
});
setupAssignmentDropZone($("today-card"), () => ({area:"today", date:selectedDate}));
setupAssignmentDropZone($("unscheduled-card"), () => ({area:"backlog", bucket:backlogView}));
setupAssignmentDropZone($("inbox-card"), () => ({area:"inbox"}));
$("planner-size-toggle").addEventListener("click", async () => {
  plannerCompact = !plannerCompact;
  applyPlannerSize();
  await chrome.storage.local.set({"panel:planner-compact":plannerCompact});
});
$("planner-heading").title = t("Свайпните влево или вправо для другого дня");
$("planner-heading").parentElement.addEventListener("touchstart", event => {
  swipeStartX = event.changedTouches[0]?.clientX ?? null;
  swipeStartY = event.changedTouches[0]?.clientY ?? null;
}, {passive:true});
$("planner-heading").parentElement.addEventListener("touchend", event => {
  const end = event.changedTouches[0];
  if (end && swipeStartX !== null && Math.abs(end.clientX - swipeStartX) > 40 && Math.abs(end.clientX - swipeStartX) > Math.abs(end.clientY - swipeStartY)) {
    shiftDay(end.clientX < swipeStartX ? 1 : -1);
  }
  swipeStartX = null; swipeStartY = null;
}, {passive:true});
$("retry").addEventListener("click",async()=>{try{await send("retry");await refresh();}catch(error){notice(error.message,true);}});
$("library-search").addEventListener("input",()=>{renderLibrary();void persistLibraryState();});
function closeFocusPanel(restoreFocus = false) {
  if ($("focus-panel").hidden) return;
  $("focus-panel").hidden = true;
  $("focus-toggle").setAttribute("aria-expanded", "false");
  if (restoreFocus) $("focus-toggle").focus();
}
function openFocusPanel() {
  closeAppearancePanel(false);
  $("settings").hidden = true;
  $("settings-toggle").setAttribute("aria-expanded", "false");
  $("focus-panel").hidden = false;
  $("focus-toggle").setAttribute("aria-expanded", "true");
  const selected = $("focus-presets").querySelector('[aria-pressed="true"]');
  (selected && !selected.disabled ? selected : $("focus-action").hidden ? $("focus-reset") : $("focus-action")).focus();
}
function showFocusError(message) {
  if ($("focus-panel").hidden) openFocusPanel();
  $("focus-error").textContent = message || t("Не удалось изменить таймер. Попробуйте ещё раз.");
  $("focus-error").hidden = false;
  $("focus-error").focus();
}
function focusChoice() {
  for (const button of $("focus-presets").querySelectorAll("button")) {
    button.setAttribute("aria-pressed", String(button.dataset.focusMinutes === selectedFocusMinutes));
  }
  $("focus-custom-label").classList.toggle("selected", selectedFocusMinutes === "custom");
  $("focus-error").hidden = true;
}
$("focus-toggle").addEventListener("click", () => { if ($("focus-panel").hidden) openFocusPanel(); else closeFocusPanel(true); });
$("focus-close").addEventListener("click", () => closeFocusPanel(true));
document.addEventListener("pointerdown", event => { if (!$("focus-panel").hidden && !$("focus-host").contains(event.target)) closeFocusPanel(false); });
document.addEventListener("keydown", event => { if (event.key === "Escape" && !$("focus-panel").hidden) { event.preventDefault(); event.stopPropagation(); closeFocusPanel(true); } });
$("focus-presets").addEventListener("click", event => {
  const button = event.target.closest("[data-focus-minutes]");
  if (!button || button.disabled) return;
  selectedFocusMinutes = button.dataset.focusMinutes;
  $("focus-custom").value = selectedFocusMinutes;
  focusChoice();
});
$("focus-custom").addEventListener("focus", () => { selectedFocusMinutes = "custom"; focusChoice(); });
$("focus-custom").addEventListener("input", () => { selectedFocusMinutes = "custom"; focusChoice(); });
async function focusAction(action) {
  if (focusPending) return;
  focusPending = true;
  $("focus-quick-action").disabled = $("focus-action").disabled = $("focus-reset").disabled = true;
  renderFocus();
  $("focus-error").hidden = true;
  try {
    const minutes = selectedFocusMinutes === "custom" ? $("focus-custom").value : selectedFocusMinutes;
    focusState = await send("timer-action", { action, minutes });
    renderFocus(action === "start" || action === "reset");
    if (focusState.overlayError && focusOverlayVisible) {
      showFocusError(t("Таймер идёт, но полоса недоступна: {overlayError}", {overlayError: focusState.overlayError}));
    } else if (action === "start") closeFocusPanel(true);
  } catch (error) {
    showFocusError(error.message);
  } finally {
    focusPending = false;
    $("focus-quick-action").disabled = $("focus-action").disabled = false;
    renderFocus();
  }
}
$("focus-quick-action").addEventListener("click", () => {
  const action = { idle:"start", running:"pause", paused:"resume", finished:"start" }[timerView(focusState).status];
  void focusAction(action);
});
$("focus-action").addEventListener("click", () => { if (!$("focus-action").hidden) void focusAction("start"); });
$("focus-reset").addEventListener("click", () => { if (!$("focus-reset").disabled) void focusAction("reset"); });
async function reportFocusOverlayLayout() {
  if (IS_LIBRARY_WINDOW || document.hidden) return;
  let tab = null;
  try { tab = await chrome.tabs.getCurrent(); } catch { /* Side Panel has no tab. */ }
  if (tab) return;
  try {
    // An extension tab is used by tests and is not a Chrome Side Panel.
    await send("timer-overlay-layout", {width: Math.round(window.innerWidth)});
  } catch { /* The timer still works when the local service is offline. */ }
}
window.addEventListener("resize", () => { void reportFocusOverlayLayout(); });
setInterval(() => { void reportFocusOverlayLayout(); }, 3000);
$("focus-overlay-visible").addEventListener("change", async event => {
  const checkbox = event.currentTarget;
  checkbox.disabled = true;
  try {
    const result = await send("timer-overlay-visibility", {visible:checkbox.checked});
    focusOverlayVisible = result.visible;
    if (result.overlayError && result.visible) showFocusError(t("Полоса недоступна: {overlayError}", {overlayError: result.overlayError}));
    else $("focus-error").hidden = true;
  } catch (error) {
    checkbox.checked = focusOverlayVisible;
    showFocusError(error.message);
  } finally {
    checkbox.disabled = false;
    renderNeonColors();
  }
});
// Neon colour of the Chrome bar: a small radio group of glowing tubes.
function renderNeonColors() {
  const host = $("focus-neon-colors");
  if (!host.childElementCount) {
    host.append(...NEON_COLORS.map(color => {
      const button = node("button", "focus-neon-swatch");
      button.type = "button";
      button.dataset.neon = color.id;
      button.setAttribute("role", "radio");
      button.setAttribute("aria-label", color.name);
      button.title = color.name;
      button.style.setProperty("--neon", color.tube);
      button.style.setProperty("--neon-core", color.core);
      return button;
    }));
  }
  for (const button of host.children) {
    const selected = button.dataset.neon === focusOverlayColor;
    button.setAttribute("aria-checked", String(selected));
    button.tabIndex = selected ? 0 : -1;
  }
  $("focus-neon").dataset.off = String(!focusOverlayVisible);
}
async function chooseNeonColor(id, {focus = false} = {}) {
  if (id === focusOverlayColor) return;
  const previous = focusOverlayColor;
  focusOverlayColor = id;
  renderNeonColors();
  if (focus) $("focus-neon-colors").querySelector(`[data-neon="${id}"]`)?.focus();
  try {
    const result = await send("timer-overlay-color", {color:id});
    if (result.overlayError && focusOverlayVisible) showFocusError(t("Полоса недоступна: {overlayError}", {overlayError: result.overlayError}));
    else $("focus-error").hidden = true;
  } catch (error) {
    focusOverlayColor = previous;
    renderNeonColors();
    showFocusError(error.message);
  }
}
$("focus-neon-colors").addEventListener("click", event => {
  const button = event.target.closest("[data-neon]");
  if (button) void chooseNeonColor(button.dataset.neon);
});
$("focus-neon-colors").addEventListener("keydown", event => {
  const step = {ArrowRight:1, ArrowDown:1, ArrowLeft:-1, ArrowUp:-1}[event.key];
  if (!step) return;
  event.preventDefault();
  const ids = NEON_COLORS.map(color => color.id);
  void chooseNeonColor(ids[(ids.indexOf(focusOverlayColor) + step + ids.length) % ids.length], {focus:true});
});
function openLibrary({focus = true, resetScroll = true, persist = true} = {}) {
  closeAppearancePanel(false);
  if (document.body.dataset.panelView === "library") { if (focus) $("library-search").focus(); return; }
  closeFocusPanel(false);
  dayScrollBeforeLibrary = $("daily-scroll").scrollTop;
  document.body.dataset.panelView = "library";
  $("library-entry").hidden = true;
  $("library-pane").hidden = false;
  $("library-entry").setAttribute("aria-expanded", "true");
  $("find-toggle").setAttribute("aria-expanded", "true");
  $("settings").hidden = true;
  $("settings-toggle").setAttribute("aria-expanded", "false");
  if (resetScroll) $("library-list").scrollTop = 0;
  if (focus) $("library-search").focus();
  if (persist) void persistLibraryState();
}
function returnToDay(restoreFocus = true, persist = true) {
  if (IS_LIBRARY_WINDOW) return;
  delete document.body.dataset.panelView;
  $("library-pane").hidden = true;
  $("library-entry").hidden = false;
  $("library-entry").setAttribute("aria-expanded", "false");
  $("find-toggle").setAttribute("aria-expanded", "false");
  $("daily-scroll").scrollTop = dayScrollBeforeLibrary;
  if (restoreFocus) $("find-toggle").focus();
  if (persist) void persistLibraryState("panel");
}
$("find-toggle").setAttribute("aria-controls", "library-view");
$("find-toggle").setAttribute("aria-expanded", "false");
$("find-toggle").addEventListener("click", openLibrary);
$("library-entry").addEventListener("click", openLibrary);
$("library-back").addEventListener("click", () => returnToDay());
$("library-clear").addEventListener("click", () => { $("library-search").value = ""; renderLibrary(); void persistLibraryState(); $("library-search").focus(); });
$("library-nav").addEventListener("click", event => {
  const button = event.target.closest("button");
  if (!button) return;
  const section = button.dataset.dossierSection;
  const category = button.dataset.libraryCategory;
  if (!section && !category) return;
  $("library-search").value = "";
  libraryRoute = section ? {...libraryRoute, section} : {category, projectId:null, section:"all"};
  renderLibrary();
  void persistLibraryState();
  $("library-nav").querySelector(section ? `[data-dossier-section="${section}"]` : `[data-library-category="${category}"]`)?.focus();
});
$("library-list").addEventListener("click", event => {
  const button = event.target.closest("button[data-project-id]");
  if (!button) return;
  $("library-search").value = "";
  libraryRoute = {category:"projects", projectId:button.dataset.projectId, section:"all"};
  renderLibrary();
  void persistLibraryState();
  $("library-breadcrumb").querySelector("button")?.focus();
});
$("library-breadcrumb").addEventListener("click", event => {
  if (!event.target.closest("button[data-library-back-projects]")) return;
  libraryRoute = {category:"projects", projectId:null, section:"all"};
  renderLibrary();
  void persistLibraryState();
  $("library-nav").querySelector('[data-library-category="projects"]')?.focus();
});
$("library-list").addEventListener("scroll", scheduleLibraryScrollSave, {passive:true});
$("library-nav").addEventListener("scroll", scheduleLibraryScrollSave, {passive:true});
function showLibraryWindowError(message) {
  $("library-window-error").textContent = message;
  $("library-window-error").hidden = false;
}
$("library-window-toggle").addEventListener("click", async () => {
  const button = $("library-window-toggle");
  if (button.disabled) return;
  $("library-window-error").hidden = true;
  if (IS_LIBRARY_WINDOW) {
    if (!Number.isInteger(originWindowId)) {
      showLibraryWindowError(t("Исходное окно Chrome закрыто. Откройте обычное окно Chrome и нажмите кнопку ещё раз."));
      try { originWindowId = (await send("library-window-context")).originWindowId; } catch { /* Retry on next click. */ }
      return;
    }
    // Chrome requires sidePanel.open to start in this click's user gesture, before any await.
    let opening;
    try { opening = chrome.sidePanel.open({windowId:originWindowId}); }
    catch (error) {
      showLibraryWindowError(error.message || t("Не удалось вернуть библиотеку в панель."));
      try { originWindowId = (await send("library-window-context")).originWindowId; } catch { originWindowId = null; }
      return;
    }
    button.disabled = true;
    try {
      await finishLibraryReturn(opening,
        () => persistLibraryState("panel", {claim:true}),
        () => send("library-window-return"),
        () => persistLibraryState("window", {claim:true}));
    } catch (error) {
      try { originWindowId = (await send("library-window-context")).originWindowId; } catch { originWindowId = null; }
      showLibraryWindowError(t("{message} Повторите попытку; окно библиотеки остаётся открытым.", {message: error.message || t("Не удалось вернуть библиотеку в панель.")}));
      button.disabled = false;
    }
    return;
  }
  button.disabled = true;
  try {
    clearTimeout(libraryScrollTimer);
    await persistLibraryState("window", {claim:true});
    libraryDetached = true;
    await send("library-window-open");
  } catch (error) {
    libraryDetached = false;
    await persistLibraryState("panel", {claim:true}).catch(() => {});
    showLibraryWindowError(t("{message} Попробуйте ещё раз.", {message: error.message || t("Не удалось открыть окно библиотеки.")}));
  } finally { button.disabled = false; }
});
async function initializeLibraryView() {
  if (IS_LIBRARY_WINDOW) {
    document.body.dataset.libraryWindow = "true";
    $("library-pane").hidden = false;
    $("library-back").hidden = true;
    $("library-window-label").textContent = t("Вернуть в панель");
    $("library-window-toggle").setAttribute("aria-label", t("Вернуть библиотеку в панель"));
    $("library-window-toggle").title = t("Вернуть в панель");
    document.body.dataset.panelView = "library";
    try { originWindowId = (await send("library-window-context")).originWindowId; }
    catch (error) { showLibraryWindowError(error.message || t("Не удалось определить исходное окно Chrome.")); }
  } else await send("library-window-status").catch(() => {});
  const saved = (await chrome.storage.local.get(LIBRARY_SNAPSHOT_KEY))[LIBRARY_SNAPSHOT_KEY];
  if (saved) applyLibrarySnapshot(saved);
  else if (IS_LIBRARY_WINDOW) renderLibrary();
}
function stepRotation(direction) {
  if (!dashboard || viewerState) return;
  const candidates = resurfaceCandidates(dashboard);
  if (candidates.length < 2) return;
  const next = stepResurface(candidates, resurfaceId, direction);
  resurfaceId = next.id;
  resurfaceHoldUntil = next.holdUntil;
  renderRotation(direction);
}
const rotationCard = $("rotation-card");
rotationCard.tabIndex = 0;
rotationCard.setAttribute("aria-label", t("Вспомнить. Стрелки влево и вправо перелистывают записи"));
rotationCard.addEventListener("click", event => {
  if (event.target.closest(".note,button,a,input,textarea,select,summary,details,label")) return;
  $("rotation-content").querySelector(".carousel-disclosure")?.click();
});
rotationCard.addEventListener("keydown", event => {
  if (event.target !== rotationCard || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
  event.preventDefault();
  stepRotation(event.key === "ArrowRight" ? 1 : -1);
});
let rotationPointer = null;
/** Scrub the stack with the finger: the dragged card follows it one to one. */
function dragStack(start, dx) {
  const stage = $("rotation-stage");
  if (stage.dataset.fanCount !== "multiple" || stage.classList.contains("stack-expanded")) return;
  const direction = dx < 0 ? 1 : -1;
  if (start.motion && start.motion.direction !== direction) { revertStackMotion(start.motion); start.motion = null; }
  if (!start.motion && dx !== 0) {
    const candidates = resurfaceCandidates(dashboard);
    const target = candidates.find(note => note.id === stepResurface(candidates, resurfaceId, direction).id);
    if (!target || candidates.length < 2) return;
    start.motion = prepareStackStep(direction, target, candidates);
    stage.classList.add("stack-animating");
  }
  if (start.motion) setStackProgress(start.motion, Math.min(1, Math.abs(dx) / (start.motion.geometry.width + 32)));
}
async function releaseStackDrag(motion, velocity, allowCommit = true) {
  const toward = motion.direction > 0 ? -velocity : velocity;
  if (allowCommit && stackDragCommits(motion.progress, toward)) {
    const next = stepResurface(motion.candidates, resurfaceId, motion.direction);
    resurfaceId = next.id;
    resurfaceHoldUntil = next.holdUntil;
    const target = motion.candidates.find(note => note.id === next.id) || motion.target;
    await animateRotation(recallContent(target), `${target.id}:${target.version}`, motion.direction, false, motion.candidates, target, motion);
    return;
  }
  const token = ++rotationMotionToken;
  rotationMotion = motion;
  rotationAnimating = true;
  await runStackMotion(motion, 0, STACK_DURATION * .75);
  if (token !== rotationMotionToken) return;
  revertStackMotion(motion);
  clearRotationMotion();
  renderRotation();
}
function releaseVelocity(samples) {
  const first = samples[0];
  const last = samples.at(-1);
  return first && last && last.t > first.t ? (last.x - first.x) / (last.t - first.t) : 0;
}
rotationCard.addEventListener("pointerdown", event => {
  const front = event.target.closest("#rotation-content.stack-front");
  if (!front || event.button !== 0 || viewerState || rotationAnimating) return;
  if (event.target.closest("button:not(.carousel-disclosure):not(.note-image-open),a,input,textarea,select,summary,details,label,[draggable='true'],.carousel-more")) return;
  const rect = front.getBoundingClientRect();
  const edge = Math.max(36, Math.min(44, rect.width * .16));
  rotationPointer = {id:event.pointerId, x:event.clientX, y:event.clientY, edge:event.clientX - rect.left < edge ? -1 : event.clientX - rect.left > rect.width - edge ? 1 : 0,
    dragged:false, selection:window.getSelection()?.toString() || "", samples:[{x:event.clientX, t:event.timeStamp}], motion:null};
});
rotationCard.addEventListener("pointermove", event => {
  const start = rotationPointer;
  if (!start || start.id !== event.pointerId) return;
  const dx = event.clientX - start.x;
  const dy = event.clientY - start.y;
  if (!start.dragged) {
    if (!(Math.abs(dx) > 7 && Math.abs(dx) > Math.abs(dy) * 1.2)) return;
    start.dragged = true;
    stackDrag = start;
    try { rotationCard.setPointerCapture(event.pointerId); } catch {}
  }
  event.preventDefault();
  start.samples.push({x:event.clientX, t:event.timeStamp});
  if (start.samples.length > 5) start.samples.shift();
  dragStack(start, dx);
});
rotationCard.addEventListener("pointerup", event => {
  const start = rotationPointer;
  rotationPointer = null;
  if (!start || start.id !== event.pointerId) return;
  stackDrag = null;
  const dx = event.clientX - start.x;
  const dy = event.clientY - start.y;
  if (start.dragged) {
    event.preventDefault();
    suppressRotationClickUntil = Date.now() + 400;
    if (start.motion) void releaseStackDrag(start.motion, releaseVelocity(start.samples));
    else if (Math.abs(dx) >= 44 && Math.abs(dx) > Math.abs(dy) * 1.35) stepRotation(dx < 0 ? 1 : -1);
    return;
  }
  const selection = window.getSelection();
  if (selection && !selection.isCollapsed && selection.toString() !== start.selection) return;
  if (start.edge) {
    event.preventDefault();
    suppressRotationClickUntil = Date.now() + 400;
    stepRotation(start.edge);
  }
});
rotationCard.addEventListener("pointercancel", () => {
  const start = rotationPointer;
  rotationPointer = null;
  stackDrag = null;
  if (start?.motion) void releaseStackDrag(start.motion, 0, false);
});
rotationCard.addEventListener("dragstart", event => { if (event.target.closest("#rotation-content")) event.preventDefault(); });
setInterval(()=>{if(!document.hidden) void refresh();},15000);
// Calendar changes made outside Brainalot appear without touching the general
// dashboard refresh. The server cache and iCal backoff limit Google requests.
setInterval(()=>{if(!document.hidden && selectedWeekStart) void loadCalendarWeek();},10000);
setInterval(()=>{if(!document.hidden && dashboard) renderCalendar();},5000);
setInterval(() => { if (!document.hidden) renderFocus(); }, 1000);
document.addEventListener("visibilitychange",()=>{if(!document.hidden) void refresh();});
chrome.storage.onChanged.addListener((changes, area)=>{
  if (area !== "local") return;
  if (localeChange(changes)) { location.reload(); return; }
  if (changes[LIBRARY_SNAPSHOT_KEY]) applyLibrarySnapshot(changes[LIBRARY_SNAPSHOT_KEY].newValue);
  if (changes[THEME_KEY]) applyTheme(changes[THEME_KEY].newValue);
  if (changes[DESIGN_KEY]) applyDesign(changes[DESIGN_KEY].newValue);
  if (changes[IMPORTANCE_CONTROL_KEY]) {
    showImportanceControl = changes[IMPORTANCE_CONTROL_KEY].newValue !== false;
    $("importance-control-visible").checked = showImportanceControl;
    renderEffectiveNotes(true); renderCalendar();
  }
  if (changes[CALENDAR_CHANGED_KEY]) void loadCalendarWeek(true);
  if (changes[IGNITION_SOUND_KEY]) {
    ignitionSound = normalizeIgnitionSound(changes[IGNITION_SOUND_KEY].newValue);
    $("ignition-sound").value = ignitionSound;
  }
  if (changes[CALENDAR_IMPORTANCE_KEY]) {
    calendarImportance = normalizeCalendarImportance(changes[CALENDAR_IMPORTANCE_KEY].newValue);
    renderCalendar();
  }
  if (changes[TIMER_CHIME_KEY]) {
    timerChime = normalizeTimerChime(changes[TIMER_CHIME_KEY].newValue);
    $("focus-chime").checked = timerChime;
  }
  if (changes[FOCUS_KEY]) { focusState = normalizeTimer(changes[FOCUS_KEY].newValue); renderFocus(true); }
  if (changes[FOCUS_OVERLAY_KEY]) {
    focusOverlayVisible = changes[FOCUS_OVERLAY_KEY].newValue !== false;
    $("focus-overlay-visible").checked = focusOverlayVisible;
    renderNeonColors();
  }
  if (changes[FOCUS_OVERLAY_COLOR_KEY]) {
    focusOverlayColor = normalizeNeonColor(changes[FOCUS_OVERLAY_COLOR_KEY].newValue);
    renderNeonColors();
  }
  if (Object.keys(changes).some(key=>key.startsWith("capture:")||key==="connection")) scheduleRefresh();
});
applyPlannerSize();
updateLocaleToggle();
void syncLocale();
applyTheme("system");
applyDesign("stream");
renderFocus(true);
void chrome.storage.local.get(THEME_KEY).then(saved => applyTheme(saved[THEME_KEY]));
void chrome.storage.local.get(DESIGN_KEY).then(saved => applyDesign(saved[DESIGN_KEY]));
void chrome.storage.local.get(TIMER_CHIME_KEY).then(saved => {
  timerChime = normalizeTimerChime(saved[TIMER_CHIME_KEY]);
  $("focus-chime").checked = timerChime;
});
void chrome.storage.local.get(FOCUS_KEY).then(saved => { focusState = normalizeTimer(saved[FOCUS_KEY]); renderFocus(true); });
renderNeonColors();
void chrome.storage.local.get([FOCUS_OVERLAY_KEY, FOCUS_OVERLAY_COLOR_KEY]).then(saved => {
  focusOverlayVisible = saved[FOCUS_OVERLAY_KEY] !== false;
  focusOverlayColor = normalizeNeonColor(saved[FOCUS_OVERLAY_COLOR_KEY]);
  $("focus-overlay-visible").checked = focusOverlayVisible;
  renderNeonColors();
});
void reportFocusOverlayLayout();
void chrome.storage.local.get("panel:planner-compact").then(saved => {
  plannerCompact = saved["panel:planner-compact"] === true;
  applyPlannerSize();
});
void chrome.storage.local.get(BACKLOG_VIEW_KEY).then(saved => {
  if (["unscheduled","week","month","thoughts"].includes(saved[BACKLOG_VIEW_KEY])) {
    backlogView = saved[BACKLOG_VIEW_KEY];
    renderBacklog();
  }
});
void chrome.storage.local.get([IMPORTANCE_CONTROL_KEY, CALENDAR_IMPORTANCE_KEY]).then(saved => {
  showImportanceControl = saved[IMPORTANCE_CONTROL_KEY] !== false;
  calendarImportance = normalizeCalendarImportance(saved[CALENDAR_IMPORTANCE_KEY]);
  $("importance-control-visible").checked = showImportanceControl;
  renderEffectiveNotes(true); renderCalendar();
});
void chrome.storage.local.get(IGNITION_SOUND_KEY).then(saved => {
  ignitionSound = normalizeIgnitionSound(saved[IGNITION_SOUND_KEY]);
  $("ignition-sound").value = ignitionSound;
});
void initializeLibraryView().catch(error => showLibraryWindowError(error.message || t("Не удалось восстановить библиотеку.")));
void refresh();
