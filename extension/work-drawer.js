import { taskTimeButton } from "./time-ui.js";
// Work mode task panel. Text fields write to the state without redrawing the
// panel (the caret stays put); structural changes redraw it.

import * as D from "./work-dates.js";
import { ic, esc } from "./work-icons.js";
import { t } from "./i18n.js";
import { uid } from "./work-store.js";
import * as tabs from "./work-tabs.js";

const kinds = () => [["task", t("Задача")], ["deadline", t("Дедлайн")], ["event", t("Событие")]];
const statuses = () => [["todo", t("Не начато")], ["doing", t("В работе")], ["done", t("Готово")]];

function host(url) {
  try {
    const u = new URL(url);
    return u.hostname.replace(/^www\./, "") + (u.pathname.length > 1 ? u.pathname.slice(0, 24) : "");
  } catch {
    return url;
  }
}

export function createDrawer(el, ctx) {
  let editingLink = null;
  let focusSel = null;

  el.addEventListener("input", onInput);
  el.addEventListener("change", onChange);
  el.addEventListener("click", onClick);
  el.addEventListener("keydown", onKey);

  const item = () => ctx.state.items.find(i => i.id === ctx.ui.selectedId);
  const struct = () => ctx.update(null, { from: "drawer-struct" });
  const quiet = () => ctx.update(null, { from: "drawer" });

  function render() {
    const it = item();
    if (!it) {
      el.classList.remove("open");
      el.inert = true;
      editingLink = null;
      return;
    }
    const p = ctx.state.projects.find(x => x.id === it.project) || ctx.state.projects[0];
    const links = it.groups.reduce((n, g) => n + g.links.length, 0);
    const withLinks = it.groups.filter(g => g.links.length).length;
    const ckDone = it.checklist.filter(c => c.done).length;

    el.className = `drawer open pc-${p?.color || "teal"} kind-${it.kind}`;
    el.inert = false;
    el.innerHTML = `
      <div class="dr-head">
        <span class="dr-dot"></span>
        <select class="dr-project" data-f="project" aria-label="${t("Проект")}">
          ${ctx.state.projects.map(x => `<option value="${x.id}" ${x.id === it.project ? "selected" : ""}>${esc(x.name)}</option>`).join("")}
        </select>
        <div class="seg seg-sm">${kinds().map(([v, l]) => `<button data-act="kind" data-v="${v}" class="${it.kind === v ? "is-on" : ""}">${l}</button>`).join("")}</div>
        <button class="icon-btn" data-act="close" title="${t("Закрыть (Esc)")}" aria-label="${t("Закрыть")}">${ic("x")}</button>
      </div>
      <div class="dr-body">
        <textarea class="dr-title" data-f="title" rows="1" placeholder="${t("Название")}">${esc(it.title)}</textarea>
        <div class="dr-when">${whenHtml(it)}</div>
        <div class="dr-count">${countHtml(it)}</div>
        <div class="seg dr-status">${statuses().map(([v, l]) => `<button data-act="status" data-v="${v}" class="${it.status === v ? "is-on" : ""} st-${v}">${v === "done" ? ic("check", 14) : ""}${l}</button>`).join("")}</div>

        <div class="dr-launch">
          <button class="primary big" data-act="open-ws">${ic("play", 15)} ${t("Открыть рабочее место")}</button>
          <div class="dr-launch-sub">${links
            ? t("Вкладок: {tabs} · групп: {groups}", { tabs: links, groups: withLinks }) + (tabs.hasTabGroups() ? ` · ${t("откроется отдельным окном")}` : "")
            : t("Пока пусто: добавьте ссылки ниже или заберите вкладки из открытого окна")}</div>
        </div>

        <label class="fld"><span class="fld-l">${t("Первый шаг")}</span>
          <textarea class="grow" data-f="firstStep" rows="1" placeholder="${t("Самое маленькое действие, с которого начать")}">${esc(it.firstStep)}</textarea></label>
        <label class="fld"><span class="fld-l">${t("Продолжить с")}</span>
          <textarea data-f="resumeFrom" rows="2" placeholder="${t("Где остановка — чтобы потом не вспоминать")}">${esc(it.resumeFrom)}</textarea></label>

        <section class="dr-sec">
          <div class="dr-sec-h"><h3>${t("Чек-лист")}</h3>${it.checklist.length ? `<span class="muted">${ckDone}/${it.checklist.length}</span>` : ""}</div>
          <div class="checklist">${it.checklist.map(c => `
            <div class="ck ${c.done ? "is-done" : ""}" data-c="${c.id}">
              <input type="checkbox" data-f="ck-done" ${c.done ? "checked" : ""} aria-label="${t("Готово")}">
              <input class="ck-text" data-f="ck-text" value="${esc(c.text)}">
              <button class="icon-btn sm" data-act="ck-del" title="${t("Удалить пункт")}" aria-label="${t("Удалить пункт")}">${ic("x", 14)}</button>
            </div>`).join("")}
          </div>
          <input class="ck-add" data-enter="ck-add" placeholder="${t("+ пункт и Enter")}">
        </section>

        <section class="dr-sec">
          <div class="dr-sec-h"><h3>${t("Рабочее место")}</h3><span class="muted">${t("группы вкладок Chrome")}</span></div>
          <div class="groups">${it.groups.map(groupHtml).join("")}</div>
          <div class="row-btns">
            <button data-act="group-add">${ic("plus", 14)} ${t("Группа")}</button>
            <button data-act="capture" title="${t("Забрать вкладки из окна этого рабочего места, а если оно не открыто — из текущего окна")}">${ic("download", 14)} ${t("Забрать вкладки из окна")}</button>
          </div>
        </section>

        <section class="dr-sec">
          <div class="dr-sec-h"><h3>${t("Заметки")}</h3></div>
          <textarea class="dr-notes" data-f="notes" rows="5" placeholder="${t("Всё, что пригодится по задаче")}">${esc(it.notes)}</textarea>
        </section>

        <div class="dr-foot"><button class="link-danger" data-act="delete">${ic("trash", 14)} ${t("Удалить")}</button></div>
      </div>`;

    el.querySelector(".dr-status").append(taskTimeButton({source:"work", id:it.id, title:it.title}));
    el.querySelectorAll(".dr-title, textarea.grow").forEach(grow);
    if (focusSel) {
      const f = el.querySelector(focusSel);
      focusSel = null;
      if (f) { f.focus(); f.select?.(); }
    }
  }

  function whenHtml(it) {
    if (!it.start) {
      return `<span class="muted">${t("Без даты")}</span><button data-act="set-date">${ic("calendar", 14)} ${t("Поставить на сегодня")}</button>`;
    }
    const undate = `<button class="icon-btn sm" data-act="undate" title="${t("Убрать дату (в лоток «без даты»)")}" aria-label="${t("Убрать дату")}">${ic("x", 14)}</button>`;
    if (it.kind === "task") {
      return `<label>${t("с")} <input type="date" data-f="start" value="${it.start}"></label>
        <label>${t("по")} <input type="date" data-f="end" value="${it.end}"></label>${undate}`;
    }
    return `<input type="date" data-f="start" value="${it.start}">
      <input type="time" data-f="time" value="${esc(it.time)}" title="${t("Время (необязательно)")}">${undate}`;
  }

  function countHtml(it) {
    const today = D.today();
    if (!it.start) return `<span class="muted">${t("Срок не назначен — перетащите из лотка на день или поставьте дату")}</span>`;
    if (it.kind !== "task") {
      const n = D.diff(today, it.start);
      const when = `${D.weekday(it.start)}, ${D.dayMonth(it.start)}${it.time ? ` · ${it.time}` : ""}`;
      return `${esc(when)} — <b class="${n <= 1 && n >= 0 ? "hot" : ""}">${D.relDays(n)}</b>`;
    }
    const parts = [];
    const n = D.diff(today, it.end);
    if (it.status !== "done" && n < 0) {
      parts.push(`<span class="warn">${t("Мягкий срок был {when}", { when: D.relDays(n) })}</span> — ${t("перенесите или закройте")}`);
    } else {
      parts.push(`${t("Мягкий срок: {date}", { date: esc(`${D.weekday(it.end)}, ${D.dayMonth(it.end)}`) })} — <b>${D.relDays(n)}</b>`);
    }
    const hard = ctx.state.items
      .filter(x => x.project === it.project && x.kind === "deadline" && x.start && x.start >= it.end && x.status !== "done")
      .sort((a, b) => a.start.localeCompare(b.start))[0];
    if (hard) {
      parts.push(`${t("Твёрдый: {title} — {date}", { title: esc(hard.title), date: esc(D.dayMonth(hard.start)) })}, <b class="hot">${D.relDays(D.diff(today, hard.start))}</b>`);
    }
    return parts.join("<br>");
  }

  function groupHtml(g) {
    return `<div class="grp" data-g="${g.id}">
      <div class="grp-head">
        <button class="grp-color gc-${g.color}" data-act="grp-color" title="${t("Цвет группы в Chrome (нажмите, чтобы сменить)")}" aria-label="${t("Цвет группы")}"></button>
        <input class="grp-title" data-f="grp-title" value="${esc(g.title)}" placeholder="${t("Название группы")}">
        <span class="grp-n">${g.links.length}</span>
        <button class="icon-btn sm" data-act="grp-open" title="${t("Открыть только эту группу")}" aria-label="${t("Открыть группу")}">${ic("external", 14)}</button>
        <button class="icon-btn sm" data-act="grp-del" title="${t("Удалить группу")}" aria-label="${t("Удалить группу")}">${ic("x", 14)}</button>
      </div>
      <div class="links">${g.links.map(linkHtml).join("")}</div>
      <input class="lnk-add" data-enter="lnk-add" placeholder="${t("Вставьте ссылку и нажмите Enter")}">
    </div>`;
  }

  function linkHtml(l) {
    if (editingLink === l.id) {
      return `<div class="lnk is-edit" data-l="${l.id}">
        <input data-f="lnk-title" value="${esc(l.title)}" placeholder="${t("Название")}">
        <input data-f="lnk-url" value="${esc(l.url)}" placeholder="https://…">
        <button class="icon-btn sm" data-act="lnk-done" title="${t("Готово")}" aria-label="${t("Готово")}">${ic("check", 14)}</button>
      </div>`;
    }
    const fav = tabs.faviconUrl(l.url);
    return `<div class="lnk" data-l="${l.id}">
      ${fav ? `<img class="fav" src="${esc(fav)}" alt="">` : '<span class="fav fav-ph"></span>'}
      <a href="${esc(l.url)}" target="_blank" rel="noreferrer" title="${esc(l.url)}">${esc(l.title || host(l.url))}</a>
      <button class="icon-btn sm" data-act="lnk-edit" title="${t("Изменить")}" aria-label="${t("Изменить ссылку")}">${ic("pencil", 14)}</button>
      <button class="icon-btn sm" data-act="lnk-del" title="${t("Удалить")}" aria-label="${t("Удалить ссылку")}">${ic("x", 14)}</button>
    </div>`;
  }

  const findGroup = (it, n) => it.groups.find(g => g.id === n.closest("[data-g]")?.dataset.g);
  const findLink = (it, n) => findGroup(it, n)?.links.find(l => l.id === n.closest("[data-l]")?.dataset.l);
  const findCk = (it, n) => it.checklist.find(c => c.id === n.closest("[data-c]")?.dataset.c);

  function grow(area) {
    if (!area) return;
    area.style.height = "auto";
    area.style.height = `${area.scrollHeight}px`;
  }

  function onInput(e) {
    const f = e.target.dataset.f;
    const it = item();
    if (!f || !it) return;
    const v = e.target.value;
    switch (f) {
      case "title": it.title = v; grow(e.target); break;
      case "firstStep": it.firstStep = v; grow(e.target); break;
      case "resumeFrom": it.resumeFrom = v; break;
      case "notes": it.notes = v; break;
      case "ck-text": findCk(it, e.target).text = v; break;
      case "grp-title": findGroup(it, e.target).title = v; break;
      case "lnk-title": findLink(it, e.target).title = v; break;
      case "lnk-url": findLink(it, e.target).url = v; break;
      default: return;
    }
    quiet();
  }

  function onChange(e) {
    const f = e.target.dataset.f;
    const it = item();
    if (!f || !it) return;
    const v = e.target.value;
    switch (f) {
      case "project": it.project = v; return struct();
      case "ck-done": findCk(it, e.target).done = e.target.checked; return struct();
      case "time": it.time = v; break;
      case "start":
        if (!v || v < "2000-01-01") return;
        it.start = v;
        if (it.kind !== "task" || it.end < v) it.end = v;
        break;
      case "end":
        if (!v || v < "2000-01-01") return;
        it.end = v < it.start ? it.start : v;
        break;
      default: return;
    }
    // The date field may still have focus, so only the dependent bits are refreshed.
    quiet();
    const endInput = el.querySelector('[data-f="end"]');
    if (endInput && endInput !== e.target) endInput.value = it.end;
    el.querySelector(".dr-count").innerHTML = countHtml(it);
  }

  function onClick(e) {
    const b = e.target.closest("[data-act]");
    const it = item();
    if (!b || !it) return;
    switch (b.dataset.act) {
      case "close": return ctx.select(null);
      case "kind":
        it.kind = b.dataset.v;
        if (it.kind !== "task" && it.start) it.end = it.start;
        return struct();
      case "status": it.status = b.dataset.v; return struct();
      case "set-date":
        it.start = it.end = D.today();
        ctx.reveal(it);
        return struct();
      case "undate": it.start = it.end = null; return struct();
      case "open-ws": return ctx.openWorkspace(it);
      case "capture": return ctx.captureWorkspace(it);
      case "ck-del":
        it.checklist = it.checklist.filter(c => c !== findCk(it, b));
        return struct();
      case "group-add": {
        const used = new Set(it.groups.map(g => g.color));
        const g = { id: uid(), title: "", color: tabs.GROUP_COLORS.find(c => !used.has(c)) || "grey", links: [] };
        it.groups.push(g);
        focusSel = `[data-g="${g.id}"] .grp-title`;
        return struct();
      }
      case "grp-color": {
        const g = findGroup(it, b);
        g.color = tabs.GROUP_COLORS[(tabs.GROUP_COLORS.indexOf(g.color) + 1) % tabs.GROUP_COLORS.length];
        return struct();
      }
      case "grp-del": {
        const g = findGroup(it, b);
        if (g.links.length && !confirm(t("Удалить группу «{name}» вместе со ссылками ({count})?", { name: g.title || t("без названия"), count: g.links.length }))) return;
        it.groups = it.groups.filter(x => x !== g);
        return struct();
      }
      case "grp-open": return ctx.openWorkspace(it, findGroup(it, b));
      case "lnk-edit":
        editingLink = b.closest("[data-l]").dataset.l;
        focusSel = `[data-l="${editingLink}"] [data-f="lnk-url"]`;
        return render();
      case "lnk-done": return finishLinkEdit(it, b);
      case "lnk-del": {
        const g = findGroup(it, b);
        g.links = g.links.filter(l => l !== findLink(it, b));
        return struct();
      }
      case "delete":
        if (confirm(t("Удалить «{title}»?", { title: it.title || t("без названия") }))) ctx.deleteItem(it.id);
        return;
    }
  }

  function finishLinkEdit(it, n) {
    const l = findLink(it, n);
    if (l) l.url = tabs.normUrl(l.url);
    editingLink = null;
    struct();
  }

  function onKey(e) {
    const it = item();
    if (!it || e.key !== "Enter" || e.isComposing) return;
    const target = e.target;
    if (target.classList.contains("dr-title")) {
      e.preventDefault();
      target.blur();
      return;
    }
    if (target.dataset.f === "lnk-url" || target.dataset.f === "lnk-title") {
      e.preventDefault();
      finishLinkEdit(it, target);
      return;
    }
    const kind = target.dataset.enter;
    const v = target.value.trim();
    if (!kind || !v) return;
    e.preventDefault();
    if (kind === "ck-add") {
      it.checklist.push({ id: uid(), text: v, done: false });
      focusSel = ".ck-add";
    } else if (kind === "lnk-add") {
      const g = findGroup(it, target);
      g.links.push({ id: uid(), title: "", url: tabs.normUrl(v) });
      focusSel = `[data-g="${g.id}"] .lnk-add`;
    }
    struct();
  }

  return {
    render,
    focusTitle() {
      const title = el.querySelector(".dr-title");
      title?.focus();
      title?.select();
    },
    isEditingLink: () => editingLink !== null,
  };
}
