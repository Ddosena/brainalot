import { ApiError, apiBase, requestApi } from "./api.js";
import { t } from "./i18n.js";

export const MAX_FILE_BYTES = 10 * 1024 * 1024;
export const MAX_NOTE_BYTES = 30 * 1024 * 1024;
export const MAX_FILES = 10;
const DB_NAME = "mmm-attachments";
const STORE = "files";
const PREVIEW_CACHE_LIMIT = 32 * 1024 * 1024;
const previewCache = new Map();
const previewPending = new Map();
const previewUrls = new Map();
let previewCacheBytes = 0;

function openDb() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(STORE, { keyPath: "localId" });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error(t("Не удалось открыть хранилище вложений.")));
  });
}

async function dbAction(mode, action) {
  const db = await openDb();
  try {
    return await new Promise((resolve, reject) => {
      const transaction = db.transaction(STORE, mode);
      const request = action(transaction.objectStore(STORE));
      let value;
      request.onsuccess = () => { value = request.result; };
      request.onerror = () => reject(request.error);
      transaction.oncomplete = () => resolve(value);
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error);
    });
  } finally { db.close(); }
}

export function localAttachments(items) {
  return Array.isArray(items) ? items.filter(item => typeof item?.localId === "string" && item.localId) : [];
}

export function safeFileName(name) {
  if (typeof name !== "string" || !name || name.length > 200 || name === "." || name === ".." ||
      /[\\/\x00-\x1f]/.test(name)) throw new Error(t("Некорректное имя файла. Переименуйте его перед добавлением."));
  return name;
}

export function checkAttachmentLimit(existing, incoming) {
  const files = Array.from(incoming || []);
  for (const file of files) safeFileName(file.name || t("Изображение из буфера.png"));
  if (existing.length + files.length > MAX_FILES) throw new Error(t("Не больше {MAX_FILES} файлов на запись.", {MAX_FILES}));
  const size = existing.reduce((sum, item) => sum + Number(item.size || 0), 0);
  if (files.some(file => !file.size || file.size > MAX_FILE_BYTES)) throw new Error(t("Размер каждого файла должен быть от 1 байта до 10 МиБ."));
  if (size + files.reduce((sum, file) => sum + file.size, 0) > MAX_NOTE_BYTES) throw new Error(t("Общий размер вложений — не больше 30 МиБ."));
  return files;
}

export async function storeAttachment(file) {
  checkAttachmentLimit([], [file]);
  const localId = crypto.randomUUID();
  const name = safeFileName(file.name || t("Изображение из буфера.png"));
  const item = { localId, name, type: file.type || "application/octet-stream", size: file.size };
  await dbAction("readwrite", store => store.put({ ...item, blob: file }));
  return item;
}

export function getLocalAttachment(localId) {
  return dbAction("readonly", store => store.get(localId));
}

function saveUploaded(item) {
  return dbAction("readwrite", store => store.put(item));
}

export async function base64Blob(blob) {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  const parts = [];
  // Each chunk is divisible by three, so only the final part receives padding.
  for (let offset = 0; offset < bytes.length; offset += 24576) {
    parts.push(btoa(String.fromCharCode(...bytes.subarray(offset, offset + 24576))));
  }
  return parts.join("");
}

export async function resolveAttachments(items, upload, { load = getLocalAttachment, save = saveUploaded } = {}) {
  const result = [];
  for (const item of items || []) {
    if (!item.localId) { result.push(item); continue; }
    const stored = await load(item.localId);
    if (!stored?.blob) throw new Error(t("Локальный файл «{name}» потерян. Запись осталась в очереди.", {name: item.name}));
    if (stored.server) { result.push(stored.server); continue; }
    const server = await upload({ name: stored.name, content_base64: await base64Blob(stored.blob) });
    if (!server?.id) throw new Error(t("Сервис не подтвердил загрузку вложения."));
    await save({ ...stored, server });
    result.push(server);
  }
  return result;
}

export async function uploadAttachments(items, token) {
  return resolveAttachments(items, body => requestApi("/api/attachments", { token, method: "POST", body, timeoutMs: 60000 }));
}

export function stripManagedAttachments(body = "") {
  return String(body).replace(/\n?<!-- mmm:attachments -->[\s\S]*?<!-- \/mmm:attachments -->\n?/g, "\n").trimEnd();
}

export function safeRaster(mime) {
  return ["image/png", "image/jpeg", "image/gif", "image/webp"].includes(mime);
}

export async function isRasterBlob(blob) {
  const bytes = new Uint8Array(await blob.slice(0, 16).arrayBuffer());
  const starts = (...values) => values.every((value, index) => bytes[index] === value);
  const ascii = (offset, value) => [...value].every((char, index) => bytes[offset + index] === char.charCodeAt(0));
  return starts(137,80,78,71,13,10,26,10) || starts(255,216,255) ||
    ascii(0,"GIF87a") || ascii(0,"GIF89a") || (ascii(0,"RIFF") && ascii(8,"WEBP"));
}

/**
 * Keep a bounded cache of verified bytes. Each cached blob may have one shared
 * object URL (see `previewUrl`); it is revoked when the bytes are evicted.
 */
export async function loadRasterPreview(item, {load} = {}) {
  if (!safeRaster(item?.mime || item?.type)) return null;
  const key = rasterKey(item);
  if (!key) return null;
  if (previewCache.has(key)) {
    const blob = previewCache.get(key);
    previewCache.delete(key); previewCache.set(key, blob);
    return blob;
  }
  if (previewPending.has(key)) return previewPending.get(key);
  const pending = (async () => {
    const blob = load ? await load(item) : item.localId
      ? (await getLocalAttachment(item.localId))?.blob : await fetchAttachment(item.id);
    if (!blob || !(await isRasterBlob(blob))) return null;
    if (blob.size <= PREVIEW_CACHE_LIMIT) {
      while (previewCacheBytes + blob.size > PREVIEW_CACHE_LIMIT && previewCache.size) {
        const oldest = previewCache.keys().next().value;
        previewCacheBytes -= previewCache.get(oldest).size;
        previewCache.delete(oldest);
        if (previewUrls.has(oldest)) { URL.revokeObjectURL(previewUrls.get(oldest)); previewUrls.delete(oldest); }
      }
      previewCache.set(key, blob);
      previewCacheBytes += blob.size;
    }
    return blob;
  })().finally(() => previewPending.delete(key));
  previewPending.set(key, pending);
  return pending;
}

/**
 * Object URL for a verified preview. Cached bytes share one URL, so a re-render,
 * a stack clone or the full-size viewer reuse already decoded pixels.
 * `shared:false` means the caller owns the URL and must revoke it.
 */
export async function previewUrl(item, options = {}) {
  const blob = await loadRasterPreview(item, options);
  if (!blob) return null;
  const key = rasterKey(item);
  const typed = () => URL.createObjectURL(new Blob([blob], {type:item.mime || item.type}));
  if (previewCache.get(key) !== blob) return {url:typed(), shared:false};
  if (!previewUrls.has(key)) previewUrls.set(key, typed());
  return {url:previewUrls.get(key), shared:true};
}

export function rasterKey(item) {
  return item?.id ? `server:${item.id}` : item?.localId ? `local:${item.localId}` : null;
}

const colorCache = new Map();

/**
 * Paint a tiny copy of a decoded image (a blurred backdrop once scaled up) and
 * return its saturation-weighted average colour for a matching card glow.
 */
export function paintImageBackdrop(image, canvas, key = null) {
  try {
    const context = canvas.getContext("2d", {willReadFrequently:true});
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    if (key && colorCache.has(key)) return colorCache.get(key);
    const data = context.getImageData(0, 0, canvas.width, canvas.height).data;
    let red = 0, green = 0, blue = 0, total = 0;
    for (let index = 0; index < data.length; index += 4) {
      const [r, g, b, alpha] = data.slice(index, index + 4);
      // Colourful pixels outweigh grey ones, so a mostly white screenshot with a
      // blue header still glows blue instead of a dull grey.
      const weight = (alpha / 255) * (1 + (Math.max(r, g, b) - Math.min(r, g, b)) / 40);
      red += r * weight; green += g * weight; blue += b * weight; total += weight;
    }
    const color = total ? [red, green, blue].map(value => Math.round(value / total)) : null;
    if (key && color) {
      if (colorCache.size > 200) colorCache.delete(colorCache.keys().next().value);
      colorCache.set(key, color);
    }
    return color;
  } catch { return null; }
}

export function cachedImageColor(item) {
  return colorCache.get(rasterKey(item)) || null;
}

/**
 * `onOpen(item, image)` turns each preview into a button for a full view;
 * `onReady(item, image, figure)` runs after a successful decode.
 */
export function renderRasterGallery(container, items, {draggable = true, onOpen = null, onReady = null, limit = Infinity} = {}) {
  const raster = (items || []).filter(item => safeRaster(item.mime || item.type));
  if (!raster.length) return null;
  const gallery = document.createElement("div");
  gallery.className = "note-image-gallery";
  for (const item of raster.slice(0, limit)) {
    const figure = document.createElement("figure");
    figure.className = "note-image-preview";
    const image = document.createElement("img");
    image.draggable = draggable;
    image.alt = item.name || t("Изображение");
    image.loading = "lazy";
    image.decoding = "async";
    let frame = image;
    if (onOpen) {
      frame = document.createElement("button");
      frame.type = "button";
      frame.className = "note-image-open";
      frame.title = t("Открыть изображение");
      frame.setAttribute("aria-label", t("Открыть изображение: {name}", {name: item.name || t("Изображение")}));
      frame.addEventListener("click", event => {
        if (!image.getAttribute("src") || !image.complete) return;
        onOpen(item, image, event);
      });
      frame.append(image);
    }
    const caption = document.createElement("figcaption");
    caption.textContent = item.name || t("Изображение");
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "secondary";
    retry.textContent = t("Повторить загрузку");
    retry.hidden = true;
    const load = async () => {
      retry.hidden = true;
      try {
        const source = await previewUrl(item);
        if (!source) throw new Error(t("Файл не является изображением поддерживаемого формата."));
        const release = () => { if (!source.shared) URL.revokeObjectURL(source.url); };
        if (!figure.isConnected) { release(); return; }
        image.onload = () => { release(); figure.dataset.loaded = "true"; onReady?.(item, image, figure); };
        image.onerror = () => { release(); image.removeAttribute("src"); retry.hidden = false; };
        image.src = source.url;
      } catch { if (figure.isConnected) retry.hidden = false; }
    };
    retry.addEventListener("click", load);
    figure.append(frame, caption, retry);
    gallery.append(figure);
    if (typeof IntersectionObserver === "undefined") void load();
    else {
      const observer = new IntersectionObserver(entries => {
        if (entries.some(entry => entry.isIntersecting)) { observer.disconnect(); void load(); }
      });
      observer.observe(figure);
    }
  }
  container.append(gallery);
  return gallery;
}

export function formatBytes(size) {
  if (size >= 1024 * 1024) return t("{size} МиБ", {size: (size / (1024 * 1024)).toFixed(1)});
  return t("{max} КиБ", {max: Math.max(1, Math.ceil(size / 1024))});
}

export async function fetchAttachment(id) {
  if (typeof id !== "string" || !/^[a-f0-9]{64}\.[a-z0-9]{1,12}$/.test(id)) throw new Error(t("Неверный ID вложения."));
  const saved = await chrome.storage.local.get("connection");
  const token = saved.connection?.token;
  if (!token) throw new ApiError(t("Укажите токен подключения в настройках."), 401);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 60000);
  try {
    const response = await fetch(`${apiBase()}/api/attachments/${encodeURIComponent(id)}`, {
      headers: { Authorization: `Bearer ${token}` }, credentials: "omit", redirect: "error", cache: "no-store", signal: controller.signal,
    });
    if (!response.ok) throw new ApiError(t("Не удалось получить вложение ({status}).", {status: response.status}), response.status);
    return response.blob();
  } finally { clearTimeout(timeout); }
}

export async function downloadAttachment(item) {
  const blob = await fetchAttachment(item.id);
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = item.name || t("Вложение");
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

export function renderAttachmentList(container, items, { removable = false, preview = true, onRemove, onError } = {}) {
  container.replaceChildren();
  for (const [index, item] of (items || []).entries()) {
    const row = document.createElement("div"); row.className = "attachment-row";
    const thumbnail = document.createElement("span"); thumbnail.className = "attachment-thumb";
    thumbnail.textContent = safeRaster(item.mime || item.type) ? "▧" : "▤";
    if (preview && (item.localId || safeRaster(item.mime))) {
      const loadPreview = () => {
        void loadRasterPreview(item).then(blob => {
          if (!blob || !row.isConnected) return;
          const url = URL.createObjectURL(blob);
          const image = document.createElement("img"); image.alt = ""; image.src = url;
          image.onload = () => URL.revokeObjectURL(url);
          image.onerror = () => { URL.revokeObjectURL(url); image.remove(); };
          thumbnail.replaceChildren(image);
        }).catch(() => {});
      };
      if (typeof IntersectionObserver === "undefined") loadPreview();
      else {
        const observer = new IntersectionObserver(entries => {
          if (entries.some(entry => entry.isIntersecting)) { observer.disconnect(); loadPreview(); }
        });
        observer.observe(row);
      }
    }
    const label = document.createElement("span"); label.className = "attachment-label";
    const name = document.createElement("span"); name.className = "attachment-name"; name.textContent = item.name || t("Вложение");
    const size = document.createElement("small"); size.textContent = formatBytes(Number(item.size || 0));
    label.append(name, size);
    const action = document.createElement("button"); action.type = "button"; action.className = "attachment-action";
    if (removable) {
      action.textContent = t("Убрать"); action.setAttribute("aria-label", t("Убрать {name}", {name: item.name}));
      action.addEventListener("click", () => onRemove?.(index));
    } else {
      action.textContent = t("Скачать"); action.setAttribute("aria-label", t("Скачать {name}", {name: item.name}));
      action.addEventListener("click", () => { action.disabled = true; void downloadAttachment(item).catch(error => onError?.(error)).finally(() => { action.disabled = false; }); });
    }
    row.append(thumbnail, label, action); container.append(row);
  }
}
