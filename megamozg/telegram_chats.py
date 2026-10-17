"""Telegram work chats read through the user's own account (Telethon), read-only.

The user signs in once in a terminal: ``mmm chats telegram-login``. The api_id and api_hash
from my.telegram.org, the phone, the code and the 2FA password are typed by the user, never by
an agent. The session in ``state/telegram`` opens the whole account, so it stays on this
computer: backups take only the library and the journal, and ``migrate`` skips the folder.
Only reading calls are used (get_me, iter_dialogs, get_entity, get_messages, iter_messages,
download_media): nothing is sent and nothing is marked as read. There is no background
connection: messages are taken when an agent or the Work page asks for them.
"""
from __future__ import annotations

import asyncio
import getpass
import json
import re
import sys
from pathlib import Path

from filelock import FileLock, Timeout

from . import __version__
from .chats import ChatStore
from .core import Store, ValidationError

FILE_LIMIT = 25 * 1024 * 1024
EDIT_RESCAN = 30
INSTALL_HINT = r"Нет библиотеки Telegram: .venv\Scripts\python.exe -m pip install -r requirements-chats.txt"


def folder(root: Path) -> Path:
    return Path(root) / "state" / "telegram"


def _telethon():
    try:
        import telethon  # noqa: F401
        from telethon import TelegramClient, types
        from telethon.errors import SessionPasswordNeededError
    except ImportError as exc:
        raise ValidationError(INSTALL_HINT) from exc
    return TelegramClient, types, SessionPasswordNeededError


def installed() -> bool:
    try:
        import telethon  # noqa: F401
    except ImportError:
        return False
    return True


def _app(root: Path) -> dict | None:
    try:
        value = json.loads((folder(root) / "app.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) and isinstance(value.get("api_id"), int) and value.get("api_hash") else None


def logged_in(root: Path) -> bool:
    """A session file exists; whether Telegram still accepts it shows on the next sync."""
    return _app(root) is not None and (folder(root) / "brainalot.session").exists()


def _lock(root: Path) -> FileLock:
    folder(root).mkdir(parents=True, exist_ok=True)
    return FileLock(str(folder(root) / "client.lock"), timeout=10)


def _client(root: Path, app: dict):
    TelegramClient, _, _ = _telethon()
    return TelegramClient(str(folder(root) / "brainalot"), app["api_id"], app["api_hash"],
                          device_model="Brainalot", system_version="Windows", app_version=__version__,
                          receive_updates=False)


def _run(root: Path, work):
    """Open the session, run ``work(client)`` and close it; one process at a time."""
    app = _app(root)
    if app is None:
        raise ValidationError("Telegram не подключён: сначала mmm chats telegram-login")
    try:
        with _lock(root):
            async def main():
                client = _client(root, app)
                await client.connect()
                try:
                    if not await client.is_user_authorized():
                        raise ValidationError("Вход в Telegram устарел: повторите mmm chats telegram-login")
                    return await work(client)
                finally:
                    await client.disconnect()
            return asyncio.run(main())
    except Timeout as exc:
        raise ValidationError("Telegram сейчас занят другой командой, повторите через минуту") from exc
    except ValidationError:
        raise
    except Exception as exc:  # Telethon RPC and connection errors: keep the JSON protocol of the CLI
        raise ValidationError(f"Telegram ответил ошибкой: {type(exc).__name__}: {exc}") from exc


def _ask(prompt: str) -> str:
    sys.stderr.write(prompt)
    sys.stderr.flush()
    return sys.stdin.readline().strip()


def _person(user) -> dict:
    return {"id": str(user.id), "name": " ".join(filter(None, [user.first_name, user.last_name])) or None,
            "username": user.username}


def login(root: Path, *, ask=_ask, ask_secret=getpass.getpass) -> dict:
    """Interactive sign-in in a terminal; prompts go to stderr so stdout stays JSON."""
    _telethon()
    root = Path(root)
    app = _app(root)
    if app is None:
        api_id = ask("api_id с my.telegram.org: ")
        api_hash = ask("api_hash с my.telegram.org: ")
        if not api_id.isdigit() or not re.fullmatch(r"[0-9a-f]{32}", api_hash):
            raise ValidationError("api_id — число, api_hash — 32 символа 0-9a-f (my.telegram.org → API development tools)")
        app = {"api_id": int(api_id), "api_hash": api_hash}
        Store._atomic_file(folder(root) / "app.json", json.dumps(app))
    _, _, SessionPasswordNeededError = _telethon()
    try:
        with _lock(root):
            async def main():
                client = _client(root, app)
                await client.connect()
                try:
                    if not await client.is_user_authorized():
                        phone = ask("Телефон в международном формате, например +7…: ")
                        await client.send_code_request(phone)
                        code = ask("Код из Telegram: ")
                        try:
                            await client.sign_in(phone=phone, code=code)
                        except SessionPasswordNeededError:
                            await client.sign_in(password=ask_secret("Пароль двухэтапной проверки (не отображается): "))
                    return {"status": "ok", "user": _person(await client.get_me())}
                finally:
                    await client.disconnect()
            return asyncio.run(main())
    except Timeout as exc:
        raise ValidationError("Telegram сейчас занят другой командой, повторите через минуту") from exc
    except ValidationError:
        raise
    except Exception as exc:  # wrong code, flood wait, no network: say it in one line
        raise ValidationError(f"Вход не удался: {type(exc).__name__}: {exc}") from exc


def dialogs(root: Path, limit: int = 100) -> list[dict]:
    async def work(client):
        found = []
        async for dialog in client.iter_dialogs(limit=limit):
            kind = "group" if dialog.is_group else "channel" if dialog.is_channel else "user"
            found.append({"id": str(dialog.id), "title": dialog.name, "type": kind})
        return found
    return _run(root, work)


async def _entity(client, chat_id: str):
    try:
        return await client.get_entity(int(chat_id))
    except (ValueError, TypeError):
        async for dialog in client.iter_dialogs():
            if str(dialog.id) == str(chat_id):
                return dialog.entity
    raise ValidationError(f"Чат {chat_id} не найден среди диалогов этого аккаунта")


def add(root: Path, chat_id: str, store: ChatStore | None = None) -> dict:
    """Connect a chat: only messages after its current last one will be taken."""
    store = store or ChatStore(root)
    if not re.fullmatch(r"-?\d{1,20}", str(chat_id)):
        raise ValidationError("ID чата — число из mmm chats telegram-dialogs")

    async def work(client):
        entity = await _entity(client, chat_id)
        last = await client.get_messages(entity, limit=1)
        title = getattr(entity, "title", None) or " ".join(filter(None, [getattr(entity, "first_name", None), getattr(entity, "last_name", None)]))
        return title or str(chat_id), str(last[0].id if last else 0)
    title, baseline = _run(root, work)
    return store.add_chat("telegram", str(chat_id), title, baseline)


def _safe(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "file"
    return name[:120]


def message_link(entity, message_id: int) -> str | None:
    username = getattr(entity, "username", None)
    if username:
        return f"https://t.me/{username}/{message_id}"
    if type(entity).__name__ == "Channel":
        return f"https://t.me/c/{entity.id}/{message_id}"
    return None


def sender_name(sender, message) -> str:
    if sender is None:
        return getattr(message, "post_author", None) or "?"
    if hasattr(sender, "first_name"):
        return " ".join(filter(None, [sender.first_name, getattr(sender, "last_name", None)])) or getattr(sender, "username", None) or "?"
    return getattr(sender, "title", None) or "?"


def message_record(message, sender, entity, files: list[dict]) -> dict | None:
    """A Telethon message in the collector shape; service messages and empty ones give None."""
    if getattr(message, "action", None) is not None:
        return None
    text = message.message or ""
    links = [entity_url for entity_url in (getattr(e, "url", None) for e in (message.entities or [])) if entity_url]
    if not text and not links and not files:
        return None
    return {"id": str(message.id), "date": message.date.astimezone().isoformat(timespec="seconds"),
            "author": sender_name(sender, message)[:200], "from_me": bool(message.out),
            "mentions_me": bool(message.mentioned), "text": text,
            "reply_to": message.reply_to_msg_id, "link": message_link(entity, message.id),
            "links": links, "files": files}


async def _files(client, message, target: Path) -> list[dict]:
    info = message.file
    if info is None or not (message.document or message.photo):
        return []
    name = info.name or f"photo-{message.id}{info.ext or '.jpg'}"
    entry = {"name": name[:255], "size": info.size, "kind": "photo" if message.photo else "file", "path": None}
    if info.size is not None and info.size <= FILE_LIMIT:
        path = target / f"{message.id}-{_safe(name)}"
        if not path.exists():
            target.mkdir(parents=True, exist_ok=True)
            await client.download_media(message, file=str(path))
        entry["path"] = str(path)
    return [entry]


def sync(root: Path, store: ChatStore | None = None) -> dict:
    """Take new messages (and edits of recent ones) of every connected Telegram chat."""
    store = store or ChatStore(root)
    chats = [chat for chat in store.chats(enabled_only=True) if chat["source"] == "telegram"]
    if not chats:
        return {"chats": [], "added": 0, "updated": 0}

    async def work(client):
        results = []
        for chat in chats:
            entity = await _entity(client, chat["chat_id"])
            target = store.files_dir / "telegram" / _safe(chat["title"])
            cursor = int(chat["cursor"] or chat["baseline"])
            records, seen = [], set()

            async def take(message):
                if message.id in seen or message.id <= int(chat["baseline"]):
                    return
                seen.add(message.id)
                sender = await message.get_sender() if message.sender_id else None
                record = message_record(message, sender, entity, await _files(client, message, target))
                if record:
                    records.append(record)

            async for message in client.iter_messages(entity, min_id=cursor, reverse=True):
                await take(message)
                cursor = max(cursor, message.id)
            # Edits do not move the cursor: look at the latest messages again.
            async for message in client.iter_messages(entity, limit=EDIT_RESCAN):
                if message.edit_date:
                    await take(message)
            counts = {"added": 0, "updated": 0, "skipped": 0}
            for start in range(0, len(records), 500):
                part = store.add_messages(chat["key"], records[start:start + 500])
                for name in counts:
                    counts[name] += part[name]
            store.set_cursor(chat["key"], str(cursor))
            results.append({"key": chat["key"], "title": chat["title"], **counts})
        return results

    results = _run(root, work)
    return {"chats": results, "added": sum(r["added"] for r in results), "updated": sum(r["updated"] for r in results)}
