// Reading a VK conversation page: which conversation it is and which messages are loaded.
// A classic script (a content script cannot import modules): it sets globalThis.BrainalotVk
// and globalThis.BrainalotChatParser, used by chat-reader.js and by the Node tests. VK changes
// its markup from time to time; every selector lives here so the reader is repaired in one place.
(function (root) {
  const CHAT_PEER_BASE = 2000000000;

  /** The peer id of the open conversation, or null: /im/convo/2000000007 or /im?sel=c7. */
  function peer(location) {
    const path = String(location.pathname || "");
    const convo = path.match(/^\/im\/convo\/(-?\d{1,20})/);
    if (convo) return convo[1];
    const selected = new URLSearchParams(location.search || "").get("sel");
    if (!selected) return null;
    const chat = selected.match(/^c(\d{1,12})$/);
    if (chat) return String(CHAT_PEER_BASE + Number(chat[1]));
    return /^-?\d{1,20}$/.test(selected) ? selected : null;
  }

  function clean(text) {
    return String(text || "").replace(/ /g, " ").replace(/[ \t]+\n/g, "\n").trim();
  }

  function title(doc) {
    const header = doc.querySelector("[data-testid='convo_header_title'], .ConvoHeader__title, ._im_page_peer_name, .im-page--title-main-inner");
    return clean(header?.textContent || doc.title).slice(0, 200);
  }

  /** VK sends outgoing links through /away.php?to=…; keep the real address. */
  function realUrl(href, base) {
    try {
      const url = new URL(href, base);
      if (url.hostname.endsWith("vk.com") && url.pathname === "/away.php") return url.searchParams.get("to") || "";
      return /^https?:$/.test(url.protocol) ? url.href : "";
    } catch {
      return "";
    }
  }

  function linksIn(node, base) {
    const found = [];
    for (const a of node.querySelectorAll("a[href]")) {
      const url = realUrl(a.getAttribute("href"), base);
      if (url && !/^https?:\/\/(m\.)?vk\.com\/(id\d+|im|away\.php)/.test(url) && !found.includes(url)) found.push(url);
    }
    return found;
  }

  function filesIn(node) {
    const files = [];
    for (const doc of node.querySelectorAll(".page_doc_title, .attach_doc_title, [data-testid='attach_doc_title']")) {
      const name = clean(doc.textContent);
      if (name) files.push({ name: name.slice(0, 255), kind: "file" });
    }
    const photos = node.querySelectorAll(".page_post_thumb_wrap, .im_msg_media_photo, [data-testid='attach_photo']").length;
    for (let i = 0; i < photos; i++) files.push({ name: `photo-${i + 1}.jpg`, kind: "photo" });
    return files.slice(0, 20);
  }

  /** Messages of the classic markup: .im-mess with data-msgid and data-ts inside an author stack. */
  function messages(doc, base = "https://vk.com/") {
    const found = [];
    for (const node of doc.querySelectorAll(".im-mess[data-msgid]")) {
      const id = node.getAttribute("data-msgid");
      const ts = Number(node.getAttribute("data-ts"));
      if (!/^\d{1,20}$/.test(id || "") || !ts) continue;
      const stack = node.closest(".im-mess-stack");
      const author = clean(stack?.querySelector(".im-mess-stack--lnk")?.textContent) || "?";
      const textNode = node.querySelector(".im-mess--text") || node;
      const text = clean(textNode.innerText ?? textNode.textContent);
      const links = linksIn(textNode, base);
      const files = filesIn(node);
      if (!text && !links.length && !files.length) continue;
      found.push({ id, date: new Date(ts * 1000).toISOString(), author: author.slice(0, 200), text: text.slice(0, 20000),
        from_me: node.classList.contains("im-mess_out"), links, files });
    }
    return found;
  }

  const api = { source: "vk", peer, title, messages, realUrl };
  api.skeleton = (doc, location) => root.BrainalotSkeleton.build(doc, location, api);
  root.BrainalotVk = api;
  root.BrainalotChatParser = api;
})(globalThis);
