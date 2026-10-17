// Markup skeleton of a chat page, for repairing a parser when the site changes its markup.
// A classic script shared by the VK and Telegram Web content scripts: it sets
// globalThis.BrainalotSkeleton. Only structure leaves the page: tags, classes, roles and
// id-like data attributes; texts, names and addresses are replaced by a length or a placeholder.
(function (root) {
  const ID_LIKE = /^[\w:.-]{1,48}$/;
  const TIME = /^\d{1,2}:\d{2}$/;

  function shape(el, budget, depth) {
    if (budget.left <= 0 || depth > 30) return null;
    budget.left -= 1;
    const node = { t: el.tagName.toLowerCase() };
    if (el.classList.length) node.c = [...el.classList].slice(0, 8).join(" ").slice(0, 160);
    const attrs = {};
    for (const a of el.attributes) {
      if (a.name === "class" || a.name === "style") continue;
      if (a.name.startsWith("data-") || a.name === "role" || a.name === "id") attrs[a.name] = ID_LIKE.test(a.value) ? a.value : `<${a.value.length}>`;
      else if (a.name === "href") attrs.href = a.value.includes("away.php") ? "away" : a.value.startsWith("/im") ? "/im" : a.value.startsWith("#") ? "#" : "<url>";
      else if (a.name === "datetime") attrs.datetime = ID_LIKE.test(a.value) ? a.value : `<${a.value.length}>`;
      else if (a.name.startsWith("aria-") || a.name === "title" || a.name === "alt") attrs[a.name] = "<text>";
    }
    if (Object.keys(attrs).length) node.a = attrs;
    const own = [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent.trim()).filter(Boolean).join(" ");
    if (own) node.x = TIME.test(own) ? "HH:MM" : own.length;
    const children = [...el.children];
    // Long lists repeat one pattern: the first and last few items are enough.
    const kept = children.length > 16 ? [...children.slice(0, 10), ...children.slice(-4)] : children;
    const kids = kept.map(child => shape(child, budget, depth + 1)).filter(Boolean);
    if (kids.length) node.k = kids;
    if (kept.length < children.length) node.more = children.length - kept.length;
    return node;
  }

  function describe(el) {
    const cls = [...el.classList].slice(0, 4).join(".");
    return el.tagName.toLowerCase() + (cls ? `.${cls}` : "") + (el.getAttribute("role") ? `[role=${el.getAttribute("role")}]` : "");
  }

  /** The history container: where most message times are, in the right part of the page. */
  function historyContainer(doc) {
    const view = doc.defaultView;
    const width = view?.innerWidth || 1200;
    const times = [...doc.querySelectorAll("body *")].filter(el => el.children.length === 0 && TIME.test((el.textContent || "").trim())
      && el.getBoundingClientRect().left > width * 0.3);
    if (times.length >= 3) {
      let box = times[0].parentElement;
      while (box && !times.every(el => box.contains(el))) box = box.parentElement;
      if (box) return box;
    }
    const probe = doc.elementFromPoint?.(width * 0.65, (view?.innerHeight || 800) * 0.5);
    let best = probe;
    for (let el = probe; el && el !== doc.body; el = el.parentElement) if (el.children.length >= 5) { best = el; break; }
    return best || doc.body;
  }

  /** ``parser`` is BrainalotVk or BrainalotTg: its own view of the address and the messages. */
  function build(doc, location, parser) {
    const container = historyContainer(doc);
    const path = [];
    for (let el = container; el && el !== doc.documentElement; el = el.parentElement) path.push(describe(el));
    return {
      source: parser.source,
      path: location.pathname.replace(/[^\w/.-]/g, ""),
      query: [...new URLSearchParams(location.search).keys()],
      hash: location.hash ? location.hash.replace(/[^#@\w-]/g, "").slice(0, 40) : "",
      peer: parser.peer(location),
      parsed: parser.messages(doc).length,
      ancestors: path.slice(0, 25),
      tree: shape(container, { left: 900 }, 0),
    };
  }

  root.BrainalotSkeleton = { build };
})(globalThis);
