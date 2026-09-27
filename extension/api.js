import { t, translateServerMessage } from "./i18n.js";
export const DEFAULT_PORT = 8765;
export const API_BASE = `http://127.0.0.1:${DEFAULT_PORT}`;

// The service takes 8765 unless another program holds it; the port it really uses comes from the
// native-host handshake and is kept next to the token (chrome.storage.local "connection").
let currentPort = DEFAULT_PORT;
export function validPort(value) {
  return Number.isInteger(value) && value >= 1024 && value <= 65535;
}
export function setApiPort(port) {
  currentPort = validPort(port) ? port : DEFAULT_PORT;
}
export function apiBase() {
  return `http://127.0.0.1:${currentPort}`;
}
if (globalThis.chrome?.storage?.local) {
  chrome.storage.local.get("connection").then(saved => setApiPort(saved.connection?.port), () => {});
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && changes.connection) setApiPort(changes.connection.newValue?.port);
  });
}

export class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.status = status;
  }
}

// Only library reads participate. Keep cached copies private: callers may mutate
// returned notes, and credentials/ports identify entirely separate libraries.
const readCache = new Map();
const CACHE_LIMIT = 8;

export async function requestApi(path, { token, port, method = "GET", body, fetcher = fetch, timeoutMs = 5000 } = {}) {
  if (!token) throw new ApiError(t("Укажите токен подключения в настройках."), 401);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  const base = validPort(port) ? `http://127.0.0.1:${port}` : apiBase();
  const scope = `${base}|${token}|`;
  const cacheKey = scope + path;
  const conditional = method === "GET" && (path === "/api/dashboard" || path === "/api/notes");
  const cached = conditional ? readCache.get(cacheKey) : null;
  try {
    const headers = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
    if (cached) headers["If-None-Match"] = cached.etag;
    const response = await fetcher(base + path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
      credentials: "omit",
      redirect: "error",
      cache: "no-store",
    });
    if (response.status === 304 && cached) return structuredClone(cached.data);
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      const fallback = response.status === 401
        ? t("Токен не подходит. Проверьте настройки подключения.")
        : response.status === 409
          ? t("Запись уже изменилась. Обновите список перед повторной правкой.")
          : t("Сервис вернул ошибку {status}.", {status: response.status});
      throw new ApiError(typeof data?.detail === "string" ? translateServerMessage(data.detail) : fallback, response.status);
    }
    if (data === null) throw new ApiError(t("Сервис вернул непонятный ответ."));
    const etag = response.headers?.get?.("ETag");
    if (conditional && etag) {
      readCache.delete(cacheKey);
      readCache.set(cacheKey, { etag, data: structuredClone(data) });
      if (readCache.size > CACHE_LIMIT) readCache.delete(readCache.keys().next().value);
    } else if (conditional) {
      readCache.delete(cacheKey);
    } else if (method !== "GET") {
      for (const key of readCache.keys()) if (key.startsWith(scope)) readCache.delete(key);
    }
    return data;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(t("Локальный сервис недоступен. Новые записи сохраняются в очереди Chrome."));
  } finally {
    clearTimeout(timeout);
  }
}
