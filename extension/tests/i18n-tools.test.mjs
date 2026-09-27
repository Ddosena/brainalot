import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { cpSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { transformHtml } from "../../tools/i18n/html.mjs";
import { tokenize, transformJs, untranslated, usedMessages } from "../../tools/i18n/js.mjs";
import { pythonMessages } from "../../tools/i18n/python.mjs";
import { ROOT, apply, catalogProblems, inventory, loadCatalog, localizeManifest } from "../../tools/i18n/cli.mjs";

const js = source => transformJs(source, { file: "sample.js" });

test("plain literals, ternaries and object values are wrapped; comments and regexes are not", () => {
  const source = [
    "// Комментарий остаётся",
    "const kinds = {task:\"Дело\", idea:'Идея'};",
    "button.title = ready ? \"Готово\" : \"Ждём\";",
    "const pattern = /Списки/;",
    "",
  ].join("\n");
  const { text } = js(source);
  assert.match(text, /^import \{ t \} from "\.\/i18n\.js";\n/);
  assert.match(text, /\/\/ Комментарий остаётся/);
  assert.match(text, /\{task:t\("Дело"\), idea:t\("Идея"\)\}/);
  assert.match(text, /ready \? t\("Готово"\) : t\("Ждём"\)/);
  assert.match(text, /\/Списки\//);
});

test("concatenations and templates become one message with named placeholders", () => {
  const { text, messages } = js([
    "import { node } from \"./dom.js\";",
    "label.setAttribute(\"aria-label\", \"Удалить запись: \" + note.title);",
    "el.textContent = `Прошло ${focusTime(view.elapsedMs)} из ${focusTime(view.durationMs)}`;",
    "hint = \"Снять выделение с «\" + note.title + \"»\";",
    "stamp = \"Обновлён: \" + new Date(value.last_updated).toLocaleString(\"ru-RU\");",
    "",
  ].join("\n"));
  assert.match(text, /import \{ node \} from "\.\/dom\.js";\nimport \{ localeTag, t \} from "\.\/i18n\.js";/);
  assert.match(text, /t\("Удалить запись: \{title\}", \{title: note\.title\}\)/);
  assert.match(text, /t\("Прошло \{focusTime\} из \{focusTime2\}", \{focusTime: focusTime\(view\.elapsedMs\), focusTime2: focusTime\(view\.durationMs\)\}\)/);
  assert.match(text, /t\("Снять выделение с «\{title\}»", \{title: note\.title\}\)/);
  assert.match(text, /t\("Обновлён: \{last_updated\}", \{last_updated: new Date\(value\.last_updated\)\.toLocaleString\(localeTag\(\)\)\}\)/);
  assert.deepEqual(messages, ["Удалить запись: {title}", "Прошло {focusTime} из {focusTime2}",
    "Снять выделение с «{title}»", "Обновлён: {last_updated}"]);
});

test("a locale code inside a translated template is replaced once, inside the parameter", () => {
  const { text } = js("x = `Таймер: ${status.toLocaleLowerCase(\"ru-RU\")}`;\n");
  assert.match(text, /t\("Таймер: \{status\}", \{status: status\.toLocaleLowerCase\(localeTag\(\)\)\}\)/);
  assert.equal((text.match(/localeTag\(\)/g) || []).length, 1);
});

test("keys, case labels, comparisons, console output and wrapped calls are left alone", () => {
  const source = [
    "const map = {\"Ключ\": 1};",
    "switch (x) { case \"Вариант\": break; }",
    "if (error.message === \"Неизвестная команда.\") retry();",
    "if (text.startsWith(\"Списки\")) retry();",
    "console.warn(\"Отладка\");",
    "done = t(\"Уже переведено\");",
    "",
  ].join("\n");
  const result = js(source);
  assert.equal(result.text, source);
  assert.deepEqual(result.report.map(entry => entry.kind).sort(),
    ["case-label", "comparison", "comparison", "console", "object-key"]);
});

test("the codemod is idempotent and the checker sees what is left", () => {
  const once = js("a = \"Первый\" + b;\nc = `Второй ${d}`;\n").text;
  assert.equal(js(once).text, once);
  assert.deepEqual(usedMessages(once), ["Первый{b}", "Второй {d}"]);
  assert.deepEqual(untranslated(once, "x.js"), []);
  assert.equal(untranslated("e = \"Третий\";\n", "x.js").length, 1);
});

test("literal braces in Russian text are escaped in the message id", () => {
  assert.match(js("x = \"Скобки {не плейсхолдер}\";\n").text, /t\("Скобки \{\{не плейсхолдер\}\}"\)/);
});

test("the lexer copes with regexes, divisions, nested templates and escapes", () => {
  const tokens = tokenize("const r = /[/]\\//g; const q = a / b / c; const s = `x${`y${z}`}`; const e = \"\\\"\";");
  assert.deepEqual(tokens.tokens.filter(token => token.type === "regex").map(token => token.value), ["/[/]\\//g"]);
  assert.equal(tokens.tokens.filter(token => token.value === "/").length, 2);
  assert.equal(tokens.tokens.find(token => token.type === "template").expressions[0].tokens[0].type, "template");
});

test("HTML text, labels with controls and attributes are marked without losing Russian", () => {
  const source = [
    "<title>Записать — Brainalot</title>",
    "<button id=\"save\" title=\"Сохранить запись\">Сохранить</button>",
    "<label>Тип записи<select><option value=\"task\">Дело</option></select></label>",
    "<input placeholder=\"Например, Работа\">",
    "<p>Ёлка&nbsp;и «кавычки»</p>",
    "<script>const x = \"Не трогать\";</script>",
  ].join("\n");
  const { text, messages } = transformHtml(source);
  assert.match(text, /<title data-i18n="Записать — Brainalot">Записать — Brainalot<\/title>/);
  assert.match(text, /<button id="save" title="Сохранить запись" data-i18n="Сохранить" data-i18n-attr="title">Сохранить<\/button>/);
  assert.match(text, /<label><span data-i18n="Тип записи">Тип записи<\/span><select><option value="task" data-i18n="Дело">Дело<\/option><\/select><\/label>/);
  assert.match(text, /<input placeholder="Например, Работа" data-i18n-attr="placeholder">/);
  assert.match(text, /<p data-i18n="Ёлка&nbsp;и «кавычки»">/);
  assert.match(text, /<script>const x = "Не трогать";<\/script>/);
  assert.ok(messages.includes("Ёлка и «кавычки»"));
  assert.equal(transformHtml(text).text, text);
});

test("server messages come from errors, warnings and f-strings, not from docstrings or data", () => {
  const source = [
    "\"\"\"Модуль про записи.\"\"\"",
    "FOLDERS = {\"task\": \"2 Дела/Задачи\"}",
    "# raise ValidationError(\"Комментарий\")",
    "def f(x, exc):",
    "    raise ValidationError(\"Неизвестный тип записи\")",
    "    raise ValidationError(\"Такой даты нет: \" + x)",
    "    warnings.append(f\"Автоперенос {note['id']}: {exc}\")",
    "    kind_label = {\"inbox\": \"входящее\",",
    "                  \"task\": \"дело\"}[card[\"kind\"]]",
    "",
  ].join("\n");
  assert.deepEqual(pythonMessages(source).map(entry => entry.message),
    ["Неизвестный тип записи", "Такой даты нет: {value}", "Автоперенос {id}: {exc}", "входящее", "дело"]);
});

test("the manifest keeps Russian by default and gains English names", () => {
  const { manifest, ru } = localizeManifest({ name: "Brainalot", description: "Локальный менеджер",
    action: { default_title: "Открыть Brainalot" } });
  assert.equal(manifest.default_locale, "ru");
  assert.deepEqual([manifest.name, manifest.description, manifest.action.default_title],
    ["__MSG_extName__", "__MSG_extDescription__", "__MSG_actionTitle__"]);
  assert.deepEqual(ru, { extName: "Brainalot", extDescription: "Локальный менеджер", actionTitle: "Открыть Brainalot" });
  assert.deepEqual(localizeManifest(manifest).manifest, manifest);
});

test("the English catalog covers the current interface", async () => {
  const { messages } = inventory();
  const { missing, mismatched } = catalogProblems(messages, await loadCatalog());
  assert.deepEqual(mismatched, []);
  // A few fresh phrases may wait for translation; a large gap means the catalog was not refreshed.
  assert.ok(missing.length <= Math.ceil(messages.size * 0.05),
    `Untranslated: ${missing.slice(0, 20).join(" | ")} — run node tools/i18n/cli.mjs extract --update`);
});

test("applying the codemod to a copy of the extension keeps every module valid and passes the checker", async () => {
  const copy = mkdtempSync(join(tmpdir(), "mmm-i18n-"));
  try {
    cpSync(join(ROOT, "extension"), join(copy, "extension"), { recursive: true });
    cpSync(join(ROOT, "megamozg"), join(copy, "megamozg"), { recursive: true });
    apply(copy, { write: true });
    for (const name of readdirSync(join(copy, "extension")).filter(file => file.endsWith(".js"))) {
      execFileSync(process.execPath, ["--check", join(copy, "extension", name)], { stdio: "pipe" });
    }
    const manifest = JSON.parse(readFileSync(join(copy, "extension", "manifest.json"), "utf-8"));
    assert.equal(manifest.default_locale, "ru");
    const english = JSON.parse(readFileSync(join(copy, "extension", "_locales", "en", "messages.json"), "utf-8"));
    assert.equal(english.actionTitle.message, "Open Brainalot");
    const second = apply(copy, { write: true });
    assert.deepEqual(second.summary, []);
    const leftovers = readdirSync(join(copy, "extension")).filter(file => file.endsWith(".js") && file !== "i18n.js")
      .flatMap(file => untranslated(readFileSync(join(copy, "extension", file), "utf-8"), file));
    assert.deepEqual(leftovers, []);
    writeFileSync(join(copy, "marker"), "");
  } finally {
    rmSync(copy, { recursive: true, force: true });
  }
});
