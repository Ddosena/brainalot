// Work chat reader, run in vk.com and web.telegram.org tabs after the user allows the site in
// the Work mode. The page's parser (vk-parse.js or tg-parse.js) sets BrainalotChatParser; the
// reader sends the messages of a watched chat to Brainalot while the tab is open. The service
// keeps only messages after the moment the chat was connected. Reads, never clicks or sends.
(function () {
  const parser = globalThis.BrainalotChatParser;
  if (globalThis.__brainalotChatReader || !parser) return;
  globalThis.__brainalotChatReader = true;
  let peer = null;
  let watched = null;
  let sent = new Set();
  let timer = null;
  let busy = false;
  let lastAsk = 0;
  let emptyRuns = 0;
  let markupSent = false;
  const ASK_EVERY_MS = 15000;

  // The parser recognizes nothing on a watched chat: send its structure once, so an agent can
  // repair the parser (see docs/chats-agent-runbook.md). No texts leave the page.
  function sendMarkup() {
    if (markupSent) return;
    markupSent = true;
    chrome.runtime.sendMessage({ type: "chat-markup", markup: parser.skeleton(document, location) }).catch(() => {});
  }

  async function post(messages, oldest) {
    try {
      const reply = await chrome.runtime.sendMessage({ type: "chat-messages", chat: { peer_id: peer, title: parser.title(document) },
        messages, oldest_loaded: oldest || undefined });
      return reply?.ok ? reply.data : null;
    } catch {
      return null; // extension reloaded or service offline: try again on the next change
    }
  }

  async function check() {
    const next = parser.peer(location);
    if (next !== peer) {
      peer = next;
      watched = null;
      sent = new Set();
      lastAsk = 0;
      emptyRuns = 0;
    }
    if (!peer || watched !== null || Date.now() - lastAsk < ASK_EVERY_MS) return;
    lastAsk = Date.now();
    const answer = await post([], null);
    watched = answer ? answer.watched === true : null;
    if (watched) schedule();
  }

  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(flush, 1200);
  }

  async function flush() {
    if (!watched || busy) return;
    const loaded = parser.messages(document, location.href, peer);
    if (!loaded.length) {
      if (++emptyRuns >= 3) sendMarkup();
      return;
    }
    emptyRuns = 0;
    const fresh = loaded.filter(m => !sent.has(`${m.id}|${m.text}`));
    if (!fresh.length) return;
    const oldest = loaded.reduce((min, m) => (m.date < min ? m.date : min), loaded[0].date);
    busy = true;
    try {
      const answer = await post(fresh.slice(0, 200), oldest);
      if (answer?.watched === false) watched = false;
      else if (answer) fresh.slice(0, 200).forEach(m => sent.add(`${m.id}|${m.text}`));
    } finally {
      busy = false;
    }
    if (fresh.length > 200) schedule();
  }

  chrome.runtime.onMessage.addListener((message, sender, respond) => {
    if (sender.id !== chrome.runtime.id) return false;
    if (message?.type === "brainalot-chat-info") {
      const here = parser.peer(location);
      respond({ peer_id: here, title: parser.title(document), parsed: parser.messages(document, location.href, here).length });
    } else if (message?.type === "brainalot-chat-skeleton") {
      respond(parser.skeleton(document, location));
    } else if (message?.type === "brainalot-chat-refresh") {
      watched = null;
      lastAsk = 0;
      check();
      respond({ ok: true });
    }
    return false;
  });

  addEventListener("hashchange", check);
  new MutationObserver(() => {
    if (parser.peer(location) !== peer) check();
    else if (watched) schedule();
    else if (watched === null) check();
  }).observe(document.documentElement, { childList: true, subtree: true });
  check();
})();
