"""Shared command boundary. Markdown is content; SQLite is the durable operation log.

The lock serializes MegaMozg processes, not Obsidian. Hash preconditions detect
stale edits; an external writer in the final check/replace window is still a
documented limitation. Pending operations are replayable, never blindly reset.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import tempfile
import time
from contextlib import contextmanager, closing
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
import zipfile
from concurrent.futures import ThreadPoolExecutor

from filelock import FileLock
import yaml
from . import attachments as attachment_tools
from . import paths

# libyaml is 3x faster than the pure-Python parser; the PyYAML wheels for Windows ship it.
# The pure-Python classes stay as the fallback and produce the same YAML byte for byte.
_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
_YAML_DUMPER = getattr(yaml, "CSafeDumper", yaml.SafeDumper)


class ValidationError(ValueError):
    pass


class ConflictError(RuntimeError):
    pass


class NotFoundError(LookupError):
    pass


KINDS = {"inbox", "task", "purchase", "media", "idea", "thought", "link", "project", "person"}
MEDIA = {"movie", "series", "book", "game", "podcast", "article"}
IMPORTANCE = {"low", "normal", "high", "critical"}
IMPORTANCE_WEIGHTS = {"low": 1, "normal": 2, "high": 3, "critical": 4}
IMPORTANCE_LABELS = {"low": "низкая", "normal": "обычная", "high": "высокая", "critical": "очень высокая"}
STATUSES = {"inbox", "active", "done", "cancelled", "archived"}
FIELDS = {"title", "body", "kind", "due", "media_type", "status", "tags", "url", "context_id", "topic", "importance", "show_in_carousel", "show_in_unscheduled", "planning_horizon", "attachments"}
FOLDERS = {
    "inbox": "1 Входящие", "task": "2 Дела/Задачи", "purchase": "2 Дела/Покупки",
    "media": "3 Библиотека", "idea": "4 Идеи и мысли/Идеи",
    "thought": "4 Идеи и мысли/Мысли", "link": "3 Библиотека/Ссылки", "project": "5 Проекты", "person": "7 Люди",
}
MEDIA_FOLDERS = {"movie": "Фильмы", "series": "Сериалы", "book": "Книги",
                 "game": "Игры", "podcast": "Подкасты", "article": "Статьи и видео"}


def digest(value: str | None) -> str | None:
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value is not None else None


def json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def folder(note: dict) -> str:
    base = FOLDERS[note["kind"]]
    if note["kind"] == "media":
        base += "/" + MEDIA_FOLDERS[note["media_type"]]
    return base


def validate(meta: dict) -> None:
    attached = meta.get("attachments", [])
    if (not isinstance(attached, list) or len(attached) > attachment_tools.MAX_COUNT
            or any(not isinstance(item, dict) or set(item) != {"id", "name", "mime", "size", "path"}
                   or not isinstance(item.get("id"), str) or not attachment_tools.ID_RE.fullmatch(item["id"])
                   or not isinstance(item.get("name"), str)
                   or not isinstance(item.get("mime"), str)
                   or item["mime"] in {"text/html", "image/svg+xml", "application/xhtml+xml"}
                   or (item["mime"].startswith("image/") and item["mime"] not in
                       {"image/png", "image/jpeg", "image/gif", "image/webp"})
                   or isinstance(item.get("size"), bool) or not isinstance(item.get("size"), int)
                   or item["size"] < 0 or item["size"] > attachment_tools.MAX_FILE
                   or item.get("path") != attachment_tools.DIRECTORY + "/" + item["id"]
                   for item in attached)):
        raise ValidationError("Некорректные свойства вложений")
    try:
        for item in attached:
            attachment_tools.safe_name(item["name"])
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    if sum(item["size"] for item in attached) > attachment_tools.MAX_TOTAL:
        raise ValidationError("Превышен лимит вложений записи")
    if not isinstance(meta.get("kind"), str) or meta.get("kind") not in KINDS:
        raise ValidationError("Неизвестный тип записи")
    if not isinstance(meta.get("status"), str) or meta.get("status") not in STATUSES:
        raise ValidationError("Неизвестный статус")
    title = meta.get("title")
    if not isinstance(title, str) or not title.strip() or len(title) > 200 or any(c in title for c in "\r\n\x00"):
        raise ValidationError("Название: от 1 до 200 символов, одна строка")
    due = meta.get("due")
    if due is not None:
        if not isinstance(due, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due):
            raise ValidationError("Дата должна быть YYYY-MM-DD")
        try:
            date.fromisoformat(due)
        except ValueError as exc:
            raise ValidationError("Такой даты нет") from exc
    planning_horizon = meta.get("planning_horizon")
    if planning_horizon is not None and (
            not isinstance(planning_horizon, str) or planning_horizon not in {"week", "month"}):
        raise ValidationError("Горизонт планирования: week, month или null")
    if planning_horizon is not None and meta.get("kind") not in {"task", "purchase"}:
        raise ValidationError("Горизонт планирования доступен только для дел и покупок")
    if planning_horizon is not None and due is not None:
        raise ValidationError("Горизонт планирования возможен только без срока")
    if meta.get("kind") == "media" and (not isinstance(meta.get("media_type"), str) or meta.get("media_type") not in MEDIA):
        raise ValidationError("Для медиа выберите вид: фильм, сериал, книга, игра, подкаст или статья")
    if meta.get("media_type") is not None and (not isinstance(meta["media_type"], str) or meta["media_type"] not in MEDIA):
        raise ValidationError("Неизвестный вид медиа")
    context_id = meta.get("context_id")
    if context_id is not None and (not isinstance(context_id, str) or not re.fullmatch(r"[0-9a-f]{32}", context_id) or context_id == meta.get("id")):
        raise ValidationError("Некорректный ID контекста")
    topic = meta.get("topic")
    if topic is not None and (not isinstance(topic, str) or len(topic) > 100 or not topic.strip() or any(c in topic for c in "\r\n\x00")):
        raise ValidationError("Тема: от 1 до 100 символов, одна строка")
    importance = meta.get("importance", "normal")
    if not isinstance(importance, str) or importance not in IMPORTANCE:
        raise ValidationError("Важность: low, normal, high или critical")
    if importance == "critical" and meta["kind"] not in {"task", "purchase"}:
        raise ValidationError("Очень высокая важность доступна только для дел и покупок")
    if not isinstance(meta.get("show_in_carousel", True), bool):
        raise ValidationError("Показ в карусели должен быть true или false")
    if not isinstance(meta.get("show_in_unscheduled", False), bool):
        raise ValidationError("Показ в несрочных должен быть true или false")
    if meta.get("show_in_unscheduled", False) and meta["kind"] != "thought":
        raise ValidationError("В несрочных можно показывать только мысли")
    manual_order = meta.get("manual_order")
    if manual_order is not None and (isinstance(manual_order, bool) or not isinstance(manual_order, int)
                                     or manual_order < 0):
        raise ValidationError("Ручной порядок должен быть целым неотрицательным числом или null")
    completed_at = meta.get("completed_at")
    if completed_at is not None:
        try:
            if datetime.fromisoformat(completed_at).tzinfo is None:
                raise ValueError()
        except (TypeError, ValueError) as exc:
            raise ValidationError("completed_at должен содержать часовой пояс") from exc
    source_capture_id = meta.get("source_capture_id")
    if source_capture_id is not None and (not isinstance(source_capture_id, str) or not re.fullmatch(r"[\w:.-]{1,160}", source_capture_id)):
        raise ValidationError("Некорректный ID исходной диктовки")
    tags = meta.get("tags", [])
    if not isinstance(tags, list) or len(tags) > 30 or any(
        not isinstance(t, str) or not re.fullmatch(r"[\w/-]{1,64}", t) for t in tags
    ):
        raise ValidationError("Теги: не более 30; буквы, цифры, _, / и -")
    url = meta.get("url")
    if url is not None:
        from urllib.parse import urlsplit
        if not isinstance(url, str) or len(url) > 4096 or any(c.isspace() for c in url):
            raise ValidationError("Некорректная ссылка")
        try:
            parsed = urlsplit(url)
        except ValueError as exc:
            raise ValidationError("Некорректная ссылка") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise ValidationError("Разрешены только ссылки http/https без пароля")


def encode_note(meta: dict, body: str) -> str:
    return "---\n" + yaml.dump(meta, Dumper=_YAML_DUMPER, allow_unicode=True, sort_keys=False).strip() + "\n---\n" + body


def decode_note(content: str) -> tuple[dict, str]:
    parts = content.split("\n---\n", 1)
    if not content.startswith("---\n") or len(parts) != 2:
        raise ValidationError("Нет блока свойств YAML")
    meta = yaml.load(parts[0][4:], Loader=_YAML_LOADER)
    if not isinstance(meta, dict) or not re.fullmatch(r"[0-9a-f]{32}", str(meta.get("id", ""))):
        raise ValidationError("Нет корректного ID Brainalot")
    for key in ("due", "created", "completed_at"):
        if isinstance(meta.get(key), (date, datetime)):
            meta[key] = meta[key].isoformat()
    meta.setdefault("context_id", None)
    meta.setdefault("topic", None)
    meta.setdefault("importance", "normal")
    meta.setdefault("show_in_carousel", True)
    meta.setdefault("show_in_unscheduled", False)
    meta.setdefault("planning_horizon", None)
    meta.setdefault("manual_order", None)
    meta.setdefault("attachments", [])
    try:
        created = datetime.fromisoformat(meta["created"])
        if created.tzinfo is None:
            raise ValueError("timezone required")
    except (KeyError, ValueError, TypeError) as exc:
        raise ValidationError("created должен содержать дату, время и часовой пояс") from exc
    validate(meta)
    return meta, parts[1]


class Store:
    def __init__(self, root: str | Path, clock=None):
        self.root = Path(root).resolve()
        # An installed copy keeps the vault in Documents (paths.py); a checkout keeps root/vault.
        self.vault = paths.vault_path(self.root).resolve()
        self.state = self.root / "state"
        self.state.mkdir(parents=True, exist_ok=True)
        self.vault.mkdir(parents=True, exist_ok=True)
        self.lock = FileLock(str(self.state / "writer.lock"), timeout=15)
        self.clock = clock or (lambda: datetime.now().astimezone())
        self.db_path = self.state / "journal.sqlite3"
        # Parsed notes by vault path: (mtime_ns, size, note, error). Only changed files are
        # read again, so a panel poll no longer parses the whole library (research, section 4.1).
        self._index: dict[str, tuple[int, int, dict | None, str | None]] = {}
        with self.lock, self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS captures (
                    external_id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                    note_id TEXT NOT NULL, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS operations (
                    id TEXT PRIMARY KEY, note_id TEXT NOT NULL, kind TEXT NOT NULL,
                    changes TEXT NOT NULL, status TEXT NOT NULL, created TEXT NOT NULL,
                    error TEXT, reverses TEXT);
                CREATE TABLE IF NOT EXISTS schedule (
                    note_id TEXT PRIMARY KEY, next_at TEXT NOT NULL, step INTEGER NOT NULL);
                -- Every dashboard asks for unapplied, recent and reversed operations; without
                -- these the journal is read in full on each poll and grows for years.
                CREATE INDEX IF NOT EXISTS operations_status ON operations(status);
                CREATE INDEX IF NOT EXISTS operations_kind_created ON operations(kind, created);
                CREATE INDEX IF NOT EXISTS operations_reverses ON operations(reverses);
                CREATE INDEX IF NOT EXISTS operations_note ON operations(note_id, kind);
                CREATE INDEX IF NOT EXISTS captures_note ON captures(note_id);
            """)
            if not (self.state / "api-token").exists():
                self._atomic_file(self.state / "api-token", secrets.token_urlsafe(32))
            self._init_vault()
            self._recover(db)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def token(self) -> str:
        return (self.state / "api-token").read_text(encoding="utf-8").strip()

    @staticmethod
    def _atomic_file(path: Path, content: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".mm-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _path(self, relative: str) -> Path:
        path = (self.vault / relative).resolve()
        if not path.is_relative_to(self.vault.resolve()) or path == self.vault:
            raise ValidationError("Путь выходит за пределы библиотеки")
        return path

    def _content(self, relative: str) -> str | None:
        p = self._path(relative)
        return p.read_text(encoding="utf-8") if p.exists() else None

    def _init_vault(self):
        for name in [*FOLDERS.values(), "6 Дни", attachment_tools.DIRECTORY, "9 Система/Оригиналы"]:
            self._path(name).mkdir(parents=True, exist_ok=True)
        for name in MEDIA_FOLDERS.values():
            self._path("3 Библиотека/" + name).mkdir(parents=True, exist_ok=True)
        display_names = ("properties:\n  note.title:\n    displayName: Название\n"
                         "  note.status:\n    displayName: Статус\n  note.due:\n    displayName: Срок\n"
                         "  note.topic:\n    displayName: Тема\n  note.importance:\n    displayName: Важность\n"
                         "  note.kind:\n    displayName: Тип\n  note.media_type:\n    displayName: Формат\n"
                         "  note.created:\n    displayName: Добавлено\n  file.name:\n    displayName: Открыть\n")
        files = {
            "Главная.md": "# Brainalot\n\nВаша библиотека. Заметки можно читать и редактировать без сервиса.\n\n"
                "## Дела\n\n![[9 Система/Дела.base]]\n\n## Библиотека\n\n![[9 Система/Библиотека.base]]\n\n"
                "## Входящие\n\n![[9 Система/Входящие.base]]\n\n"
                "## Люди\n\n![[9 Система/Люди.base]]\n\n"
                "Папки слева работают и без Bases. Свойства `kind`, `status`, `due` можно менять вручную.\n"
                "Не удаляйте `id`. Для новых записей удобнее пульт или Chrome.\n"
                "Исходные формулировки: `9 Система/Оригиналы`.\n",
            "9 Система/Дела.base": display_names + 'filters:\n  and:\n    - \'["task", "purchase"].contains(kind)\'\n    - \'status != "archived"\'\nviews:\n  - type: table\n    name: Дела\n    order: [note.title, note.status, note.due, note.topic, note.importance, file.name]\n',
            "9 Система/Библиотека.base": display_names + 'filters:\n  and:\n    - \'["media", "link", "idea", "thought", "project"].contains(kind)\'\n    - \'status != "archived"\'\nviews:\n  - type: table\n    name: Библиотека\n    order: [note.title, note.kind, note.media_type, note.topic, note.importance, note.status, file.name]\n',
            "9 Система/Люди.base": display_names + 'filters:\n  and:\n    - \'kind == "person"\'\n    - \'status != "archived"\'\nviews:\n  - type: table\n    name: Люди\n    order: [note.title, note.topic, note.importance, file.name]\n',
            "9 Система/Входящие.base": display_names + 'filters:\n  and:\n    - \'kind == "inbox"\'\n    - \'status != "archived"\'\nviews:\n  - type: table\n    name: Входящие\n    order: [note.title, note.created, file.name]\n',
            "9 Система/Как пользоваться.md": "# Как пользоваться\n\n"
                "Типы: inbox, task, purchase, media, idea, thought, link, project, person.\n"
                "Статусы: inbox, active, done, cancelled, archived. Даты due: YYYY-MM-DD.\n"
                "Медиа: movie, series, book, game, podcast, article.\n\n"
                "Служебные таймеры и журнал находятся рядом с vault, в state. Резервируйте всю папку data.\n"
                "Изменение через пульт обновляет свойства, сохраняя тело заметки.\n"
                "Не редактируйте ту же заметку одновременно в двух окнах: проверка версии обнаруживает\n"
                "устаревшие правки, но Obsidian не участвует в блокировке сервиса.\n",
        }
        for name, content in files.items():
            p = self._path(name)
            if not p.exists():
                self._atomic_file(p, content)

    def _apply(self, db, op):
        changes = json.loads(op["changes"])
        try:
            # Preflight every file before starting a multi-file move.
            for change in changes:
                current = self._content(change["path"])
                if current not in (change["before"], change["after"]):
                    raise ConflictError("Файл изменён вне операции: " + change["path"])
            for change in changes:
                current = self._content(change["path"])
                if current == change["after"]:
                    continue
                if current != change["before"]:
                    raise ConflictError("Файл изменён во время операции: " + change["path"])
                self._index.pop(change["path"], None)  # never trust a same-tick mtime after our own write
                if change["after"] is None:
                    self._path(change["path"]).unlink()
                else:
                    self._atomic_file(self._path(change["path"]), change["after"])
            db.execute("UPDATE operations SET status='applied', error=NULL WHERE id=?", (op["id"],))
            db.commit()
        except ConflictError as exc:
            db.execute("UPDATE operations SET status='conflict',error=? WHERE id=?", (str(exc), op["id"]))
            db.commit()
            raise

    def _recover(self, db):
        for op in db.execute("SELECT * FROM operations WHERE status='pending' ORDER BY rowid").fetchall():
            try:
                self._apply(db, op)
            except (ConflictError, OSError):
                # Keep pending failures visible and retry them on the next command.
                continue

    def _operation(self, db, note_id, kind, changes, reverses=None):
        op_id = uuid4().hex
        db.execute("INSERT INTO operations (id,note_id,kind,changes,status,created,reverses) VALUES (?,?,?,?,?,?,?)",
                   (op_id, note_id, kind, json_text(changes), "pending", self.clock().isoformat(), reverses))
        db.commit()  # Durable intent precedes the first filesystem mutation.
        self._apply(db, db.execute("SELECT * FROM operations WHERE id=?", (op_id,)).fetchone())
        return op_id

    def _attachment_path(self, attachment_id: str) -> Path:
        if not isinstance(attachment_id, str) or not attachment_tools.ID_RE.fullmatch(attachment_id):
            raise ValidationError("Некорректный ID вложения")
        expected_base = self.vault.resolve() / attachment_tools.DIRECTORY
        base = expected_base.resolve()
        if base != expected_base:
            raise ValidationError("Папка вложений выходит за пределы библиотеки")
        path = (base / attachment_id).resolve()
        if path != base / attachment_id:
            raise ValidationError("Путь вложения выходит за пределы папки")
        return path

    def _attachment_descriptor(self, attachment_id: str, name: str) -> dict:
        path = self._attachment_path(attachment_id)
        if not path.is_file():
            raise ValidationError("Вложение не найдено")
        if path.stat().st_size > attachment_tools.MAX_FILE:
            raise ValidationError("Вложение повреждено")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != attachment_id[:64] or len(data) > attachment_tools.MAX_FILE:
            raise ValidationError("Вложение повреждено")
        return dict(id=attachment_id, name=name, mime=attachment_tools.mime_for(data, name),
                    size=len(data), path=attachment_tools.DIRECTORY + "/" + attachment_id)

    def _normalize_attachments(self, values) -> list[dict]:
        if not isinstance(values, list) or len(values) > attachment_tools.MAX_COUNT:
            raise ValidationError("Не более 10 вложений в записи")
        result = []
        for value in values:
            if not isinstance(value, dict) or not isinstance(value.get("id"), str):
                raise ValidationError("Некорректное вложение")
            try:
                name = attachment_tools.safe_name(value.get("name"))
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
            result.append(self._attachment_descriptor(value["id"], name))
        if sum(item["size"] for item in result) > attachment_tools.MAX_TOTAL:
            raise ValidationError("Превышен лимит вложений записи")
        return result

    def upload_attachment(self, name, content_base64):
        try:
            name = attachment_tools.safe_name(name)
            data = attachment_tools.decode_base64(content_base64)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        attachment_id = attachment_tools.attachment_id(data, name)
        with self.lock:
            path = self._attachment_path(attachment_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                fd, temp = tempfile.mkstemp(prefix=".mm-", suffix=".tmp", dir=path.parent)
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    try:
                        os.link(temp, path)
                    except FileExistsError:
                        pass
                finally:
                    if os.path.exists(temp):
                        os.unlink(temp)
            return self._attachment_descriptor(attachment_id, name)

    def get_attachment(self, attachment_id):
        with self.lock:
            path = self._attachment_path(attachment_id)
            if not path.is_file():
                raise NotFoundError("Вложение не найдено")
            if path.stat().st_size > attachment_tools.MAX_FILE:
                raise ValidationError("Вложение повреждено")
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != attachment_id[:64]:
                raise ValidationError("Вложение повреждено")
            mime = attachment_tools.mime_for(data, attachment_id)
            return data, mime

    def _walk(self):
        """Every note file of the vault as (relative path, DirEntry), without the system folders."""
        skip = ("9 Система", attachment_tools.DIRECTORY)
        stack = [(self.vault, "")]
        while stack:
            directory, prefix = stack.pop()
            try:
                entries = list(os.scandir(directory))
            except OSError:
                continue
            for entry in entries:
                relative = prefix + entry.name
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if is_dir:
                    if relative not in skip:
                        stack.append((entry.path, relative + "/"))
                elif entry.name.endswith(".md") and relative != "Главная.md":
                    yield relative, entry

    @staticmethod
    def _clone(note: dict) -> dict:
        # Callers change the notes they get; the cached copy must stay as it was read from disk.
        copy = dict(note)
        for key, value in copy.items():
            if isinstance(value, list):
                copy[key] = [dict(item) if isinstance(item, dict) else item for item in value]
        return copy

    def _read_note(self, relative: str) -> tuple[dict | None, str | None]:
        try:
            content = self._content(relative)
            if content is None:
                return None, None
            meta, body = decode_note(content)
            return {**meta, "body": body, "path": relative, "version": digest(content)}, None
        except (ValueError, yaml.YAMLError, OSError, TypeError) as exc:
            return None, relative + ": " + str(exc)

    def _scan(self):
        notes, warnings, seen, present = [], [], {}, set()
        entries = []
        changed = []
        # A file changed within the last seconds may change again inside the same timestamp tick
        # with the same size; like git's "racily clean" entries, such files are always read again.
        racy_after = time.time_ns() - 2_000_000_000
        for relative, entry in self._walk():
            present.add(relative)
            try:
                stat = entry.stat()
                key = (stat.st_mtime_ns, stat.st_size)
            except OSError:
                key = None
            cached = self._index.get(relative)
            if key is not None and cached is not None and cached[:2] == key and key[0] < racy_after:
                entries.append((relative, key, (cached[2], cached[3])))
            else:
                entries.append((relative, key, None))
                changed.append(relative)
        # Windows file opens dominate cold scans. Bounded parallel reads overlap that latency;
        # parsing and cache publication stay in scan order so duplicate-ID warnings stay stable.
        if len(changed) >= 32:
            with ThreadPoolExecutor(max_workers=8) as pool:
                loaded = dict(zip(changed, pool.map(self._read_note, changed)))
        else:
            loaded = {relative: self._read_note(relative) for relative in changed}
        for relative, key, cached_result in entries:
            if cached_result is None:
                note, error = loaded[relative]
                if key is not None:
                    self._index[relative] = (*key, note, error)
            else:
                note, error = cached_result
            if error:
                warnings.append(error)
            if note is None:
                continue
            if note["id"] in seen:
                warnings.append("Повтор ID: " + relative + " и " + seen[note["id"]])
            seen[note["id"]] = relative
            notes.append(self._clone(note))
        for gone in self._index.keys() - present:
            del self._index[gone]
        return notes, warnings

    def _find(self, note_id):
        notes, _ = self._scan()
        found = [n for n in notes if n["id"] == note_id]
        if not found:
            raise NotFoundError("Запись не найдена или её свойства повреждены")
        if len(found) != 1:
            raise ConflictError("Несколько заметок с одним ID. Исправьте копию в Obsidian")
        return found[0]

    def _validate_context(self, context_id):
        try:
            context = self._find(context_id)
        except NotFoundError as exc:
            raise ValidationError("Контекст с таким ID не найден") from exc
        if context["kind"] not in {"project", "person"}:
            raise ValidationError("Контекст должен указывать на проект или человека")

    def capture(self, text, *, kind="inbox", title=None, due=None, media_type=None,
                url=None, tags=None, source="manual", external_id=None, source_capture_id=None,
                context_id=None, topic=None, importance="normal", show_in_unscheduled=False,
                planning_horizon=None, attachments=None):
        attachments = [] if attachments is None else self._normalize_attachments(attachments)
        if not isinstance(text, str) or (not text.strip() and not attachments) or len(text) > 50000 or "\x00" in text:
            raise ValidationError("Текст: от 1 до 50000 символов или вложение")
        if not isinstance(source, str) or not re.fullmatch(r"[\w-]{1,40}", source):
            raise ValidationError("Некорректный источник")
        if external_id is not None and (not isinstance(external_id, str) or not re.fullmatch(r"[\w:.-]{1,160}", external_id)):
            raise ValidationError("Некорректный ID события")
        tags = [] if tags is None else tags
        if due is not None:
            planning_horizon = None
        payload = dict(text=text, kind=kind, title=title, due=due, media_type=media_type,
                       url=url, tags=tags, source=source, source_capture_id=source_capture_id,
                       context_id=context_id, topic=topic, importance=importance, show_in_carousel=True,
                       show_in_unscheduled=show_in_unscheduled, planning_horizon=planning_horizon,
                       attachments=[{"id": item["id"], "name": item["name"]} for item in attachments])
        meta = dict(id=uuid4().hex, title=title or (" ".join(text.split())[:120] if text.strip() else attachments[0]["name"][:120]), kind=kind,
                    status="inbox" if kind == "inbox" else "active", created=self.clock().isoformat(),
                    due=due, media_type=media_type, tags=tags, url=url, source=source,
                    completed_at=None, source_capture_id=source_capture_id,
                    context_id=context_id, topic=topic, importance=importance, show_in_carousel=True,
                    show_in_unscheduled=show_in_unscheduled, planning_horizon=planning_horizon,
                    manual_order=None, attachments=attachments)
        validate(meta)
        external_id = external_id or uuid4().hex
        with self.lock, self._db() as db:
            self._recover(db)
            if source_capture_id is not None and not db.execute("SELECT 1 FROM captures WHERE note_id=?", (source_capture_id,)).fetchone():
                raise ValidationError("Исходная диктовка с таким ID не найдена")
            if context_id is not None:
                self._validate_context(context_id)
            old = db.execute("SELECT * FROM captures WHERE external_id=?", (external_id,)).fetchone()
            if old:
                previous_payload = json.loads(old["payload"])
                for key, default in (("context_id", None), ("topic", None), ("importance", "normal"),
                                     ("show_in_carousel", True), ("show_in_unscheduled", False),
                                     ("planning_horizon", None), ("attachments", [])):
                    previous_payload.setdefault(key, default)
                if json_text(previous_payload) != json_text(payload):
                    raise ConflictError("Этот ID события уже использован для другого содержания")
                failed = db.execute("SELECT status FROM operations WHERE note_id=? AND kind='capture'", (old["note_id"],)).fetchone()
                if failed is not None and failed["status"] != "applied":
                    raise ConflictError("Исходник сохранён, но запись в библиотеку не завершена. Проверьте журнал")
                try:
                    return self._find(old["note_id"])
                except NotFoundError as exc:
                    # The delivery key remains reserved after a journaled deletion.  Reusing
                    # it must not silently create a second note with the same event identity.
                    raise ConflictError("Запись с этим ID события была удалена; восстановите её через undo или используйте новый ID события") from exc
            slug = re.sub(r'[<>:"/\\|?*\x00-\x1f\[\]#^]', "-", meta["title"]).strip(" .")[:90] or "Запись"
            filename = f"{slug} — {meta['id'][:8]}.md"
            relative = folder(meta) + "/" + filename
            body = "\n# " + meta["title"] + "\n\n" + text + "\n"
            body = attachment_tools.render_block(body, attachments, relative)
            original_path = f"9 Система/Оригиналы/{meta['id']}.md"
            original = f"# Исходная запись\n\nПолучено: {meta['created']}\nИсточник: {source}\nID: {external_id}\n\n{text}\n"
            changes = [dict(path=original_path, before=None, after=original),
                       dict(path=relative, before=None, after=encode_note(meta, body))]
            db.execute("INSERT INTO captures VALUES (?,?,?,?)", (external_id, json_text(payload), meta["id"], meta["created"]))
            self._operation(db, meta["id"], "capture", changes)
            return self._find(meta["id"])

    def _update(self, db, note, patch, expected_version, *, reverses=None, operation_kind="update"):
        if not isinstance(patch, dict) or not patch or set(patch) - FIELDS:
            raise ValidationError("Пустое изменение или неподдерживаемое поле")
        if not expected_version or note["version"] != expected_version:
            raise ConflictError("Заметка уже изменена. Обновите список и повторите решение")
        before = self._content(note["path"])
        if digest(before) != expected_version:
            raise ConflictError("Заметка изменилась во время чтения")
        meta, body = decode_note(before)
        patch = dict(patch)
        if "attachments" in patch:
            patch["attachments"] = self._normalize_attachments(patch["attachments"])
        body_was_patched = "body" in patch
        if body_was_patched:
            body = patch.pop("body")
            if not isinstance(body, str) or len(body) > 50000 or "\x00" in body:
                raise ValidationError("Текст записи должен быть строкой до 50000 символов")
            body = body.replace("\r\n", "\n").replace("\r", "\n")
        old_title = meta["title"]
        meta.update(patch)
        if "title" in patch and not body_was_patched:
            generated_heading = re.match(r"^(\s*# )([^\r\n]+)(\r?\n)", body)
            if generated_heading and generated_heading.group(2) == old_title:
                body = (body[:generated_heading.start(2)] + meta["title"]
                        + body[generated_heading.end(2):])
        # Changing a deadline to a date or moving out of task/purchase clears
        # the planning basket.  Apply this before validation so one PATCH can
        # move a note directly between a date and a planning horizon.
        if (("due" in patch and meta.get("due") is not None)
                or ("kind" in patch and meta.get("kind") not in {"task", "purchase"})):
            meta["planning_horizon"] = None
        if (("due" in patch and patch["due"] != note.get("due"))
                or meta.get("planning_horizon") != note.get("planning_horizon")):
            # A rank belongs to one exact date or planning basket, so moving a
            # note must not leak its old group's position.
            meta["manual_order"] = None
        if "status" in patch:
            meta["completed_at"] = self.clock().isoformat() if patch["status"] == "done" and note["status"] != "done" else (note.get("completed_at") if patch["status"] == "done" else None)
        if "kind" in patch and "status" not in patch and meta["status"] in {"active", "inbox"}:
            meta["status"] = "inbox" if meta["kind"] == "inbox" else "active"
        if meta["kind"] != "media":
            meta["media_type"] = None
        validate(meta)
        if meta.get("context_id") is not None:
            self._validate_context(meta["context_id"])
        # Keep filename stable on title edits to avoid needlessly breaking links.
        relative = folder(meta) + "/" + Path(note["path"]).name
        body = attachment_tools.render_block(body, meta["attachments"], relative)
        after = encode_note(meta, body)
        if relative == note["path"]:
            changes = [dict(path=relative, before=before, after=after)]
        else:
            if self._content(relative) is not None:
                raise ConflictError("В целевой папке уже есть такой файл")
            # Create target first; durable journal can finish removal after restart.
            changes = [dict(path=relative, before=None, after=after),
                       dict(path=note["path"], before=before, after=None)]
        self._operation(db, note["id"], "undo" if reverses else operation_kind, changes, reverses)
        return self._find(note["id"])

    def update(self, note_id, patch, expected_version):
        with self.lock, self._db() as db:
            self._recover(db)
            return self._update(db, self._find(note_id), patch, expected_version)

    def reorder(self, due, ordered_ids, expected_versions):
        """Persist the complete manual order of active tasks for one due-date group."""
        if due is not None:
            if not isinstance(due, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due):
                raise ValidationError("Группа порядка должна быть датой YYYY-MM-DD или null")
            try:
                date.fromisoformat(due)
            except ValueError as exc:
                raise ValidationError("Такой даты нет") from exc
        if (not isinstance(ordered_ids, list) or not ordered_ids
                or any(not isinstance(note_id, str) or not re.fullmatch(r"[0-9a-f]{32}", note_id)
                       for note_id in ordered_ids)):
            raise ValidationError("ordered_ids должен быть непустым списком ID записей")
        if len(set(ordered_ids)) != len(ordered_ids):
            raise ValidationError("ordered_ids содержит повторяющиеся ID")
        if (not isinstance(expected_versions, dict) or set(expected_versions) != set(ordered_ids)
                or any(not isinstance(version, str) or not re.fullmatch(r"[0-9a-f]{64}", version)
                       for version in expected_versions.values())):
            raise ValidationError("expected_versions должен содержать версию каждого ordered_ids")

        with self.lock, self._db() as db:
            self._recover(db)
            notes, _ = self._scan()
            group = [note for note in notes if note["kind"] in {"task", "purchase"}
                     and note["status"] == "active" and note.get("due") == due]
            group_ids = [note["id"] for note in group]
            if len(group_ids) != len(set(group_ids)):
                raise ConflictError("В группе есть повторяющийся ID. Исправьте копию в Obsidian")
            if set(ordered_ids) != set(group_ids):
                raise ConflictError("Передайте полный актуальный список активных дел этой группы")
            by_id = {note["id"]: note for note in group}
            changes = []
            for order, note_id in enumerate(ordered_ids):
                note = by_id[note_id]
                before = self._content(note["path"])
                if note["version"] != expected_versions[note_id] or digest(before) != expected_versions[note_id]:
                    raise ConflictError("Одно из дел уже изменено. Обновите список и повторите порядок")
                meta, body = decode_note(before)
                meta["manual_order"] = order
                validate(meta)
                changes.append(dict(path=note["path"], before=before, after=encode_note(meta, body)))
            operation_id = self._operation(db, ordered_ids[0], "reorder", changes)
            return {"operation_id": operation_id,
                    "notes": [self._find(note_id) for note_id in ordered_ids]}

    def delete(self, note_id, expected_version):
        """Remove a note and its source copy through one reversible operation."""
        with self.lock, self._db() as db:
            self._recover(db)
            note = self._find(note_id)
            if not expected_version or note["version"] != expected_version:
                raise ConflictError("Заметка уже изменена. Обновите список и повторите решение")

            main_before = self._content(note["path"])
            if digest(main_before) != expected_version:
                raise ConflictError("Заметка изменилась во время чтения")

            linked = [other for other in self._scan()[0] if other["id"] != note_id and
                      (other.get("context_id") == note_id or other.get("source_capture_id") == note_id)]
            if linked:
                titles = ", ".join(other["title"] for other in linked[:3])
                suffix = "" if len(linked) <= 3 else " и ещё " + str(len(linked) - 3)
                raise ConflictError("Нельзя удалить запись: на неё ссылаются " + titles + suffix)

            changes = [dict(path=note["path"], before=main_before, after=None)]
            original_path = f"9 Система/Оригиналы/{note_id}.md"
            original_before = self._content(original_path)
            if original_before is not None:
                changes.append(dict(path=original_path, before=original_before, after=None))
            operation_id = self._operation(db, note_id, "delete", changes)
            return {"id": note_id, "deleted": True, "operation_id": operation_id}

    def list_notes(self):
        with self.lock, self._db() as db:
            self._recover(db)
            notes, _ = self._scan()
            return sorted(notes, key=lambda n: (n["created"], n["id"]), reverse=True)

    def dashboard(self):
        with self.lock, self._db() as db:
            self._recover(db)
            now = self.clock()
            today = now.date()
            # An explicit undo keeps the restored date visible until the next local day.
            # A later manual choice of a different past date is a new decision.
            undone_today = {}
            for row in db.execute("""
                SELECT original.note_id, original.changes FROM operations AS original
                JOIN operations AS reversal ON reversal.reverses = original.id
                WHERE original.kind = 'auto_rollover' AND reversal.kind = 'undo'
                  AND reversal.status = 'applied' AND substr(reversal.created, 1, 10) = ?
            """, (today.isoformat(),)):
                original_note = next(decode_note(change["before"])[0]
                                     for change in json.loads(row["changes"]) if change["before"])
                undone_today.setdefault(row["note_id"], set()).add(original_note["due"])
            unresolved = {row["note_id"] for row in db.execute("""
                SELECT note_id FROM operations
                WHERE kind = 'auto_rollover' AND status != 'applied'
            """)}
            notes, scan_warnings = self._scan()
            rollover_warnings = []
            attempted_rollover = False
            for note in notes:
                if (note["kind"] not in {"task", "purchase"} or note["status"] != "active"
                        or not note.get("due") or note["due"] >= today.isoformat()
                        or note["due"] in undone_today.get(note["id"], ())
                        or note["id"] in unresolved):
                    continue
                attempted_rollover = True
                try:
                    self._update(db, note, {"due": None, "planning_horizon": None}, note["version"], operation_kind="auto_rollover")
                except ConflictError as exc:
                    rollover_warnings.append(f"Автоперенос {note['id']}: {exc}")
            if attempted_rollover:
                notes, scan_warnings = self._scan()
            warnings = rollover_warnings + scan_warnings
            warnings += [f"Операция {r['id']}: {r['status']}. {r['error'] or 'Запись на диск ожидает повторения'}"
                         for r in db.execute("SELECT id,status,error FROM operations WHERE status!='applied'")]
            notes.sort(key=lambda n: (n.get("due") or "9999", n.get("manual_order") is None,
                                      n.get("manual_order") if n.get("manual_order") is not None else 0,
                                      n["created"], n["id"]))
            active = [n for n in notes if n["status"] not in {"done", "cancelled", "archived"}]
            today_tasks = [n for n in active if n["kind"] in {"task", "purchase"} and n.get("due") and n["due"] <= today.isoformat()]
            week = [n for n in active if n["kind"] in {"task", "purchase"} and n.get("due") and today.isoformat() < n["due"] <= (today + timedelta(days=7)).isoformat()]
            upcoming = [n for n in active if n["kind"] in {"task", "purchase"} and n.get("due") and n["due"] > (today + timedelta(days=7)).isoformat()]
            unscheduled = [n for n in active if (
                n["kind"] in {"task", "purchase"} and n["status"] == "active"
                and not n.get("due") and n.get("planning_horizon") is None
            ) or (
                n["kind"] == "thought" and n.get("show_in_unscheduled") and not n.get("due")
            )]
            backlog_week = [n for n in active if n["kind"] in {"task", "purchase"}
                            and n["status"] == "active" and not n.get("due")
                            and n.get("planning_horizon") == "week"]
            backlog_month = [n for n in active if n["kind"] in {"task", "purchase"}
                             and n["status"] == "active" and not n.get("due")
                             and n.get("planning_horizon") == "month"]
            library = [n for n in active if n["kind"] not in {"task", "purchase", "inbox"}]
            schedules = {r["note_id"]: r for r in db.execute("SELECT * FROM schedule")}
            candidates = []
            for note in active:
                if note["kind"] in {"inbox", "project"} or not note["show_in_carousel"]:
                    continue
                if note["kind"] in {"task", "purchase"} and note.get("due") == today.isoformat():
                    continue
                row = schedules.get(note["id"])
                if row and datetime.fromisoformat(row["next_at"]) > now:
                    continue
                candidates.append(note)
            candidates.sort(key=lambda n: n["id"])
            # One minute per card; each pass gives critical/high/normal/low 4/3/2/1 slots.
            weighted = [n for weight in range(1, max(IMPORTANCE_WEIGHTS.values()) + 1) for n in candidates
                        if IMPORTANCE_WEIGHTS[n["importance"]] >= weight]
            card = None
            if weighted:
                card = dict(weighted[int(now.timestamp() // 60) % len(weighted)])
                importance_label = IMPORTANCE_LABELS[card["importance"]]
                kind_label = {"inbox": "входящее", "task": "дело", "purchase": "покупка",
                              "media": "медиа", "idea": "идея", "thought": "мысль", "link": "ссылка",
                              "project": "проект", "person": "человек"}[card["kind"]]
                card["reason"] = f"Вспомнить: {kind_label}; важность — {importance_label}. Показ не меняет статус."
            yesterday = (today - timedelta(days=1)).isoformat()
            overdue_yesterday = [n for n in today_tasks if n["due"] == yesterday]
            overdue_older = [n for n in today_tasks if n["due"] < yesterday]
            recent = [n for n in notes if n["status"] == "done" and n.get("completed_at") and n["completed_at"][:10] >= (today - timedelta(days=6)).isoformat()]
            completed_tasks = sorted(
                (n for n in notes if n["status"] == "done" and (
                    n["kind"] in {"task", "purchase"}
                    or (n["kind"] == "thought" and n.get("show_in_unscheduled"))
                )),
                key=lambda n: (n.get("completed_at") or "", n["id"]), reverse=True,
            )
            events = self._recent_decisions(db, today)
            return dict(date=today.isoformat(), mode="manual", today=today_tasks, week=week, upcoming=upcoming, unscheduled=unscheduled,
                        backlog_week=backlog_week, backlog_month=backlog_month,
                        contexts=[{key: n[key] for key in ("id", "title", "kind", "status")}
                                  for n in notes if n["kind"] in {"project", "person"}],
                        inbox=[n for n in active if n["kind"] == "inbox"], library=library,
                        overdue_yesterday=overdue_yesterday, overdue_older=overdue_older,
                        completed_tasks=completed_tasks, stats={"completed_recent": len(recent), **events},
                        resurface=card, resurface_candidates=candidates, warnings=warnings)

    def _recent_decisions(self, db, today):
        result = {"done": 0, "deferred": 0, "cancelled": 0}
        cutoff = (today - timedelta(days=6)).isoformat()
        for op in db.execute("""SELECT u.changes FROM operations u
            WHERE u.kind='update' AND u.status='applied' AND u.created >= ?
            AND NOT EXISTS (SELECT 1 FROM operations r WHERE r.reverses=u.id AND r.status='applied')""", (cutoff,)):
            changes = json.loads(op["changes"])
            before = next((decode_note(c["before"])[0] for c in changes if c["before"]), None)
            after = next((decode_note(c["after"])[0] for c in changes if c["after"]), None)
            if before and after:
                if before["status"] != after["status"]:
                    if after["status"] in {"done", "cancelled"}: result[after["status"]] += 1
                if before.get("due") != after.get("due") and before.get("due"):
                    result["deferred"] += 1
        return result

    def feedback(self, note_id, action, expected_version):
        with self.lock, self._db() as db:
            self._recover(db)
            note = self._find(note_id)
            if note["version"] != expected_version:
                raise ConflictError("Заметка уже изменена. Обновите список")
            if action == "archive":
                return self._update(db, note, {"status": "archived"}, expected_version)
            if action not in {"soon", "later"}:
                raise ValidationError("Неизвестная команда карточки")
            row = db.execute("SELECT * FROM schedule WHERE note_id=?", (note_id,)).fetchone()
            steps = [7, 21, 45, 90, 180] if note["kind"] == "media" else [3, 14, 45, 120]
            step = min((row["step"] + 1) if row else 0, len(steps) - 1) if action == "later" else 0
            days = steps[step] if action == "later" else 1
            db.execute("INSERT OR REPLACE INTO schedule VALUES (?,?,?)", (note_id, (self.clock() + timedelta(days=days)).isoformat(), step))
            return note

    def history(self):
        with self.lock, self._db() as db:
            self._recover(db)
            return [dict(row) for row in db.execute("SELECT id,note_id,kind,status,created,error,reverses FROM operations ORDER BY rowid DESC LIMIT 200")]

    def undo(self, operation_id):
        with self.lock, self._db() as db:
            self._recover(db)
            op = db.execute("SELECT * FROM operations WHERE id=?", (operation_id,)).fetchone()
            if not op:
                raise NotFoundError("Операция не найдена")
            if op["status"] != "applied" or op["kind"] == "undo":
                raise ConflictError("Эту операцию нельзя отменить автоматически")
            if db.execute("SELECT 1 FROM operations WHERE reverses=? AND status IN ('applied','pending')", (operation_id,)).fetchone():
                raise ConflictError("Эта операция уже отменена")
            changes = json.loads(op["changes"])
            for c in changes:
                if self._content(c["path"]) != c["after"]:
                    raise ConflictError("После операции заметка менялась. Автоматическая отмена остановлена")
            if op["kind"] == "capture":
                note = self._find(op["note_id"])
                # Original captures are retained, even when the user undoes capture.
                return self._update(db, note, {"status": "archived"}, note["version"], reverses=operation_id)
            inverse = [dict(path=c["path"], before=c["after"], after=c["before"]) for c in reversed(changes)]
            self._operation(db, op["note_id"], "undo", inverse, reverses=operation_id)
            return self._find(op["note_id"])

    def backup(self, destination):
        dest = Path(destination).resolve()
        if dest.is_relative_to(self.root) or dest.is_relative_to(self.vault):
            raise ValidationError("Сохраняйте архив вне папки данных")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self.lock, self._db() as db, tempfile.TemporaryDirectory() as tmp:
            self._recover(db)
            snapshot = Path(tmp) / "journal.sqlite3"
            with closing(sqlite3.connect(snapshot)) as target:
                db.backup(target)
            # Exclusive create: never replace an existing backup.
            with zipfile.ZipFile(dest, "x", zipfile.ZIP_DEFLATED) as archive:
                for p in self.vault.rglob("*"):
                    if p.is_file() and p.resolve().is_relative_to(self.vault):
                        archive.write(p, "vault/" + p.relative_to(self.vault).as_posix())
                archive.write(snapshot, "state/journal.sqlite3")
            # API token intentionally regenerated on restore.
        return dest
