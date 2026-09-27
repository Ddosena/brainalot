// Interface language for Brainalot.
//
// Russian text is the message id (gettext style): t("Сохранить") returns
// "Сохранить" in Russian and the English catalog value in English. Messages may
// carry named placeholders — t("Записей: {count}", {count}) — and literal
// braces are doubled ({{ }}). A missing or empty translation falls back to the
// Russian text, so a new phrase never breaks the panel.
import en from "./locales/en.js";

export const LOCALES = ["ru", "en"];
export const LOCALE_KEY = "panel:locale";
const CATALOGS = { en };
const TAGS = { ru: "ru-RU", en: "en-US" };
let current = "ru";
let patterns = null;

export function locale() {
  return current;
}

/** BCP 47 tag for Intl.DateTimeFormat, toLocaleString and friends. */
export function localeTag() {
  return TAGS[current];
}

export function setLocale(value) {
  current = LOCALES.includes(value) ? value : "ru";
  patterns = null;
  if (typeof document !== "undefined" && document.documentElement) document.documentElement.lang = current;
  return current;
}

/** A saved choice wins; otherwise Chrome's language, with Russian when it is unknown. */
export function detectLocale(saved, uiLanguage = globalThis.chrome?.i18n?.getUILanguage?.()) {
  if (LOCALES.includes(saved)) return saved;
  if (!uiLanguage) return "ru";
  return String(uiLanguage).toLowerCase().startsWith("ru") ? "ru" : "en";
}

function savedLocale() {
  try {
    return globalThis.localStorage?.getItem(LOCALE_KEY) ?? undefined;
  } catch {
    return undefined;
  }
}

// Module-level tables such as {inbox: t("Входящие")} are evaluated when their
// module loads, so the language is chosen synchronously on import: the saved
// choice from localStorage (shared by the panel and capture pages), otherwise
// Chrome's language. A language switch therefore reloads the page.
setLocale(detectLocale(savedLocale()));

/** For the service worker, which has no localStorage: read the choice from chrome.storage. */
export async function initI18n({ storage = globalThis.chrome?.storage?.local } = {}) {
  let saved;
  try {
    saved = (await storage?.get(LOCALE_KEY))?.[LOCALE_KEY];
  } catch {
    saved = undefined;
  }
  return setLocale(detectLocale(saved));
}

/**
 * Pages take the language from localStorage when they load. If chrome.storage
 * holds a different saved choice (localStorage was cleared, or another page
 * switched the language), copy it over and reload once. Returns true when a
 * reload was requested; without working localStorage the page keeps its language.
 */
export async function syncLocale({ storage = globalThis.chrome?.storage?.local,
  reload = () => globalThis.location?.reload() } = {}) {
  let saved;
  try {
    saved = (await storage?.get(LOCALE_KEY))?.[LOCALE_KEY];
  } catch {
    return false;
  }
  if (!LOCALES.includes(saved) || saved === current) return false;
  try {
    globalThis.localStorage?.setItem(LOCALE_KEY, saved);
    if (globalThis.localStorage?.getItem(LOCALE_KEY) !== saved) return false;
  } catch {
    return false;
  }
  reload();
  return true;
}

/** The language a chrome.storage change asks for, or null when it keeps the current one. */
export function localeChange(changes) {
  if (!changes || !Object.hasOwn(changes, LOCALE_KEY)) return null;
  const next = detectLocale(changes[LOCALE_KEY].newValue);
  return next === current ? null : next;
}

export async function saveLocale(value, { storage = globalThis.chrome?.storage?.local } = {}) {
  const chosen = setLocale(value);
  try {
    globalThis.localStorage?.setItem(LOCALE_KEY, chosen);
  } catch {
    // Storage can be unavailable; chrome.storage below still keeps the choice.
  }
  await storage?.set({ [LOCALE_KEY]: chosen });
  return chosen;
}

export function format(text, params) {
  return String(text).replace(/\{\{|\}\}|\{([A-Za-z_$][\w$]*)\}/g, (match, name) => {
    if (match === "{{") return "{";
    if (match === "}}") return "}";
    return params && Object.hasOwn(params, name) ? String(params[name]) : match;
  });
}

function lookup(message) {
  if (current === "ru") return undefined;
  const value = CATALOGS[current]?.[message];
  return typeof value === "string" && value ? value : undefined;
}

export function t(message, params) {
  return format(lookup(message) ?? message, params);
}

export function hasTranslation(message, language = current) {
  const value = language === "ru" ? message : CATALOGS[language]?.[message];
  return typeof value === "string" && value.length > 0;
}

export function formatDate(value, options) {
  return new Intl.DateTimeFormat(localeTag(), options).format(value instanceof Date ? value : new Date(value));
}

function compiledPatterns() {
  if (patterns) return patterns;
  patterns = [];
  for (const [message, value] of Object.entries(CATALOGS[current] || {})) {
    if (!value || !/\{[A-Za-z_$][\w$]*\}/.test(message)) continue;
    const names = [];
    const source = message.split(/(\{\{|\}\}|\{[A-Za-z_$][\w$]*\})/).map(part => {
      if (part === "{{") return "\\{";
      if (part === "}}") return "\\}";
      const name = /^\{([A-Za-z_$][\w$]*)\}$/.exec(part)?.[1];
      if (name) { names.push(name); return "([\\s\\S]+?)"; }
      return part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    }).join("");
    patterns.push({ regex: new RegExp(`^${source}$`), names, value });
  }
  return patterns;
}

/** Translate a message that came from the local service, including ones with a variable tail. */
export function translateServerMessage(text) {
  if (typeof text !== "string" || current === "ru") return text;
  const exact = lookup(text);
  if (exact) return format(exact, {});
  for (const { regex, names, value } of compiledPatterns()) {
    const match = regex.exec(text);
    if (!match) continue;
    const params = Object.fromEntries(names.map((name, index) => [name, lookup(match[index + 1]) ?? match[index + 1]]));
    return format(value, params);
  }
  return text;
}

/**
 * Translate a page marked up by tools/i18n: elements with data-i18n get their
 * text, and data-i18n-attr lists attributes whose Russian value is the id.
 */
export function applyDocument(root = globalThis.document) {
  if (!root?.querySelectorAll) return;
  for (const element of root.querySelectorAll("[data-i18n]")) {
    element.textContent = t(element.getAttribute("data-i18n"));
  }
  for (const element of root.querySelectorAll("[data-i18n-attr]")) {
    for (const name of element.getAttribute("data-i18n-attr").split(",").map(item => item.trim()).filter(Boolean)) {
      const sourceName = `data-i18n-${name}`;
      if (!element.hasAttribute(sourceName)) element.setAttribute(sourceName, element.getAttribute(name) ?? "");
      element.setAttribute(name, t(element.getAttribute(sourceName)));
    }
  }
}
