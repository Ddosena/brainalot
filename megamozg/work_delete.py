"""Narrow recoverable Work-cube/Markdown deletion; no cross-store success guesswork."""
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

from filelock import FileLock

from .core import ConflictError, NotFoundError, Store, ValidationError
from .shared_state import encoded, identifier


class DeleteStateError(ConflictError):
    def __init__(self, row):
        self.delete_result = public_row(row)
        self.status_code = 503 if row["status"] == "pending" else 409
        super().__init__(row["error"] or "Удаление ещё не завершено; повторите проверку")


def public_row(row):
    command = json.loads(row["command"])
    return {"request_id": row["request_id"], "cube_id": command["data"]["cube_id"],
            "note_id": row["note_id"], "status": row["status"], "error": row["error"]}


def metadata(db):
    rows = db.execute("SELECT * FROM work_cube_deletes WHERE status IN ('pending','conflict') ORDER BY rowid").fetchall()
    if not rows:
        return None
    return {"pending": [public_row(row) for row in rows if row["status"] == "pending"],
            "conflicts": [public_row(row) for row in rows if row["status"] == "conflict"]}


def ensure_writable(db):
    row = db.execute("SELECT * FROM work_cube_deletes WHERE status='pending' ORDER BY rowid LIMIT 1").fetchone()
    if row is not None:
        raise DeleteStateError(row)


def guard_library_delete(root, note_id):
    """Protect existing Work singletons; never infer links from titles/context."""
    path = Path(root) / "state" / "shared.sqlite3"
    if not path.exists():
        return
    with sqlite3.connect(path) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='work_versions'").fetchone():
            return
        row = db.execute("SELECT b.body FROM work_versions v JOIN work_blobs b ON b.hash=v.state_blob ORDER BY v.revision DESC LIMIT 1").fetchone()
    if row:
        planning = json.loads(row[0]).get("planning", {})
        for cid, cube in planning.get("constructor", {}).get("cubes", {}).items():
            if cube.get("kind") == "brainalot" and cube.get("source_ref") == note_id and cid not in planning.get("cube_deletions", {}):
                raise ConflictError("Задача представлена кубиком Work. Удалите этот кубик через Work: он также удалит связанную запись Brainalot")


def delete_linked_note(root, note_id, expected_version):
    """The existing Library/CLI delete enters the same singleton coordinator."""
    path = Path(root) / "state" / "shared.sqlite3"
    if not path.exists():
        return None
    request_id = "library-delete:"+hashlib.sha256((str(note_id)+":"+str(expected_version)).encode()).hexdigest()
    with sqlite3.connect(path) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='work_versions'").fetchone():
            return None
        saved = None
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='work_cube_deletes'").fetchone():
            saved = db.execute("SELECT command,status,operation_id FROM work_cube_deletes WHERE request_id=?", (request_id,)).fetchone()
        if saved:
            # Undo restores identical Markdown bytes/version. A new Library delete
            # must not reuse the old success while leaving that restored note alive.
            journal = Path(root) / "state" / "journal.sqlite3"
            if saved[1] == "complete" and journal.exists():
                with sqlite3.connect(journal) as notes:
                    if notes.execute("SELECT 1 FROM operations WHERE reverses=? AND status IN ('applied','pending')",
                                     (saved[2],)).fetchone():
                        return None
            command = json.loads(saved[0])
        else:
            row = db.execute("SELECT v.revision,b.body FROM work_versions v JOIN work_blobs b ON b.hash=v.state_blob ORDER BY v.revision DESC LIMIT 1").fetchone()
            if not row:
                return None
            planning = json.loads(row[1]).get("planning", {})
            cube_id = next((cid for cid, cube in planning.get("constructor", {}).get("cubes", {}).items()
                            if cube.get("kind") == "brainalot" and cube.get("source_ref") == note_id
                            and cid not in planning.get("cube_deletions", {})), None)
            if cube_id is None:
                return None
            command = {"request_id": request_id, "expected_revision": row[0], "action": "cube_delete",
                       "data": {"cube_id": cube_id, "expected_note_version": expected_version}}
    from .work_store import WorkStore
    result = WorkStore(root).planning_operation(command)
    return {"id": note_id, "deleted": True, "operation_id": result["delete_result"]["operation_id"]}


def _boundary(name, row):
    """Named durability boundaries for fault-injection tests."""


class ActiveCubeTimerError(ConflictError):
    pass


def _guard_timer_row(row, cube_id, blueprint_ref=None):
    if row and row[0] in {"running", "paused"} and row[1]:
        task = json.loads(row[1])
        identities = {"work-cube:"+value for value in (cube_id, blueprint_ref) if value}
        if task.get("source") == "work" and task.get("id") in identities:
            raise ActiveCubeTimerError("Перед удалением остановите таймер этого кубика и сохраните фактическое время")


def guard_active_timer(root, cube_id, blueprint_ref=None):
    """Read only: deletion must not stop, settle or erase the shared time ledger."""
    path = Path(root) / "state" / "time.sqlite3"
    if not path.exists():
        return
    with sqlite3.connect(path) as db:
        row = db.execute("SELECT status,task FROM timer WHERE id=1").fetchone() if db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='timer'").fetchone() else None
    _guard_timer_row(row, cube_id, blueprint_ref)


@contextmanager
def timer_reservation(root, cube_id, blueprint_ref=None):
    # Same order as TimeStore.command: time writer lock, then shared-state read/write.
    # Holding this through outbox prepare closes start/check/prepare races.
    with sqlite3.connect(Path(root) / "state" / "time.sqlite3", timeout=30) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT status,task FROM timer WHERE id=1").fetchone() if db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='timer'").fetchone() else None
        _guard_timer_row(row, cube_id, blueprint_ref)
        yield



@contextmanager
def timer_reservation_many(root, cube_ids):
    """Hold the shared timer writer lock before Work while deleting overlay cubes."""
    with sqlite3.connect(Path(root) / "state" / "time.sqlite3", timeout=30) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT status,task FROM timer WHERE id=1").fetchone() if db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='timer'").fetchone() else None
        aliases = set(cube_ids)
        note_ids = {cid[len("brainalot-"):] for cid in aliases
                    if re.fullmatch(r"brainalot-[a-f0-9]{32}", cid)}
        path = Path(root) / "state" / "shared.sqlite3"
        if path.exists():
            with sqlite3.connect(path) as work_db:
                if work_db.execute("SELECT 1 FROM sqlite_master WHERE name='work_versions'").fetchone():
                    current = work_db.execute("SELECT b.body FROM work_versions v JOIN work_blobs b ON b.hash=v.state_blob ORDER BY v.revision DESC LIMIT 1").fetchone()
                    if current:
                        state = json.loads(current[0])
                        cubes = (state.get("planning") or {}).get("constructor", {}).get("cubes", {})
                        note_ids.update(cubes[cid].get("source_ref") for cid in list(aliases)
                                        if cid in cubes and cubes[cid].get("kind") == "brainalot" and cubes[cid].get("source_ref"))
                        aliases.update(cid for cid, cube in cubes.items()
                                       if cube.get("kind") == "brainalot" and cube.get("source_ref") in note_ids)
        aliases.update("brainalot-" + note_id for note_id in note_ids)
        if row and row[0] in {"running", "paused"} and row[1]:
            active = json.loads(row[1])
            if active.get("source") == "library" and active.get("id") in note_ids:
                raise ActiveCubeTimerError("Перед удалением остановите таймер этой записи и сохраните фактическое время")
        for cid in aliases:
            blueprint = cid[len("planned-"):] if cid.startswith("planned-") else None
            _guard_timer_row(row, cid, blueprint)
        yield



@contextmanager
def timer_source_lease(root):
    """Freeze the active timer identity without settling or modifying its ledger."""
    with sqlite3.connect(Path(root) / "state" / "time.sqlite3", timeout=30) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT status,task FROM timer WHERE id=1").fetchone() if db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='timer'").fetchone() else None
        task = json.loads(row[1]) if row and row[0] in {"running", "paused"} and row[1] else None
        yield task

def tracked_work_cube_ids(root, cube_ids):
    """Read the existing ledger only; no timer initialization, settlement or writes."""
    ids = {cid for cid in cube_ids if isinstance(cid, str) and cid}
    path = Path(root) / "state" / "time.sqlite3"
    if not ids or not path.exists():
        return set()
    result = set()
    with sqlite3.connect(path) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='intervals'").fetchone():
            return result
        rows = db.execute("SELECT DISTINCT task FROM intervals WHERE task IS NOT NULL AND end_ms>start_ms AND mode!='break'")
        for item in rows:
            try:
                ref = json.loads(item[0])
            except (TypeError, ValueError):
                continue
            target = ref.get("id") if isinstance(ref, dict) and ref.get("source") == "work" else None
            if isinstance(target, str) and target.startswith("work-cube:"):
                cid = target[len("work-cube:"):]
                if cid in ids:
                    result.add(cid)
    return result

def guard_timer_target(root, task):
    """Reject pending/deleted exact sources while holding the timer writer lock."""
    if not task:
        return
    source, target = task.get("source"), task.get("id", "")
    work_cube = source == "work" and isinstance(target, str) and target.startswith("work-cube:")
    if not work_cube and source != "library":
        return
    cid = target[len("work-cube:"):] if work_cube else None
    note_id = (cid[len("brainalot-"):] if cid and re.fullmatch(r"brainalot-[a-f0-9]{32}", cid)
               else target if source == "library" and isinstance(target, str) else None)
    linked_note = bool(work_cube and note_id)
    path = Path(root) / "state" / "shared.sqlite3"
    if path.exists():
        from .work_planning import deleted_cube
        with sqlite3.connect(path) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='work_versions'").fetchone():
                blobs = db.execute("SELECT b.body FROM work_versions v JOIN work_blobs b ON b.hash=v.state_blob ORDER BY v.revision DESC LIMIT 1").fetchall()
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='work_cube_deletes'").fetchone():
                    if note_id and db.execute("SELECT 1 FROM work_cube_deletes WHERE note_id=? LIMIT 1", (note_id,)).fetchone():
                        linked_note = True
                    if note_id and db.execute("SELECT 1 FROM work_cube_deletes WHERE note_id=? AND status='pending' LIMIT 1", (note_id,)).fetchone():
                        raise ConflictError("Связанная запись удаляется; её таймер нельзя запустить")
                    blobs += db.execute("SELECT b.body FROM work_cube_deletes d JOIN work_blobs b ON b.hash=d.state_blob WHERE d.status='pending'").fetchall()
                if work_cube:
                    for blob in blobs:
                        state = json.loads(blob[0])
                        planning = state.get("planning") or {}
                        cancellations = state.get("period_planner", {}).get("cancellations", {})
                        legacy_group = cid.rsplit("-day-", 1)[0] if cid.startswith("calendar-offer-series-") and "-day-" in cid else None
                        if deleted_cube(planning, cid) or cid in cancellations or legacy_group in cancellations:
                            raise ConflictError("Кубик удаляется или уже удалён; его таймер нельзя запустить")
                        native_cubes = planning.get("constructor", {}).get("cubes", {})
                        native = native_cubes.get(cid, {})
                        ext = state.get("period_planner", {})
                        absorbed = {source for merge in ext.get("merges", {}).values() for source in merge.get("source_ids", [])}
                        if cid in absorbed:
                            raise ConflictError("Кубик объединён; его прежний таймер нельзя запустить")
                        if cid.startswith(("repeat-instance-", "manual-cube-", "merged-", "planned-")):
                            forecast = (cid.startswith("planned-") and any(row.get("id") == cid[len("planned-"):]
                                and not row.get("manual") and not row.get("locked") and not row.get("allocation_stale")
                                for row in planning.get("result", {}).get("cubes", [])))
                            exists = (cid in native_cubes or cid in ext.get("merged_cubes", {})
                                      or cid in ext.get("retained_cubes", {}) or forecast)
                            if not exists:
                                raise ConflictError("Кубик больше недоступен; его таймер нельзя запустить")
                        if native.get("kind") == "brainalot" and native.get("source_ref"):
                            note_id, linked_note = native["source_ref"], True
                if note_id and db.execute("SELECT 1 FROM sqlite_master WHERE name='work_cube_deletes'").fetchone():
                    if db.execute("SELECT 1 FROM work_cube_deletes WHERE note_id=? AND status='pending' LIMIT 1", (note_id,)).fetchone():
                        raise ConflictError("Связанная запись удаляется; её таймер нельзя запустить")
    journal = Path(root) / "state" / "journal.sqlite3"
    if note_id and journal.exists():
        with sqlite3.connect(journal) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='operations'").fetchone():
                if db.execute("SELECT 1 FROM operations WHERE note_id=? AND status='pending' LIMIT 1", (note_id,)).fetchone():
                    raise ConflictError("Связанная запись изменяется или удаляется; её таймер нельзя запустить")
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='work_note_requests'").fetchone():
                linked_note = linked_note or bool(db.execute("SELECT 1 FROM work_note_requests WHERE note_id=? LIMIT 1", (note_id,)).fetchone())
    # Unknown legacy library TaskRefs remain valid; proven linked Note sources
    # must still exist. Work/Note reads are closed before acquiring the note lock.
    if note_id and linked_note:
        from .work_period.library_rules import LibraryReader
        note = next((row for row in LibraryReader(root).notes() if row.get("id") == note_id), None)
        if not note or note.get("kind") != "task" or note.get("status") not in {"active", "done"}:
            raise ConflictError("Связанная задача Brainalot недоступна; её таймер нельзя запустить")


def guard_note_undo(root, operation_id):
    path = Path(root) / "state" / "shared.sqlite3"
    if not path.exists():
        return
    with sqlite3.connect(path) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='work_cube_deletes'").fetchone() and db.execute(
                "SELECT 1 FROM work_cube_deletes WHERE operation_id=? AND status='pending'", (operation_id,)).fetchone():
            raise ConflictError("Сначала завершите связанное удаление Work; отмена записи пока недоступна")


def resolve_cube(planning, cube_id, store):
    from .work_planning import deleted_cube
    if deleted_cube(planning, cube_id):
        raise ConflictError("Кубик уже удалён")
    cube = planning.get("constructor", {}).get("cubes", {}).get(cube_id)
    if cube is not None:
        cube = deepcopy(cube)
    else:
        source = next((c for c in planning.get("result", {}).get("cubes", [])
                       if cube_id in (c["id"], "planned-"+c["id"])), None)
        if source is not None:
            cube = {**deepcopy(source), "id": cube_id, "kind": "task", "blueprint_ref": source["id"],
                    "planned_date": source["date"]}
        else:
            for entry in planning.get("result", {}).get("task_pool", []):
                for segment in entry.get("segments") or [None]:
                    if cube_id != "pool:"+entry["task_id"]+":"+(segment["id"] if segment else "main"):
                        continue
                    available = (segment or entry).get("available_for_manual_minutes", (segment or {}).get("remaining_minutes", 0))
                    if available < 5:
                        raise ConflictError("В этапе больше нет доступной работы")
                    cube = {"id": cube_id, "kind": "task", "task_id": entry["task_id"],
                            **({"segment_id": segment["id"]} if segment else {}),
                            "minutes": min(60, available//5*5), "title": (segment or entry)["title"],
                            "output": (segment or {}).get("output", ""), "steps": (segment or {}).get("steps", [])}
            if cube is None and re.fullmatch(r"brainalot-[a-f0-9]{32}", cube_id):
                note_id = cube_id[len("brainalot-"):]
                note = store._find(note_id)
                if note["kind"] != "task" or note["status"] != "active":
                    raise ConflictError("Исходная активная задача Brainalot не найдена")
                cube = {"id": cube_id, "kind": "brainalot", "source_ref": note_id,
                        "title": note["title"], "minutes": 60}
    if cube is None:
        raise NotFoundError("Кубик не найден в актуальном Work")
    if cube.get("kind") not in {"task", "brainalot"}:
        raise ValidationError("Удаление доступно для этапа Work или задачи Brainalot")
    if deleted_cube(planning, cube_id, cube.get("blueprint_ref")):
        raise ConflictError("Этот этап уже удалён")
    for field, default in (("output", ""), ("steps", []), ("comment", ""), ("links", []), ("groups", [])):
        cube.setdefault(field, default)
    return cube


def apply_delete(planning, cube, command):
    from .work_planning import (_constructor_report, _constructor_selected_date, _refresh_constructor_result,
                                _refresh_manual_day, _refresh_result_remaining, _task_pool, _dates,
                                _retired_minutes)
    planning = deepcopy(planning)
    cube = deepcopy(cube)
    cid = cube["id"]
    ctor = planning.setdefault("constructor", {"version": 1, "cubes": {}, "days": {}, "event_required": {}, "source_reports": {}})
    report = _constructor_report(planning, cube)
    selected_date = _constructor_selected_date(planning, cid)
    # A forecast may already have a report under its canonical blueprint ID.
    blueprint = cube.get("blueprint_ref")
    if not report and blueprint:
        report = planning.get("completed_cubes", {}).get(blueprint, {})
        if report:
            # Keep the real report identity; never duplicate earned credit.
            cube["id"] = blueprint
            cid = blueprint
    ctor["cubes"][cid] = cube
    planning.setdefault("cube_deletions", {})[cid] = {
        "cube_id": cid, "request_id": command["request_id"], "deleted_at": datetime.now(timezone.utc).isoformat(),
        "blueprint_ref": blueprint or (command["data"]["cube_id"] if command["data"]["cube_id"].startswith("pool:") else None),
        "retired_minutes": max(0, cube["minutes"]-round(cube["minutes"]*report.get("percent", 0)/100)) if cube["kind"] == "task" else 0,
        "selected_date": selected_date,
        "remaining_epoch": planning.get("remaining_epochs", {}).get(cube.get("task_id"), 0),
    }
    ids = {cid, command["data"]["cube_id"], blueprint}
    affected = {lock["date"] for lock in planning.get("locks", []) if lock["cube_id"] in ids}
    planning["locks"] = [lock for lock in planning.get("locks", []) if lock["cube_id"] not in ids]
    for key, day in ctor["days"].items():
        if cid in day.get("selected_source_ids", []):
            affected.add(key)
        for field in ("selected_source_ids", "hidden_cube_ids"):
            if field in day:
                day[field] = [value for value in day[field] if value not in ids]
        for value in ids:
            day.get("source_slots", {}).pop(value, None)
            for positions in day.get("positions", {}).values():
                positions.pop(value, None)
    removed = [row for row in planning["result"].get("cubes", []) if row["id"] in ids]
    planning["result"]["cubes"] = [row for row in planning["result"].get("cubes", []) if row["id"] not in ids]
    for row in removed:
        affected.add(row["date"])
        if row["date"] not in planning.get("manual_days", {}):
            day = next((d for d in planning["result"]["days"] if d["date"] == row["date"]), None)
            if day and not report:
                for field in ("development_minutes", "planned_development_minutes", "total_work_minutes", "builder_selected_minutes", "builder_total_work_minutes"):
                    if field in day:
                        day[field] = max(0, day[field]-row["minutes"])
                day["unused_capacity_minutes"] = max(0, day["day_limit_minutes"]-day["total_work_minutes"])
    for key in affected:
        if key in planning.get("manual_days", {}):
            _refresh_manual_day(planning, key)
    _refresh_result_remaining(planning)
    retired = _retired_minutes(planning, active_only=False)
    for task in planning["result"].get("tasks", []):
        task["cancelled_minutes"] = retired[task["id"]]
    planning["result"]["task_pool"] = _task_pool(planning, planning["result"]["cubes"], _dates(planning["settings"]))
    # Removing a pool piece cancels demand without silently replanning other days.
    # Existing forecasts can therefore exceed the new demand: expose them as stale.
    task_id = cube.get("task_id")
    if task_id:
        demand = next((task["remaining_minutes"] for task in planning["result"].get("tasks", []) if task["id"] == task_id), 0)
        forecasts = [row for row in planning["result"]["cubes"] if row["task_id"] == task_id
                     and not row.get("manual") and not row.get("locked")
                     and not planning.get("completed_cubes", {}).get(row["id"])]
        if sum(row["minutes"] for row in forecasts) > demand:
            for row in forecasts:
                row["allocation_stale"] = True
    _refresh_constructor_result(planning)
    return planning


def _finish(work, row):
    command = json.loads(row["command"])
    with work.db() as db:
        current = work._version(db)
        work.check_revision(command, current["revision"])
        images = json.loads(row["images"])
        descriptors = work._validate(db, row["state_blob"], images)
        db.execute("INSERT INTO work_versions VALUES(?,?,?,?)", (current["revision"]+1, int(time.time()*1000), row["state_blob"], encoded(descriptors)))
        result = work._read(db)
        result["delete_result"] = {"status": "complete", "request_id": row["request_id"],
                                  "cube_id": command["data"]["cube_id"], "note_id": row["note_id"],
                                  "operation_id": row["operation_id"]}
        work.remember(db, "work", command, result)
        db.execute("UPDATE work_cube_deletes SET status='complete',error=NULL WHERE request_id=?", (row["request_id"],))
        for prior in db.execute("SELECT * FROM work_cube_deletes WHERE status='conflict'").fetchall():
            if json.loads(prior["command"])["data"]["cube_id"] == command["data"]["cube_id"]:
                db.execute("UPDATE work_cube_deletes SET status='superseded' WHERE request_id=?", (prior["request_id"],))
    _boundary("after_work_commit", row)


def _advance(work, row):
    try:
        _boundary("after_prepare", row)
        data = json.loads(row["command"])["data"]
        with work.db() as db:
            proposed = json.loads(work._blob(db, row["state_blob"]))["planning"]
        record = next(record for record in proposed["cube_deletions"].values() if record["request_id"] == row["request_id"])
        guard_active_timer(work.root, data["cube_id"], record["blueprint_ref"])
        if row["note_id"] is not None:
            store = Store(work.root)
            store.delete(row["note_id"], row["note_version"], operation_id=row["operation_id"], _coordinated=True)
        _boundary("after_note_delete", row)
        _finish(work, row)
    except ActiveCubeTimerError as exc:
        with work.db() as db:
            db.execute("UPDATE work_cube_deletes SET error=? WHERE request_id=? AND status='pending'", (str(exc), row["request_id"]))
    except (ConflictError, NotFoundError, ValidationError) as exc:
        with work.db() as db:
            db.execute("UPDATE work_cube_deletes SET status='conflict',error=? WHERE request_id=? AND status='pending'", (str(exc), row["request_id"]))
    except OSError as exc:
        with work.db() as db:
            db.execute("UPDATE work_cube_deletes SET error=? WHERE request_id=? AND status='pending'", (str(exc), row["request_id"]))


def _recover_locked(work):
    with work.db() as db:
        rows = db.execute("SELECT * FROM work_cube_deletes WHERE status='pending' ORDER BY rowid").fetchall()
    for row in rows:
        _advance(work, row)


def recover(work):
    with FileLock(str(work.root / "state" / "work-delete.lock"), timeout=15):
        _recover_locked(work)


def delete_cube(work, command):
    data = command.get("data")
    if not isinstance(data, dict) or set(data)-{"cube_id", "expected_note_version"} or "cube_id" not in data:
        raise ValidationError("cube_delete: нужны cube_id и версия связанной записи Brainalot")
    cid = identifier(data["cube_id"], "cube_id")
    identifier(command.get("request_id"), "request_id")
    with FileLock(str(work.root / "state" / "work-delete.lock"), timeout=15):
        _recover_locked(work)
        with work.db() as db:
            old = work.receipt(db, "work", command)
            if old is not None:
                return old
            previous = db.execute("SELECT * FROM work_cube_deletes WHERE request_id=?", (command["request_id"],)).fetchone()
            if previous is not None:
                if previous["command"] != encoded(command):
                    raise ConflictError("request_id уже использован с другим содержимым")
                raise DeleteStateError(previous)
            ensure_writable(db)
            current = work._version(db)
            if current is None:
                raise ValidationError("Work не инициализирован")
            work.check_revision(command, current["revision"])
            state = json.loads(work._blob(db, current["state_blob"]))
            images = [{key: image[key] for key in ("id", "blob", "mime")} for image in json.loads(current["images"])]
        store = Store(work.root)
        with timer_reservation_many(work.root, [cid]), store.lock:
            cube = resolve_cube(state.get("planning", {}), cid, store)
            guard_active_timer(work.root, cid, cube.get("blueprint_ref"))
            note_id = cube.get("source_ref") if cube["kind"] == "brainalot" else None
            note_version = data.get("expected_note_version")
            if note_id is not None:
                if not isinstance(note_version, str) or not re.fullmatch(r"[a-f0-9]{64}", note_version):
                    raise ValidationError("Brainalot: требуется expected_note_version")
                note = store._find(note_id)
                if note["kind"] != "task":
                    raise ConflictError("Brainalot: источник не является задачей")
                store._delete_changes(note_id, note_version)
            elif "expected_note_version" in data:
                raise ValidationError("У этапа Work нет связанной записи Brainalot")
            state["planning"] = apply_delete(state["planning"], cube, command)
            raw = encoded(state).encode()
            digest = hashlib.sha256(raw).hexdigest()
            operation_id = hashlib.sha256(("work-cube-delete:"+command["request_id"]).encode()).hexdigest()[:32] if note_id else None
            with work.db() as db:
                ensure_writable(db)
                work.check_revision(command, work._version(db)["revision"])
                db.execute("INSERT OR IGNORE INTO work_blobs VALUES(?,?)", (digest, raw))
                work._validate(db, digest, images)
                db.execute("INSERT INTO work_cube_deletes VALUES(?,?,?,?,?,?,?,?,?)", (command["request_id"], encoded(command), "pending", note_id, note_version, operation_id, digest, encoded(images), None))
        with work.db() as db:
            row = db.execute("SELECT * FROM work_cube_deletes WHERE request_id=?", (command["request_id"],)).fetchone()
        _advance(work, row)
        with work.db() as db:
            old = work.receipt(db, "work", command)
            if old is not None:
                return old
            row = db.execute("SELECT * FROM work_cube_deletes WHERE request_id=?", (command["request_id"],)).fetchone()
        raise DeleteStateError(row)
