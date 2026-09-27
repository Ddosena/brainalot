// Connecting the extension to the local service without a pasted token (Claude, 2026-09-25).
//
// The native-messaging host (megamozg/native_host.py, registered by the installer) starts the service
// when needed and answers "hello" with the port and token of this Windows user's data. They are saved
// in chrome.storage.local "connection", next to a token typed by hand, which stays as the fallback.
import { requestApi, setApiPort, validPort } from "./api.js";
import { t } from "./i18n.js";

export const CONFIG_KEY = "connection";
export const NATIVE_HOST = "com.brainalot.host";
const RETRY_AFTER_MS = 15000;

/** One native-host round trip; rejects with a readable message when the host is not installed. */
export function nativeRequest(chromeApi, action) {
  return new Promise((resolve, reject) => chromeApi.runtime.sendNativeMessage(NATIVE_HOST, { action }, response => {
    const failure = chromeApi.runtime.lastError;
    if (failure) {
      const missing = /not found|forbidden|not allowed/i.test(failure.message || "");
      const error = new Error(missing
        ? t("Установите программу Brainalot для Windows и нажмите «Подключиться снова».")
        : failure.message);
      error.hostMissing = missing;
      reject(error);
    } else resolve(response);
  }));
}

export function createConnection(chromeApi, { now = () => Date.now(), request = requestApi } = {}) {
  let handshake = null;
  let lastAttempt = -Infinity;
  let hostMissing = false;

  async function saved() {
    return (await chromeApi.storage.local.get(CONFIG_KEY))[CONFIG_KEY] || {};
  }

  /** Ask the host for the port and token; at most once per 15 s unless forced (a user action). */
  function connect(force = false) {
    if (handshake) return handshake;
    if (!force && now() - lastAttempt < RETRY_AFTER_MS) return Promise.resolve(null);
    lastAttempt = now();
    handshake = nativeRequest(chromeApi, "hello").then(async answer => {
      if (!answer?.ok || typeof answer.token !== "string" || !validPort(answer.port)) return null;
      hostMissing = false;
      const next = { ...(await saved()), token: answer.token, port: answer.port, auto: true };
      await chromeApi.storage.local.set({ [CONFIG_KEY]: next });
      setApiPort(answer.port);
      return next;
    }).catch(error => { hostMissing = error.hostMissing === true; return null; }).finally(() => { handshake = null; });
    return handshake;
  }

  /** The saved connection, or a fresh one from the host when there is no token yet. */
  async function current() {
    const config = await saved();
    return config.token ? config : (await connect()) || config;
  }

  async function api(path, method = "GET", body, options = {}) {
    const config = await current();
    try {
      return await request(path, { token: config.token, port: config.port, method, body, ...options });
    } catch (error) {
      // 401: the token changed (a reinstall); 0: the service is down or moved to another port.
      if (error?.status !== 401 && error?.status !== 0) throw error;
      const fresh = await connect();
      if (!fresh) throw error;
      // A write that timed out may have reached the service: repeat it only when it surely did not.
      const unreached = error.status === 401 || fresh.port !== config.port || fresh.token !== config.token;
      if (method !== "GET" && !unreached) throw error;
      return request(path, { token: fresh.token, port: fresh.port, method, body, ...options });
    }
  }

  return { api, connect, current, saved, get hostMissing() { return hostMissing; } };
}
