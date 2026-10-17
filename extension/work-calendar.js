import { taskTimeButton } from "./time-ui.js";
// Work mode calendar: a lane per project, columns are days. Tasks are bars
// (drag whole or by the edges), deadlines and events are one-day marks.
// Undated items wait in a tray under the lane and can be dropped on a day.

import * as D from "./work-dates.js";
import { ic, esc } from "./work-icons.js";
import { t } from "./i18n.js";
import { uid } from "./work-store.js";

export const PROJECT_COLORS = ["teal", "violet", "coral", "amber", "blue", "pink"];
const KIND_ORDER = { deadline: 0, event: 1, task: 2 };
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

export function createCalendar(el, ctx) {
  let drag = null;

  el.addEventListener("pointerdown", onPointerDown);
  el.addEventListener("dblclick", onDblClick);
  el.addEventListener("click", onClick);
  el.addEventListener("input", onProjectInput);
  el.addEventListener("keydown", onProjectKey);

  function render() {
    const { state } = ctx;
    const { days, weekStart } = ctx.ui;
    const today = D.today();
    const last = D.addDays(weekStart, days - 1);
    const list = Array.from({ length: days }, (_, i) => D.addDays(weekStart, i));

    el.style.setProperty("--days", days);
    el.classList.toggle("is-wide", days > 7);

    let html = '<div class="cal-row cal-head"><div class="cal-corner"></div><div class="cal-days">';
    for (const d of list) {
      html += `<div class="${dayClass("dh", d, today)}"><span class="dh-wd">${esc(D.weekday(d))}</span><span class="dh-d">${esc(D.dayMonth(d))}</span>${d === today ? `<span class="dh-today">${t("сегодня")}</span>` : ""}</div>`;
    }
    html += "</div></div>";
    for (const p of state.projects) html += laneHtml(p, list, today, weekStart, last, days);
    html += state.projects.length
      ? `<div class="cal-foot"><button data-act="project-add">${ic("plus", 14)} ${t("Проект")}</button></div>`
      : `<div class="cal-empty">
          <p>${t("Добавьте первый проект, например курс, и раскладывайте задачи по дням.")}</p>
          <div class="cal-empty-actions">
            <button class="primary" data-act="project-add">${ic("plus", 14)} ${t("Проект")}</button>
            <button data-act="import">${t("Загрузить копию…")}</button>
          </div>
        </div>`;
    el.innerHTML = html;
    for(const row of el.querySelectorAll(".it[data-id],.chip[data-id]")) {
      const item=state.items.find(item=>item.id===row.dataset.id);
      if(item)row.append(taskTimeButton({source:"work",id:item.id,title:item.title || t("Без названия")}));
    }

    const editing = el.querySelector(".lane-label.is-editing .pe-name");
    if (editing && ctx.ui.focusProject) {
      ctx.ui.focusProject = false;
      editing.focus();
      editing.select();
    }
  }

  function dayClass(base, d, today) {
    return [base, d === today && "is-today", D.isWeekend(d) && "is-weekend", d < today && "is-past"].filter(Boolean).join(" ");
  }

  function labelHtml(p, items) {
    if (ctx.ui.editingProject === p.id) {
      return `<div class="lane-label is-editing">
        <input class="pe-name" data-pf="name" value="${esc(p.name)}" placeholder="${t("Название")}">
        <input class="pe-about" data-pf="about" value="${esc(p.about || "")}" placeholder="${t("Описание (необязательно)")}">
        <div class="pe-colors">${PROJECT_COLORS.map(c => `<button class="pe-color pc-${c} ${c === p.color ? "is-on" : ""}" data-act="project-color" data-v="${c}" title="${t("Цвет проекта")}" aria-label="${t("Цвет проекта")}"></button>`).join("")}</div>
        <div class="pe-actions">
          <button class="primary sm" data-act="project-done">${t("Готово")}</button>
          <button class="link-danger" data-act="project-delete">${t("Удалить")}</button>
        </div>
      </div>`;
    }
    const open = items.filter(i => i.kind === "task" && i.status !== "done").length;
    return `<div class="lane-label" data-act="project-edit" title="${t("Нажмите, чтобы изменить проект")}">
      <div class="lane-name">${esc(p.name || t("Без названия"))}</div>
      <div class="lane-about">${esc(p.about || "")}</div>
      <div class="lane-count">${t("Открытых задач: {count}", { count: open })}</div>
    </div>`;
  }

  function laneHtml(p, list, today, weekStart, last, days) {
    const items = ctx.state.items.filter(i => i.project === p.id);
    const visible = items
      .filter(i => i.start && i.end >= weekStart && i.start <= last)
      .sort((a, b) => a.start.localeCompare(b.start) || KIND_ORDER[a.kind] - KIND_ORDER[b.kind] || b.end.localeCompare(a.end));

    // Put each item on the first row whose previous item has already ended.
    const rowEnds = [];
    let itemsHtml = "";
    for (const it of visible) {
      const s = Math.max(0, D.diff(weekStart, it.start));
      const e = Math.min(days - 1, D.diff(weekStart, it.end));
      let r = rowEnds.findIndex(end => end < s);
      if (r < 0) { r = rowEnds.length; rowEnds.push(e); } else rowEnds[r] = e;
      itemsHtml += itemHtml(it, s, e, r, it.start < weekStart, it.end > last, today);
    }

    const cells = list.map(d => `<div class="${dayClass("cell", d, today)}" data-day="${d}" title="${t("Двойной клик — новая задача")}"></div>`).join("");
    const undated = items.filter(i => !i.start);
    const tray = undated.length
      ? `<div class="tray"><span class="tray-label">${t("без даты")}</span>${undated.map(chipHtml).join("")}<span class="tray-hint">${ic("arrowRight", 12)} ${t("перетащите на день")}</span></div>`
      : "";

    return `<div class="cal-row lane pc-${p.color}" data-project="${p.id}">
      ${labelHtml(p, items)}
      <div class="lane-body">
        <div class="lane-grid" style="grid-template-rows:repeat(${Math.max(1, rowEnds.length)}, var(--row-h))">
          <div class="lane-bg">${cells}</div>${itemsHtml}
        </div>${tray}
      </div>
    </div>`;
  }

  function kindIcon(it) {
    if (it.kind === "deadline") return ic("flag", 13);
    if (it.kind === "event") return ic("video", 13);
    if (it.status === "done") return ic("check", 13);
    return "";
  }

  function progress(it) {
    if (!it.checklist?.length) return "";
    const done = it.checklist.filter(c => c.done).length;
    return `<span class="it-prog">${done}/${it.checklist.length}</span>`;
  }

  function tooltip(it) {
    const when = it.start === it.end ? `${D.weekday(it.start)}, ${D.dayMonth(it.start)}` : `${D.dayMonth(it.start)} — ${D.dayMonth(it.end)}`;
    return [it.title, when + (it.time ? ` · ${it.time}` : ""), it.firstStep && t("Первый шаг: {step}", { step: it.firstStep })].filter(Boolean).join("\n");
  }

  function itemHtml(it, s, e, r, clipL, clipR, today) {
    const done = it.status === "done";
    const cls = [
      "it", `it-${it.kind}`, `st-${it.status}`,
      done && "is-done",
      it.kind === "task" && !done && it.end < today && "is-overdue",
      clipL && "clip-l", clipR && "clip-r",
      ctx.ui.selectedId === it.id && "is-sel",
    ].filter(Boolean).join(" ");
    const handles = it.kind === "task"
      ? `${clipL ? "" : '<span class="it-h it-hl"></span>'}${clipR ? "" : '<span class="it-h it-hr"></span>'}`
      : "";
    const icon = kindIcon(it);
    const time = it.time ? `<span class="it-time">${esc(it.time)}</span>` : "";
    return `<div class="${cls}" data-id="${it.id}" style="grid-column:${s + 1}/${e + 2};grid-row:${r + 1}" title="${esc(tooltip(it))}">${handles}${icon ? `<span class="it-ic">${icon}</span>` : ""}${time}<span class="it-t">${esc(it.title || t("Без названия"))}</span>${progress(it)}</div>`;
  }

  function chipHtml(it) {
    const icon = kindIcon(it);
    return `<div class="chip it-${it.kind} ${ctx.ui.selectedId === it.id ? "is-sel" : ""}" data-id="${it.id}" title="${t("Нажмите, чтобы открыть; перетащите на день, чтобы назначить")}">${icon ? `<span class="it-ic">${icon}</span>` : ""}<span class="it-t">${esc(it.title || t("Без названия"))}</span>${progress(it)}</div>`;
  }

  // --- projects ---

  function addProject() {
    const used = new Set(ctx.state.projects.map(p => p.color));
    const p = { id: uid(), name: t("Новый проект"), about: "", color: PROJECT_COLORS.find(c => !used.has(c)) || "teal" };
    ctx.update(st => {
      st.projects.push(p);
      st.boards.push({ id: `b-${p.id}`, project: p.id, view: { x: 40, y: 28, z: 1 }, items: [] });
    }, { from: "project" });
    ctx.ui.editingProject = p.id;
    ctx.ui.focusProject = true;
    ctx.render();
  }

  function deleteProject(p) {
    const count = ctx.state.items.filter(i => i.project === p.id).length;
    if (!confirm(t("Удалить проект «{name}» вместе с его задачами ({count}) и доской?", { name: p.name, count }))) return;
    ctx.ui.editingProject = null;
    if (ctx.state.items.some(i => i.id === ctx.ui.selectedId && i.project === p.id)) ctx.ui.selectedId = null;
    ctx.update(st => {
      st.projects = st.projects.filter(x => x !== p);
      st.items = st.items.filter(i => i.project !== p.id);
      st.boards = st.boards.filter(b => b.project !== p.id);
      if (!st.boards.some(b => b.id === st.view.board)) st.view.board = "general";
    }, { from: "project" });
  }

  function onClick(e) {
    const b = e.target.closest("[data-act]");
    if (!b || drag) return;
    const project = ctx.state.projects.find(p => p.id === b.closest(".lane")?.dataset.project);
    switch (b.dataset.act) {
      case "project-add": return addProject();
      case "import": return ctx.importData();
      case "project-edit":
        ctx.ui.editingProject = project.id;
        ctx.ui.focusProject = true;
        return ctx.render();
      case "project-color":
        return ctx.update(() => { project.color = b.dataset.v; }, { from: "project" });
      case "project-done":
        ctx.ui.editingProject = null;
        return ctx.update(null, { from: "project" });
      case "project-delete": return deleteProject(project);
    }
  }

  function onProjectInput(e) {
    const field = e.target.dataset.pf;
    if (!field) return;
    const project = ctx.state.projects.find(p => p.id === e.target.closest(".lane")?.dataset.project);
    if (project) ctx.update(() => { project[field] = e.target.value; }, { from: "quiet" });
  }

  function onProjectKey(e) {
    if (!e.target.dataset.pf || (e.key !== "Enter" && e.key !== "Escape")) return;
    e.preventDefault();
    e.stopPropagation();
    ctx.ui.editingProject = null;
    ctx.update(null, { from: "project" });
  }

  // --- dragging ---

  function onPointerDown(e) {
    if (e.button !== 0) return;
    const target = e.target.closest(".it, .chip");
    if (!target) return;
    const item = ctx.state.items.find(i => i.id === target.dataset.id);
    if (!item) return;
    e.preventDefault();
    const isChip = target.classList.contains("chip");
    drag = {
      item, el: target, isChip,
      x0: e.clientX, y0: e.clientY, moved: false,
      mode: e.target.classList.contains("it-hl") ? "l" : e.target.classList.contains("it-hr") ? "r" : "move",
      colW: isChip ? 0 : target.parentElement.getBoundingClientRect().width / ctx.ui.days,
    };
    target.setPointerCapture(e.pointerId);
    target.addEventListener("pointermove", onMove);
    target.addEventListener("pointerup", onUp);
    target.addEventListener("pointercancel", onCancel);
  }

  function onMove(e) {
    const dx = e.clientX - drag.x0, dy = e.clientY - drag.y0;
    if (!drag.moved && Math.hypot(dx, dy) < 5) return;
    drag.moved = true;
    if (drag.isChip) return moveGhost(e);

    drag.el.classList.add("is-dragging");
    drag.next = shifted(drag.item, drag.mode, Math.round(dx / drag.colW));
    const { weekStart, days } = ctx.ui;
    const s = clamp(D.diff(weekStart, drag.next.start), 0, days - 1);
    const en = clamp(D.diff(weekStart, drag.next.end), 0, days - 1);
    drag.el.style.gridColumn = `${s + 1}/${en + 2}`;
  }

  function shifted(item, mode, d) {
    if (mode === "move" || item.kind !== "task") return { start: D.addDays(item.start, d), end: D.addDays(item.end, d) };
    if (mode === "l") {
      const s = D.addDays(item.start, d);
      return { start: s > item.end ? item.end : s, end: item.end };
    }
    const en = D.addDays(item.end, d);
    return { start: item.start, end: en < item.start ? item.start : en };
  }

  function moveGhost(e) {
    if (!drag.ghost) {
      drag.ghost = drag.el.cloneNode(true);
      drag.ghost.classList.add("ghost");
      document.body.append(drag.ghost);
      drag.el.classList.add("is-lifted");
    }
    drag.ghost.style.left = `${e.clientX + 12}px`;
    drag.ghost.style.top = `${e.clientY + 8}px`;
    el.querySelectorAll(".cell.is-drop").forEach(c => c.classList.remove("is-drop"));
    dropTarget(e.clientX, e.clientY)?.cell.classList.add("is-drop");
  }

  function dropTarget(x, y) {
    const grid = document.elementFromPoint(x, y)?.closest(".lane-grid");
    if (!grid || !el.contains(grid)) return null;
    const r = grid.getBoundingClientRect();
    const idx = clamp(Math.floor((x - r.left) / (r.width / ctx.ui.days)), 0, ctx.ui.days - 1);
    const cell = grid.querySelectorAll(".cell")[idx];
    return { project: grid.closest(".lane").dataset.project, day: cell.dataset.day, cell };
  }

  function cleanup() {
    const target = drag.el;
    target.removeEventListener("pointermove", onMove);
    target.removeEventListener("pointerup", onUp);
    target.removeEventListener("pointercancel", onCancel);
    drag.ghost?.remove();
    el.querySelectorAll(".cell.is-drop").forEach(c => c.classList.remove("is-drop"));
    const d = drag;
    drag = null;
    return d;
  }

  function onCancel() {
    cleanup();
    ctx.render();
  }

  function onUp(e) {
    const d = cleanup();
    if (!d.moved) return ctx.select(d.item.id);

    if (d.isChip) {
      const hit = dropTarget(e.clientX, e.clientY);
      if (!hit) return ctx.render();
      return ctx.update(() => {
        d.item.project = hit.project;
        d.item.start = hit.day;
        d.item.end = hit.day;
      });
    }

    if (d.next && (d.next.start !== d.item.start || d.next.end !== d.item.end)) {
      ctx.update(() => {
        d.item.start = d.next.start;
        d.item.end = d.next.end;
      });
    } else ctx.render();
  }

  function onDblClick(e) {
    const cell = e.target.closest(".cell");
    if (!cell) return;
    ctx.createItem({ project: cell.closest(".lane").dataset.project, start: cell.dataset.day, end: cell.dataset.day });
  }

  return { render };
}
