// Reading a Telegram Web chat page (web.telegram.org/k/): which chat it is and which messages
// are loaded. A classic script for the content script and the Node tests: it sets
// globalThis.BrainalotTg and globalThis.BrainalotChatParser. Checked on the real page on
// 2026-09-29: each message is a .bubble with data-mid, data-peer-id and data-timestamp; the
// author's name is shown on the first bubble of a group; is-out marks the user's own messages.
(function (root) {
  /** The chat id from the address: web.telegram.org/k/#-5230437713 (also /a/#…). */
  function peer(location) {
    const match = String(location.hash || "").match(/^#(-?\d{1,20})(?:$|[^\d])/);
    return match ? match[1] : null;
  }

  function clean(text) {
    return String(text || "").replace(/ /g, " ").replace(/[ \t]+\n/g, "\n").trim();
  }

  function title(doc) {
    const header = doc.querySelector(".chat-info .peer-title, .chat .top .peer-title");
    return clean(header?.textContent || doc.title).slice(0, 200);
  }

  function linksIn(node) {
    const found = [];
    for (const a of node.querySelectorAll("a[href]")) {
      const href = a.getAttribute("href") || "";
      if (/^https?:\/\//i.test(href) && !/^https?:\/\/web\.telegram\.org\//i.test(href) && !found.includes(href)) found.push(href);
    }
    return found;
  }

  function filesIn(bubble) {
    const files = [];
    for (const name of bubble.querySelectorAll(".document-name")) {
      const text = clean(name.textContent);
      if (text) files.push({ name: text.slice(0, 255), kind: "file" });
    }
    if (!files.length && bubble.classList.contains("photo")) files.push({ name: "photo.jpg", kind: "photo" });
    return files.slice(0, 20);
  }

  const NAME = ".name .peer-title-inner, .name .peer-title";

  function authorOf(bubble) {
    if (bubble.classList.contains("is-out")) return "me";
    const own = bubble.querySelector(NAME);
    if (own) return clean(own.textContent) || "?";
    // Later bubbles of a group hide the name: take it from the group's first one.
    return clean(bubble.closest(".bubbles-group")?.querySelector(NAME)?.textContent) || "?";
  }

  function textOf(bubble) {
    const node = bubble.querySelector(".message .translatable-message");
    if (node) return clean(node.innerText ?? node.textContent);
    const message = bubble.querySelector(".message");
    if (!message) return "";
    const copy = message.cloneNode(true);
    copy.querySelectorAll(".time, reactions-element, .reactions, .bubble-tail").forEach(n => n.remove());
    return clean(copy.innerText ?? copy.textContent);
  }

  /** ``only`` keeps one chat: Telegram Web may still hold another chat's bubbles in the page. */
  function messages(doc, _base, only = null) {
    const found = [];
    for (const bubble of doc.querySelectorAll(".bubble[data-mid][data-timestamp]:not(.service)")) {
      if (only && bubble.getAttribute("data-peer-id") !== only) continue;
      const id = bubble.getAttribute("data-mid");
      const ts = Number(bubble.getAttribute("data-timestamp"));
      if (!/^\d{1,20}$/.test(id || "") || !ts) continue;
      const text = textOf(bubble);
      const links = linksIn(bubble.querySelector(".message") || bubble);
      const files = filesIn(bubble);
      if (!text && !links.length && !files.length) continue;
      const chat = bubble.getAttribute("data-peer-id");
      found.push({ id, date: new Date(ts * 1000).toISOString(), author: authorOf(bubble).slice(0, 200), text: text.slice(0, 20000),
        from_me: bubble.classList.contains("is-out"), links, files,
        ...(/^-?\d{1,20}$/.test(chat || "") ? { link: `https://web.telegram.org/k/#${chat}` } : {}) });
    }
    return found;
  }

  const api = { source: "tgweb", peer, title, messages };
  api.skeleton = (doc, location) => root.BrainalotSkeleton.build(doc, location, api);
  root.BrainalotTg = api;
  root.BrainalotChatParser = api;
})(globalThis);
