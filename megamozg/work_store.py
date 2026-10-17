"""Full Work documents and immutable chunked blobs, committed atomically with CAS."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import time

from .core import ConflictError, NotFoundError, ValidationError
from .shared_state import SharedState, encoded, identifier

CHUNK_SIZE = 128 * 1024
MAX_BLOB = 64 * 1024 * 1024


class WorkStore(SharedState):
    def __init__(self, root):
        super().__init__(root)
        with self.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS work_blobs (hash TEXT PRIMARY KEY, body BLOB NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS work_chunks (hash TEXT, idx INTEGER, total INTEGER NOT NULL, body BLOB NOT NULL, PRIMARY KEY(hash,idx))")
            db.execute("CREATE TABLE IF NOT EXISTS work_versions (revision INTEGER PRIMARY KEY, created INTEGER NOT NULL, state_blob TEXT NOT NULL, images TEXT NOT NULL)")

    @staticmethod
    def _hash(value):
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
            raise ValidationError("Требуется SHA-256 lowercase hex")
        return value

    def upload_chunk(self, digest, command):
        self._hash(digest)
        index, total = command.get("index"), command.get("total")
        if type(index) is not int or type(total) is not int or not 0 <= index < total <= MAX_BLOB // CHUNK_SIZE:
            raise ValidationError("Неверные index/total chunks")
        try:
            raw = base64.b64decode(command.get("content_base64", ""), validate=True)
        except (ValueError, TypeError, binascii.Error) as exc:
            raise ValidationError("Неверный base64") from exc
        if len(raw) > CHUNK_SIZE or (not raw and (index != 0 or total != 1)):
            raise ValidationError("Chunk до 131072 bytes; пустой blob передаётся одним chunk")
        with self.db() as db:
            done = db.execute("SELECT body FROM work_blobs WHERE hash=?", (digest,)).fetchone()
            if done:
                # Verify retry against original fixed-size chunk boundaries too.
                body = done[0]
                if total != max(1, (len(body) + CHUNK_SIZE - 1) // CHUNK_SIZE) or body[index * CHUNK_SIZE:(index + 1) * CHUNK_SIZE] != raw:
                    raise ConflictError("Blob уже загружен с другим разбиением/содержимым")
                return dict(blob=digest, complete=True, received=total, total=total, size=len(body))
            if index < total - 1 and len(raw) != CHUNK_SIZE:
                raise ValidationError("Промежуточные chunks должны быть ровно 128 KiB")
            rows = db.execute("SELECT idx,total,body FROM work_chunks WHERE hash=? ORDER BY idx", (digest,)).fetchall()
            if any(r["total"] != total or (r["idx"] == index and r["body"] != raw) for r in rows):
                raise ConflictError("Chunk уже существует с другим содержимым")
            db.execute("INSERT OR IGNORE INTO work_chunks VALUES(?,?,?,?)", (digest, index, total, raw))
            rows = db.execute("SELECT body FROM work_chunks WHERE hash=? ORDER BY idx", (digest,)).fetchall()
            size = sum(len(r[0]) for r in rows)
            complete = len(rows) == total
            if complete:
                body = b"".join(r[0] for r in rows)
                if hashlib.sha256(body).hexdigest() != digest:
                    raise ValidationError("SHA-256 загруженного blob не совпал")
                db.execute("INSERT INTO work_blobs VALUES(?,?)", (digest, body))
                db.execute("DELETE FROM work_chunks WHERE hash=?", (digest,))
            return dict(blob=digest, complete=complete, received=len(rows), total=total, size=size)

    @staticmethod
    def _blob(db, digest):
        row = db.execute("SELECT body FROM work_blobs WHERE hash=?", (digest,)).fetchone()
        if not row:
            raise ValidationError("Blob не загружен полностью: " + digest)
        return row[0]

    @staticmethod
    def _version(db, revision=None):
        if revision is None:
            return db.execute("SELECT * FROM work_versions ORDER BY revision DESC LIMIT 1").fetchone()
        return db.execute("SELECT * FROM work_versions WHERE revision=?", (revision,)).fetchone()

    def _read(self, db, version=None):
        row = version if version is not None else self._version(db)
        if row is None:
            return dict(revision=0, initialized=False, state=None, images=[])
        return dict(revision=row["revision"], initialized=True,
                    state=json.loads(self._blob(db, row["state_blob"])), images=json.loads(row["images"]))

    def read(self):
        with self.db() as db:
            return self._read(db)

    def _validate(self, db, digest, images):
        self._hash(digest)
        try:
            state = json.loads(self._blob(db, digest).decode("utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise ValidationError("Work state должен быть UTF-8 JSON") from exc
        if not isinstance(state, dict) or state.get("version") != 1:
            raise ValidationError("Ожидается Work state version=1")
        for collection in ("projects", "items", "boards"):
            entries = state.get(collection)
            if not isinstance(entries, list):
                raise ValidationError("Work: требуется список " + collection)
            seen = set()
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValidationError("Work: запись должна быть объектом")
                key = identifier(entry.get("id"))
                for field in ("title", "name", "notes", "about", "firstStep"):
                    if field in entry and not isinstance(entry[field], str):
                        raise ValidationError("Work: " + field + " должен быть строкой")
                if key in seen:
                    raise ValidationError("Work: повторный ID в " + collection)
                seen.add(key)
        if not isinstance(images, list) or len(images) > 10000:
            raise ValidationError("Ожидается список images")
        seen, descriptors = set(), []
        for image in images:
            if not isinstance(image, dict) or set(image) != {"id", "blob", "mime"}:
                raise ValidationError("Image: требуются id, blob, mime")
            key = identifier(image["id"], "image.id")
            if key in seen:
                raise ValidationError("Повторный image.id")
            seen.add(key)
            self._hash(image["blob"])
            mime = image["mime"]
            if not isinstance(mime, str) or (mime != "application/octet-stream" and not re.fullmatch(r"image/[a-zA-Z0-9.+-]{1,64}", mime)):
                raise ValidationError("Image: требуется image MIME или application/octet-stream")
            data = self._blob(db, image["blob"])
            descriptors.append({**image, "size": len(data)})
        for board in state["boards"]:
            if not isinstance(board.get("items"), list):
                raise ValidationError("Work board: требуется items")
            board_ids = set()
            for item in board["items"]:
                if not isinstance(item, dict):
                    raise ValidationError("Work board item должен быть объектом")
                key = identifier(item.get("id"), "board.item.id")
                if key in board_ids:
                    raise ValidationError("Повторный board.item.id")
                board_ids.add(key)
                if item.get("type") == "image":
                    image_id = identifier(item.get("imageId"), "imageId")
                    if image_id not in seen:
                        raise ValidationError("Не перенесено изображение доски: " + image_id)
        # Serialize once to reject non-finite JSON numbers, while preserving all fields.
        try:
            encoded(state)
        except ValueError as exc:
            raise ValidationError("Неверные числа в Work JSON") from exc
        return descriptors

    def commit(self, command):
        with self.db() as db:
            old = self.receipt(db, "work", command)
            if old is not None:
                return old
            current = self._version(db)
            revision = current["revision"] if current else 0
            self.check_revision(command, revision)
            descriptors = self._validate(db, command.get("state_blob"), command.get("images"))
            db.execute("INSERT INTO work_versions VALUES(?,?,?,?)", (revision + 1, int(time.time()*1000),
                       command["state_blob"], encoded(descriptors)))
            return self.remember(db, "work", command, self._read(db))

    def revisions(self):
        with self.db() as db:
            return {"revisions": [dict(r) for r in db.execute("SELECT revision,created FROM work_versions ORDER BY revision DESC")]}

    def restore(self, command):
        with self.db() as db:
            old = self.receipt(db, "work_restore", command)
            if old is not None:
                return old
            current = self._version(db)
            self.check_revision(command, current["revision"] if current else 0)
            if type(command.get("revision")) is not int:
                raise ValidationError("Требуется revision")
            target = self._version(db, command["revision"])
            if target is None:
                raise NotFoundError("Версия Work не найдена")
            db.execute("INSERT INTO work_versions VALUES(?,?,?,?)", (current["revision"] + 1, int(time.time()*1000),
                       target["state_blob"], target["images"]))
            return self.remember(db, "work_restore", command, self._read(db))

    def image(self, image_id):
        with self.db() as db:
            row = self._version(db)
            for item in json.loads(row["images"]) if row else []:
                if item["id"] == image_id:
                    return self._blob(db, item["blob"]), item["mime"]
            raise NotFoundError("Изображение Work не найдено")
