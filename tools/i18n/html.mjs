// HTML side of the Brainalot i18n codemod.
//
// Elements whose own text is Russian get `data-i18n="<message id>"`; Russian
// title/placeholder/aria-label/alt/label attributes are listed in
// `data-i18n-attr`. The Russian text stays in the page as the fallback, and
// `applyDocument()` from extension/i18n.js swaps in the current language.

import { CYRILLIC } from "./js.mjs";

export const TRANSLATED_ATTRIBUTES = ["title", "placeholder", "aria-label", "alt", "label", "aria-description",
  "aria-roledescription", "aria-valuetext"];
const VOID = new Set(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track",
  "wbr", "param"]);
const RAW = new Set(["script", "style", "textarea"]);
const ENTITIES = { nbsp: " ", laquo: "«", raquo: "»", mdash: "—", ndash: "–", hellip: "…", amp: "&", lt: "<",
  gt: ">", quot: "\"", apos: "'", minus: "−", times: "×", middot: "·", rarr: "→", larr: "←", thinsp: " " };

export function decodeEntities(text) {
  return text.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (entity, name) => {
    if (name[0] === "#") return String.fromCodePoint(name[1].toLowerCase() === "x" ? parseInt(name.slice(2), 16) : Number(name.slice(1)));
    return ENTITIES[name.toLowerCase()] ?? entity;
  });
}

export function encodeAttribute(text) {
  return text.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/ /g, "&nbsp;");
}

export function normalizeText(text) {
  return decodeEntities(text).replace(/[ \t\r\n]+/g, " ").trim();
}

function lineOf(source, offset) {
  return source.slice(0, offset).split("\n").length;
}

export function parseAttributes(text) {
  const attributes = [];
  for (const match of text.matchAll(/([^\s"'<>/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g)) {
    attributes.push({ name: match[1].toLowerCase(), value: decodeEntities(match[2] ?? match[3] ?? match[4] ?? "") });
  }
  return attributes;
}

/** Transform one HTML document. Returns {text, messages, report, changed}. */
export function transformHtml(source, { file = "page.html" } = {}) {
  const insertions = [], messages = [], report = [];
  const stack = [];
  const tag = /<!--[\s\S]*?-->|<(\/?)([a-zA-Z][\w-]*)((?:[^>"']|"[^"]*"|'[^']*')*?)(\/?)>/g;
  let cursor = 0, match;
  const addText = (text, at) => {
    const top = stack[stack.length - 1];
    if (top && text.trim()) top.text.push({ text, at });
  };
  while ((match = tag.exec(source))) {
    addText(source.slice(cursor, match.index), cursor);
    cursor = tag.lastIndex;
    if (match[0].startsWith("<!--")) continue;
    const [, closing, rawName, attributeText, selfClosing] = match;
    const name = rawName.toLowerCase();
    if (!closing) {
      const attributes = parseAttributes(attributeText);
      const element = { name, attributes, text: [], children: 0, end: match.index + match[0].length - (selfClosing ? 2 : 1),
        start: match.index };
      const translatable = attributes.filter(attribute => TRANSLATED_ATTRIBUTES.includes(attribute.name)
        && CYRILLIC.test(attribute.value));
      const listed = attributes.find(attribute => attribute.name === "data-i18n-attr");
      if (translatable.length) {
        const known = new Set((listed?.value || "").split(",").map(item => item.trim()).filter(Boolean));
        const missing = translatable.filter(attribute => !known.has(attribute.name));
        for (const attribute of translatable) messages.push(normalizeText(attribute.value));
        if (missing.length && !listed) {
          insertions.push({ at: element.end, text: ` data-i18n-attr="${missing.map(item => item.name).join(",")}"` });
        } else if (missing.length) {
          report.push({ file, line: lineOf(source, match.index), kind: "attribute-list", snippet: match[0] });
        }
      }
      if (stack.length) stack[stack.length - 1].children += 1;
      if (RAW.has(name)) {
        const close = source.indexOf(`</${name}`, cursor);
        cursor = close === -1 ? source.length : close;
        tag.lastIndex = cursor;
        stack.push(element);
        continue;
      }
      if (!VOID.has(name) && !selfClosing) stack.push(element);
      continue;
    }
    const index = stack.map(item => item.name).lastIndexOf(name);
    if (index === -1) continue;
    const [element] = stack.splice(index);
    const text = element.text.map(part => part.text).join("");
    if (RAW.has(element.name) || !CYRILLIC.test(text)) continue;
    const message = normalizeText(text);
    const already = element.attributes.find(attribute => attribute.name === "data-i18n");
    if (element.children) {
      // «<label>Тип записи<select>…» — wrap each Russian text run in its own span.
      for (const part of element.text) {
        if (!CYRILLIC.test(part.text)) continue;
        const lead = part.text.length - part.text.trimStart().length;
        const body = part.text.trim();
        const runMessage = normalizeText(body);
        messages.push(runMessage);
        insertions.push({ at: part.at + lead, text: `<span data-i18n="${encodeAttribute(runMessage)}">` });
        insertions.push({ at: part.at + lead + body.length, text: "</span>", after: true });
        report.push({ file, line: lineOf(source, part.at), kind: "wrapped-text", snippet: runMessage });
      }
      continue;
    }
    messages.push(already?.value || message);
    if (!already) insertions.push({ at: element.end, text: ` data-i18n="${encodeAttribute(message)}"` });
  }
  let text = source;
  // Later offsets first. At one offset the last applied lands first, so a closing
  // tag is applied after any opening one to stay in front of it.
  for (const insertion of insertions.sort((a, b) => b.at - a.at || (a.after ? 1 : -1) - (b.after ? 1 : -1))) {
    text = text.slice(0, insertion.at) + insertion.text + text.slice(insertion.at);
  }
  return { text, messages, report, changed: text !== source };
}
