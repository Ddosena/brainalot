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
            db.execute("CREATE TABLE IF NOT EXISTS work_cube_deletes (request_id TEXT PRIMARY KEY, command TEXT NOT NULL, status TEXT NOT NULL, note_id TEXT, note_version TEXT, operation_id TEXT, state_blob TEXT NOT NULL, images TEXT NOT NULL, error TEXT)")
        from .work_delete import recover
        recover(self)

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
        state = json.loads(self._blob(db, row["state_blob"]))
        if "planning" in state:
            from .work_planning import project_task_importance
            project_task_importance(state["planning"])
        return dict(revision=row["revision"], initialized=True,
                    state=state, images=json.loads(row["images"]))

    def read(self):
        from .work_delete import recover, metadata
        recover(self)
        with self.db() as db:
            result = self._read(db)
            state = metadata(db)
            if state is not None:
                result["deletion_state"] = state
            return result

    @staticmethod
    def _guard_deletions(previous, incoming, *, allow_remaining_epoch=False):
        before = (previous or {}).get("cube_deletions", {})
        after = (incoming or {}).get("cube_deletions", {})
        if before != after:
            raise ConflictError("Нельзя восстановить или изменить удалённые этапы обычным импортом/восстановлением Work")
        if not allow_remaining_epoch and (previous or {}).get("remaining_epochs", {}) != (incoming or {}).get("remaining_epochs", {}):
            raise ConflictError("Нельзя изменить базу остатка обычным восстановлением Work")
        for cube_id in before:
            old = previous.get("constructor", {}).get("cubes", {}).get(cube_id)
            new = (incoming or {}).get("constructor", {}).get("cubes", {}).get(cube_id)
            if old != new:
                raise ConflictError("История удалённого этапа защищена от изменения")

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
        if "planning" in state:
            from .work_planning import validate_planning
            validate_planning(state["planning"], state)
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
        from .work_delete import recover, ensure_writable
        recover(self)
        with self.db() as db:
            ensure_writable(db)
            if "restore_import" in command and type(command["restore_import"]) is not bool:
                raise ValidationError("restore_import: требуется bool")
            old = self.receipt(db, "work", command)
            if old is not None:
                return old
            current = self._version(db)
            revision = current["revision"] if current else 0
            self.check_revision(command, revision)
            descriptors = self._validate(db, command.get("state_blob"), command.get("images"))
            incoming = json.loads(self._blob(db, command["state_blob"]))
            previous = json.loads(self._blob(db, current["state_blob"])) if current else {}
            self._guard_deletions(previous.get("planning"), incoming.get("planning"))
            # A round trip of read-time defaults is not a planning edit.
            from .work_planning import project_task_importance
            for document in (incoming, previous):
                if "planning" in document:
                    project_task_importance(document["planning"])
            if incoming.get("planning") != previous.get("planning") and command.get("restore_import") is not True:
                raise ValidationError("План Work меняется только через /api/work/planning")
            db.execute("INSERT INTO work_versions VALUES(?,?,?,?)", (revision + 1, int(time.time()*1000),
                       command["state_blob"], encoded(descriptors)))
            return self.remember(db, "work", command, self._read(db))

    def _calendar_response(self, planning, *, force):
        """Read the whole planning horizon from Brainalot Calendar as one response."""
        from datetime import date, timedelta
        from .calendar import CalendarFeed
        from .calendar_bridge import CalendarBridge
        from .google_calendar_api import GoogleCalendarApi
        first = date.fromisoformat(planning["settings"]["start"])
        last = date.fromisoformat(planning["settings"]["end"])
        feed = CalendarFeed(self.root)
        google = GoogleCalendarApi(self.root)
        api = google if google.status().get("authorized") else None
        bridge = CalendarBridge(self.root)
        readers = {"google_calendar": api, **({"bridge": bridge} if bridge.configured() else {})}
        responses = []
        cursor = first
        while cursor <= last:
            chunk_end = min(last, cursor + timedelta(days=62))
            responses.append(feed.events(cursor, chunk_end, force=force, **readers))
            cursor = chunk_end + timedelta(days=1)
        identities = set()
        events = []
        for part in responses:
            for event in part["events"]:
                identity = (event.get("calendar_id"), event.get("occurrence_id") or event.get("id"))
                if identity not in identities:
                    identities.add(identity)
                    events.append(event)
        counts = {part.get("calendar_count") for part in responses}
        return {
            "events": events,
            "configured": all(part.get("configured") for part in responses),
            "calendar_count": responses[0].get("calendar_count", 0) if responses else 0,
            "stale": any(part.get("stale") or part.get("error") for part in responses) or len(counts) > 1,
            "error": "Неполный плановый снимок календаря" if
                     any(part.get("error") for part in responses) or len(counts) > 1 else None,
        }

    def sync_calendar(self):
        """Bring the plan's Calendar snapshot up to date without an agent.

        The snapshot used to change only through an explicit CLI recalculation,
        so events added in Google never reached Day or Week. Compare the local
        feed cache by content and recalculate only when an event really changed.
        """
        current = self.read()
        planning = (current.get("state") or {}).get("planning")
        if not planning or not planning.get("settings") or "deletion_state" in current:
            return None
        response = self._calendar_response(planning, force=False)
        if response["stale"] or response["error"] or not response["configured"]:
            return None
        def content(events):
            # A recoloured event moves between project rulers, so colour counts too.
            return sorted(json.dumps([event.get("calendar_id"), event.get("occurrence_id") or event.get("id"),
                                      event.get("start"), event.get("end"), event.get("title"),
                                      bool(event.get("all_day")), event.get("color"), event.get("color_name")],
                                     ensure_ascii=False) for event in events)
        incoming = content(response["events"])
        if incoming == content(planning.get("calendar", {}).get("events", [])):
            return None
        digest = hashlib.sha256("\n".join(incoming).encode("utf-8")).hexdigest()[:24]
        return self.planning_operation({"request_id": f"calendar-sync-{current['revision']}-{digest}",
                                        "expected_revision": current["revision"], "action": "recalculate",
                                        "data": {"refresh_calendar": True, "reason": "calendar-sync"}})

    def planning_operation(self, command):
        """Apply a typed planning edit under the existing Work CAS and receipt.

        Calendar refresh may make a network request, so it occurs between two
        short transactions. The second CAS prevents it from overwriting edits.
        """
        from copy import deepcopy
        from datetime import date, timedelta
        from .work_planning import (apply_action, calculate, merge_calendar_refresh,
                                    reconcile_segment_estimates, validate_planning, CONSTRUCTOR_ACTIONS,
                                    _constructor_allocation, _constructor_budget_guard)
        if not isinstance(command, dict) or set(command) != {"request_id", "expected_revision", "action", "data"}:
            raise ValidationError("planning: нужны request_id, expected_revision, action, data")
        action, data = command["action"], command["data"]
        from .work_delete import delete_cube, recover, ensure_writable
        if action == "cube_delete":
            return delete_cube(self, command)
        recover(self)
        if action not in {"import", "recalculate", "cube_progress", "day_outcome", "place_cube", "resource",
                          "capacity_override", "weekly_review_done", "describe_cubes",
                          "day_budget", "prep_set", "prep_progress", "day_cube_add", "day_cube_remove",
                          "day_cube_move", "day_cube_resize", "segment_create",
                          "segment_progress", "day_segment_add", "scope_exempt", "task_importance",
                          "monthly_plan_approve"} | CONSTRUCTOR_ACTIONS:
            raise ValidationError("Неизвестное действие planning")
        if not isinstance(data, dict):
            raise ValidationError("planning.data: требуется объект")
        with self.db() as db:
            ensure_writable(db)
            old = self.receipt(db, "work", command)
            if old is not None:
                return old
            row = self._version(db)
            if row is None:
                raise ValidationError("Work не инициализирован")
            self.check_revision(command, row["revision"])
            state = json.loads(self._blob(db, row["state_blob"]))
            images = [{k: image[k] for k in ("id", "blob", "mime")} for image in json.loads(row["images"])]
        allocation_baseline = deepcopy(state.get("planning"))
        if action == "import":
            if set(data) != {"planning"}:
                raise ValidationError("import.data: требуется только planning")
            planning = deepcopy(data["planning"])
            if not isinstance(planning, dict) or not isinstance(planning.get("tasks"), list):
                raise ValidationError("import.planning: требуются объект и tasks[]")
            planning.setdefault("completed_cubes", {})
            planning.setdefault("day_outcomes", [])
            planning.setdefault("locks", [])
            planning.setdefault("history", [])
            planning.setdefault("result", {})
            existing_items = {item["id"] for item in state["items"]}
            for task in planning.get("tasks", []):
                if not isinstance(task, dict):
                    raise ValidationError("import.planning.tasks: нужны объекты")
                item_id = task.get("work_item_id")
                if not item_id or item_id in existing_items:
                    continue
                # Extend the existing Work calendar/tray in the same CAS commit.
                # Do not edit an older manually maintained item.
                state["items"].append({
                    "id": item_id, "project": task["project_id"], "kind": "task",
                    "title": task["title"], "start": None, "end": None, "time": "",
                    "status": "todo", "firstStep": "", "resumeFrom": "", "notes": "",
                    "checklist": [], "groups": [], "createdAt": int(time.time()*1000),
                    "planning_task_id": task["id"],
                })
                existing_items.add(item_id)
            previous = state.get("planning")
            previous_deletions = (previous or {}).get("cube_deletions", {})
            previous_epochs = (previous or {}).get("remaining_epochs", {})
            if "remaining_epochs" in planning and planning["remaining_epochs"] != previous_epochs:
                raise ConflictError("remaining_epochs: служебное поле")
            if previous_epochs:
                planning["remaining_epochs"] = deepcopy(previous_epochs)
            if "cube_deletions" in planning and planning["cube_deletions"] != previous_deletions:
                raise ConflictError("cube_deletions меняется только через cube_delete")
            if previous_deletions:
                planning["cube_deletions"] = deepcopy(previous_deletions)
            previous_monthly = previous.get("monthly_plan") if isinstance(previous, dict) else None
            if "monthly_plan" in planning and planning["monthly_plan"] != previous_monthly:
                raise ValidationError("План месяца согласуется только через monthly_plan_approve")
            if previous_monthly is not None:
                monthly = deepcopy(previous_monthly)
                known_ids = {task["id"] for task in planning["tasks"] if isinstance(task.get("id"), str)}
                retained = [tid for tid in monthly["task_ids"] if tid in known_ids]
                if retained != monthly["task_ids"]:
                    monthly.update(task_ids=retained, status="draft", approved_at=None)
                planning["monthly_plan"] = monthly
            if isinstance(previous, dict) and previous.get("version") == 1:
                # Updating estimates/source data must not erase observed work.
                previous_tasks = {task["id"]: task for task in previous["tasks"]}
                for task in planning["tasks"]:
                    old_task = previous_tasks.get(task.get("id"), {})
                    if "importance_score" not in task and "importance_score" in old_task:
                        task["importance_score"] = old_task["importance_score"]
                planning["completed_cubes"] = {
                    **planning["completed_cubes"], **previous.get("completed_cubes", {})}
                outcomes = {(x["task_id"], x["date"]): x for x in planning["day_outcomes"]}
                outcomes.update({(x["task_id"], x["date"]): x for x in previous.get("day_outcomes", [])})
                planning["day_outcomes"] = list(outcomes.values())
                from .work_planning import latest_remaining_baseline
                for task in planning["tasks"]:
                    tid = task["id"]
                    latest = latest_remaining_baseline(planning, tid)
                    if latest and latest != latest_remaining_baseline(previous, tid):
                        epochs = planning.setdefault("remaining_epochs", {})
                        epochs[tid] = epochs.get(tid, 0)+1
                locks = {x["cube_id"]: x for x in planning["locks"]}
                locks.update({x["cube_id"]: x for x in previous.get("locks", [])})
                planning["locks"] = list(locks.values())
                planning["weekly_review_done"] = {
                    **planning.get("weekly_review_done", {}), **previous.get("weekly_review_done", {})}
                planning["cube_details"] = {
                    **planning.get("cube_details", {}), **previous.get("cube_details", {})}
                planning["manual_days"] = {
                    **planning.get("manual_days", {}), **previous.get("manual_days", {})}
                planning["prep_overrides"] = {
                    **planning.get("prep_overrides", {}), **previous.get("prep_overrides", {})}
                planning["prep_progress"] = {
                    **planning.get("prep_progress", {}), **previous.get("prep_progress", {})}
                if "constructor" in previous:
                    planning["constructor"] = deepcopy(previous["constructor"])
                if "scope_exemptions" in previous:
                    planning["scope_exemptions"] = deepcopy(previous["scope_exemptions"])
                if "task_segments" in previous:
                    planning["task_segments"] = deepcopy(previous["task_segments"])
                    planning["segment_baseline_remaining"] = deepcopy(previous["segment_baseline_remaining"])
                    planning["segment_progress"] = deepcopy(previous.get("segment_progress", {}))
                    reconcile_segment_estimates(planning, previous)
                planning["history"] = previous.get("history", [])[-39:] + planning["history"][-1:]
                if planning["completed_cubes"] or planning["day_outcomes"]:
                    planning["result"] = deepcopy(previous.get("result", planning["result"]))
            validate_planning(planning, state)
            planning = calculate(planning, reason="import")
        else:
            planning = deepcopy(state.get("planning"))
            if planning is None:
                raise ValidationError("План Work ещё не импортирован")
            validate_planning(planning, state)
            if action == "recalculate":
                if set(data) - {"from_date", "refresh_calendar", "reason"}:
                    raise ValidationError("recalculate.data: неизвестное поле")
                refresh = data.get("refresh_calendar", False)
                if type(refresh) is not bool:
                    raise ValidationError("refresh_calendar: требуется bool")
                if refresh:
                    planning = merge_calendar_refresh(planning, self._calendar_response(planning, force=True))
                reason = data.get("reason", "recalculate")
                if not isinstance(reason, str) or len(reason) > 300:
                    raise ValidationError("reason: текст до 300 символов")
                linked = {item["id"]: item for item in state["items"]}
                for task in planning["tasks"]:
                    item = linked.get(task.get("work_item_id"))
                    if (item and item.get("planning_task_id") == task["id"]
                            and isinstance(item.get("title"), str) and item["title"].strip()):
                        task["title"] = item["title"]
                planning = calculate(planning, from_date=data.get("from_date"), reason=reason)
            else:
                candidate = data.get("cube", {}) if action == "constructor_select" else data if action == "constructor_cube" else {}
                if action == "constructor_select" and not candidate:
                    candidate = planning.get("constructor", {}).get("cubes", {}).get(data.get("cube_id"), {})
                if candidate.get("kind") == "brainalot":
                    from .core import Store
                    source = next((note for note in Store(self.root).list_notes()
                                   if note["id"] == candidate.get("source_ref")), None)
                    if source is None or source.get("kind") != "task" or source.get("status") != "active":
                        raise ValidationError("Brainalot: исходная активная задача не найдена")
                planning = apply_action(planning, action, data)
                if action == "day_outcome" and ("done" in data or data.get("remaining_minutes") == 0):
                    task = next((item for item in planning["tasks"] if item["id"] == data.get("task_id")), None)
                    linked = next((item for item in state["items"]
                                   if task and item["id"] == task.get("work_item_id")
                                   and item.get("planning_task_id") == task["id"]), None)
                    if linked is not None:
                        if data.get("done", data.get("remaining_minutes") == 0) is True:
                            linked["status"] = "done"
                        elif data["done"] is False and linked.get("status") == "done":
                            linked["status"] = "todo"
        if action not in {"cube_progress", "day_outcome", "segment_progress", "prep_progress",
                          "task_importance", "monthly_plan_approve"}:
            _constructor_allocation(planning, allocation_baseline)
            _constructor_budget_guard(planning, allocation_baseline)
        from .work_planning import _refresh_constructor_result, update_constructor_awards
        if action not in {"task_importance", "monthly_plan_approve"}:
            update_constructor_awards(planning, allocation_baseline,
                                      backfill=action in {"constructor_day", "recalculate", "scope_exempt"},
                                      allow_new=action != "import")
            _refresh_constructor_result(planning)
        state["planning"] = planning
        self._guard_deletions(allocation_baseline, planning, allow_remaining_epoch=action in {"day_outcome", "import"})
        encoded_state = encoded(state).encode("utf-8")
        digest = hashlib.sha256(encoded_state).hexdigest()
        with self.db() as db:
            ensure_writable(db)
            old = self.receipt(db, "work", command)
            if old is not None:
                return old
            current = self._version(db)
            self.check_revision(command, current["revision"])
            db.execute("INSERT OR IGNORE INTO work_blobs VALUES(?,?)", (digest, encoded_state))
            descriptors = self._validate(db, digest, images)
            db.execute("INSERT INTO work_versions VALUES(?,?,?,?)", (current["revision"] + 1, int(time.time()*1000),
                       digest, encoded(descriptors)))
            return self.remember(db, "work", command, self._read(db))

    def revisions(self):
        with self.db() as db:
            return {"revisions": [dict(r) for r in db.execute("SELECT revision,created FROM work_versions ORDER BY revision DESC")]}

    def restore(self, command):
        from .work_delete import recover, ensure_writable
        recover(self)
        with self.db() as db:
            ensure_writable(db)
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
            previous = json.loads(self._blob(db, current["state_blob"])) if current else {}
            incoming = json.loads(self._blob(db, target["state_blob"]))
            self._guard_deletions(previous.get("planning"), incoming.get("planning"))
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
