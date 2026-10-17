"""Explicit authenticated browser actions; ambiguous executions are never retried."""
import json
import time

from .core import ConflictError, NotFoundError, ValidationError
from .shared_state import SharedState, encoded, identifier

ACTIONS = {"open-workspace", "stop-workspace", "capture-workspace", "chat-setup", "chat-connect", "active-tab"}


class BrowserBridge(SharedState):
    def __init__(self, root, clock=None):
        super().__init__(root)
        self.clock = clock or (lambda: int(time.time() * 1000))
        with self.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS browser_clients (id TEXT PRIMARY KEY, seen INTEGER)")
            db.execute("""CREATE TABLE IF NOT EXISTS browser_commands
                (id TEXT PRIMARY KEY, action TEXT NOT NULL, payload TEXT NOT NULL,
                 status TEXT NOT NULL, created INTEGER, claimed INTEGER, client TEXT,
                 result TEXT, error TEXT)""")

    @staticmethod
    def _expire(db, now):
        db.execute("UPDATE browser_commands SET status='uncertain',error=? WHERE status='running' AND claimed<?",
                   ("Браузер не подтвердил результат. Проверьте действие перед повтором.", now - 30000))
        db.execute("UPDATE browser_commands SET status='error',error=? WHERE status='pending' AND created<?",
                   ("Браузер не получил команду вовремя", now - 30000))

    @staticmethod
    def _status(db, now):
        seen = db.execute("SELECT MAX(seen) FROM browser_clients").fetchone()[0]
        return {"available": seen is not None and 0 <= now - seen <= 15000, "last_seen": seen}

    def status(self):
        with self.db() as db:
            return self._status(db, self.clock())

    @staticmethod
    def _command(row):
        return dict(id=row["id"], action=row["action"], payload=json.loads(row["payload"]),
                    status=row["status"], result=json.loads(row["result"]) if row["result"] else None, error=row["error"])

    def submit(self, data):
        key = identifier(data.get("request_id"), "request_id")
        action, payload = data.get("action"), data.get("payload", {})
        if not isinstance(action, str) or action not in ACTIONS or not isinstance(payload, dict):
            raise ValidationError("Неизвестное действие браузера/неверный payload")
        with self.db() as db:
            self._expire(db, self.clock())
            row = db.execute("SELECT * FROM browser_commands WHERE id=?", (key,)).fetchone()
            if row:
                if row["action"] != action or row["payload"] != encoded(payload):
                    raise ConflictError("request_id уже использован")
                return self._command(row)
            if not self._status(db, self.clock())["available"]:
                raise ConflictError("Расширение Chrome недоступно; откройте Chrome и подключите Brainalot")
            db.execute("INSERT INTO browser_commands(id,action,payload,status,created) VALUES(?,?,?,'pending',?)",
                       (key, action, encoded(payload), self.clock()))
            return self._command(db.execute("SELECT * FROM browser_commands WHERE id=?", (key,)).fetchone())

    def poll(self, client_id):
        identifier(client_id, "client_id")
        with self.db() as db:
            now = self.clock()
            db.execute("INSERT OR REPLACE INTO browser_clients VALUES(?,?)", (client_id, now))
            self._expire(db, now)
            rows = db.execute("SELECT id FROM browser_commands WHERE status='pending' ORDER BY created LIMIT 20").fetchall()
            result = []
            for row in rows:
                db.execute("UPDATE browser_commands SET status='running',client=?,claimed=? WHERE id=?", (client_id, now, row[0]))
                result.append(self._command(db.execute("SELECT * FROM browser_commands WHERE id=?", (row[0],)).fetchone()))
            return {"commands": result}

    def get(self, key):
        with self.db() as db:
            self._expire(db, self.clock())
            row = db.execute("SELECT * FROM browser_commands WHERE id=?", (key,)).fetchone()
            if not row:
                raise NotFoundError("Команда не найдена")
            return self._command(row)

    def complete(self, key, data):
        client = identifier(data.get("client_id"), "client_id")
        if "error" in data and data["error"] is not None and (not isinstance(data["error"], str) or len(data["error"]) > 4000):
            raise ValidationError("error: строка до 4000 символов")
        result, error = encoded(data.get("result")), data.get("error")
        with self.db() as db:
            row = db.execute("SELECT * FROM browser_commands WHERE id=?", (key,)).fetchone()
            if not row:
                raise NotFoundError("Команда не найдена")
            if row["client"] != client:
                raise ConflictError("Команда принадлежит другому клиенту")
            if row["status"] in {"done", "error"}:
                if row["result"] != result or row["error"] != error:
                    raise ConflictError("Результат команды уже записан")
            else:
                db.execute("UPDATE browser_commands SET status=?,result=?,error=? WHERE id=?",
                           ("error" if error else "done", result, error, key))
            return self._command(db.execute("SELECT * FROM browser_commands WHERE id=?", (key,)).fetchone())
