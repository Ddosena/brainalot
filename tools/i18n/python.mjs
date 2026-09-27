// Server messages for the Brainalot i18n catalog.
//
// The service answers errors and warnings in Russian («Нужен действительный
// токен», «Такой даты нет: 31 ноября»). The extension shows them as they come,
// so their English text lives in the same catalog and
// `translateServerMessage()` in extension/i18n.js maps them back, including
// messages with a variable tail («… {value}»).

import { CYRILLIC } from "./js.mjs";

const USER_FACING = /\braise\b|Error\(|detail|warnings|HTTPException|reason|_label|IMPORTANCE_LABELS/;

function lineAt(source, offset) {
  const start = source.lastIndexOf("\n", offset - 1) + 1;
  const end = source.indexOf("\n", offset);
  return { text: source.slice(start, end === -1 ? source.length : end), number: source.slice(0, offset).split("\n").length };
}

/** The line of `offset` plus the lines it continues (an open bracket or a trailing comma). */
function statementText(source, offset) {
  let start = source.lastIndexOf("\n", offset - 1) + 1;
  const end = source.indexOf("\n", offset);
  for (let steps = 0; steps < 6 && start > 0; steps += 1) {
    const previousStart = source.lastIndexOf("\n", start - 2) + 1;
    const previous = source.slice(previousStart, start - 1).trimEnd();
    if (!/[,([{\\]$/.test(previous)) break;
    start = previousStart;
  }
  return source.slice(start, end === -1 ? source.length : end);
}

function placeholder(expression) {
  if (/\bor\b/.test(expression)) return placeholder(expression.split(/\bor\b/)[0]);
  const names = expression.split(/[!:]/)[0].match(/[A-Za-z_]\w*/g) || [];
  const skip = new Set(["str", "len", "int", "float", "repr", "self", "note", "card", "exc", "or", "and", "not",
    "if", "else", "None", "True", "False", "in", "is"]);
  const useful = names.filter(name => !skip.has(name));
  const fallback = names.filter(name => !["or", "and", "not", "if", "else", "in", "is"].includes(name));
  return useful.length ? useful[useful.length - 1] : fallback.length ? fallback[fallback.length - 1] : "value";
}

function fStringMessage(body) {
  let message = "", i = 0;
  while (i < body.length) {
    if (body.startsWith("{{", i) || body.startsWith("}}", i)) { message += body.slice(i, i + 2); i += 2; continue; }
    if (body[i] === "{") {
      let depth = 1, j = i + 1;
      while (j < body.length && depth) { if (body[j] === "{") depth += 1; else if (body[j] === "}") depth -= 1; j += 1; }
      message += `{${placeholder(body.slice(i + 1, j - 1))}}`;
      i = j;
      continue;
    }
    message += body[i];
    i += 1;
  }
  return message;
}

function unescape(body) {
  return body.replace(/\\(u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|[\s\S])/g, (_, escape) => {
    if (escape[0] === "u" && escape.length === 5) return String.fromCharCode(parseInt(escape.slice(1), 16));
    if (escape[0] === "x" && escape.length === 3) return String.fromCharCode(parseInt(escape.slice(1), 16));
    return { n: "\n", t: "\t", "\\": "\\", "'": "'", "\"": "\"" }[escape] ?? "\\" + escape;
  });
}

/** Russian user-facing string literals of one Python module as catalog messages. */
export function pythonMessages(source, file = "module.py") {
  const found = [];
  const string = /(^|[^\w])([rRbBuUfF]{0,2})("""|'''|"|')/g;
  let i = 0;
  while (i < source.length) {
    if (source[i] === "#") {
      const end = source.indexOf("\n", i);
      i = end === -1 ? source.length : end;
      continue;
    }
    string.lastIndex = i;
    const match = string.exec(source);
    if (!match) break;
    const comment = source.indexOf("#", i);
    if (comment !== -1 && comment < match.index + match[1].length) {
      const lineStart = source.lastIndexOf("\n", comment - 1) + 1;
      const before = source.slice(lineStart, comment);
      if (!/["']/.test(before)) { i = comment; continue; }
    }
    const prefix = match[2].toLowerCase(), quote = match[3];
    const bodyStart = match.index + match[1].length + match[2].length + quote.length;
    let j = bodyStart;
    while (j < source.length) {
      if (source[j] === "\\" && !prefix.includes("r")) { j += 2; continue; }
      if (source.startsWith(quote, j)) break;
      j += 1;
    }
    const body = source.slice(bodyStart, j);
    const end = j + quote.length;
    i = end;
    if (quote.length === 3 || prefix.includes("b") || !CYRILLIC.test(body) || /<[a-zA-Z/]|\n|\\n/.test(body)) continue;
    const line = lineAt(source, bodyStart);
    if (!USER_FACING.test(statementText(source, bodyStart))) continue;
    if (prefix.includes("f")) {
      for (const inner of body.matchAll(/(["'])((?:(?!\1).)*[А-Яа-яЁё](?:(?!\1).)*)\1/g)) {
        found.push({ file, line: line.number, message: unescape(inner[2]).replace(/[{}]/g, brace => brace + brace) });
      }
    }
    let message = prefix.includes("f") ? fStringMessage(body) : unescape(body).replace(/[{}]/g, brace => brace + brace);
    const before = source.slice(0, match.index + match[1].length).trimEnd();
    const after = source.slice(end).trimStart();
    if (before.endsWith("+")) message = "{value}" + message;
    if (after.startsWith("+")) message += "{value}";
    // A bare joiner such as « и » between two variables is not a phrase of its own.
    if ((message.replace(/\{[A-Za-z_]\w*\}/g, "").match(/[А-Яа-яЁё]/g) || []).length < 3) continue;
    found.push({ file, line: line.number, message });
  }
  return found;
}
