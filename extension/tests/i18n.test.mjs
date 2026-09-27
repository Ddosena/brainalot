import assert from "node:assert/strict";
import test from "node:test";

import catalog from "../locales/en.js";
import {
  applyDocument, detectLocale, format, formatDate, hasTranslation, locale, localeChange, localeTag, saveLocale,
  setLocale, syncLocale, t, translateServerMessage,
} from "../i18n.js";

test.afterEach(() => setLocale("ru"));

test("Russian is the default and the message id is the text", () => {
  assert.equal(locale(), "ru");
  assert.equal(t("Сохранить"), "Сохранить");
  assert.equal(t("Изменить запись: {title}", { title: "Купить хлеб" }), "Изменить запись: Купить хлеб");
});

test("English uses the catalog and falls back to Russian for unknown text", () => {
  setLocale("en");
  assert.equal(localeTag(), "en-US");
  assert.equal(t("Сохранить"), "Save");
  assert.equal(t("Изменить запись: {title}", { title: "Buy bread" }), "Edit entry: Buy bread");
  assert.equal(t("Фраза, которой нет в каталоге"), "Фраза, которой нет в каталоге");
});

test("placeholders keep unknown names and doubled braces", () => {
  assert.equal(format("{a} {{literal}} {missing}", { a: 1 }), "1 {literal} {missing}");
  assert.equal(format("{count}", { count: 0 }), "0");
});

test("a saved choice wins over Chrome's language", () => {
  assert.equal(detectLocale("en", "ru-RU"), "en");
  assert.equal(detectLocale(undefined, "ru-RU"), "ru");
  assert.equal(detectLocale(undefined, "de-DE"), "en");
  assert.equal(detectLocale(undefined, undefined), "ru");
  assert.equal(detectLocale("fr", undefined), "ru");
});

test("server messages translate exactly or by pattern, including captured words", () => {
  setLocale("en");
  assert.equal(translateServerMessage("Нужен действительный токен"), "A valid token is required");
  assert.equal(translateServerMessage("Такой даты нет: 31 ноября"), "No such date: 31 ноября");
  assert.equal(
    translateServerMessage("Вспомнить: дело; важность — высокая. Показ не меняет статус."),
    "Recall: task; priority — high. Showing it does not change its status.");
  assert.equal(translateServerMessage("Совсем другой текст"), "Совсем другой текст");
  setLocale("ru");
  assert.equal(translateServerMessage("Нужен действительный токен"), "Нужен действительный токен");
});

test("saveLocale remembers the choice in chrome.storage", async () => {
  const saved = {};
  await saveLocale("en", { storage: { set: async value => Object.assign(saved, value) } });
  assert.equal(locale(), "en");
  assert.deepEqual(saved, { "panel:locale": "en" });
});

test("syncLocale copies a choice made elsewhere into localStorage and reloads once", async () => {
  const memory = new Map();
  const previous = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: {
    getItem: key => memory.get(key) ?? null, setItem: (key, value) => memory.set(key, String(value)) } });
  try {
    let reloads = 0;
    const storage = value => ({ get: async () => ({ "panel:locale": value }) });
    assert.equal(await syncLocale({ storage: storage("en"), reload: () => { reloads += 1; } }), true);
    assert.equal(memory.get("panel:locale"), "en");
    assert.equal(reloads, 1);
    assert.equal(await syncLocale({ storage: storage("ru"), reload: () => { reloads += 1; } }), false);
    assert.equal(await syncLocale({ storage: storage("de"), reload: () => { reloads += 1; } }), false);
    assert.equal(await syncLocale({ storage: { get: async () => { throw new Error("gone"); } } }), false);
    Object.defineProperty(globalThis, "localStorage", { configurable: true, value: {
      getItem: () => null, setItem: () => { throw new Error("blocked"); } } });
    assert.equal(await syncLocale({ storage: storage("en"), reload: () => { reloads += 1; } }), false);
    assert.equal(reloads, 1);
  } finally {
    if (previous) Object.defineProperty(globalThis, "localStorage", previous);
    else delete globalThis.localStorage;
  }
});

test("localeChange reports only a switch to another language", () => {
  assert.equal(localeChange({ "panel:locale": { newValue: "en" } }), "en");
  assert.equal(localeChange({ "panel:locale": { newValue: "ru" } }), null);
  assert.equal(localeChange({ "panel:theme": { newValue: "dark" } }), null);
  setLocale("en");
  assert.equal(localeChange({ "panel:locale": { newValue: "ru" } }), "ru");
});

test("dates follow the interface language", () => {
  const day = new Date(Date.UTC(2026, 8, 24, 12));
  setLocale("en");
  assert.equal(formatDate(day, { month: "long", timeZone: "UTC" }), "September");
  setLocale("ru");
  assert.equal(formatDate(day, { month: "long", timeZone: "UTC" }), "сентябрь");
});

test("applyDocument translates marked text and attributes and can switch back", () => {
  const element = (attributes, text = "") => ({
    attributes: { ...attributes }, textContent: text,
    getAttribute(name) { return this.attributes[name] ?? null; },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    hasAttribute(name) { return name in this.attributes; },
  });
  const button = element({ "data-i18n": "Сохранить" }, "Сохранить");
  const input = element({ "data-i18n-attr": "placeholder,title", placeholder: "Название", title: "Закрыть" });
  const root = { querySelectorAll: selector => (selector === "[data-i18n]" ? [button] : [input]) };
  setLocale("en");
  applyDocument(root);
  assert.equal(button.textContent, "Save");
  assert.deepEqual([input.attributes.placeholder, input.attributes.title], ["Title", "Close"]);
  setLocale("ru");
  applyDocument(root);
  assert.equal(button.textContent, "Сохранить");
  assert.deepEqual([input.attributes.placeholder, input.attributes.title], ["Название", "Закрыть"]);
});

test("every catalog value keeps the placeholders of its Russian id", () => {
  const names = text => [...text.matchAll(/\{([A-Za-z_$][\w$]*)\}/g)].map(match => match[1]).sort().join();
  // A language picker names each language in itself, so its option reads the same in English.
  const endonyms = new Set(["Русский"]);
  for (const [message, value] of Object.entries(catalog)) {
    if (!value) continue;
    assert.equal(names(value), names(message), message);
    assert.ok(endonyms.has(value) || !/[А-Яа-яЁё]/.test(value), `English text contains Cyrillic: ${value}`);
    assert.ok(hasTranslation(message, "en"));
  }
});
