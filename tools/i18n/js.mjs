// JavaScript side of the Brainalot i18n codemod.
//
// A small lexer (strings, templates with nested expressions, comments, regular
// expressions, names, numbers, punctuators) finds every Russian literal in a
// module. `transformJs` wraps each one in `t(...)`, turning concatenations and
// template literals into one message with named placeholders:
//   "Удалить запись: " + note.title   →   t("Удалить запись: {title}", {title: note.title})
// The Russian text is the message id (gettext style), so the code stays readable
// and a changed phrase simply becomes a new id that needs a translation.

export const CYRILLIC = /[А-Яа-яЁё]/;

const PUNCTUATORS = [">>>=", "...", "===", "!==", "**=", "<<=", ">>=", ">>>", "&&=", "||=", "??=", "=>", "==",
  "!=", "<=", ">=", "&&", "||", "??", "?.", "++", "--", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "**",
  "<<", ">>", "{", "}", "(", ")", "[", "]", ";", ",", "<", ">", "+", "-", "*", "/", "%", "&", "|", "^", "!",
  "~", "?", ":", "=", ".", "@", "#"];
const REGEX_AFTER_WORDS = new Set(["return", "typeof", "case", "do", "else", "in", "of", "new", "delete", "void",
  "throw", "instanceof", "yield", "await"]);

export function cook(raw) {
  return raw.replace(/\\(u\{[0-9a-fA-F]+\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|\r\n|[\s\S])/g, (_, escape) => {
    if (escape.startsWith("u{")) return String.fromCodePoint(parseInt(escape.slice(2, -1), 16));
    if (escape[0] === "u" && escape.length === 5) return String.fromCharCode(parseInt(escape.slice(1), 16));
    if (escape[0] === "x" && escape.length === 3) return String.fromCharCode(parseInt(escape.slice(1), 16));
    return { n: "\n", t: "\t", r: "\r", b: "\b", f: "\f", v: "\v", 0: "\0", "\n": "", "\r\n": "" }[escape] ?? escape;
  });
}

function scanString(source, start) {
  const quote = source[start];
  let i = start + 1;
  while (i < source.length) {
    if (source[i] === "\\") { i += 2; continue; }
    if (source[i] === quote) return i + 1;
    if (source[i] === "\n") break;
    i += 1;
  }
  throw new SyntaxError(`Unterminated string at ${start}`);
}

function scanRegex(source, start) {
  let i = start + 1, inClass = false;
  while (i < source.length) {
    const c = source[i];
    if (c === "\\") { i += 2; continue; }
    if (c === "\n") break;
    if (inClass) { if (c === "]") inClass = false; }
    else if (c === "[") inClass = true;
    else if (c === "/") {
      i += 1;
      while (i < source.length && /[a-z]/i.test(source[i])) i += 1;
      return i;
    }
    i += 1;
  }
  throw new SyntaxError(`Unterminated regular expression at ${start}`);
}

function scanTemplate(source, start) {
  const quasis = [], expressions = [];
  let i = start + 1, chunk = i;
  while (i < source.length) {
    const c = source[i];
    if (c === "\\") { i += 2; continue; }
    if (c === "`") {
      quasis.push({ start: chunk, end: i, raw: source.slice(chunk, i) });
      return { end: i + 1, quasis, expressions };
    }
    if (c === "$" && source[i + 1] === "{") {
      quasis.push({ start: chunk, end: i, raw: source.slice(chunk, i) });
      const inner = tokenize(source, i + 2, true);
      expressions.push({ start: i + 2, end: inner.end, tokens: inner.tokens });
      i = inner.end + 1;
      chunk = i;
      continue;
    }
    i += 1;
  }
  throw new SyntaxError(`Unterminated template at ${start}`);
}

function regexAllowed(previous) {
  if (!previous) return true;
  if (previous.type === "name") return REGEX_AFTER_WORDS.has(previous.value);
  if (previous.type === "number" || previous.type === "string" || previous.type === "template"
      || previous.type === "regex") return false;
  return !(previous.value === ")" || previous.value === "]" || previous.value === "}");
}

/** Tokenize `source` from `start`; with `stopAtBrace`, stop at the `}` closing a template expression. */
export function tokenize(source, start = 0, stopAtBrace = false) {
  const tokens = [];
  let i = start, depth = 0;
  const previous = () => tokens.findLast(token => token.type !== "comment");
  while (i < source.length) {
    const c = source[i];
    if (/\s/.test(c)) { i += 1; continue; }
    if (c === "/" && source[i + 1] === "/") {
      const end = source.indexOf("\n", i);
      const stop = end === -1 ? source.length : end;
      tokens.push({ type: "comment", start: i, end: stop, value: source.slice(i, stop) });
      i = stop;
      continue;
    }
    if (c === "/" && source[i + 1] === "*") {
      const end = source.indexOf("*/", i + 2);
      if (end === -1) throw new SyntaxError(`Unterminated comment at ${i}`);
      tokens.push({ type: "comment", start: i, end: end + 2, value: source.slice(i, end + 2) });
      i = end + 2;
      continue;
    }
    if (c === "\"" || c === "'") {
      const end = scanString(source, i);
      tokens.push({ type: "string", start: i, end, quote: c, value: cook(source.slice(i + 1, end - 1)) });
      i = end;
      continue;
    }
    if (c === "`") {
      const template = scanTemplate(source, i);
      tokens.push({ type: "template", start: i, end: template.end, quasis: template.quasis,
        expressions: template.expressions });
      i = template.end;
      continue;
    }
    if (/[\p{L}_$]/u.test(c) || (c === "\\" && source[i + 1] === "u")) {
      let end = i + 1;
      while (end < source.length && /[\p{L}\p{N}_$‌‍]/u.test(source[end])) end += 1;
      tokens.push({ type: "name", start: i, end, value: source.slice(i, end) });
      i = end;
      continue;
    }
    if (/\d/.test(c) || (c === "." && /\d/.test(source[i + 1]))) {
      const match = /^(?:0[xob][\da-f_]+n?|(?:\d[\d_]*\.?[\d_]*|\.\d[\d_]*)(?:e[+-]?\d+)?n?)/i.exec(source.slice(i));
      const end = i + match[0].length;
      tokens.push({ type: "number", start: i, end, value: match[0] });
      i = end;
      continue;
    }
    if (c === "/" && regexAllowed(previous())) {
      const end = scanRegex(source, i);
      tokens.push({ type: "regex", start: i, end, value: source.slice(i, end) });
      i = end;
      continue;
    }
    const punctuator = PUNCTUATORS.find(candidate => source.startsWith(candidate, i));
    if (!punctuator) throw new SyntaxError(`Unexpected character ${JSON.stringify(c)} at ${i}`);
    if (stopAtBrace) {
      if (punctuator === "{") depth += 1;
      if (punctuator === "}") {
        if (depth === 0) return { tokens, end: i };
        depth -= 1;
      }
    }
    tokens.push({ type: "punct", start: i, end: i + punctuator.length, value: punctuator });
    i += punctuator.length;
  }
  if (stopAtBrace) throw new SyntaxError(`Unterminated template expression at ${start}`);
  return { tokens, end: i };
}

export function lineOf(source, offset) {
  let line = 1;
  for (let i = 0; i < offset; i += 1) if (source[i] === "\n") line += 1;
  return line;
}

const OPEN = { ")": "(", "]": "[", "}": "{" };
const CLOSE = { "(": ")", "[": "]", "{": "}" };
const GENERIC_METHODS = new Set(["toLocaleLowerCase", "toLowerCase", "toLocaleUpperCase", "toUpperCase", "trim",
  "trimEnd", "trimStart", "toString", "slice", "join", "toFixed", "padStart", "padEnd", "format", "at",
  "toLocaleString", "toLocaleDateString", "toLocaleTimeString", "formatToParts"]);
const LOCALE_CALLS = new Set(["DateTimeFormat", "NumberFormat", "Collator", "PluralRules", "RelativeTimeFormat",
  "ListFormat", "DisplayNames", "Segmenter", "toLocaleString", "toLocaleDateString", "toLocaleTimeString",
  "toLocaleLowerCase", "toLocaleUpperCase", "localeCompare"]);
const COMPARISON = new Set(["===", "!==", "==", "!="]);
const MATCH_METHODS = new Set(["includes", "startsWith", "endsWith", "indexOf", "lastIndexOf", "split", "replace",
  "replaceAll", "match", "matchAll", "search", "test", "localeCompare"]);

function matchingForward(tokens, index) {
  const open = tokens[index].value, close = CLOSE[open];
  let depth = 0;
  for (let k = index; k < tokens.length; k += 1) {
    if (tokens[k].type !== "punct") continue;
    if (tokens[k].value === open) depth += 1;
    else if (tokens[k].value === close && --depth === 0) return k;
  }
  return -1;
}

function matchingBackward(tokens, index) {
  const close = tokens[index].value, open = OPEN[close];
  let depth = 0;
  for (let k = index; k >= 0; k -= 1) {
    if (tokens[k].type !== "punct") continue;
    if (tokens[k].value === close) depth += 1;
    else if (tokens[k].value === open && --depth === 0) return k;
  }
  return -1;
}

const isPrimary = token => token && (["name", "number", "string", "template"].includes(token.type));

/** Index of the last token of a simple operand that starts at `index`, or -1. */
function operandEnd(tokens, index) {
  let k = index;
  const first = tokens[k];
  if (!first) return -1;
  if (first.type === "punct" && (first.value === "(" || first.value === "[")) {
    k = matchingForward(tokens, k);
    if (k < 0) return -1;
  } else if (first.type === "name" && ["new", "typeof", "await"].includes(first.value)) {
    return operandEnd(tokens, k + 1);
  } else if (!isPrimary(first) || (first.type === "name" && REGEX_AFTER_WORDS.has(first.value))) {
    return -1;
  }
  for (;;) {
    const next = tokens[k + 1];
    if (next?.type === "punct" && (next.value === "." || next.value === "?.")) {
      const after = tokens[k + 2];
      if (after?.type === "name") { k += 2; continue; }
      if (after?.type === "punct" && (after.value === "(" || after.value === "[")) {
        k = matchingForward(tokens, k + 2);
        if (k < 0) return -1;
        continue;
      }
      return -1;
    }
    if (next?.type === "punct" && (next.value === "(" || next.value === "[")) {
      k = matchingForward(tokens, k + 1);
      if (k < 0) return -1;
      continue;
    }
    if (next?.type === "template") { k += 1; continue; }
    return k;
  }
}

/** Index of the first token of a simple operand that ends at `index`, or -1. */
function operandStart(tokens, index) {
  let k = index;
  for (;;) {
    const token = tokens[k];
    if (!token) return -1;
    if (token.type === "punct" && (token.value === ")" || token.value === "]")) {
      k = matchingBackward(tokens, k);
      if (k < 0) return -1;
      const before = tokens[k - 1];
      if (before && (before.type === "name" && !REGEX_AFTER_WORDS.has(before.value) || before.value === ")"
          || before.value === "]" || before.value === "?.")) {
        k -= before.value === "?." ? 2 : 1;
        continue;
      }
      return k;
    }
    if (!isPrimary(token) || (token.type === "name" && REGEX_AFTER_WORDS.has(token.value))) return -1;
    const before = tokens[k - 1];
    if (before?.type === "punct" && (before.value === "." || before.value === "?.")) {
      k -= 2;
      continue;
    }
    return k;
  }
}

function placeholderName(tokens, start, end) {
  const slice = tokens.slice(start, end + 1);
  if (slice.length === 1 && slice[0].type === "name") return slice[0].value;
  const names = slice.filter(token => token.type === "name");
  const last = slice[slice.length - 1];
  if (last.type === "punct" && last.value === ")") {
    const opener = matchingBackward(slice, slice.length - 1);
    const callee = slice[opener - 1];
    if (callee?.type === "name" && !GENERIC_METHODS.has(callee.value) && callee.value !== "Date") return callee.value;
    const receiver = slice.slice(0, Math.max(opener - 1, 0))
      .filter(token => token.type === "name" && !["new", "Date"].includes(token.value));
    if (receiver.length) return receiver[receiver.length - 1].value;
    if (slice.some(token => token.value === "Date")) return "time";
  }
  if (last.type === "name") return last.value;
  if (last.type === "punct" && last.value === "]") {
    const opener = matchingBackward(slice, slice.length - 1);
    const owner = slice[opener - 1];
    if (owner?.type === "name") return owner.value;
  }
  return names.length ? names[names.length - 1].value : "value";
}

function jsString(text) {
  return JSON.stringify(text);
}

function escapeMessage(text) {
  return text.replace(/[{}]/g, brace => brace + brace);
}

function isSkippedContext(tokens, first, last) {
  const before = tokens[first - 1], after = tokens[last + 1];
  if (after?.type === "punct" && after.value === ":" && before?.type === "punct" && ["{", ","].includes(before.value)) {
    return "object-key";
  }
  if (before?.type === "name" && before.value === "case") return "case-label";
  if (before?.type === "punct" && COMPARISON.has(before.value) || after?.type === "punct" && COMPARISON.has(after.value)) {
    return "comparison";
  }
  if (before?.type === "punct" && before.value === "(") {
    const callee = tokens[first - 2];
    if (callee?.type === "name" && (callee.value === "t" || callee.value === "tn")) return "translated";
    if (callee?.type === "name" && MATCH_METHODS.has(callee.value) && tokens[first - 3]?.value === ".") return "comparison";
    if (callee?.type === "name" && tokens[first - 3]?.value === "." && tokens[first - 4]?.value === "console") {
      return "console";
    }
  }
  return null;
}

/**
 * Rewrite one token list (a module or one template expression).
 * Returns replacements [{start, end, text}] and report entries.
 */
function localeCallee(significant, index) {
  let depth = 0;
  for (let k = index - 1; k >= 0; k -= 1) {
    const item = significant[k];
    if (item.type !== "punct") continue;
    if (item.value === ")" || item.value === "]" || item.value === "}") depth += 1;
    else if (item.value === "(" || item.value === "[" || item.value === "{") {
      if (depth === 0) return item.value === "(" ? significant[k - 1] : null;
      depth -= 1;
    } else if (depth === 0 && [";", "=", "=>"].includes(item.value)) return null;
  }
  return null;
}

function planTokens(source, tokens, file) {
  const significant = tokens.filter(token => token.type !== "comment");
  const replacements = [], report = [], helpers = new Set();
  const consumed = new Set();
  const record = (kind, start, extra = {}) => report.push({ file, line: lineOf(source, start), kind, ...extra });
  const merge = inner => { report.push(...inner.report); inner.helpers.forEach(helper => helpers.add(helper)); };
  for (let index = 0; index < significant.length; index += 1) {
    const token = significant[index];
    if (consumed.has(index)) continue;
    if (token.type === "string" && /^(?:ru|ru-RU)$/.test(token.value)) {
      const callee = localeCallee(significant, index);
      if (callee?.type === "name" && LOCALE_CALLS.has(callee.value)) {
        const helper = token.value === "ru" ? "locale" : "localeTag";
        helpers.add(helper);
        replacements.push({ start: token.start, end: token.end, text: `${helper}()` });
        record("locale-call", token.start, { snippet: `${token.value} → ${helper}()` });
      } else {
        record("locale-code", token.start, { snippet: source.slice(token.start, token.end) });
      }
      continue;
    }
    const russianTemplate = token.type === "template" && token.quasis.some(quasi => CYRILLIC.test(quasi.raw));
    const russian = (token.type === "string" && CYRILLIC.test(token.value)) || russianTemplate;
    if (token.type === "template" && !russian) {
      for (const expression of token.expressions) {
        const inner = planTokens(source, expression.tokens, file);
        replacements.push(...inner.replacements);
        merge(inner);
      }
      continue;
    }
    if (token.type === "regex" && CYRILLIC.test(token.value)) { record("regex", token.start, { snippet: token.value }); continue; }
    if (!russian) continue;
    // Grow a `+` chain around the literal.
    let first = index, last = index;
    for (;;) {
      const plus = significant[first - 1];
      if (plus?.type !== "punct" || plus.value !== "+") break;
      const start = operandStart(significant, first - 2);
      if (start < 0) break;
      first = start;
    }
    for (;;) {
      const plus = significant[last + 1];
      if (plus?.type !== "punct" || plus.value !== "+") break;
      const end = operandEnd(significant, last + 2);
      if (end < 0) break;
      last = end;
    }
    const skipped = isSkippedContext(significant, first, last);
    if (skipped) {
      if (skipped !== "translated") record(skipped, token.start, { snippet: source.slice(significant[first].start, significant[last].end) });
      for (let k = first; k <= last; k += 1) consumed.add(k);
      continue;
    }
    // Split the chain into operands at top-level `+`.
    const operands = [];
    let cursor = first;
    while (cursor <= last) {
      const end = significant[cursor].type === "string" || significant[cursor].type === "template"
        ? (operandEnd(significant, cursor) === cursor ? cursor : operandEnd(significant, cursor))
        : operandEnd(significant, cursor);
      operands.push([cursor, end]);
      cursor = end + 2;
    }
    let message = "";
    const params = [];
    const names = new Map();
    const addParam = (name, expression) => {
      let unique = name.replace(/[^A-Za-z0-9_$]/g, "") || "value";
      if (/^\d/.test(unique)) unique = "value";
      for (let n = 2; names.has(unique) && names.get(unique) !== expression; n += 1) unique = `${name}${n}`;
      names.set(unique, expression);
      if (!params.some(param => param.name === unique)) params.push({ name: unique, expression });
      return `{${unique}}`;
    };
    for (const [start, end] of operands) {
      const operand = significant[start];
      if (start === end && operand.type === "string") {
        message += escapeMessage(operand.value);
      } else if (start === end && operand.type === "template") {
        operand.quasis.forEach((quasi, k) => {
          message += escapeMessage(cook(quasi.raw));
          const expression = operand.expressions[k];
          if (!expression) return;
          const inner = planTokens(source, expression.tokens, file);
          merge(inner);
          const text = applyReplacements(source, expression.start, expression.end, inner.replacements).trim();
          const innerTokens = expression.tokens.filter(item => item.type !== "comment");
          const name = innerTokens.length ? placeholderName(innerTokens, 0, innerTokens.length - 1) : "value";
          message += addParam(name, text);
        });
      } else {
        const inner = planTokens(source, tokens.filter(item => item.start >= significant[start].start
          && item.end <= significant[end].end), file);
        merge(inner);
        message += addParam(placeholderName(significant, start, end),
          applyReplacements(source, significant[start].start, significant[end].end, inner.replacements));
      }
    }
    for (let k = first; k <= last; k += 1) consumed.add(k);
    const args = params.map(({ name, expression }) => (expression === name ? name : `${name}: ${expression}`));
    const call = args.length ? `t(${jsString(message)}, {${args.join(", ")}})` : `t(${jsString(message)})`;
    replacements.push({ start: significant[first].start, end: significant[last].end, text: call });
    helpers.add("t");
    record(operands.length > 1 ? "concatenation" : token.type === "template" ? "template" : "literal", token.start,
      { message, params: params.map(param => param.name) });
  }
  return { replacements, report, helpers };
}

export function applyReplacements(source, start, end, replacements) {
  let text = source.slice(start, end);
  const inside = replacements.filter(item => item.start >= start && item.end <= end)
    .sort((a, b) => b.start - a.start);
  inside.forEach((item, index) => {
    if (index && item.end > inside[index - 1].start) throw new Error(`Overlapping i18n replacements at ${item.start}`);
  });
  for (const item of inside) {
    text = text.slice(0, item.start - start) + item.text + text.slice(item.end - start);
  }
  return text;
}

function addImport(source, importPath, names = ["t"]) {
  const existing = new RegExp(`import\\s*\\{([^}]*)\\}\\s*from\\s*["']${importPath.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}["'];?`);
  const found = existing.exec(source);
  if (found) {
    const current = found[1].split(",").map(name => name.trim()).filter(Boolean);
    const merged = [...new Set([...current, ...names])].sort();
    if (merged.length === current.length) return source;
    return source.replace(found[0], `import { ${merged.join(", ")} } from "${importPath}";`);
  }
  const line = `import { ${[...new Set(names)].sort().join(", ")} } from "${importPath}";\n`;
  const imports = [...source.matchAll(/^import[\s\S]*?from\s*["'][^"']+["'];?[^\n]*\n/gm)];
  if (imports.length) {
    const lastImport = imports[imports.length - 1];
    const at = lastImport.index + lastImport[0].length;
    return source.slice(0, at) + line + source.slice(at);
  }
  return line + source;
}

/**
 * Transform one module. Returns {text, messages, report, changed}.
 * `messages` lists every message id the file uses after the rewrite.
 */
export function transformJs(source, { file = "module.js", importPath = "./i18n.js", addImports = true } = {}) {
  const { tokens } = tokenize(source);
  const { replacements, report, helpers } = planTokens(source, tokens, file);
  let text = applyReplacements(source, 0, source.length, replacements);
  if (helpers.size && addImports) text = addImport(text, importPath, [...helpers]);
  const messages = report.filter(entry => entry.message !== undefined).map(entry => entry.message);
  return { text, messages, report, changed: text !== source };
}

/** Message ids already wrapped as t("…") in a module (used by the checker). */
export function usedMessages(source) {
  const { tokens } = tokenize(source);
  const found = [];
  const visit = list => {
    const significant = list.filter(token => token.type !== "comment");
    significant.forEach((token, index) => {
      if (token.type === "template") token.expressions.forEach(expression => visit(expression.tokens));
      const callee = significant[index - 2], open = significant[index - 1];
      if (token.type === "string" && open?.value === "(" && callee?.type === "name" && ["t", "tn"].includes(callee.value)) {
        found.push(token.value);
      }
    });
  };
  visit(tokens);
  return found;
}

/** Russian literals that are still not wrapped (for the checker). */
export function untranslated(source, file) {
  return transformJs(source, { file, addImports: false }).report
    .filter(entry => ["literal", "template", "concatenation"].includes(entry.kind));
}
