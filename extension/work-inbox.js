// Work mode "From chats" tray. Agents (Codex or Claude, asked in their chat) read the collected
// Telegram and VK messages through the CLI and leave proposals in the local service; here the
// user edits and accepts or rejects them. Nothing reaches the calendar without that click.
// The page also sends the service a trimmed snapshot of its data, so agents can see what exists.
import { t, translateServerMessage } from "./i18n.js";
import * as D from "./work-dates.js";
import { ic, esc } from "./work-icons.js";
import { uid } from "./work-store.js";

const POLL_MS = 20_000;
const SNAPSHOT_DELAY_MS = 2000;
const SNAPSHOT_LIMIT = 230_000;
export const CHAT_ORIGINS = { vk: ["https://vk.com/*"], tgweb: ["https://web.telegram.org/*"] };

export function send(type, extra = {}) {
  return chrome.runtime.sendMessage({ type, ...extra }).then(reply => {
    if (!reply?.ok) throw Object.assign(new Error(translateServerMessage(reply?.error) || t("Brainalot не ответил")), { status: reply?.status || 0 });
    return reply.data;
  });
}

/** The page data an agent needs: projects and items without the board and its pictures. */
export function snapshotOf(state, noteLimit = 1000) {
  const items = state.items.map(it => ({
    id: it.id, project: it.project, kind: it.kind, title: it.title, start: it.start, end: it.end, time: it.time,
    status: it.status, firstStep: it.firstStep, notes: (it.notes || "").slice(0, noteLimit),
    checklist: (it.checklist || []).map(c => ({ text: c.text, done: c.done })),
    groups: (it.groups || []).map(g => ({ title: g.title, links: (g.links || []).map(l => ({ title: l.title, url: l.url })) })),
  }));
  const projects = state.projects.map(p => ({ id: p.id, name: p.name, about: p.about || "", color: p.color }));
  const snapshot = { projects, items };
  if (JSON.stringify(snapshot).length > SNAPSHOT_LIMIT && noteLimit > 0) return snapshotOf(state, 0);
  return snapshot;
}

/** One line per source: who wrote what, where and when, with a link to the message when there is one. */
export function sourceNote(proposal) {
  return (proposal.sources || []).map(s => {
    const when = s.date ? `${D.dayMonth(s.date.slice(0, 10))} ${s.date.slice(11, 16)}` : "";
    return t("Из чата «{chat}», {author}, {when}: «{quote}»", { chat: s.chat_title || s.chat, author: s.author, when, quote: s.quote })
      + (s.link ? ` ${s.link}` : "");
  }).join("\n");
}

/**
 * What accepting a proposal changes, without touching the state: a new project (when the agent
 * named one that does not exist yet), a new item, or links and notes added to an existing item.
 * ``choice`` holds the values the user edited on the card.
 */
export function acceptance(proposal, choice, state, makeId = uid) {
  const links = (proposal.links || []).map(l => ({ id: makeId(), title: l.title || "", url: l.url }));
  const files = (proposal.files || []).map(f => `${f.name}${f.path ? ` — ${f.path}` : ""}`);
  const extra = [
    proposal.notes,
    files.length ? `${t("Файлы из чата:")}\n${files.join("\n")}` : "",
    sourceNote(proposal),
  ].filter(Boolean).join("\n\n");
  const chatGroup = () => ({ id: makeId(), title: t("Из чатов"), color: "purple", links: [] });

  if (proposal.kind === "attach") {
    const target = state.items.find(i => i.id === proposal.target_item_id);
    if (!target) throw new Error(t("Задача для вложений уже удалена"));
    return { project: null, item: null, attach: { itemId: target.id, links, notes: extra }, itemId: target.id };
  }

  let project = null;
  let projectId = choice.project;
  if (projectId === "__new") {
    const used = new Set(state.projects.map(p => p.color));
    const colors = ["teal", "violet", "coral", "amber", "blue", "pink"];
    project = { id: makeId(), name: proposal.project_name || t("Новый проект"), about: "", color: colors.find(c => !used.has(c)) || "teal" };
    projectId = project.id;
  }
  if (!projectId || (!project && !state.projects.some(p => p.id === projectId))) throw new Error(t("Выберите проект"));
  const kind = proposal.kind;
  const start = choice.start || null;
  const end = kind === "task" ? (choice.end || start) : start;
  const item = {
    id: makeId(), project: projectId, kind, title: (choice.title || proposal.title || "").trim() || t("Без названия"),
    start, end: start ? (end && end >= start ? end : start) : null, time: kind === "task" ? "" : (choice.time || ""),
    status: "todo", firstStep: proposal.first_step || "", resumeFrom: "", notes: extra,
    checklist: (proposal.checklist || []).map(text => ({ id: makeId(), text, done: false })),
    groups: links.length ? [{ ...chatGroup(), links }] : [], createdAt: Date.now(),
  };
  return { project, item, attach: null, itemId: item.id };
}

/** Apply an ``acceptance`` to the state in place (inside a store update). */
export function applyAcceptance(state, result, makeId = uid) {
  if (result.project) {
    state.projects.push(result.project);
    state.boards.push({ id: `b-${result.project.id}`, project: result.project.id, view: { x: 40, y: 28, z: 1 }, items: [] });
  }
  if (result.item) state.items.push(result.item);
  if (result.attach) {
    const target = state.items.find(i => i.id === result.attach.itemId);
    if (!target) return;
    if (result.attach.links.length) {
      let group = target.groups.find(g => g.title === t("Из чатов"));
      if (!group) {
        group = { id: makeId(), title: t("Из чатов"), color: "purple", links: [] };
        target.groups.push(group);
      }
      const known = new Set(group.links.map(l => l.url));
      group.links.push(...result.attach.links.filter(l => !known.has(l.url)));
    }
    if (result.attach.notes) target.notes = [target.notes, result.attach.notes].filter(Boolean).join("\n\n");
  }
}

const KIND_LABELS = () => ({ task: t("Задача"), deadline: t("Дедлайн"), event: t("Событие"), attach: t("Вложения") });

export function createInbox(el, toggle, ctx) {
  let proposals = [];
  let status = null;
  let offline = false;
  let open = false;
  // Choosing a chat to connect: {source, candidates, markupSent} while the chooser is open.
  let chooser = null;
  let snapshotTimer = null;
  let lastCount = 0;
  const drafts = new Map();

  toggle.addEventListener("click", () => { open = !open; render(); if (open) refresh(); });
  el.addEventListener("click", onClick);
  el.addEventListener("input", onInput);
  el.addEventListener("change", onInput);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  setInterval(() => { if (!document.hidden) refresh(); }, POLL_MS);

  function draft(p) {
    if (!drafts.has(p.id)) {
      const projects = ctx.state.projects;
      const known = p.project_id && projects.some(x => x.id === p.project_id);
      const named = p.project_name && projects.find(x => x.name.trim().toLowerCase() === p.project_name.trim().toLowerCase());
      drafts.set(p.id, {
        project: known ? p.project_id : named ? named.id : (p.project_name || !projects.length) ? "__new" : projects[0].id,
        title: p.title || "", start: p.start || "", end: p.end || "", time: p.time || "",
      });
    }
    return drafts.get(p.id);
  }

  // Accepted here but not yet marked in the service (it was offline): never show them twice.
  const UNRESOLVED_KEY = "work:unresolved-proposals";
  function unresolved() {
    try { return JSON.parse(localStorage.getItem(UNRESOLVED_KEY) || "{}"); } catch { return {}; }
  }
  function saveUnresolved(map) {
    try { localStorage.setItem(UNRESOLVED_KEY, JSON.stringify(map)); } catch { /* the service will show it again */ }
  }
  async function retryUnresolved() {
    const map = unresolved();
    for (const [id, itemId] of Object.entries(map)) {
      try {
        await send("work-proposal-resolve", { id, action: "accept", itemId });
        delete map[id];
      } catch (error) {
        if (error.status === 404 || error.status === 409) delete map[id];
      }
    }
    saveUnresolved(map);
    return map;
  }

  async function refresh() {
    try {
      const waiting = await retryUnresolved();
      const [list, chats] = await Promise.all([send("work-proposals"), send("chats-status")]);
      proposals = (list.proposals || []).filter(p => !(p.id in waiting));
      status = chats;
      offline = false;
    } catch {
      offline = true;
    }
    for (const id of drafts.keys()) if (!proposals.some(p => p.id === id)) drafts.delete(id);
    if (proposals.length > lastCount && lastCount >= 0 && !open && proposals.length) {
      ctx.toast(t("Новые предложения из чатов: {count}", { count: proposals.length }));
    }
    lastCount = proposals.length;
    render();
  }

  function render() {
    const pending = proposals.length;
    const unsorted = status?.unprocessed || 0;
    toggle.innerHTML = `${ic("inbox", 15)} ${t("Из чатов")}${pending ? `<span class="inbox-badge">${pending}</span>` : ""}${unsorted && !pending ? '<span class="inbox-dot"></span>' : ""}`;
    toggle.classList.toggle("is-on", open);
    toggle.title = offline ? t("Brainalot не подключён") : t("Неразобранных сообщений: {count}", { count: unsorted });
    el.hidden = !open;
    if (!open) return;
    if (offline) {
      el.innerHTML = `<div class="inbox-status"><span class="warn">${t("Brainalot не подключён: запустите программу, и предложения из чатов появятся здесь.")}</span></div>`;
      return;
    }
    el.innerHTML = `${statusHtml()}${chooserHtml()}
      <div class="inbox-head"><h3>${t("Предложения агента")}</h3>${pending > 1 ? `<button data-act="accept-all">${ic("check", 14)} ${t("Принять все")}</button>` : ""}</div>
      ${pending ? `<div class="inbox-cards">${proposals.map(cardHtml).join("")}</div>`
        : `<p class="inbox-empty">${unsorted
          ? t("Попросите Codex или Claude в их чате: «разбери рабочие чаты» — предложения появятся здесь.")
          : t("Предложений пока нет.")}</p>`}`;
  }

  function statusHtml() {
    const chats = (status?.chats || []).filter(c => c.enabled);
    const tg = status?.telegram || {};
    const chips = chats.map(c => `<span class="inbox-chat ${c.gap ? "has-gap" : ""}" title="${esc(c.key)}">
        <b>${c.source === "vk" ? t("ВК") : "Telegram"}</b> ${esc(c.title)}
        <span class="muted">${c.last_message ? t("последнее {time}", { time: esc(c.last_message.slice(11, 16)) }) : ""}</span>
        ${c.gap ? `<span class="warn">${t("возможен пропуск — откройте чат и прокрутите вверх")}</span>` : ""}
        <button class="icon-btn sm" data-act="chat-remove" data-key="${esc(c.key)}" title="${t("Перестать собирать")}" aria-label="${t("Перестать собирать")}">${ic("x", 12)}</button>
      </span>`).join("");
    // The account route (Telethon, mmm chats telegram-login) stays for the CLI; the tray offers the tab route.
    const accountTelegram = chats.some(c => c.source === "telegram") && tg.logged_in;
    return `<div class="inbox-status">
      <div class="inbox-chats">${chips || `<span class="muted">${t("Чаты пока не подключены.")}</span>`}</div>
      <div class="inbox-actions">
        <button data-act="chat-setup" data-source="tgweb">${ic("plus", 14)} ${t("Подключить чат Telegram")}</button>
        <button data-act="chat-setup" data-source="vk">${ic("plus", 14)} ${t("Подключить беседу ВК")}</button>
        ${accountTelegram ? `<button data-act="tg-sync">${ic("download", 14)} ${t("Проверить Telegram")}</button>` : ""}
      </div>
      <div class="muted">${t("Сообщения собираются, пока чат открыт во вкладке Chrome: её удобно закрепить.")}</div>
      ${status?.unprocessed ? `<div class="inbox-unsorted">${t("Неразобранных сообщений: {count}. Скажите Codex или Claude: «разбери рабочие чаты».", { count: status.unprocessed })}</div>` : ""}
    </div>`;
  }

  function chooserHtml() {
    if (!chooser) return "";
    const vk = chooser.source === "vk";
    const note = chooser.markupSent
      ? `<div class="inbox-vk-note">${vk
        ? t("Эту страницу ВК Brainalot пока не понимает: её устройство (без текста сообщений) отправлено для настройки. Скажите Codex или Claude: «настрой чтение ВК».")
        : t("Эту страницу Telegram Brainalot пока не понимает: её устройство (без текста сообщений) отправлено для настройки. Скажите Codex или Claude: «настрой чтение Telegram».")}</div>`
      : "";
    const close = `<button class="icon-btn sm" data-act="chooser-close" aria-label="${t("Закрыть")}">${ic("x", 12)}</button>`;
    if (!chooser.candidates.length) {
      const hint = vk
        ? t("Откройте нужную беседу ВК в соседней вкладке и нажмите «Подключить беседу ВК» ещё раз.")
        : t("Откройте нужный чат на web.telegram.org в соседней вкладке и нажмите «Подключить чат Telegram» ещё раз.");
      return `<div class="inbox-vk"><span>${hint}</span>${close}${note}</div>`;
    }
    return `<div class="inbox-vk"><span>${vk ? t("Какую беседу собирать?") : t("Какой чат собирать?")}</span>
      ${chooser.candidates.map(c => `<button data-act="chat-connect" data-peer="${esc(c.peer_id)}" data-title="${esc(c.title)}">${esc(c.title || c.peer_id)}</button>`).join("")}
      ${close}${note}</div>`;
  }

  function projectOptions(p, value) {
    const options = ctx.state.projects.map(x => `<option value="${esc(x.id)}" ${x.id === value ? "selected" : ""}>${esc(x.name)}</option>`);
    if (p.project_name && !ctx.state.projects.some(x => x.name === p.project_name)) {
      options.push(`<option value="__new" ${value === "__new" ? "selected" : ""}>${esc(t("Новый проект «{name}»", { name: p.project_name }))}</option>`);
    }
    if (!options.length) options.push(`<option value="__new" selected>${esc(t("Новый проект"))}</option>`);
    return options.join("");
  }

  function cardHtml(p) {
    const d = draft(p);
    const kind = KIND_LABELS()[p.kind] || p.kind;
    const target = p.kind === "attach" ? ctx.state.items.find(i => i.id === p.target_item_id) : null;
    const dates = p.kind === "attach" ? "" : p.kind === "task"
      ? `<label>${t("с")} <input type="date" data-f="start" value="${esc(d.start)}"></label><label>${t("по")} <input type="date" data-f="end" value="${esc(d.end)}"></label>`
      : `<input type="date" data-f="start" value="${esc(d.start)}">${p.kind === "event" ? `<input type="time" data-f="time" value="${esc(d.time)}">` : ""}`;
    const sources = (p.sources || []).map(s => `<blockquote class="inbox-quote">
        <div class="inbox-quote-head">${esc(s.author)} · ${esc(s.chat_title || s.chat)} · ${esc(s.date ? `${D.dayMonth(s.date.slice(0, 10))} ${s.date.slice(11, 16)}` : "")}
        ${s.link ? `<a href="${esc(s.link)}" target="_blank" rel="noreferrer">${ic("external", 12)} ${t("сообщение")}</a>` : ""}</div>
        <div>${esc(s.quote)}</div></blockquote>`).join("");
    const links = (p.links || []).map(l => `<li><a href="${esc(l.url)}" target="_blank" rel="noreferrer">${esc(l.title || l.url)}</a></li>`).join("");
    const files = (p.files || []).map(f => `<li title="${esc(f.path || "")}">${ic("note", 12)} ${esc(f.name)}</li>`).join("");
    return `<article class="inbox-card kind-${esc(p.kind)}" data-p="${esc(p.id)}">
      <div class="inbox-card-head"><span class="inbox-kind">${kind}</span><span class="muted">${esc(p.agent)}</span></div>
      ${p.kind === "attach"
        ? `<div class="inbox-target">${t("Добавить к задаче:")} <b>${esc(target?.title || t("задача не найдена"))}</b></div>`
        : `<input class="inbox-title" data-f="title" value="${esc(d.title)}" placeholder="${t("Название")}">
           <div class="inbox-fields"><select data-f="project" aria-label="${t("Проект")}">${projectOptions(p, d.project)}</select>${dates}</div>`}
      ${p.reason ? `<div class="inbox-reason">${esc(p.reason)}</div>` : ""}
      ${sources}
      ${links || files ? `<ul class="inbox-links">${links}${files}</ul>` : ""}
      <div class="inbox-buttons">
        <button class="primary sm" data-act="accept">${t("Принять")}</button>
        ${p.kind === "attach" ? "" : `<button data-act="edit">${t("Изменить")}</button>`}
        <button class="link-danger" data-act="reject">${t("Не надо")}</button>
      </div>
    </article>`;
  }

  function onInput(e) {
    const field = e.target.dataset.f;
    const card = e.target.closest("[data-p]");
    if (!field || !card) return;
    const d = drafts.get(card.dataset.p);
    if (d) d[field] = e.target.value;
  }

  async function accept(p, { openAfter = false, quiet = false } = {}) {
    let result;
    try {
      result = acceptance(p, draft(p), ctx.state);
    } catch (error) {
      if (!quiet) ctx.toast(error.message);
      return false;
    }
    if (result.item) ctx.reveal(result.item);
    ctx.update(st => applyAcceptance(st, result), { from: "project" });
    try {
      await send("work-proposal-resolve", { id: p.id, action: "accept", itemId: result.itemId });
    } catch (error) {
      // The item is already in the calendar; the tray marks the proposal on a later refresh.
      saveUnresolved({ ...unresolved(), [p.id]: result.itemId });
      if (!quiet) ctx.toast(t("Задача добавлена; Brainalot отметит предложение, когда снова будет на связи."));
    }
    proposals = proposals.filter(x => x.id !== p.id);
    drafts.delete(p.id);
    if (openAfter) ctx.select(result.itemId);
    return true;
  }

  async function onClick(e) {
    const b = e.target.closest("[data-act]");
    if (!b) return;
    const card = b.closest("[data-p]");
    const p = card && proposals.find(x => x.id === card.dataset.p);
    switch (b.dataset.act) {
      case "accept":
        if (p && await accept(p)) { ctx.toast(t("Принято")); render(); }
        return;
      case "edit":
        if (p && await accept(p, { openAfter: true })) render();
        return;
      case "reject":
        if (!p) return;
        try {
          await send("work-proposal-resolve", { id: p.id, action: "reject" });
          proposals = proposals.filter(x => x.id !== p.id);
          render();
        } catch (error) { ctx.toast(error.message); }
        return;
      case "accept-all": {
        let done = 0;
        for (const item of [...proposals]) if (await accept(item, { quiet: true })) done++;
        ctx.toast(proposals.length
          ? t("Принято: {count}. Остальным нужен проект — выберите его в карточке.", { count: done })
          : t("Принято: {count}", { count: done }));
        render();
        return;
      }
      case "tg-sync":
        b.disabled = true;
        try {
          const result = await send("chats-sync");
          ctx.toast(t("Telegram: новых сообщений {count}", { count: result.added || 0 }));
        } catch (error) { ctx.toast(error.message); }
        return refresh();
      case "chat-remove":
        if (!confirm(t("Перестать собирать этот чат? Собранные сообщения останутся."))) return;
        try { await send("chats-remove", { key: b.dataset.key }); } catch (error) { ctx.toast(error.message); }
        return refresh();
      case "chat-setup":
        return chatSetup(b.dataset.source);
      case "chat-connect":
        try {
          await send("chat-connect", { source: chooser.source, peerId: b.dataset.peer, title: b.dataset.title });
          ctx.toast(t("Чат «{title}» подключён: новые сообщения собираются, пока он открыт в Chrome.", { title: b.dataset.title }));
          chooser = null;
        } catch (error) { ctx.toast(error.message); }
        return refresh();
      case "chooser-close":
        chooser = null;
        return render();
    }
  }

  async function chatSetup(source) {
    // The permission prompt must come straight from the click.
    let granted = chrome.desktop === true;
    try { if (!chrome.desktop) granted = await chrome.permissions.request({ origins: CHAT_ORIGINS[source] }); } catch { granted = false; }
    if (!granted) {
      ctx.toast(source === "vk" ? t("Без доступа к vk.com беседу не прочитать") : t("Без доступа к web.telegram.org чат не прочитать"));
      return;
    }
    try {
      const result = await send("chat-setup", { source });
      chooser = { source, candidates: result.candidates || [], markupSent: result.markupSent === true };
    } catch (error) {
      ctx.toast(error.message);
      chooser = null;
    }
    render();
  }

  function snapshotSoon() {
    clearTimeout(snapshotTimer);
    snapshotTimer = setTimeout(() => {
      send("work-snapshot", { state: snapshotOf(ctx.state) }).catch(() => {});
    }, SNAPSHOT_DELAY_MS);
  }

  render();
  return { refresh, snapshotSoon, render };
}
