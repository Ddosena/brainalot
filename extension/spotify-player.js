import {sharedApi} from "./time-client.js";
import {t} from "./i18n.js";

export const SPOTIFY_VISIBLE_KEY = "desktop:spotify-visible";
const actions = ["previous", "play_pause", "next", "repeat", "shuffle", "open"];
const paths = {
  previous: '<path d="M5 5v14m14-14-10 7 10 7z"/>',
  play: '<path d="m8 5 11 7-11 7z"/>',
  pause: '<path d="M8 5v14M16 5v14"/>',
  next: '<path d="M19 5v14M5 5l10 7-10 7z"/>',
  repeat: '<path d="m17 2 4 4-4 4M3 11V9a3 3 0 0 1 3-3h15M7 22l-4-4 4-4m14-1v2a3 3 0 0 1-3 3H3"/>',
  shuffle: '<path d="m18 3 3 3-3 3m-15-3h3c4 0 8 12 12 12h3m-3 3 3-3-3-3M3 18h3c2 0 4-3 6-6m3-5c1-1 2-1 3-1h3"/>',
  open: '<path d="M14 3h7v7m0-7-11 11M10 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-5"/>',
};
const svg = key => `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[key]}</svg>`;
export function nextRepeat(value) { return value === "track" ? "list" : value === "list" ? "off" : "track"; }

export function mountSpotifyPlayer(container, {onVisibility = () => {}} = {}) {
  if (!container) throw new Error("Spotify container is required");
  const doc = container.ownerDocument, storage = globalThis.chrome?.storage;
  let visible = true, state = null, busy = false, fetching = false, disposed = false;
  let notice = "", pollNotice = false, generation = 0, started = false, visibilityQueue = Promise.resolve();
  container.classList.add("spotify-player");
  const details = doc.createElement("div"); details.className = "spotify-details";
  const title = doc.createElement("strong"), artist = doc.createElement("span");
  details.append(title, artist);
  const controls = doc.createElement("div"); controls.className = "spotify-controls";
  controls.setAttribute("aria-label", t("Управление Spotify"));
  const buttons = new Map();
  for (const action of actions) {
    const button = doc.createElement("button"); button.type = "button";
    button.id = `${container.id}-${action}`; button.dataset.spotifyAction = action;
    button.innerHTML = svg(action === "play_pause" ? "play" : action);
    button.onclick = () => void command(action);
    controls.append(button); buttons.set(action, button);
  }
  const status = doc.createElement("span"); status.className = "spotify-status"; status.setAttribute("role", "status");
  container.replaceChildren(details, controls, status);
  function render() {
    const connected = state?.connected === true;
    title.textContent = connected ? state.title || t("Spotify") : t("Spotify");
    artist.textContent = notice || (connected ? state.artist || (state.playing ? t("Играет") : t("На паузе"))
      : state?.error ? t("Spotify сейчас недоступен") : t("Открой Spotify и выбери музыку"));
    details.title = [title.textContent, artist.textContent].filter(Boolean).join(" · ");
    container.dataset.connected = String(connected);
    container.dataset.error = String(Boolean(notice || state?.error));
    const labels = {
      previous: t("Предыдущая композиция"), next: t("Следующая композиция"),
      play_pause: state?.playing ? t("Приостановить музыку") : t("Продолжить музыку"),
      shuffle: state?.shuffle ? t("Выключить shuffle") : t("Включить shuffle"),
      repeat: state?.repeat === "track" ? t("Повтор композиции") : state?.repeat === "list" ? t("Повтор плейлиста") : t("Повтор выключен"),
      open: connected ? t("Открыть Spotify") : t("Подключить Spotify"),
    };
    for (const [action, button] of buttons) {
      const allowed = state?.controls?.[action] === true;
      button.disabled = busy || !allowed;
      button.setAttribute("aria-label", labels[action]);
      button.title = allowed || busy ? labels[action] : `${labels[action]} · ${t("Spotify не предоставляет эту команду")}`;
      if (action === "shuffle" || action === "repeat") button.setAttribute("aria-pressed",
        String(action === "shuffle" ? state?.shuffle === true : ["track", "list"].includes(state?.repeat)));
      if (action === "play_pause") button.innerHTML = svg(state?.playing ? "pause" : "play");
      if (action === "repeat") button.dataset.repeat = state?.repeat || "off";
    }
    status.textContent = notice;
  }
  async function refresh() {
    if (disposed || !visible || doc.hidden || fetching || busy) return;
    fetching = true;
    const current = generation;
    try {
      const snapshot = await sharedApi("/api/media/spotify");
      if (!disposed && current === generation && !busy) {
        state = snapshot;
        if (pollNotice) { notice = ""; pollNotice = false; }
        render();
      }
    }
    catch { if (!disposed && current === generation && !busy) { state = null; pollNotice = true; notice = t("Не удалось получить состояние Spotify"); render(); } }
    finally { fetching = false; }
  }
  async function command(action) {
    if (disposed || busy || !state?.controls?.[action]) return;
    busy = true; generation++; notice = ""; pollNotice = false; render();
    const value = action === "shuffle" ? !state.shuffle : action === "repeat" ? nextRepeat(state.repeat) : undefined;
    try {
      const result = await sharedApi("/api/media/spotify/command", "POST",
        {action, ...(value === undefined ? {} : {value})}, {replay:false});
      if (disposed) return;
      if (result.state) state = result.state;
      if (!result.ok) notice = t("Spotify не подтвердил команду. Проверь состояние плеера.");
    } catch {
      if (!disposed) notice = t("Результат команды неизвестен. Проверь Spotify перед повтором.");
    } finally {
      busy = false;
      if (!disposed) { render(); void refresh(); }
    }
  }
  function applyVisible(value) {
    if (disposed) return;
    const next = value !== false, changed = !started || next !== visible;
    started = true; visible = next; container.hidden = !visible;
    if (changed) {
      visibilityQueue = visibilityQueue.catch(() => {}).then(() => onVisibility(next))
        .catch(() => { notice = t("Не удалось изменить размер плеера"); render(); });
    }
    if (visible) void refresh();
  }
  const onChanged = (changes, area) => {
    if (area === "local" && changes[SPOTIFY_VISIBLE_KEY]) applyVisible(changes[SPOTIFY_VISIBLE_KEY].newValue);
    if (area === "local" && changes["panel:locale"]) render();
  };
  storage?.onChanged?.addListener(onChanged);
  const visibilityChanged = () => { if (!doc.hidden) void refresh(); };
  doc.addEventListener("visibilitychange", visibilityChanged);
  const timer = setInterval(() => void refresh(), 2000); timer.unref?.();
  render();
  void (storage?.local?.get([SPOTIFY_VISIBLE_KEY]) || Promise.resolve({}))
    .then(saved => applyVisible(saved[SPOTIFY_VISIBLE_KEY])).catch(() => applyVisible(true));
  const controller = {
    get visible() { return visible; },
    async setVisible(value) {
      const next = value === true;
      await storage?.local?.set({[SPOTIFY_VISIBLE_KEY]: next});
      applyVisible(next);
      await visibilityQueue;
    },
    refresh,
    destroy() {
      disposed = true; clearInterval(timer);
      storage?.onChanged?.removeListener?.(onChanged);
      doc.removeEventListener("visibilitychange", visibilityChanged);
    },
  };
  window.addEventListener("pagehide", () => controller.destroy(), {once:true});
  return controller;
}
