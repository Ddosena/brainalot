"""Work chats: messages collected from Telegram and a VK conversation for agents to sort.

Messages live in ``state/chats.sqlite3``, apart from the library: they are raw material, not
notes. A chat is connected with a baseline, and only messages after it are kept ("только новые").
Sorting is on request: the user asks Codex or Claude in their chat, the agent reads the unsorted
messages (``mmm chats new``), proposes tasks to the Work mode (``mmm work propose``) and marks
the batch it has read (``mmm chats done --batch``). An edited message comes back as unsorted.
Proposals are kept here too; ``work_inbox`` validates them against the Work page snapshot.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid

from filelock import FileLock

from .core import NotFoundError, Store, ValidationError

SOURCES = ("telegram", "vk", "tgweb")
# Chats read from an open browser tab: VK conversations and Telegram Web (tgweb) chats.
WEB_PEERS = {"vk": re.compile(r"\d{1,20}"), "tgweb": re.compile(r"-?\d{1,20}")}
URL_RE = re.compile(r"https?://[^\s<>\"'«»]+")
ID_RE = re.compile(r"^[\w.:-]{1,64}$")
TEXT_LIMIT = 20000
MESSAGE_BATCH_LIMIT = 500
CONTEXT_MESSAGES = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    key TEXT PRIMARY KEY, source TEXT NOT NULL, chat_id TEXT NOT NULL, title TEXT NOT NULL,
    baseline TEXT NOT NULL, cursor TEXT, added TEXT NOT NULL, synced TEXT,
    enabled INTEGER NOT NULL DEFAULT 1, gap INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS messages (
    chat TEXT NOT NULL, message_id TEXT NOT NULL, date TEXT NOT NULL,
    author TEXT NOT NULL, from_me INTEGER NOT NULL DEFAULT 0, mentions_me INTEGER NOT NULL DEFAULT 0,
    text TEXT NOT NULL, reply_to TEXT, link TEXT,
    links TEXT NOT NULL DEFAULT '[]', files TEXT NOT NULL DEFAULT '[]',
    version TEXT NOT NULL, received TEXT NOT NULL, edited TEXT, processed TEXT,
    PRIMARY KEY (chat, message_id));
CREATE INDEX IF NOT EXISTS messages_unprocessed ON messages(processed, chat, date);
CREATE TABLE IF NOT EXISTS batches (
    id TEXT PRIMARY KEY, created TEXT NOT NULL, refs TEXT NOT NULL, done TEXT);
CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY, external_id TEXT NOT NULL UNIQUE, created TEXT NOT NULL,
    agent TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL,
    resolved TEXT, resolution TEXT);
CREATE INDEX IF NOT EXISTS proposals_status ON proposals(status, created);
"""


def chat_key(source: str, chat_id: str) -> str:
    return f"{source}:{chat_id}"


def _text(value, name: str, limit: int, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValidationError(f"{name}: ожидается строка")
    value = value.strip()
    if required and not value:
        raise ValidationError(f"{name}: пустое значение")
    if len(value) > limit:
        raise ValidationError(f"{name}: не длиннее {limit} символов")
    return value


def _url(value, name: str) -> str:
    value = _text(value, name, 2000, required=True)
    if not re.match(r"^https?://", value, re.I):
        raise ValidationError(f"{name}: только ссылки http(s)")
    return value


def _iso(value, name: str) -> str:
    value = _text(value, name, 40, required=True)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"{name}: ожидается дата и время ISO 8601") from exc
    if parsed.tzinfo is None:
        raise ValidationError(f"{name}: нужен часовой пояс")
    return parsed.astimezone().isoformat(timespec="seconds")


def _moment(value: str) -> datetime:
    return datetime.fromisoformat(value)


def normalize_message(raw: dict) -> dict:
    """One message from a collector, checked and in the stored shape; links in the text are added."""
    if not isinstance(raw, dict):
        raise ValidationError("Сообщение должно быть объектом")
    allowed = {"id", "date", "author", "from_me", "mentions_me", "text", "reply_to", "link", "links", "files"}
    if set(raw) - allowed:
        raise ValidationError("Неизвестные поля сообщения")
    message_id = _text(str(raw.get("id", "")), "id", 64, required=True)
    if not ID_RE.match(message_id):
        raise ValidationError("id: недопустимые символы")
    text = _text(raw.get("text"), "text", TEXT_LIMIT)
    links = raw.get("links") or []
    files = raw.get("files") or []
    if not isinstance(links, list) or len(links) > 50:
        raise ValidationError("links: список до 50 ссылок")
    if not isinstance(files, list) or len(files) > 20:
        raise ValidationError("files: список до 20 файлов")
    found = [_url(link, "links") for link in links]
    for match in URL_RE.findall(text):
        url = match.rstrip(".,;:!?)")
        if url not in found and len(found) < 50:
            found.append(url)
    clean_files = []
    for item in files:
        if not isinstance(item, dict) or set(item) - {"name", "size", "path", "kind"}:
            raise ValidationError("files: ожидаются объекты name/size/path/kind")
        size = item.get("size")
        if size is not None and (not isinstance(size, int) or size < 0):
            raise ValidationError("files.size: целое число байт")
        clean_files.append({
            "name": _text(item.get("name"), "files.name", 255, required=True),
            "size": size,
            "path": _text(item.get("path"), "files.path", 1000) or None,
            "kind": _text(item.get("kind"), "files.kind", 20) or "file",
        })
    if not text and not found and not clean_files:
        raise ValidationError("Пустое сообщение")
    reply_to = raw.get("reply_to")
    return {
        "id": message_id,
        "date": _iso(raw.get("date"), "date"),
        "author": _text(raw.get("author"), "author", 200) or "?",
        "from_me": bool(raw.get("from_me")),
        "mentions_me": bool(raw.get("mentions_me")),
        "text": text,
        "reply_to": _text(str(reply_to), "reply_to", 64) if reply_to not in (None, "") else None,
        "link": _url(raw["link"], "link") if raw.get("link") else None,
        "links": found,
        "files": clean_files,
    }


def _version(message: dict) -> str:
    body = json.dumps([message["text"], message["links"], message["files"]], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


class ChatStore:
    def __init__(self, root: str | Path, clock=None):
        self.root = Path(root).resolve()
        self.state = self.root / "state"
        self.state.mkdir(parents=True, exist_ok=True)
        self.path = self.state / "chats.sqlite3"
        self.files_dir = self.root / "chat-files"
        self.lock = FileLock(str(self.state / "chats.lock"), timeout=15)
        self.clock = clock or (lambda: datetime.now().astimezone())
        with self.lock, self._db() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def now(self) -> str:
        return self.clock().isoformat(timespec="seconds")

    # --- chats ---

    def add_chat(self, source: str, chat_id: str, title: str, baseline: str) -> dict:
        if source not in SOURCES:
            raise ValidationError("Источник: telegram или vk")
        chat_id = _text(str(chat_id), "chat_id", 64, required=True)
        if not ID_RE.match(chat_id):
            raise ValidationError("chat_id: недопустимые символы")
        key = chat_key(source, chat_id)
        with self.lock, self._db() as db:
            existing = db.execute("SELECT * FROM chats WHERE key=?", (key,)).fetchone()
            if existing:
                # Reconnecting keeps the old baseline and messages: nothing is lost or taken twice.
                db.execute("UPDATE chats SET title=?, enabled=1 WHERE key=?", (_text(title, "title", 200) or key, key))
            else:
                db.execute("INSERT INTO chats(key, source, chat_id, title, baseline, cursor, added) VALUES (?,?,?,?,?,?,?)",
                           (key, source, chat_id, _text(title, "title", 200) or key, baseline, baseline, self.now()))
        return self.chat(key)

    def remove_chat(self, key: str) -> dict:
        with self.lock, self._db() as db:
            if not db.execute("UPDATE chats SET enabled=0 WHERE key=?", (key,)).rowcount:
                raise NotFoundError("Чат не подключён")
        return self.chat(key)

    def chat(self, key: str) -> dict:
        with self._db() as db:
            row = db.execute("SELECT * FROM chats WHERE key=?", (key,)).fetchone()
            if not row:
                raise NotFoundError("Чат не подключён")
            return self._chat_row(db, row)

    def chats(self, *, enabled_only: bool = False) -> list[dict]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM chats" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY added").fetchall()
            return [self._chat_row(db, row) for row in rows]

    @staticmethod
    def _chat_row(db, row) -> dict:
        counts = db.execute("SELECT COUNT(*) AS total, SUM(processed IS NULL) AS unprocessed, MAX(date) AS last "
                            "FROM messages WHERE chat=?", (row["key"],)).fetchone()
        return {"key": row["key"], "source": row["source"], "chat_id": row["chat_id"], "title": row["title"],
                "enabled": bool(row["enabled"]), "added": row["added"], "synced": row["synced"],
                "baseline": row["baseline"], "cursor": row["cursor"], "gap": bool(row["gap"]),
                "messages": counts["total"] or 0, "unprocessed": counts["unprocessed"] or 0,
                "last_message": counts["last"]}

    def set_cursor(self, key: str, cursor: str) -> None:
        with self.lock, self._db() as db:
            db.execute("UPDATE chats SET cursor=?, synced=? WHERE key=?", (cursor, self.now(), key))

    def set_gap(self, key: str, gap: bool) -> None:
        with self.lock, self._db() as db:
            db.execute("UPDATE chats SET gap=?, synced=? WHERE key=?", (int(gap), self.now(), key))

    # --- messages ---

    def add_messages(self, key: str, messages: list[dict]) -> dict:
        """Store checked messages after the chat's baseline; a changed text is a new version."""
        if not isinstance(messages, list) or len(messages) > MESSAGE_BATCH_LIMIT:
            raise ValidationError(f"Сообщения: список до {MESSAGE_BATCH_LIMIT}")
        chat = self.chat(key)
        clean = [normalize_message(message) for message in messages]
        added = updated = skipped = 0
        now = self.now()
        with self.lock, self._db() as db:
            for message in clean:
                if not self._after_baseline(chat, message):
                    skipped += 1
                    continue
                version = _version(message)
                row = db.execute("SELECT version FROM messages WHERE chat=? AND message_id=?",
                                 (key, message["id"])).fetchone()
                values = (message["date"], message["author"], int(message["from_me"]), int(message["mentions_me"]),
                          message["text"], message["reply_to"], message["link"],
                          json.dumps(message["links"], ensure_ascii=False), json.dumps(message["files"], ensure_ascii=False),
                          version)
                if row is None:
                    db.execute("INSERT INTO messages(date, author, from_me, mentions_me, text, reply_to, link, links, files, "
                               "version, chat, message_id, received) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               values + (key, message["id"], now))
                    added += 1
                elif row["version"] != version:
                    db.execute("UPDATE messages SET date=?, author=?, from_me=?, mentions_me=?, text=?, reply_to=?, link=?, "
                               "links=?, files=?, version=?, edited=?, processed=NULL WHERE chat=? AND message_id=?",
                               values + (now, key, message["id"]))
                    updated += 1
                else:
                    skipped += 1
        return {"added": added, "updated": updated, "skipped": skipped}

    @staticmethod
    def _after_baseline(chat: dict, message: dict) -> bool:
        if chat["source"] == "telegram":
            try:
                return int(message["id"]) > int(chat["baseline"])
            except ValueError:
                return False
        return _moment(message["date"]) >= _moment(chat["baseline"])

    def last_message_date(self, key: str) -> str | None:
        with self._db() as db:
            return db.execute("SELECT MAX(date) FROM messages WHERE chat=?", (key,)).fetchone()[0]

    @staticmethod
    def _message_row(row) -> dict:
        return {"chat": row["chat"], "id": row["message_id"], "date": row["date"], "author": row["author"],
                "from_me": bool(row["from_me"]), "mentions_me": bool(row["mentions_me"]), "text": row["text"],
                "reply_to": row["reply_to"], "link": row["link"], "links": json.loads(row["links"]),
                "files": json.loads(row["files"]), "edited": row["edited"]}

    def message(self, key: str, message_id: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM messages WHERE chat=? AND message_id=?", (key, str(message_id))).fetchone()
            return self._message_row(row) if row else None

    def unprocessed(self, *, limit: int = MESSAGE_BATCH_LIMIT) -> dict:
        """Unsorted messages by chat, with a few earlier ones for context, as one batch to mark later."""
        chats = []
        refs = []
        with self.lock, self._db() as db:
            for chat in db.execute("SELECT * FROM chats ORDER BY added").fetchall():
                rows = db.execute("SELECT * FROM messages WHERE chat=? AND processed IS NULL ORDER BY date, message_id LIMIT ?",
                                  (chat["key"], limit - len(refs))).fetchall()
                if not rows:
                    continue
                context = db.execute("SELECT * FROM messages WHERE chat=? AND processed IS NOT NULL AND date<=? "
                                     "ORDER BY date DESC LIMIT ?", (chat["key"], rows[0]["date"], CONTEXT_MESSAGES)).fetchall()
                chats.append({"key": chat["key"], "source": chat["source"], "title": chat["title"],
                              "context": [self._message_row(row) for row in reversed(context)],
                              "messages": [self._message_row(row) for row in rows]})
                refs.extend([row["chat"], row["message_id"], row["version"]] for row in rows)
                if len(refs) >= limit:
                    break
            batch = None
            if refs:
                batch = uuid.uuid4().hex[:12]
                db.execute("INSERT INTO batches(id, created, refs) VALUES (?,?,?)", (batch, self.now(), json.dumps(refs)))
        return {"batch": batch, "count": len(refs), "chats": chats}

    def mark_batch(self, batch: str) -> dict:
        """Mark what the agent has read; a message edited since then stays unsorted."""
        with self.lock, self._db() as db:
            row = db.execute("SELECT * FROM batches WHERE id=?", (batch,)).fetchone()
            if not row:
                raise NotFoundError("Пачка не найдена: сначала mmm chats new")
            marked = changed = 0
            now = self.now()
            for chat, message_id, version in json.loads(row["refs"]):
                result = db.execute("UPDATE messages SET processed=? WHERE chat=? AND message_id=? AND version=? AND processed IS NULL",
                                    (now, chat, message_id, version))
                if result.rowcount:
                    marked += 1
                elif db.execute("SELECT 1 FROM messages WHERE chat=? AND message_id=? AND version<>?",
                                (chat, message_id, version)).fetchone():
                    changed += 1
            db.execute("UPDATE batches SET done=? WHERE id=?", (now, batch))
        return {"batch": batch, "marked": marked, "changed_since": changed}

    def ingest_web(self, source: str, chat: dict, messages: list | None = None, *, connect: bool = False,
                   oldest_loaded: str | None = None) -> dict:
        """Messages the extension read from an open chat tab (a VK conversation or Telegram Web).

        ``connect`` starts watching the chat from now on. When the oldest message the tab has
        loaded is newer than the last stored one, messages in between may be missing: the chat
        gets a gap mark until the user scrolls up and the reader sends them.
        """
        if source not in WEB_PEERS:
            raise ValidationError("Источник: vk или tgweb")
        if not isinstance(chat, dict):
            raise ValidationError("chat: ожидается объект")
        peer = str(chat.get("peer_id", ""))
        if not WEB_PEERS[source].fullmatch(peer):
            raise ValidationError("chat.peer_id: число")
        key = chat_key(source, peer)
        if connect:
            return {"chat": self.add_chat(source, peer, _text(chat.get("title"), "chat.title", 200), self.now()), "added": 0}
        try:
            info = self.chat(key)
        except NotFoundError:
            return {"watched": False}
        if not info["enabled"]:
            return {"watched": False}
        last = self.last_message_date(key)
        result = self.add_messages(key, messages or [])
        if oldest_loaded and last is not None:
            oldest = _moment(_iso(oldest_loaded, "oldest_loaded"))
            if oldest > _moment(last):
                self.set_gap(key, True)
            elif info["gap"]:
                self.set_gap(key, False)
        return {"watched": True, "baseline": info["baseline"], **result, "gap": self.chat(key)["gap"]}

    def save_markup(self, markup, source: str = "vk") -> dict:
        """Structure of a chat page the reader could not parse (tags and classes, no texts).

        Kept in ``state/<source>-markup.json`` (``vk-markup.json``, ``tgweb-markup.json``) for the
        agent that repairs ``extension/vk-parse.js`` or ``extension/tg-parse.js``.
        """
        if source not in WEB_PEERS:
            raise ValidationError("Источник: vk или tgweb")
        if not isinstance(markup, dict):
            raise ValidationError("markup: ожидается объект")
        body = {"received": self.now(), **markup}
        text = json.dumps(body, ensure_ascii=False)
        if len(text.encode("utf-8")) > 250_000:
            raise ValidationError("Разметка страницы слишком большая")
        Store._atomic_file(self.state / f"{source}-markup.json", text)
        return {"saved": True, "received": body["received"]}

    def status(self) -> dict:
        chats = self.chats()
        with self._db() as db:
            pending = db.execute("SELECT COUNT(*) FROM proposals WHERE status='pending'").fetchone()[0]
        return {"chats": chats, "unprocessed": sum(chat["unprocessed"] for chat in chats if chat["enabled"]),
                "proposals_pending": pending}
