import "./desktop-platform.js";
import { mountTimeUI } from "./time-ui.js";
import { sharedApi } from "./time-client.js";
// Work mode page: a week of projects with deadlines, a notes board and task
// workspaces made of Chrome tab groups. Opened from the side panel's Work button.
import * as store from "./work-store.js";
import * as D from "./work-dates.js";
import { ic, esc } from "./work-icons.js";
import * as tabs from "./work-tabs.js";
import { createCalendar } from "./work-calendar.js";
import { createDrawer } from "./work-drawer.js";
import { createBoard } from "./work-board.js";
import { createPlanning } from "./work-planning.js?v=constructor-20261023-a";
import { applyDocument, localeChange, syncLocale, t } from "./i18n.js";

const $ = s => document.querySelector(s);
const THEME_KEY = "panel:theme";
const ui = { weekStart: D.mondayOf(D.today()), days: 7, selectedId: null, editingProject: null, focusProject: false, mode: "projects" };

function newItem(fields) {
  return {
    id: store.uid(), project: null, kind: "task", title: "", start: null, end: null, time: "",
    status: "todo", firstStep: "", resumeFrom: "", notes: "", checklist: [], groups: [],
    createdAt: Date.now(), ...fields,
  };
}

const ctx = {
  get state() { return store.get(); },
  ui,
  update: (fn, meta = {}) => store.update(fn, meta),
  render: () => renderAll(),
  select(id) {
    ui.selectedId = id;
    renderAll();
  },
  reveal(it) {
    if (!it.start) return;
    const last = D.addDays(ui.weekStart, ui.days - 1);
    if (it.end < ui.weekStart || it.start > last) ui.weekStart = D.mondayOf(it.start);
  },
  createItem(fields = {}) {
    const st = store.get();
    if (!st.projects.length) {
      toast(t("Сначала добавьте проект"));
      return null;
    }
    const it = newItem({ project: st.projects[0].id, start: D.today(), end: D.today(), ...fields });
    store.update(s => { s.items.push(it); }, { from: "create" });
    ctx.reveal(it);
    ctx.select(it.id);
    drawer.focusTitle();
    return it;
  },
  deleteItem(id) {
    const st = store.get();
    const idx = st.items.findIndex(i => i.id === id);
    if (idx < 0) return;
    const [removed] = st.items.splice(idx, 1);
    ui.selectedId = null;
    store.update(null, { from: "delete" });
    toast(t("Удалено: {title}", { title: removed.title || t("без названия") }), {
      label: t("Вернуть"),
      fn: () => store.update(s => { s.items.splice(idx, 0, removed); }),
    });
  },
  importData: () => $("#import-file").click(),
  openWorkspace,
  captureWorkspace,
  toast,
};

let calendar, drawer, board, planning;
let hostFullscreen = false, fullscreenRequest = null;

function fullscreenLabel() {
  const expanded = !!document.fullscreenElement || hostFullscreen;
  const label = expanded ? t("Вернуть окно") : t("Развернуть");
  const button = $("#work-fullscreen");
  button.innerHTML = `${ic("fit", 14)} <span>${esc(label)}</span>`;
  button.title = label;
  button.setAttribute("aria-label", label);
  button.setAttribute("aria-pressed", String(expanded));
}

async function toggleFullscreen() {
  const expanded = !!document.fullscreenElement || hostFullscreen;
  try {
    if (window.parent !== window) {
      const requestId = crypto.randomUUID?.() || store.uid();
      fullscreenRequest = requestId;
      window.parent.postMessage({ type: "brainalot-work-fullscreen-request", expanded: !expanded, request_id: requestId }, window.location.origin);
      return;
    }
    if (expanded) await document.exitFullscreen();
    else await document.documentElement.requestFullscreen();
    fullscreenLabel();
  } catch (error) {
    toast(t("Не удалось изменить размер Work: {error}", { error: error.message }));
  }
}

function setMode(mode) {
  ui.mode = mode;
  document.body.classList.toggle("plan-mode", mode === "plan");
  $("#planning-root").hidden = mode !== "plan";
  $("#mode-plan").setAttribute("aria-selected", String(mode === "plan"));
  $("#mode-projects").setAttribute("aria-selected", String(mode === "projects"));
  if (mode === "plan") planning?.render();
  else { calendar?.render(); board?.render(); }
}

function renderHeader() {
  const today = D.today();
  $("#today-label").textContent = D.longDay(today);
  const last = D.addDays(ui.weekStart, ui.days - 1);
  $("#range-label").textContent = `${D.dayMonth(ui.weekStart)} — ${D.dayMonth(last)}`;
  document.querySelectorAll("#range-seg button").forEach(b => b.classList.toggle("is-on", Number(b.dataset.days) === ui.days));
  $("#go-today").classList.toggle("is-off", D.mondayOf(today) === ui.weekStart);

  // The nearest hard deadlines and events stay in sight.
  const st = store.get();
  const upcoming = st.items
    .filter(i => i.start && i.kind !== "task" && i.status !== "done" && i.start >= today)
    .sort((a, b) => (a.start + (a.time || "")).localeCompare(b.start + (b.time || "")))
    .slice(0, 4);
  $("#countdowns").innerHTML = upcoming.map(i => {
    const p = st.projects.find(x => x.id === i.project) || st.projects[0];
    const n = D.diff(today, i.start);
    return `<button class="cd pc-${p?.color || "teal"} cd-${i.kind} ${n <= 1 ? "is-soon" : ""}" data-id="${i.id}" title="${esc(`${p?.name || ""} · ${D.weekday(i.start)}, ${D.dayMonth(i.start)}`)}">
      ${ic(i.kind === "deadline" ? "flag" : "video", 14)}<span class="cd-t">${esc(i.title)}</span><span class="cd-n">${D.relDays(n)}</span></button>`;
  }).join("");
}

function renderAll() {
  renderHeader();
  if (ui.mode === "plan") planning?.render();
  else { calendar.render(); drawer.render(); }
  document.body.classList.toggle("drawer-open", !!ui.selectedId);
}

// --- toasts ---

let toastTimer = null;
function toast(message, action = null) {
  const el = $("#toast");
  el.innerHTML = `<span>${esc(message)}</span>${action ? `<button>${esc(action.label)}</button>` : ""}`;
  el.hidden = false;
  if (action) el.querySelector("button").onclick = () => { el.hidden = true; action.fn(); };
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, action ? 7000 : 3500);
}

// --- workspaces ---

async function openWorkspace(it, onlyGroup = null) {
  try {
    const r = await tabs.openWorkspace(it, onlyGroup);
    if (!r.ok) return toast(t("В рабочем месте пока нет ссылок"));
    if (r.preview) return toast(t("Это предпросмотр: открыта первая ссылка. Группы вкладок работают в расширении"));
    if (r.focused) return toast(t("Это рабочее место уже открыто — переключаю на его окно"));
    if (!onlyGroup && it.status === "todo") {
      store.update(() => { it.status = "doing"; }, { from: "drawer-struct" });
      toast(t("Открыто вкладок: {count}. Задача — в работе", { count: r.tabs }));
    }
  } catch (error) {
    console.error(error);
    toast(t("Не получилось открыть: {error}", { error: error.message }));
  }
}

async function captureWorkspace(it) {
  try {
    const r = await tabs.captureWorkspace(it);
    if (!r.ok) return toast(t("Забрать вкладки можно только в расширении Chrome"));
    if (!r.groups.length) return toast(t("В окне нет других вкладок"));
    const count = r.groups.reduce((a, g) => a + g.links.length, 0);
    const summary = t("Вкладок: {tabs} · групп: {groups}", { tabs: count, groups: r.groups.length });
    const question = r.fromWorkspace
      ? t("Заменить рабочее место вкладками из окна этого рабочего места?")
      : t("Заменить рабочее место вкладками из текущего окна?");
    if (it.groups.some(g => g.links.length) && !confirm(`${question}\n${summary}`)) return;
    store.update(() => {
      it.groups = r.groups.map(g => ({
        id: store.uid(),
        title: g.title ?? t("Без группы"),
        color: g.color,
        links: g.links.map(l => ({ id: store.uid(), title: l.title, url: l.url })),
      }));
    }, { from: "drawer-struct" });
    toast(t("Сохранено. {summary}", { summary }));
  } catch (error) {
    console.error(error);
    toast(t("Не получилось забрать вкладки: {error}", { error: error.message }));
  }
}

// The workspace window was closed: ask where to pick up next time.
async function checkPendingStop() {
  if (!tabs.hasTabGroups()) return;
  const pending = (await chrome.storage.local.get(tabs.PENDING_STOP_KEY))[tabs.PENDING_STOP_KEY];
  const banner = $("#banner");
  const it = pending && store.get().items.find(i => i.id === pending.itemId);
  if (!it) {
    banner.hidden = true;
    if (pending) chrome.storage.local.remove(tabs.PENDING_STOP_KEY);
    return;
  }
  banner.innerHTML = `
    <span class="banner-t">${esc(t("Рабочее место «{title}» закрыто. С чего продолжить в следующий раз?", { title: it.title }))}</span>
    <input id="pb-input" value="${esc(it.resumeFrom)}" placeholder="${t("Одна строка, например «дописать шаги 4–6»")}">
    <button class="primary" id="pb-save">${t("Сохранить")}</button>
    <button id="pb-done">${ic("check", 14)} ${t("Задача готова")}</button>
    <button class="icon-btn" id="pb-x" title="${t("Не сейчас")}" aria-label="${t("Не сейчас")}">${ic("x")}</button>`;
  banner.hidden = false;
  const close = () => { banner.hidden = true; chrome.storage.local.remove(tabs.PENDING_STOP_KEY); };
  $("#pb-save").onclick = () => { store.update(() => { it.resumeFrom = $("#pb-input").value.trim(); }); close(); toast(t("Записано")); };
  $("#pb-done").onclick = () => { store.update(() => { it.status = "done"; it.resumeFrom = $("#pb-input").value.trim(); }); close(); toast(t("Отмечено готовым")); };
  $("#pb-x").onclick = close;
  $("#pb-input").onkeydown = e => { if (e.key === "Enter") $("#pb-save").click(); };
  $("#pb-input").focus();
}

// --- data copy ---

const blobToDataUrl = blob => new Promise((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => resolve(reader.result);
  reader.onerror = () => reject(reader.error);
  reader.readAsDataURL(blob);
});

function dataUrlToBlob(u) {
  const [head, body] = u.split(",");
  const mime = head.match(/:(.*?);/)?.[1] || "application/octet-stream";
  const bin = atob(body);
  const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  return new Blob([arr], { type: mime });
}

async function exportData() {
  await store.flush();
  const images = {};
  for (const [k, blob] of await store.imgAll()) images[k] = await blobToDataUrl(blob);
  const data = { app: "work-canvas", exportedAt: new Date().toISOString(), state: store.get(), images };
  const url = URL.createObjectURL(new Blob([JSON.stringify(data)], { type: "application/json" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = `work-${D.today()}.json`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

async function importData(file) {
  try {
    const data = JSON.parse(await file.text());
    if (data.app !== "work-canvas" || !Array.isArray(data.state?.items) || !Array.isArray(data.state?.projects)) {
      return toast(t("Это не копия данных Work-режима"));
    }
    const current = store.get();
    if ((current.items.length || current.projects.length) && !confirm(t("Заменить все текущие данные Work-режима этой копией?"))) return;

    ui.selectedId = null;
    ui.editingProject = null;
    await store.replaceWithImages(data.state, Object.entries(data.images || {}).map(([id,url])=>[id,dataUrlToBlob(url)]));
    board.render();
    toast(t("Копия загружена"));
  } catch (error) {
    console.error(error);
    toast(t("Не получилось прочитать файл: {error}", { error: error.message }));
  }
}

// --- theme follows the panel's appearance setting ---

function applyTheme(value) {
  if (value === "light" || value === "dark") document.documentElement.dataset.theme = value;
  else delete document.documentElement.dataset.theme;
}

// --- header controls ---

function bindChrome() {
  $("#prev").innerHTML = ic("chevL");
  $("#next").innerHTML = ic("chevR");
  $("#menu-btn").innerHTML = ic("dots");
  $("#add-task").innerHTML = `${ic("plus", 15)} ${t("Задача")}`;
  $("#work-fullscreen").onclick = toggleFullscreen;
  document.addEventListener("fullscreenchange", fullscreenLabel);
  window.addEventListener("message", event => {
    if (event.source !== window.parent || event.origin !== window.location.origin || event.data?.type !== "brainalot-work-fullscreen-result" ||
        event.data.request_id !== fullscreenRequest) return;
    fullscreenRequest = null;
    if (event.data.ok) hostFullscreen = !!event.data.expanded;
    else toast(t("Не удалось развернуть Work: {error}", { error: event.data.error || t("неизвестная ошибка") }));
    fullscreenLabel();
  });
  fullscreenLabel();

  $("#prev").onclick = () => { ui.weekStart = D.addDays(ui.weekStart, -7); renderAll(); };
  $("#next").onclick = () => { ui.weekStart = D.addDays(ui.weekStart, 7); renderAll(); };
  $("#go-today").onclick = () => { ui.weekStart = D.mondayOf(D.today()); renderAll(); };
  $("#range-seg").onclick = e => {
    const days = Number(e.target.closest("[data-days]")?.dataset.days);
    if (!days) return;
    ui.days = days;
    store.update(st => { st.view.days = days; }, { from: "view" });
  };
  $("#add-task").onclick = () => ctx.createItem();
  $("#countdowns").onclick = e => {
    const id = e.target.closest("[data-id]")?.dataset.id;
    const it = id && store.get().items.find(i => i.id === id);
    if (!it) return;
    ctx.reveal(it);
    ctx.select(id);
  };

  const menu = $("#menu");
  $("#menu-btn").onclick = e => { e.stopPropagation(); menu.hidden = !menu.hidden; };
  document.addEventListener("click", () => { menu.hidden = true; });
  menu.onclick = e => {
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (act === "export") exportData();
    if (act === "import") ctx.importData();
  };
  $("#import-file").onchange = e => {
    const f = e.target.files[0];
    e.target.value = "";
    if (f) importData(f);
  };

  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && hostFullscreen) { e.preventDefault(); void toggleFullscreen(); return; }
    if (e.key !== "Escape" || board.isEditing()) return;
    if (ui.selectedId && !drawer.isEditingLink()) {
      document.activeElement?.blur?.();
      ctx.select(null);
    }
  });

  // Drag the splitter to change the calendar height; double-click resets it.
  const wrap = $("#cal-wrap");
  const splitter = $("#splitter");
  splitter.addEventListener("pointerdown", e => {
    const h0 = wrap.getBoundingClientRect().height, y0 = e.clientY;
    splitter.setPointerCapture(e.pointerId);
    document.body.classList.add("is-resizing");
    const move = ev => { wrap.style.height = `${Math.max(120, Math.min(innerHeight - 220, h0 + ev.clientY - y0))}px`; };
    const up = () => {
      splitter.removeEventListener("pointermove", move);
      splitter.removeEventListener("pointerup", up);
      document.body.classList.remove("is-resizing");
      store.update(st => { st.view.calH = Math.round(wrap.getBoundingClientRect().height); }, { from: "view" });
    };
    splitter.addEventListener("pointermove", move);
    splitter.addEventListener("pointerup", up);
  });
  splitter.addEventListener("dblclick", () => {
    wrap.style.height = "";
    store.update(st => { st.view.calH = null; }, { from: "view" });
  });
}

async function main() {
  applyDocument();
  if (globalThis.chrome?.storage) {
    if (await syncLocale()) return;
    applyTheme((await chrome.storage.local.get(THEME_KEY))[THEME_KEY]);
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area !== "local") return;
      if (changes[THEME_KEY]) applyTheme(changes[THEME_KEY].newValue);
      if (localeChange(changes)) location.reload();
      if (changes[tabs.PENDING_STOP_KEY]) checkPendingStop();
    });
  }

  const timeHost = document.createElement("div");
  timeHost.className = "work-time-host";
  document.querySelector(".top").after(timeHost);
  mountTimeUI(timeHost);
  let loadError = null;
  store.onSaveStatus((status,error)=>{ if (["error","conflict"].includes(status)) loadError=error; });
  await store.load();
  if (!store.isInitialized()) {
    const gate = document.createElement("section"); gate.className = "work-connect-gate";
    const message = document.createElement("p"); message.textContent = t("Work ещё не перенесён. Откройте Work в обновлённом расширении Chrome: задачи, доски и изображения перенесутся вместе.");
    const empty = document.createElement("button"); empty.textContent = t("Начать пустой Work");
    empty.onclick = async () => { if (confirm(t("Начать пустой Work? Данные расширения останутся в резервной копии и потребуют ручного импорта."))) { await store.initializeEmpty(); location.reload(); } };
    const retry = document.createElement("button"); retry.textContent = t("Проверить перенос"); retry.onclick = () => location.reload();
    gate.append(message, retry, empty);
    if (loadError) { const detail=document.createElement("p"); detail.setAttribute("role","alert"); detail.textContent=loadError.message; gate.prepend(detail); }
    timeHost.after(gate);
    for (const child of [...document.body.children]) if (![timeHost, gate].includes(child)) child.inert = true;
    return;
  }
  const freeze = blocked => {
    for (const child of [...document.body.children]) if (child !== timeHost) child.inert = blocked;
  };
  store.onMigrationChange(blocked => freeze(blocked || !store.isWritable()&&!store.getDeletionState().pending.length));
  if (!store.isWritable()&&!store.getDeletionState().pending.length) showWorkRecovery("conflict");
  const applyDeletionGate=()=>{
    const blocked=store.getDeletionState().pending.length>0;
    for(const selector of ['#cal-wrap','#board-wrap']){const element=$(selector);if(element)element.inert=blocked;}
    if($('#drawer'))$('#drawer').inert=blocked||!ui.selectedId;
    for(const selector of ['#add-task','#import-file','[data-act="import"]']){const element=$(selector);if(element)element.disabled=blocked;}
  };
  const st = store.get();
  ui.days = st.view?.days || 7;
  if (st.view?.calH) $("#cal-wrap").style.height = `${st.view.calH}px`;

  calendar = createCalendar($("#calendar"), ctx);
  drawer = createDrawer($("#drawer"), ctx);
  board = createBoard($("#board-wrap"), ctx);
  planning = createPlanning($("#planning-root"), {
    get state() { return store.get(); },
    action: store.planningAction,
    pendingDraft: store.getPendingPlanningDraft,
    deletionState: store.getDeletionState,
    refreshWork: store.refreshRemote,
    retryDraft: store.retryPlanningDraft,
    discardDraft: store.discardPlanningDraft,
    openWorkspace: item => openWorkspace(item),
    openCubeWorkspace: (item, onlyGroup = null) => tabs.openWorkspace(item, onlyGroup),
    readDashboard: () => sharedApi("/api/dashboard"),
    toast,
  });
  $("#mode-plan").onclick = () => setMode("plan");
  $("#mode-projects").onclick = () => setMode("projects");
  setMode(st.planning ? "plan" : "projects");
  bindChrome();

  let hadPlanning = !!st.planning;
  store.subscribe(meta => {
    applyDeletionGate();
    if(meta.from==='deletion'){planning.remoteChanged();return;}
    if (!hadPlanning && store.get()?.planning) { hadPlanning = true; setMode("plan"); }
    if (meta.from === "remote" && ui.mode === "plan") { renderHeader(); planning.remoteChanged(); return; }
    if (meta.from === "board" || meta.from === "quiet") return;
    if (meta.from === "drawer") { renderHeader(); calendar.render(); return; }
    renderAll();
    if (meta.from === "project" || meta.from === "replace") board.render();
  });
  store.onSaveStatus((status, error) => {
    if (["error", "conflict"].includes(status)) {
      toast(error?.message || t("Не получилось сохранить — проверьте место на диске"));
      showWorkRecovery(status);
    }
  });
  store.onRemoteChange(() => {
    if (!board.isEditing()) board.render();
    if (ui.mode === "plan") return;
    if (!document.activeElement?.closest?.("#drawer, .lane-label")) renderAll();
  });

  renderAll();
  applyDeletionGate();
  board.render();
  checkPendingStop();

  // Midnight passes while the tab is open: refresh "today" and the countdowns.
  let day = D.today();
  setInterval(() => {
    if (D.today() !== day) { day = D.today(); renderAll(); }
  }, 60_000);
}

main().catch(error => {
  console.error(error);
  document.body.insertAdjacentHTML("afterbegin", `<div class="fatal">${esc(t("Work-режим не запустился: {error}", { error: error.message }))}</div>`);
});

function showWorkRecovery(status) {
  let banner = document.getElementById("work-recovery");
  if (banner) return;
  banner = document.createElement("section"); banner.id = "work-recovery"; banner.className = "work-connect-gate";
  const text = document.createElement("p"); text.textContent = t("Правки сохранены локально. Общие данные изменились или сервис не подтвердил запись.");
  const drafts = document.createElement("select"); drafts.ariaLabel = t("Локальные копии");
  let selectedDraft;
  store.listDrafts().then(items=>{ for(const item of items) drafts.append(new Option(`${new Date(item.created).toLocaleString()} · ${item.request_id.slice(0,8)}`,item.key)); selectedDraft=drafts.value; });
  drafts.onchange=()=>{selectedDraft=drafts.value;};
  const exportDraft = document.createElement("button"); exportDraft.textContent = t("Скачать локальную копию");
  exportDraft.onclick = async () => {
    const draft = await store.recoverDraft(selectedDraft);
    const images = {}; for (const [id, blob] of await store.imgAll()) images[id] = await blobToDataUrl(blob);
    const link = document.createElement("a"); link.href = URL.createObjectURL(new Blob([JSON.stringify({app:"work-canvas",state:draft?.state || store.get(),images})],{type:"application/json"})); link.download="work-draft.json"; link.click();
  };
  const retry = document.createElement("button"); retry.textContent = t("Проверить повтор"); retry.onclick = async () => { try { await store.recoverDraft(selectedDraft); await store.retryDraft(); location.reload(); } catch(error) { toast(error.message); } };
  const remote = document.createElement("button"); remote.textContent = t("Загрузить общую версию"); remote.onclick = async () => { if (confirm(t("Сначала скачайте локальную копию. Загрузить общую версию?"))) { await store.discardDraft(selectedDraft); location.reload(); } };
  banner.append(text, drafts, exportDraft, retry, remote); document.body.prepend(banner);
  for (const child of [...document.body.children]) if (child !== banner) child.inert = true;
}
