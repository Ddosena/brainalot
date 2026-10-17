// Work mode board: notes and pictures, wheel zooms, dragging the background pans.
// One board per project plus a shared one.

import { ic, esc, linkify } from "./work-icons.js";
import { t } from "./i18n.js";
import { uid, imgGet, imgPut } from "./work-store.js";

const NOTE_COLORS = ["yellow", "pink", "green", "blue", "violet", "white"];
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const isTyping = n => n && (n.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(n.tagName));

export function createBoard(root, ctx) {
  root.innerHTML = `
    <div class="board-bar">
      <div class="board-tabs"></div>
      <div class="board-tools">
        <button data-t="note">${ic("note", 15)} ${t("Заметка")}</button>
        <button data-t="image">${ic("image", 15)} ${t("Картинка")}</button>
        <div class="swatches"></div>
        <span class="zoom-label" title="${t("Масштаб (колёсико мыши)")}"></span>
        <button class="icon-btn" data-t="fit" title="${t("Показать всё")}" aria-label="${t("Показать всё")}">${ic("fit")}</button>
      </div>
    </div>
    <div class="board" tabindex="-1">
      <div class="world"></div>
      <div class="board-empty">${t("Двойной клик — заметка · Ctrl+V — картинка или текст · можно перетащить файл")}</div>
    </div>
    <input type="file" accept="image/*" multiple hidden class="board-file">`;

  const boardEl = root.querySelector(".board");
  const world = root.querySelector(".world");
  const tabsEl = root.querySelector(".board-tabs");
  const swatchesEl = root.querySelector(".swatches");
  const zoomLabel = root.querySelector(".zoom-label");
  const fileInput = root.querySelector(".board-file");
  const urls = new Map();
  let selId = null;
  let editingId = null;
  let act = null;
  let lastColor = "yellow";

  const cur = () => ctx.state.boards.find(b => b.id === ctx.state.view.board) || ctx.state.boards[0];
  const find = id => cur().items.find(i => i.id === id);
  const save = () => ctx.update(null, { from: "board" });

  function render() {
    renderTabs();
    applyView();
    renderItems();
    renderSwatches();
  }

  function renderTabs() {
    tabsEl.innerHTML = ctx.state.boards.map(b => {
      const p = b.project && ctx.state.projects.find(x => x.id === b.project);
      const name = p ? p.name : t("Общая");
      return `<button class="btab ${b.id === cur().id ? "is-on" : ""} ${p ? `pc-${p.color}` : ""}" data-b="${b.id}">${p ? '<span class="dot"></span>' : ""}${esc(name)}</button>`;
    }).join("");
  }

  function renderSwatches() {
    const it = selId && find(selId);
    const active = it?.type === "note" ? it.color : lastColor;
    swatchesEl.innerHTML = NOTE_COLORS.map(c => `<button class="sw nc-${c} ${c === active ? "is-on" : ""}" data-c="${c}" title="${t("Цвет заметки")}" aria-label="${t("Цвет заметки")}"></button>`).join("");
  }

  function applyView() {
    const v = cur().view;
    world.style.transform = `translate(${v.x}px, ${v.y}px) scale(${v.z})`;
    const g = 24 * v.z;
    boardEl.style.backgroundSize = `${g}px ${g}px`;
    boardEl.style.backgroundPosition = `${v.x}px ${v.y}px`;
    zoomLabel.textContent = `${Math.round(v.z * 100)}%`;
  }

  function itemHtml(it) {
    const sel = it.id === selId ? " is-sel" : "";
    if (it.type === "image") {
      return `<div class="bi bi-img${sel}" data-id="${it.id}" style="left:${it.x}px;top:${it.y}px;width:${it.w}px;height:${it.h}px"><img data-img="${it.imageId}" draggable="false" alt=""><span class="bi-rs"></span></div>`;
    }
    return `<div class="bi bi-note nc-${it.color}${sel}" data-id="${it.id}" style="left:${it.x}px;top:${it.y}px;width:${it.w}px;min-height:${it.h}px"><div class="bi-text">${linkify(it.text)}</div><span class="bi-rs"></span></div>`;
  }

  function renderItems() {
    const b = cur();
    world.innerHTML = b.items.map(itemHtml).join("");
    world.querySelectorAll("img[data-img]").forEach(loadImg);
    root.classList.toggle("is-empty", !b.items.length);
  }

  async function loadImg(img) {
    const id = img.dataset.img;
    let u = urls.get(id);
    if (!u) {
      const blob = await imgGet(id);
      if (!blob) { img.alt = t("картинка не найдена"); return; }
      u = URL.createObjectURL(blob);
      urls.set(id, u);
    }
    img.src = u;
  }

  function select(id) {
    selId = id;
    world.querySelectorAll(".bi").forEach(n => n.classList.toggle("is-sel", n.dataset.id === id));
    renderSwatches();
  }

  function toWorld(cx, cy) {
    const r = boardEl.getBoundingClientRect();
    const v = cur().view;
    return { x: (cx - r.left - v.x) / v.z, y: (cy - r.top - v.y) / v.z };
  }

  function viewCenter() {
    const r = boardEl.getBoundingClientRect();
    return toWorld(r.left + r.width / 2, r.top + r.height / 2);
  }

  function addNote(x, y, text = "") {
    const it = { id: uid(), type: "note", x: Math.round(x), y: Math.round(y), w: 230, h: 130, color: lastColor, text };
    cur().items.push(it);
    save();
    renderItems();
    select(it.id);
    if (!text) startEdit(it.id);
  }

  async function downscale(file) {
    const bmp = await createImageBitmap(file);
    const w0 = bmp.width, h0 = bmp.height;
    const s = Math.min(1, 1600 / Math.max(w0, h0));
    if (s === 1 && file.size < 1_500_000) { bmp.close(); return { blob: file, w: w0, h: h0 }; }
    const w = Math.round(w0 * s), h = Math.round(h0 * s);
    const canvas = new OffscreenCanvas(w, h);
    canvas.getContext("2d").drawImage(bmp, 0, 0, w, h);
    bmp.close();
    return { blob: await canvas.convertToBlob({ type: "image/webp", quality: 0.9 }), w, h };
  }

  async function addImage(file, cx, cy) {
    try {
      const { blob, w, h } = await downscale(file);
      const imageId = uid();
      await imgPut(imageId, blob);
      const W = Math.min(360, w), H = Math.round(W * h / w);
      const it = { id: uid(), type: "image", imageId, x: Math.round(cx - W / 2), y: Math.round(cy - H / 2), w: W, h: H };
      cur().items.push(it);
      save();
      renderItems();
      select(it.id);
    } catch (error) {
      console.error(error);
      ctx.toast(t("Не получилось вставить картинку"));
    }
  }

  function startEdit(id) {
    const n = world.querySelector(`[data-id="${id}"]`);
    const it = find(id);
    if (!n || !it) return;
    editingId = id;
    const text = n.querySelector(".bi-text");
    text.textContent = it.text;
    text.contentEditable = "plaintext-only";
    n.classList.add("is-editing");
    text.focus();
    const range = document.createRange();
    range.selectNodeContents(text);
    range.collapse(false);
    getSelection().removeAllRanges();
    getSelection().addRange(range);
  }

  function finishEdit() {
    if (!editingId) return;
    const id = editingId;
    editingId = null;
    const n = world.querySelector(`[data-id="${id}"]`);
    const it = find(id);
    if (!n || !it) return;
    const text = n.querySelector(".bi-text");
    it.text = text.innerText.replace(/\n+$/, "");
    text.contentEditable = "false";
    n.classList.remove("is-editing");
    if (!it.text.trim()) {
      cur().items = cur().items.filter(x => x !== it);
      n.remove();
      select(null);
    } else {
      text.innerHTML = linkify(it.text);
    }
    save();
  }

  function removeSelected() {
    const b = cur();
    const idx = b.items.findIndex(i => i.id === selId);
    if (idx < 0) return;
    const [removed] = b.items.splice(idx, 1);
    select(null);
    save();
    renderItems();
    ctx.toast(removed.type === "image" ? t("Картинка удалена") : t("Заметка удалена"), {
      label: t("Вернуть"),
      fn: () => { b.items.splice(idx, 0, removed); save(); renderItems(); },
    });
  }

  function fit() {
    const b = cur();
    const r = boardEl.getBoundingClientRect();
    if (!b.items.length) { b.view = { x: 40, y: 28, z: 1 }; applyView(); save(); return; }
    const minX = Math.min(...b.items.map(i => i.x)), minY = Math.min(...b.items.map(i => i.y));
    const maxX = Math.max(...b.items.map(i => i.x + i.w)), maxY = Math.max(...b.items.map(i => i.y + i.h));
    const z = clamp(Math.min((r.width - 80) / (maxX - minX), (r.height - 80) / (maxY - minY)), 0.2, 1.25);
    b.view = { x: (r.width - (maxX - minX) * z) / 2 - minX * z, y: (r.height - (maxY - minY) * z) / 2 - minY * z, z };
    applyView();
    save();
  }

  // --- pointer ---

  boardEl.addEventListener("pointerdown", e => {
    if (e.button !== 0 && e.button !== 1) return;
    const n = e.target.closest(".bi");
    const onHandle = e.target.classList.contains("bi-rs");
    if (n && n.dataset.id === editingId && !onHandle) return;
    finishEdit();
    const v = cur().view;
    if (!n || e.button === 1) {
      select(null);
      act = { mode: "pan", x0: e.clientX, y0: e.clientY, vx: v.x, vy: v.y, moved: false };
      boardEl.classList.add("is-panning");
    } else {
      const b = cur();
      const it = find(n.dataset.id);
      b.items.splice(b.items.indexOf(it), 1);
      b.items.push(it);
      world.append(n);
      select(it.id);
      act = {
        mode: onHandle ? "resize" : "move", it, n,
        x0: e.clientX, y0: e.clientY, ox: it.x, oy: it.y, ow: it.w, oh: it.h,
        moved: false, link: e.target.closest("a"),
      };
    }
    boardEl.focus({ preventScroll: true });
    boardEl.setPointerCapture(e.pointerId);
    e.preventDefault();
  });

  boardEl.addEventListener("pointermove", e => {
    if (!act) return;
    const dx = e.clientX - act.x0, dy = e.clientY - act.y0;
    if (!act.moved && Math.hypot(dx, dy) < 3) return;
    act.moved = true;
    const v = cur().view;
    if (act.mode === "pan") {
      v.x = act.vx + dx;
      v.y = act.vy + dy;
      applyView();
    } else if (act.mode === "move") {
      act.it.x = Math.round(act.ox + dx / v.z);
      act.it.y = Math.round(act.oy + dy / v.z);
      act.n.style.left = `${act.it.x}px`;
      act.n.style.top = `${act.it.y}px`;
    } else {
      const w = Math.max(80, act.ow + dx / v.z);
      const h = act.it.type === "image" ? w * act.oh / act.ow : Math.max(60, act.oh + dy / v.z);
      act.it.w = Math.round(w);
      act.it.h = Math.round(h);
      act.n.style.width = `${act.it.w}px`;
      act.n.style[act.it.type === "image" ? "height" : "minHeight"] = `${act.it.h}px`;
    }
  });

  const endAct = () => {
    if (!act) return;
    const a = act;
    act = null;
    boardEl.classList.remove("is-panning");
    if (a.moved) save();
    else if (a.link) window.open(a.link.href, "_blank", "noopener");
  };
  boardEl.addEventListener("pointerup", endAct);
  boardEl.addEventListener("pointercancel", endAct);
  world.addEventListener("click", e => { if (e.target.closest("a") && !e.target.isContentEditable) e.preventDefault(); });

  boardEl.addEventListener("dblclick", e => {
    // With pointer capture the event targets the board itself, so look under the pointer.
    const n = document.elementFromPoint(e.clientX, e.clientY)?.closest(".bi");
    if (n) {
      if (find(n.dataset.id)?.type === "note") startEdit(n.dataset.id);
      return;
    }
    const p = toWorld(e.clientX, e.clientY);
    addNote(p.x - 115, p.y - 50);
  });

  world.addEventListener("focusout", e => {
    if (editingId && e.target.classList?.contains("bi-text")) finishEdit();
  });

  let viewTimer = null;
  boardEl.addEventListener("wheel", e => {
    e.preventDefault();
    const v = cur().view;
    const r = boardEl.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    const z = clamp(v.z * Math.exp(-e.deltaY * 0.0015), 0.2, 3);
    const wx = (mx - v.x) / v.z, wy = (my - v.y) / v.z;
    v.x = mx - wx * z;
    v.y = my - wy * z;
    v.z = z;
    applyView();
    clearTimeout(viewTimer);
    viewTimer = setTimeout(save, 400);
  }, { passive: false });

  // --- paste and file drops ---

  document.addEventListener("paste", async e => {
    if (isTyping(e.target) || editingId) return;
    const dt = e.clipboardData;
    if (!dt) return;
    const files = [...dt.files].filter(f => f.type.startsWith("image/"));
    const c = viewCenter();
    if (files.length) {
      e.preventDefault();
      for (const [i, f] of files.entries()) await addImage(f, c.x + i * 30, c.y + i * 30);
      return;
    }
    const text = dt.getData("text/plain")?.trim();
    if (text) {
      e.preventDefault();
      addNote(c.x - 115, c.y - 65, text);
    }
  });

  boardEl.addEventListener("dragover", e => { e.preventDefault(); boardEl.classList.add("is-drop"); });
  boardEl.addEventListener("dragleave", () => boardEl.classList.remove("is-drop"));
  boardEl.addEventListener("drop", async e => {
    e.preventDefault();
    boardEl.classList.remove("is-drop");
    const p = toWorld(e.clientX, e.clientY);
    const files = [...e.dataTransfer.files].filter(f => f.type.startsWith("image/"));
    if (files.length) {
      for (const [i, f] of files.entries()) await addImage(f, p.x + i * 30, p.y + i * 30);
      return;
    }
    const text = (e.dataTransfer.getData("text/uri-list") || e.dataTransfer.getData("text/plain")).trim();
    if (text) addNote(p.x - 115, p.y - 50, text);
  });

  document.addEventListener("keydown", e => {
    if (editingId) {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finishEdit(); }
      return;
    }
    if (isTyping(e.target) || !selId) return;
    if (e.key === "Delete" || e.key === "Backspace") {
      e.preventDefault();
      removeSelected();
    }
  }, true);

  // --- toolbar ---

  root.querySelector(".board-bar").addEventListener("click", e => {
    const tab = e.target.closest("[data-b]");
    if (tab) {
      finishEdit();
      ctx.state.view.board = tab.dataset.b;
      select(null);
      save();
      render();
      return;
    }
    const sw = e.target.closest("[data-c]");
    if (sw) {
      lastColor = sw.dataset.c;
      const it = selId && find(selId);
      if (it?.type === "note") {
        it.color = lastColor;
        world.querySelector(`[data-id="${it.id}"]`).className = `bi bi-note nc-${it.color} is-sel`;
        save();
      }
      renderSwatches();
      return;
    }
    const tool = e.target.closest("[data-t]")?.dataset.t;
    if (tool === "note") { const c = viewCenter(); addNote(c.x - 115, c.y - 65); }
    else if (tool === "image") fileInput.click();
    else if (tool === "fit") fit();
  });

  fileInput.addEventListener("change", async () => {
    const c = viewCenter();
    for (const [i, f] of [...fileInput.files].entries()) await addImage(f, c.x + i * 30, c.y + i * 30);
    fileInput.value = "";
  });

  return {
    render,
    isEditing: () => !!editingId,
  };
}
