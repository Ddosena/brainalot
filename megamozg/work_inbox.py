"""Work mode inbox: the Work page's snapshot for agents and the agents' proposals for the page.

The Work mode keeps its data in the browser. After each save the page sends a trimmed copy here
(``state/work/snapshot.json``) so an agent can see projects and tasks (``mmm work snapshot``)
without touching the browser. The agent answers with proposals (``mmm work propose``): they are
checked against the snapshot and the collected chat messages, and wait in the page's "Из чатов"
tray until the user accepts or rejects them. Nothing reaches the calendar without that click.
"""
from __future__ import annotations

from datetime import date, datetime
import json
from pathlib import Path
import re
import uuid

from .chats import ChatStore, _text, _url
from .core import ConflictError, NotFoundError, Store, ValidationError

KINDS = ("task", "deadline", "event", "attach")
STATUSES = ("pending", "accepted", "rejected")
SNAPSHOT_LIMIT = 240_000
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _day(value, name: str) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValidationError(f"{name}: дата YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"{name}: несуществующая дата") from exc
    return value


class WorkInbox:
    def __init__(self, root: str | Path, chats: ChatStore | None = None):
        self.root = Path(root).resolve()
        self.chats = chats or ChatStore(self.root)
        self.snapshot_path = self.root / "state" / "work" / "snapshot.json"

    # --- snapshot ---

    def save_snapshot(self, state) -> dict:
        from .work_store import WorkStore
        canonical = WorkStore(self.root).read()
        if canonical["initialized"]:
            raise ConflictError("Work перенесён в общий сервис; устаревший снимок браузера не записан")
        body = self._snapshot_body(state)
        Store._atomic_file(self.snapshot_path, json.dumps(body, ensure_ascii=False))
        return {"received": body["received"], "projects": len(body["projects"]), "items": len(body["items"])}

    @staticmethod
    def _snapshot_body(state, *, canonical=False) -> dict:
        def text(value, name, limit, required=False):
            if canonical:
                # Full Work has already been validated. Agent presentation truncates
                # descriptive fields, never identities, without imposing the old HTTP limit.
                value = value if isinstance(value, str) else ""
                return value if name.endswith((".id", ".project")) else value[:limit]
            return _text(value, name, limit, required=required)

        def day(value, name):
            try:
                return _day(value, name)
            except ValidationError:
                if canonical:
                    return None
                raise
        if not isinstance(state, dict) or not isinstance(state.get("projects"), list) or not isinstance(state.get("items"), list):
            raise ValidationError("Снимок Work: ожидаются projects и items")
        projects = []
        for project in state["projects"] if canonical else state["projects"][:100]:
            if not isinstance(project, dict):
                raise ValidationError("Снимок Work: проект должен быть объектом")
            projects.append({"id": text(project.get("id"), "project.id", 64, required=True),
                             "name": text(project.get("name"), "project.name", 200),
                             "about": text(project.get("about"), "project.about", 300)})
        items = []
        for item in state["items"] if canonical else state["items"][:2000]:
            if not isinstance(item, dict):
                raise ValidationError("Снимок Work: запись должна быть объектом")
            groups = item.get("groups") if isinstance(item.get("groups"), list) else []
            checklist = item.get("checklist") if isinstance(item.get("checklist"), list) else []
            items.append({
                "id": text(item.get("id"), "item.id", 64, required=True),
                "project": text(item.get("project"), "item.project", 64) or None,
                "kind": item.get("kind") if item.get("kind") in ("task", "deadline", "event") else "task",
                "title": text(item.get("title"), "item.title", 300),
                "start": day(item.get("start"), "item.start"),
                "end": day(item.get("end"), "item.end"),
                "time": item.get("time") if isinstance(item.get("time"), str) and TIME_RE.match(item.get("time") or "") else "",
                "status": item.get("status") if item.get("status") in ("todo", "doing", "done") else "todo",
                "first_step": text(item.get("firstStep"), "item.firstStep", 500),
                "notes": text(item.get("notes"), "item.notes", 20000)[:1000],
                "checklist": [{"text": str(c.get("text", ""))[:300], "done": bool(c.get("done"))}
                              for c in checklist[:50] if isinstance(c, dict)],
                "links": [str(link.get("url"))[:2000] for group in groups[:20] if isinstance(group, dict)
                          for link in (group.get("links") if isinstance(group.get("links"), list) else [])[:50] if isinstance(link, dict) and link.get("url")],
            })
        body = {"received": datetime.now().astimezone().isoformat(timespec="seconds"),
                "projects": projects, "items": items}
        serialized = json.dumps(body, ensure_ascii=False)
        if not canonical and len(serialized.encode("utf-8")) > SNAPSHOT_LIMIT:
            raise ValidationError("Снимок Work слишком большой")
        return body

    def snapshot(self) -> dict | None:
        from .work_store import WorkStore
        canonical = WorkStore(self.root).read()
        if canonical["initialized"]:
            # Derive on read, so desktop edits are authoritative without Chrome.
            return self._snapshot_body(canonical["state"], canonical=True)
        try:
            return json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def snapshot_for_agent(self) -> dict:
        snapshot = self.snapshot()
        if snapshot is None:
            return {"available": False, "hint": "Снимка нет: попросите пользователя открыть Work-вкладку Brainalot"}
        received = datetime.fromisoformat(snapshot["received"])
        age_hours = round((datetime.now().astimezone() - received).total_seconds() / 3600, 1)
        return {"available": True, "received": snapshot["received"], "age_hours": age_hours,
                "stale": age_hours > 24, "today": date.today().isoformat(),
                "projects": snapshot["projects"], "items": snapshot["items"]}

    # --- proposals ---

    def _check(self, raw: dict, snapshot: dict | None) -> dict:
        if not isinstance(raw, dict):
            raise ValidationError("Предложение должно быть объектом")
        allowed = {"external_id", "kind", "project_id", "project_name", "title", "start", "end", "time", "first_step",
                   "notes", "checklist", "links", "files", "target_item_id", "sources", "reason"}
        if set(raw) - allowed:
            raise ValidationError(f"Неизвестные поля предложения: {', '.join(sorted(set(raw) - allowed))}")
        kind = raw.get("kind")
        if kind not in KINDS:
            raise ValidationError("kind: task, deadline, event или attach")
        projects = {p["id"] for p in (snapshot or {}).get("projects", [])}
        items = {i["id"]: i for i in (snapshot or {}).get("items", [])}
        project_id = _text(raw.get("project_id"), "project_id", 64) or None
        project_name = _text(raw.get("project_name"), "project_name", 200) or None
        if project_id and snapshot is not None and project_id not in projects:
            raise ValidationError(f"project_id: нет проекта {project_id} в снимке Work")
        target = _text(raw.get("target_item_id"), "target_item_id", 64) or None
        if kind == "attach":
            if not target:
                raise ValidationError("attach: укажите target_item_id")
            if snapshot is not None and target not in items:
                raise ValidationError(f"target_item_id: нет записи {target} в снимке Work")
            project_id = project_id or (items.get(target) or {}).get("project")
        elif target:
            raise ValidationError("target_item_id только для attach")
        title = _text(raw.get("title"), "title", 300, required=kind != "attach")
        start, end = _day(raw.get("start"), "start"), _day(raw.get("end"), "end")
        if kind in ("deadline", "event") and start:
            end = start
        elif start and not end:
            end = start
        elif end and not start:
            start = end
        if start and end and end < start:
            raise ValidationError("end раньше start")
        time = _text(raw.get("time"), "time", 5)
        if time and not TIME_RE.match(time):
            raise ValidationError("time: ЧЧ:ММ")
        checklist = raw.get("checklist") or []
        if not isinstance(checklist, list) or len(checklist) > 30:
            raise ValidationError("checklist: список до 30 пунктов")
        links = raw.get("links") or []
        if not isinstance(links, list) or len(links) > 30:
            raise ValidationError("links: список до 30 ссылок")
        clean_links = []
        for link in links:
            if not isinstance(link, dict) or set(link) - {"title", "url"}:
                raise ValidationError("links: объекты title/url")
            clean_links.append({"title": _text(link.get("title"), "links.title", 300), "url": _url(link.get("url"), "links.url")})
        files = raw.get("files") or []
        if not isinstance(files, list) or len(files) > 20:
            raise ValidationError("files: список до 20 файлов")
        clean_files = []
        for item in files:
            if not isinstance(item, dict) or set(item) - {"name", "path"}:
                raise ValidationError("files: объекты name/path")
            path = _text(item.get("path"), "files.path", 1000)
            if path and not Path(path).resolve().is_relative_to(self.chats.files_dir.resolve()):
                raise ValidationError("files.path: только файлы, скачанные из чатов")
            clean_files.append({"name": _text(item.get("name"), "files.name", 255, required=True), "path": path or None})
        sources = raw.get("sources") or []
        if not isinstance(sources, list) or not 1 <= len(sources) <= 20:
            raise ValidationError("sources: от 1 до 20 исходных сообщений")
        clean_sources = []
        for source in sources:
            if not isinstance(source, dict) or set(source) - {"chat", "message_id", "quote"}:
                raise ValidationError("sources: объекты chat/message_id/quote")
            chat = _text(source.get("chat"), "sources.chat", 80, required=True)
            message = self.chats.message(chat, _text(str(source.get("message_id", "")), "sources.message_id", 64, required=True))
            if message is None:
                raise ValidationError(f"sources: нет сообщения {source.get('message_id')} в чате {chat}")
            chat_title = next((c["title"] for c in self.chats.chats() if c["key"] == chat), chat)
            # Author, time and link come from the collected message, not from the agent.
            clean_sources.append({"chat": chat, "chat_title": chat_title, "message_id": message["id"],
                                  "author": message["author"], "date": message["date"], "link": message["link"],
                                  "quote": _text(source.get("quote"), "sources.quote", 500) or message["text"][:300]})
        return {
            "kind": kind, "project_id": project_id, "project_name": project_name, "title": title,
            "start": start, "end": end, "time": time,
            "first_step": _text(raw.get("first_step"), "first_step", 500),
            "notes": _text(raw.get("notes"), "notes", 4000),
            "checklist": [_text(item, "checklist", 300, required=True) for item in checklist],
            "links": clean_links, "files": clean_files, "target_item_id": target,
            "sources": clean_sources, "reason": _text(raw.get("reason"), "reason", 500),
        }

    def propose(self, proposals, *, agent: str = "agent") -> dict:
        if isinstance(proposals, dict):
            proposals = proposals.get("proposals")
        if not isinstance(proposals, list) or not 1 <= len(proposals) <= 100:
            raise ValidationError("Ожидается {\"proposals\": [...]} от 1 до 100 предложений")
        agent = _text(agent, "agent", 40) or "agent"
        snapshot = self.snapshot()
        checked = []
        for index, raw in enumerate(proposals):
            external_id = _text((raw or {}).get("external_id") if isinstance(raw, dict) else None,
                                f"proposals[{index}].external_id", 200, required=True)
            try:
                checked.append((external_id, self._check(raw, snapshot)))
            except ValidationError as exc:
                raise ValidationError(f"proposals[{index}]: {exc}") from exc
        results = []
        with self.chats.lock, self.chats._db() as db:
            for external_id, payload in checked:
                body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                row = db.execute("SELECT * FROM proposals WHERE external_id=?", (external_id,)).fetchone()
                if row:
                    if row["payload"] != body:
                        raise ConflictError(f"external_id {external_id} уже использован с другим содержимым")
                    results.append({"id": row["id"], "external_id": external_id, "status": row["status"], "repeated": True})
                    continue
                proposal_id = uuid.uuid4().hex[:12]
                db.execute("INSERT INTO proposals(id, external_id, created, agent, status, payload) VALUES (?,?,?,?,?,?)",
                           (proposal_id, external_id, self.chats.now(), agent, "pending", body))
                results.append({"id": proposal_id, "external_id": external_id, "status": "pending", "repeated": False})
        return {"proposals": results, "snapshot": bool(snapshot)}

    def list(self, status: str | None = "pending") -> list[dict]:
        if status not in (None, "all") and status not in STATUSES:
            raise ValidationError("status: pending, accepted, rejected или all")
        query = "SELECT * FROM proposals" + ("" if status in (None, "all") else " WHERE status=?") + " ORDER BY created, rowid"
        with self.chats._db() as db:
            rows = db.execute(query, () if status in (None, "all") else (status,)).fetchall()
        return [{"id": row["id"], "external_id": row["external_id"], "created": row["created"], "agent": row["agent"],
                 "status": row["status"], "resolved": row["resolved"],
                 "resolution": json.loads(row["resolution"]) if row["resolution"] else None,
                 **json.loads(row["payload"])} for row in rows]

    def resolve(self, proposal_id: str, action: str, item_id: str | None = None) -> dict:
        if action not in ("accept", "reject"):
            raise ValidationError("action: accept или reject")
        status = "accepted" if action == "accept" else "rejected"
        item_id = _text(item_id, "item_id", 64) or None
        with self.chats.lock, self.chats._db() as db:
            row = db.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
            if not row:
                raise NotFoundError("Предложение не найдено")
            if row["status"] == status:
                return {"id": proposal_id, "status": status, "repeated": True}
            if row["status"] != "pending":
                raise ConflictError("Предложение уже разобрано")
            db.execute("UPDATE proposals SET status=?, resolved=?, resolution=? WHERE id=?",
                       (status, self.chats.now(), json.dumps({"item_id": item_id}), proposal_id))
        return {"id": proposal_id, "status": status, "repeated": False}
