"""Durable shared preferences and transaction receipts for all local clients."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3

from .core import ConflictError, KINDS, ValidationError


def encoded(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ValidationError("Требуется JSON с конечными числами") from exc


def identifier(value, name="id"):
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValidationError(f"{name}: требуется непустая строка до 256 символов")
    return value


class SharedState:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.path = self.root / "state" / "shared.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS receipts (scope TEXT, id TEXT, hash TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(scope,id))")

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def receipt(db, scope, command):
        if not isinstance(command, dict):
            raise ValidationError("Ожидается объект команды")
        key = identifier(command.get("request_id"), "request_id")
        fingerprint = hashlib.sha256(encoded(command).encode()).hexdigest()
        row = db.execute("SELECT hash,result FROM receipts WHERE scope=? AND id=?", (scope, key)).fetchone()
        if row:
            if row["hash"] != fingerprint:
                raise ConflictError("request_id уже использован с другим содержимым")
            return json.loads(row["result"])
        return None

    @staticmethod
    def remember(db, scope, command, result):
        db.execute("INSERT INTO receipts VALUES(?,?,?,?)", (scope, command["request_id"],
                   hashlib.sha256(encoded(command).encode()).hexdigest(), encoded(result)))
        return result

    @staticmethod
    def check_revision(command, revision):
        expected = command.get("expected_revision")
        if type(expected) is not int or expected < 0:
            raise ValidationError("Требуется expected_revision: целое >=0")
        if expected != revision:
            raise ConflictError("Данные изменены другим окном; перечитайте актуальную версию")

    @staticmethod
    def _recall(db):
        row = db.execute("SELECT value FROM settings WHERE key='recall'").fetchone()
        return json.loads(row[0]) if row else dict(revision=0, kinds=None, context_ids=None, paused=False, selected_id=None)

    def recall(self):
        with self.db() as db:
            return self._recall(db)

    def set_recall(self, command):
        with self.db() as db:
            previous = self.receipt(db, "recall", command)
            if previous is not None:
                return previous
            current = self._recall(db)
            self.check_revision(command, current["revision"])
            patch = command.get("patch", {})
            if not isinstance(patch, dict) or set(patch) - {"kinds", "context_ids", "paused", "selected_id"}:
                raise ValidationError("Неизвестные настройки Вспомнить")
            if "reset" in command and type(command["reset"]) is not bool:
                raise ValidationError("reset должен быть boolean")
            if command.get("reset"):
                current.update(kinds=None, context_ids=None, paused=False, selected_id=None)
            if "kinds" in patch and patch["kinds"] is not None:
                values = patch["kinds"]
                if not isinstance(values, list) or any(not isinstance(v, str) or v not in KINDS for v in values):
                    raise ValidationError("Неизвестные разделы библиотеки")
                patch = {**patch, "kinds": sorted(set(values))}
            if "context_ids" in patch and patch["context_ids"] is not None:
                values = patch["context_ids"]
                if not isinstance(values, list) or len(values) > 1000:
                    raise ValidationError("context_ids должен быть списком")
                for value in values:
                    if value is not None:
                        identifier(value, "context_id")
                patch = {**patch, "context_ids": list(dict.fromkeys(values))}
            if "paused" in patch and type(patch["paused"]) is not bool:
                raise ValidationError("paused должен быть boolean")
            if patch.get("selected_id") is not None:
                identifier(patch["selected_id"], "selected_id")
            current.update(patch)
            current["revision"] += 1
            db.execute("INSERT OR REPLACE INTO settings VALUES('recall',?)", (encoded(current),))
            return self.remember(db, "recall", command, current)

    def preferences(self):
        with self.db() as db:
            row = db.execute("SELECT value FROM settings WHERE key='ui'").fetchone()
            return json.loads(row[0]) if row else {"revision": 0, "values": {}}

    def set_preferences(self, command):
        with self.db() as db:
            old = self.receipt(db, "ui", command)
            if old is not None:
                return old
            row = db.execute("SELECT value FROM settings WHERE key='ui'").fetchone()
            current = json.loads(row[0]) if row else {"revision": 0, "values": {}}
            self.check_revision(command, current["revision"])
            patch = command.get("patch")
            allowed = {"panel:calendar-importance", "panel:theme", "panel:design", "panel:locale", "time:preset"}
            if not isinstance(patch, dict) or set(patch) - allowed or len(encoded(patch)) > 100000:
                raise ValidationError("Недопустимые общие настройки интерфейса")
            if "time:preset" in patch:
                preset = patch["time:preset"]
                if (not isinstance(preset, dict) or set(preset) != {"work_ms", "break_ms"}
                        or any(type(value) is not int or not 60_000 <= value <= 10_800_000
                               for value in preset.values())):
                    raise ValidationError("time:preset requires work_ms and break_ms between 60000 and 10800000")
            current["values"].update(patch)
            current["revision"] += 1
            db.execute("INSERT OR REPLACE INTO settings VALUES('ui',?)", (encoded(current),))
            return self.remember(db, "ui", command, current)
