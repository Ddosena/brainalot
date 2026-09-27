#!/usr/bin/env node
// Brainalot localization tool.
//
//   node tools/i18n/cli.mjs extract [--update]   inventory of Russian UI text; --update refreshes
//                                                extension/locales/en.js (new ids get "")
//   node tools/i18n/cli.mjs apply [--write]      wrap Russian literals in t(…), mark HTML with
//                                                data-i18n and localize manifest.json (dry run
//                                                without --write)
//   node tools/i18n/cli.mjs check                fail on unwrapped Russian literals or missing
//                                                English translations
//
// The codemod works on whatever code is current, so it can be re-run on a fresh
// checkout after other changes land.
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { transformHtml } from "./html.mjs";
import { transformJs, untranslated, usedMessages } from "./js.mjs";
import { pythonMessages } from "./python.mjs";

export const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const EXTENSION = join(ROOT, "extension");
const CATALOG = join(EXTENSION, "locales", "en.js");
const RUNTIME_FILES = new Set(["i18n.js"]);
const SERVER_FILES = ["api.py", "core.py", "calendar.py", "google_calendar_api.py", "attachments.py",
  "focus_overlay.py", "dictation.py", "dictation_service.py", "voice.py"];
export const MANIFEST_MESSAGES = {
  extName: { field: ["name"], en: "Brainalot" },
  extDescription: { field: ["description"], en: "Local manager for thoughts, tasks and schedule in the Chrome side panel." },
  actionTitle: { field: ["action", "default_title"], en: "Open Brainalot" },
};

const read = file => readFileSync(file, "utf-8");
const listed = (directory, extension) => (existsSync(directory) ? readdirSync(directory) : [])
  .filter(name => name.endsWith(extension)).sort();

export function scriptFiles(root = ROOT) {
  return listed(join(root, "extension"), ".js").filter(name => !RUNTIME_FILES.has(name))
    .map(name => join(root, "extension", name));
}

export function pageFiles(root = ROOT) {
  return listed(join(root, "extension"), ".html").map(name => join(root, "extension", name));
}

export function serverFiles(root = ROOT) {
  return SERVER_FILES.map(name => join(root, "megamozg", name)).filter(existsSync);
}

/** Every message id the interface uses or would use after `apply`, with where it comes from. */
export function inventory(root = ROOT) {
  const messages = new Map();
  const add = (message, file) => {
    if (!message) return;
    if (!messages.has(message)) messages.set(message, new Set());
    messages.get(message).add(relative(root, file).replaceAll("\\", "/"));
  };
  const review = [];
  for (const file of scriptFiles(root)) {
    const source = read(file);
    const result = transformJs(source, { file: relative(root, file) });
    result.messages.forEach(message => add(message, file));
    usedMessages(source).forEach(message => add(message, file));
    review.push(...result.report.filter(entry => !["literal", "template", "concatenation", "locale-call"].includes(entry.kind)));
  }
  for (const file of pageFiles(root)) {
    const result = transformHtml(read(file), { file: relative(root, file) });
    result.messages.forEach(message => add(message, file));
    review.push(...result.report.filter(entry => entry.kind !== "wrapped-text"));
  }
  for (const file of serverFiles(root)) {
    for (const entry of pythonMessages(read(file), relative(root, file))) add(entry.message, file);
  }
  return { messages, review };
}

export async function loadCatalog(file = CATALOG) {
  if (!existsSync(file)) return {};
  return (await import(`${pathToFileURL(file).href}?v=${Date.now()}`)).default;
}

export function renderCatalog(messages, catalog) {
  const lines = [
    "// English interface text for Brainalot. Keys are the Russian message ids used by",
    "// t(\"…\"), data-i18n and the local service; an empty value falls back to Russian.",
    "// Refresh the key list with `node tools/i18n/cli.mjs extract --update`.",
    "export default {",
  ];
  let group = null;
  const seen = new Set();
  for (const [message, files] of messages) {
    const first = [...files][0];
    if (first !== group) { lines.push(`  // ${first}`); group = first; }
    lines.push(`  ${JSON.stringify(message)}: ${JSON.stringify(catalog[message] ?? "")},`);
    seen.add(message);
  }
  const unused = Object.keys(catalog).filter(message => !seen.has(message));
  if (unused.length) {
    lines.push("  // Not found in the current code; kept so a returning phrase keeps its translation.");
    for (const message of unused) lines.push(`  ${JSON.stringify(message)}: ${JSON.stringify(catalog[message])},`);
  }
  lines.push("};", "");
  return lines.join("\n");
}

function placeholders(text) {
  return [...String(text).matchAll(/\{([A-Za-z_$][\w$]*)\}/g)].map(match => match[1]).sort();
}

export function catalogProblems(messages, catalog) {
  const missing = [], mismatched = [];
  for (const message of messages.keys()) {
    const value = catalog[message];
    if (typeof value !== "string" || !value) missing.push(message);
    else if (placeholders(value).join() !== placeholders(message).join()) mismatched.push(message);
  }
  return { missing, mismatched };
}

export function localizeManifest(manifest) {
  const copy = structuredClone(manifest);
  const ru = {};
  for (const [key, { field }] of Object.entries(MANIFEST_MESSAGES)) {
    let owner = copy;
    for (const part of field.slice(0, -1)) owner = owner?.[part];
    const name = field[field.length - 1];
    if (!owner || typeof owner[name] !== "string") continue;
    if (!owner[name].startsWith("__MSG_")) ru[key] = owner[name];
    owner[name] = `__MSG_${key}__`;
  }
  copy.default_locale = copy.default_locale || "ru";
  return { manifest: copy, ru };
}

function writeLocales(root, ru) {
  for (const [language, values] of [["ru", ru], ["en", Object.fromEntries(Object.entries(MANIFEST_MESSAGES)
    .map(([key, { en }]) => [key, en]))]]) {
    const directory = join(root, "extension", "_locales", language);
    const file = join(directory, "messages.json");
    const existing = existsSync(file) ? JSON.parse(read(file)) : {};
    for (const [key, message] of Object.entries(values)) if (message) existing[key] = { message };
    mkdirSync(directory, { recursive: true });
    writeFileSync(file, JSON.stringify(existing, null, 2) + "\n");
  }
}

export function apply(root = ROOT, { write = false } = {}) {
  const summary = [];
  const review = [];
  for (const file of scriptFiles(root)) {
    const source = read(file);
    const result = transformJs(source, { file: relative(root, file) });
    review.push(...result.report.filter(entry => !["literal", "template", "concatenation", "locale-call"].includes(entry.kind)));
    const wrapped = result.report.filter(entry => ["literal", "template", "concatenation"].includes(entry.kind)).length;
    if (result.changed) {
      summary.push({ file: relative(root, file), wrapped });
      if (write) writeFileSync(file, result.text);
    }
  }
  for (const file of pageFiles(root)) {
    const result = transformHtml(read(file), { file: relative(root, file) });
    review.push(...result.report.filter(entry => entry.kind !== "wrapped-text"));
    if (result.changed) {
      summary.push({ file: relative(root, file), wrapped: result.messages.length });
      if (write) writeFileSync(file, result.text);
    }
  }
  const manifestFile = join(root, "extension", "manifest.json");
  if (existsSync(manifestFile)) {
    const manifest = JSON.parse(read(manifestFile));
    const localized = localizeManifest(manifest);
    if (JSON.stringify(localized.manifest) !== JSON.stringify(manifest)) {
      summary.push({ file: "extension/manifest.json", wrapped: Object.keys(localized.ru).length });
      if (write) {
        writeFileSync(manifestFile, JSON.stringify(localized.manifest, null, 2) + "\n");
        writeLocales(root, localized.ru);
      }
    }
  }
  return { summary, review };
}

export async function check(root = ROOT) {
  const unwrapped = scriptFiles(root).flatMap(file => untranslated(read(file), relative(root, file)));
  const { messages } = inventory(root);
  const catalog = await loadCatalog(join(root, "extension", "locales", "en.js"));
  return { unwrapped, ...catalogProblems(messages, catalog), total: messages.size };
}

function printReview(review) {
  if (!review.length) return;
  console.log("\nНужно посмотреть вручную:");
  for (const entry of review) console.log(`  ${entry.file}:${entry.line}  ${entry.kind}  ${entry.snippet ?? entry.message ?? ""}`);
}

async function main(argv) {
  const [command, ...flags] = argv;
  if (command === "extract") {
    const { messages, review } = inventory();
    const catalog = await loadCatalog();
    const { missing, mismatched } = catalogProblems(messages, catalog);
    console.log(`Сообщений: ${messages.size}; без перевода: ${missing.length}; расходятся плейсхолдеры: ${mismatched.length}`);
    if (flags.includes("--update")) {
      writeFileSync(CATALOG, renderCatalog(messages, catalog));
      console.log(`Обновлён ${relative(ROOT, CATALOG)}`);
    }
    printReview(review);
    return 0;
  }
  if (command === "apply") {
    const write = flags.includes("--write");
    const { summary, review } = apply(ROOT, { write });
    for (const item of summary) console.log(`${write ? "изменён" : "будет изменён"}  ${item.file}  (${item.wrapped})`);
    printReview(review);
    if (!write) console.log("\nПробный запуск. Для записи: node tools/i18n/cli.mjs apply --write");
    return 0;
  }
  if (command === "check") {
    const result = await check();
    for (const entry of result.unwrapped) console.log(`без t(): ${entry.file}:${entry.line}  ${entry.message}`);
    for (const message of result.missing) console.log(`нет перевода: ${message}`);
    for (const message of result.mismatched) console.log(`плейсхолдеры: ${message}`);
    console.log(`Сообщений: ${result.total}; без t(): ${result.unwrapped.length}; без перевода: ${result.missing.length}`);
    return result.unwrapped.length || result.missing.length || result.mismatched.length ? 1 : 0;
  }
  console.log("Команды: extract [--update] | apply [--write] | check");
  return 2;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  process.exitCode = await main(process.argv.slice(2));
}
