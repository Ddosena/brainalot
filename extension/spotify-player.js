import {sharedApi} from "./time-client.js";
import {t} from "./i18n.js";

export const SPOTIFY_VISIBLE_KEY = "desktop:spotify-visible";
const paths = {
  previous: '<path d="M5 5v14m14-14-10 7 10 7z"/>',
  play: '<path d="m8 5 11 7-11 7z"/>',
  pause: '<path d="M8 5v14M16 5v14"/>',
  next: '<path d="M19 5v14M5 5l10 7-10 7z"/>',
  repeat: '<path d="m17 2 4 4-4 4M3 11V9a3 3 0 0 1 3-3h15M7 22l-4-4 4-4m14-1v2a3 3 0 0 1-3 3H3"/>',
  shuffle: '<path d="m18 3 3 3-3 3m-15-3h3c4 0 8 12 12 12h3m-3 3 3-3-3-3M3 18h3c2 0 4-3 6-6m3-5c1-1 2-1 3-1h3"/>',
  volume: '<path d="m11 5-6 4H2v6h3l6 4zM15 8a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
};
const svg = key => `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[key]}</svg>`;
export function nextRepeat(value) { return value === "track" ? "list" : value === "list" ? "off" : "track"; }

export function mountSpotifyPlayer(container, {onVisibility = () => {}, settingsContainer = null, onSettingsOpen = null} = {}) {
  if (!container) throw new Error("Spotify container is required");
  const doc = container.ownerDocument, storage = globalThis.chrome?.storage;
  let visible = true, state = null, busy = false, fetching = false, disposed = false, expanded = false;
  let notice = "", pollNotice = false, generation = 0, started = false, visibilityQueue = Promise.resolve();
  let busyAction = null, queuedVolume = null, volumeDraft = null, volumeEditing = false;
  const confirmations = new Set();
  const buttons = new Map();
  container.classList.add("spotify-player");
  const details = doc.createElement("button"); details.type = "button"; details.className = "spotify-details";
  details.id = `${container.id}-details`; details.onclick = () => void setSettingsOpen(!expanded);
  const title = doc.createElement("strong"), artist = doc.createElement("span"); details.append(title, artist);
  const controls = doc.createElement("div"); controls.className = "spotify-controls";
  const settings = settingsContainer || doc.createElement("div");
  settings.classList.add("spotify-settings"); settings.classList.toggle("spotify-settings-external", Boolean(settingsContainer));
  settings.hidden = true; settings.setAttribute("data-no-drag", "");
  if (!settings.id) settings.id = `${container.id}-settings-panel`;
  const panelHead = doc.createElement("div"); panelHead.className = "spotify-settings-head";
  const panelTitle = doc.createElement("strong");
  const close = doc.createElement("button"); close.type = "button"; close.id = `${container.id}-settings-close`;
  close.innerHTML = svg("close"); close.onclick = () => void setSettingsOpen(false);
  panelHead.append(panelTitle, close);
  const volumeLabel = doc.createElement("label"); volumeLabel.className = "spotify-volume-label";
  const volumeText = doc.createElement("span"), volumeOutput = doc.createElement("output"); volumeLabel.append(volumeText, volumeOutput);
  const volume = doc.createElement("input"); volume.type = "range"; volume.min = "0"; volume.max = "100"; volume.step = "1";
  volume.id = `${container.id}-volume`; volumeLabel.htmlFor = volume.id;
  const volumeNote = doc.createElement("p"); volumeNote.className = "spotify-volume-note";
  const modes = doc.createElement("div"); modes.className = "spotify-modes";
  settings.replaceChildren(panelHead, volumeLabel, volume, volumeNote, modes);
  function makeButton(action, host, text = false) {
    const button = doc.createElement("button"); button.type = "button";
    button.id = `${container.id}-${action}`; button.dataset.spotifyAction = action;
    if (action === "open") {
      button.className = "spotify-brand";
      for (const color of ["black", "white"]) {
        const logo = doc.createElement("img"); logo.src = new URL(`./assets/spotify-${color}.svg`, import.meta.url).href;
        logo.alt = ""; logo.className = `spotify-logo-${color}`; button.append(logo);
      }
    } else {
      button.innerHTML = svg(action === "play_pause" ? "play" : action);
      if (text) { const label = doc.createElement("span"); button.append(label); }
    }
    button.onclick = () => void command(action);
    buttons.set(action, button); host.append(button); return button;
  }
  const brand = makeButton("open", doc.createElement("div"));
  for (const action of ["previous", "play_pause", "next"]) makeButton(action, controls);
  for (const action of ["repeat", "shuffle"]) makeButton(action, modes, true);
  const expand = doc.createElement("button"); expand.type = "button"; expand.id = `${container.id}-settings`;
  expand.innerHTML = svg("volume"); expand.setAttribute("aria-controls", settings.id);
  expand.onclick = () => void setSettingsOpen(!expanded); controls.append(expand);
  const status = doc.createElement("span"); status.className = "spotify-status"; status.setAttribute("role", "status");
  container.replaceChildren(brand, details, controls, status);
  if (!settingsContainer) container.append(settings);

  function render() {
    const connected = state?.connected === true;
    title.textContent = connected ? state.title || t("Spotify") : t("Spotify");
    artist.textContent = notice || (connected ? state.artist || (state.playing ? t("Играет") : t("На паузе"))
      : state?.error ? t("Spotify сейчас недоступен") : t("Открой Spotify и выбери музыку"));
    details.title = [title.textContent, artist.textContent].filter(Boolean).join(" · ");
    details.setAttribute("aria-label", `${t("Управление Spotify")}: ${details.title}`);
    container.dataset.connected = String(connected); container.dataset.error = String(Boolean(notice || state?.error));
    container.dataset.pending = String(busy);
    container.dataset.settingsOpen = String(expanded);
    controls.setAttribute("aria-label", t("Управление Spotify"));
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
      button.dataset.pending = String(busyAction === action);
      button.setAttribute("aria-label", labels[action]);
      button.title = allowed || busy ? labels[action] : `${labels[action]} · ${t("Spotify не предоставляет эту команду")}`;
      if (action === "shuffle" || action === "repeat") {
        button.setAttribute("aria-pressed", String(action === "shuffle" ? state?.shuffle === true : ["track", "list"].includes(state?.repeat)));
        button.querySelector("span").textContent = action === "shuffle" ? t("Shuffle") : labels.repeat;
      }
      if (action === "play_pause") button.innerHTML = svg(state?.playing ? "pause" : "play");
      if (action === "repeat") button.dataset.repeat = state?.repeat || "off";
    }
    panelTitle.textContent = t("Spotify"); close.setAttribute("aria-label", t("Закрыть управление музыкой"));
    const settingsLabel = t("Громкость и режимы Spotify");
    expand.setAttribute("aria-label", settingsLabel); expand.title = settingsLabel;
    expand.setAttribute("aria-expanded", String(expanded)); details.setAttribute("aria-expanded", String(expanded));
    volumeText.textContent = t("Громкость Spotify в Windows"); volume.setAttribute("aria-label", t("Громкость Spotify в Windows"));
    const allowedVolume = state?.controls?.volume === true;
    const knownVolume = allowedVolume && Number.isFinite(state.volume);
    volume.disabled = !allowedVolume; volume.dataset.pending = String(busyAction === "volume");
    const percent = Number.isFinite(volumeDraft) ? volumeDraft : knownVolume ? Math.round(state.volume * 100) : null;
    if (!volumeEditing && percent !== null) volume.value = String(percent);
    volumeOutput.textContent = percent === null ? "—" : `${percent}%`;
    volumeNote.textContent = allowedVolume ? knownVolume ? t("Только Spotify, общая громкость не меняется")
      : t("Разная громкость у сессий Spotify. Выберите общий уровень.") : t("Громкость Spotify сейчас недоступна");
    status.textContent = notice;
  }
  function cancelConfirmations() { for (const id of confirmations) clearTimeout(id); confirmations.clear(); }
  function scheduleConfirmation() {
    cancelConfirmations();
    for (const delay of [120, 400, 1000]) {
      const id = setTimeout(() => { confirmations.delete(id); void refresh(); }, delay);
      confirmations.add(id); id.unref?.();
    }
  }
  async function refresh() {
    if (disposed || !visible || doc.hidden || fetching || busy) return;
    fetching = true; const current = generation;
    try {
      const snapshot = await sharedApi("/api/media/spotify");
      if (!disposed && current === generation && !busy) {
        state = snapshot;
        if (pollNotice) { notice = ""; pollNotice = false; }
        render();
      }
    } catch {
      if (!disposed && current === generation && !busy) { state = null; pollNotice = true; notice = t("Не удалось получить состояние Spotify"); render(); }
    } finally { fetching = false; }
  }
  async function command(action, suppliedValue) {
    if (disposed || busy || !state?.controls?.[action]) return;
    busy = true; busyAction = action; generation++; notice = ""; pollNotice = false; cancelConfirmations(); render();
    const value = action === "volume" ? suppliedValue : action === "shuffle" ? !state.shuffle : action === "repeat" ? nextRepeat(state.repeat) : undefined;
    let successful = false;
    try {
      const result = await sharedApi("/api/media/spotify/command", "POST",
        {request_id: crypto.randomUUID(), action, ...(value === undefined ? {} : {value})}, {replay:false});
      if (disposed) return;
      if (result.state) state = result.state;
      successful = result.ok === true;
      if (!successful) notice = result.pending
        ? t("Результат команды неизвестен. Проверь Spotify перед повтором.")
        : t("Spotify не подтвердил команду. Проверь состояние плеера.");
    } catch { if (!disposed) notice = t("Результат команды неизвестен. Проверь Spotify перед повтором."); }
    finally {
      busy = false; busyAction = null;
      if (!disposed) {
        if (action === "volume" && queuedVolume === null && !volumeEditing) volumeDraft = null;
        render(); scheduleConfirmation();
        if (queuedVolume !== null) {
          const next = queuedVolume; queuedVolume = null;
          // Only the person's newer, explicit slider value is delivered. Never
          // resend the failed value or repeat a playback command automatically.
          if (action !== "volume" || next !== value) void command("volume", next);
          else if (!successful) volumeDraft = null;
        }
      }
    }
  }
  async function setSettingsOpen(value) {
    if (disposed) return;
    expanded = value === true; settings.hidden = !expanded; render();
    try { await onSettingsOpen?.(expanded); }
    catch { expanded = false; settings.hidden = true; notice = t("Не удалось открыть управление музыкой"); render(); }
  }
  function closeSettings() { expanded = false; settings.hidden = true; render(); }
  volume.oninput = () => {
    if (volume.disabled) return;
    volumeEditing = true; volumeDraft = Number(volume.value); volumeOutput.textContent = `${volumeDraft}%`;
  };
  volume.onchange = () => {
    volumeEditing = false;
    if (volume.disabled) return;
    const value = Math.max(0, Math.min(100, Number(volume.value))) / 100;
    volumeDraft = Math.round(value * 100);
    if (busy) queuedVolume = value; else void command("volume", value);
  };
  const outside = event => { if (expanded && !container.contains(event.target) && !settings.contains(event.target)) void setSettingsOpen(false); };
  const escape = event => { if (expanded && event.key === "Escape") { void setSettingsOpen(false); expand.focus(); } };
  doc.addEventListener("pointerdown", outside); doc.addEventListener("keydown", escape);
  function applyVisible(value) {
    if (disposed) return;
    const next = value !== false, changed = !started || next !== visible;
    started = true; visible = next; container.hidden = !visible;
    if (!visible) { closeSettings(); cancelConfirmations(); }
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
  void (storage?.local?.get([SPOTIFY_VISIBLE_KEY]) || Promise.resolve({})).then(saved => applyVisible(saved[SPOTIFY_VISIBLE_KEY])).catch(() => applyVisible(true));
  const controller = {
    get visible() { return visible; },
    async setVisible(value) { const next = value === true; await storage?.local?.set({[SPOTIFY_VISIBLE_KEY]: next}); applyVisible(next); await visibilityQueue; },
    closeSettings, refresh,
    destroy() {
      disposed = true; clearInterval(timer); cancelConfirmations();
      storage?.onChanged?.removeListener?.(onChanged); doc.removeEventListener("visibilitychange", visibilityChanged);
      doc.removeEventListener("pointerdown", outside); doc.removeEventListener("keydown", escape);
    },
  };
  window.addEventListener("pagehide", () => controller.destroy(), {once:true});
  return controller;
}
