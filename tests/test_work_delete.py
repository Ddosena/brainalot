"""Deletion uses synthetic roots, real SQLite receipts and real Markdown writes."""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from megamozg import cli, work_delete
from megamozg.api import create_app
from megamozg.core import ConflictError, NotFoundError, Store, ValidationError
from megamozg.work_delete import DeleteStateError
from megamozg.work_planning import _remaining, _remaining_before_deletions
from megamozg.work_store import WorkStore
from test_work_planning import op, planning, seed, upload_state
from test_work_planning_constructor import setup, draft, DAY, NEXT, view


def command(work, cube_id, **data):
    return {"request_id": "delete-"+cube_id, "expected_revision": work.read()["revision"],
            "action": "cube_delete", "data": {"cube_id": cube_id, **data}}


def brainalot(root, *, canonical=True):
    work = setup(root)
    store = Store(root)
    note = store.capture("Linked note", kind="task", importance_score=4, external_id="source")
    cid = "brainalot-"+note["id"]
    if canonical:
        op(work, "source", "constructor_cube", {"cube_id": cid, "kind": "brainalot",
                                                "source_ref": note["id"], "title": note["title"], "minutes": 60})
    return work, store, note, cid


@pytest.mark.parametrize("percent,actual", [(0, 0), (35, 47), (100, 83)])
def test_delete_one_piece_preserves_parent_other_pieces_and_facts(tmp_path, percent, actual):
    work = setup(tmp_path)
    op(work, "day", "constructor_day", {"date": DAY})
    draft(work, "first", planned_date=DAY)
    draft(work, "second", planned_date=NEXT)
    op(work, "select", "constructor_select", {"cube_id": "first", "date": DAY})
    if percent or actual:
        op(work, "progress", "cube_progress", {"cube_id": "first", "percent": percent, "actual_minutes": actual})
        if percent == 100:
            op(work, "close", "constructor_goal_status", {"scope": "day", "date": DAY, "status": "completed"})
    before = work.read()
    before_plan = before["state"]["planning"]
    cmd = command(work, "first")
    result = work.planning_operation(cmd)
    assert result["revision"] == before["revision"]+1
    assert work.planning_operation(cmd) == result
    after = result["state"]["planning"]
    assert after["tasks"] == before_plan["tasks"]
    assert result["state"]["items"] == before["state"]["items"]
    assert after["task_segments"] == before_plan["task_segments"]
    for field in ("completed_cubes", "day_outcomes", "history", "segment_progress"):
        assert after.get(field) == before_plan.get(field)
    assert after["constructor"].get("awards") == before_plan["constructor"].get("awards")
    assert after["constructor"]["cubes"]["second"] == before_plan["constructor"]["cubes"]["second"]
    assert "second" in {cube["id"] for cube in after["result"]["constructor"]["cubes"]}
    assert "first" not in {cube["id"] for cube in after["result"]["constructor"]["cubes"]}
    retired = 60-round(60*percent/100)
    assert after["cube_deletions"]["first"]["retired_minutes"] == retired
    assert _remaining_before_deletions(after) == _remaining_before_deletions(before_plan)
    assert _remaining(after)["a"] == _remaining(before_plan)["a"]-retired
    assert view(result)["actual_minutes"] == actual
    assert view(result)["selected_minutes"] == 0
    if percent == 100:
        goal_before = next(g for g in before_plan["result"]["constructor"]["goals"] if g["id"] == "day:"+DAY)
        goal_after = next(g for g in after["result"]["constructor"]["goals"] if g["id"] == "day:"+DAY)
        assert goal_after == goal_before
    with pytest.raises(ValidationError):
        op(work, "resurrect", "constructor_cube", {"cube_id": "first", "kind": "task", "task_id": "a", "title": "No", "minutes": 60})
    with pytest.raises(ValidationError):
        op(work, "rewrite-facts", "cube_progress", {"cube_id": "first", "percent": 0})


def test_brainalot_true_delete_journal_receipt_and_undo_note(tmp_path):
    work, store, note, cid = brainalot(tmp_path)
    op(work, "day", "constructor_day", {"date": DAY})
    op(work, "select", "constructor_select", {"cube_id": cid, "date": DAY})
    op(work, "progress", "cube_progress", {"cube_id": cid, "percent": 40, "actual_minutes": 28})
    before = work.read()
    cmd = command(work, cid, expected_note_version=note["version"])
    result = work.planning_operation(cmd)
    assert result["revision"] == before["revision"]+1
    assert work.planning_operation(cmd) == result
    assert not (store.vault / note["path"]).exists()
    assert not (store.vault / "9 Система" / "Оригиналы" / (note["id"]+".md")).exists()
    assert store.list_notes() == []
    operation_id = result["delete_result"]["operation_id"]
    assert next(row for row in store.history() if row["id"] == operation_id)["kind"] == "delete"
    assert view(result)["actual_minutes"] == 28
    assert view(result)["credited_minutes"] == 24
    assert view(result)["total_work_minutes"] >= view(result)["fixed_minutes"]+28
    restored = store.undo(operation_id)
    assert restored["id"] == note["id"]
    assert cid in work.read()["state"]["planning"]["cube_deletions"]
    with pytest.raises(ConflictError):
        work.restore({"request_id": "unsafe-restore", "expected_revision": result["revision"], "revision": before["revision"]})
    # Library deletion after undo is a new real deletion, even with identical YAML/version.
    deleted_again = store.delete(restored["id"], restored["version"])
    assert deleted_again["operation_id"] != operation_id
    assert not (store.vault / restored["path"]).exists()


def test_brainalot_requires_exact_note_cas_and_does_not_guess_links(tmp_path):
    work, store, note, cid = brainalot(tmp_path)
    before = work.read()
    with pytest.raises(ValidationError):
        work.planning_operation(command(work, cid))
    with pytest.raises(ConflictError):
        work.planning_operation(command(work, cid, expected_note_version="0"*64))
    assert work.read() == before
    assert store.list_notes()[0]["id"] == note["id"]
    # An identically titled Work task is independent of that note.
    draft(work, "same-title", title=note["title"])
    work.planning_operation(command(work, "same-title"))
    assert store.list_notes()[0]["id"] == note["id"]


@pytest.mark.parametrize("canonical", [True, False])
def test_brainalot_candidate_and_library_delete_share_coordinator(tmp_path, canonical):
    work, store, note, cid = brainalot(tmp_path, canonical=canonical)
    before_revision = work.read()["revision"]
    if canonical:
        result = store.delete(note["id"], note["version"])
        assert store.delete(note["id"], note["version"]) == result
        assert result["deleted"] is True
    else:
        result = work.planning_operation(command(work, cid, expected_note_version=note["version"]))
        assert result["delete_result"]["note_id"] == note["id"]
    assert work.read()["revision"] == before_revision+1
    assert cid in work.read()["state"]["planning"]["cube_deletions"]
    assert not (store.vault / note["path"]).exists()


@pytest.mark.parametrize("boundary", ["after_prepare", "after_note_delete", "after_work_commit"])
def test_outbox_crash_boundary_recovery_has_one_work_revision(tmp_path, monkeypatch, boundary):
    work, store, note, cid = brainalot(tmp_path)
    cmd = command(work, cid, expected_note_version=note["version"])
    before_revision = work.read()["revision"]
    def crash(name, row):
        if name == boundary:
            raise OSError("interruption at "+name)
    monkeypatch.setattr(work_delete, "_boundary", crash)
    if boundary == "after_work_commit":
        result = work.planning_operation(cmd)
        assert result["delete_result"]["status"] == "complete"
    else:
        with pytest.raises(DeleteStateError) as error:
            work.planning_operation(cmd)
        assert error.value.status_code == 503
        pending = work.read()
        assert pending["revision"] == before_revision
        assert pending["deletion_state"]["pending"][0]["cube_id"] == cid
        with pytest.raises(DeleteStateError):
            op(work, "frozen", "task_importance", {"task_id": "a", "importance_score": 5})
    monkeypatch.undo()
    recovered = WorkStore(tmp_path)
    snapshot = recovered.read()
    assert snapshot["revision"] == before_revision+1
    result = recovered.planning_operation(cmd)
    assert result["revision"] == before_revision+1
    assert "deletion_state" not in snapshot
    assert not (store.vault / note["path"]).exists()
    assert sum(op["kind"] == "delete" for op in store.history()) == 1


def test_partial_markdown_delete_recovers_from_existing_journal(tmp_path, monkeypatch):
    work, store, note, cid = brainalot(tmp_path)
    original = Store._apply
    def partial(self, db, operation):
        if operation["kind"] == "delete":
            import json
            changes = json.loads(operation["changes"])
            self._path(changes[0]["path"]).unlink(missing_ok=True)
            raise OSError("between note and source")
        return original(self, db, operation)
    monkeypatch.setattr(Store, "_apply", partial)
    cmd = command(work, cid, expected_note_version=note["version"])
    with pytest.raises(DeleteStateError):
        work.planning_operation(cmd)
    assert not (store.vault / note["path"]).exists()
    assert (store.vault / "9 Система" / "Оригиналы" / (note["id"]+".md")).exists()
    monkeypatch.undo()
    result = WorkStore(tmp_path).planning_operation(cmd)
    assert result["delete_result"]["status"] == "complete"
    assert not (store.vault / "9 Система" / "Оригиналы" / (note["id"]+".md")).exists()


def test_note_version_changes_during_pending_become_visible_conflict(tmp_path, monkeypatch):
    work, store, note, cid = brainalot(tmp_path)
    cmd = command(work, cid, expected_note_version=note["version"])
    def pause(name, row):
        if name == "after_prepare":
            raise OSError("pause")
    monkeypatch.setattr(work_delete, "_boundary", pause)
    with pytest.raises(DeleteStateError):
        work.planning_operation(cmd)
    changed = store.update(note["id"], {"title": "New owner text"}, note["version"])
    monkeypatch.undo()
    snapshot = work.read()
    assert snapshot["revision"] == cmd["expected_revision"]
    assert snapshot["deletion_state"]["pending"] == []
    assert snapshot["deletion_state"]["conflicts"][0]["status"] == "conflict"
    assert store.list_notes()[0]["title"] == "New owner text"
    with pytest.raises(DeleteStateError) as error:
        work.planning_operation(cmd)
    assert error.value.status_code == 409
    fresh = {**command(work, cid, expected_note_version=changed["version"]), "request_id": "fresh-delete"}
    result = work.planning_operation(fresh)
    assert result["delete_result"]["status"] == "complete"
    assert "deletion_state" not in work.read()


def test_no_arbitrary_not_found_is_treated_as_completed_delete(tmp_path, monkeypatch):
    work, store, note, cid = brainalot(tmp_path)
    def pause(name, row):
        if name == "after_prepare":
            raise OSError("pause")
    monkeypatch.setattr(work_delete, "_boundary", pause)
    with pytest.raises(DeleteStateError):
        work.planning_operation(command(work, cid, expected_note_version=note["version"]))
    (store.vault / note["path"]).unlink()
    monkeypatch.undo()
    result = work.read()
    assert result["deletion_state"]["conflicts"][0]["status"] == "conflict"
    assert cid not in result["state"]["planning"].get("cube_deletions", {})


def test_planned_and_pool_candidates_delete_without_day_initialization(tmp_path):
    work = setup(tmp_path)
    before = work.read()
    source = before["state"]["planning"]["result"]["cubes"][0]
    cid = "planned-"+source["id"]
    result = work.planning_operation(command(work, cid))
    assert result["revision"] == before["revision"]+1
    assert not any(day.get("initialized") for day in result["state"]["planning"]["constructor"]["days"].values())
    assert result["state"]["planning"]["cube_deletions"][cid]["blueprint_ref"] == source["id"]
    pool = result["state"]["planning"]["result"]["task_pool"][0]
    segment = pool["segments"][0] if pool["segments"] else None
    pool_id = "pool:"+pool["task_id"]+":"+(segment["id"] if segment else "main")
    result = work.planning_operation({**command(work, pool_id), "request_id": "delete-pool"})
    assert pool_id in result["state"]["planning"]["cube_deletions"]
    with pytest.raises(ValidationError):
        op(work, "blueprint-resurrection", "constructor_cube", {"cube_id": "new-name", "kind": "task", "task_id": source["task_id"], "blueprint_ref": source["id"], "title": "No", "minutes": 60})
    previous = deepcopy(result["state"]["planning"]["cube_deletions"])
    imported = op(work, "reimport", "import", {"planning": planning()})
    assert imported["state"]["planning"]["cube_deletions"] == previous
    recalculated = op(work, "replan", "recalculate", {})
    assert recalculated["state"]["planning"]["cube_deletions"] == previous
    assert source["id"] not in {c["id"] for c in recalculated["state"]["planning"]["result"]["cubes"]}
    assert cid not in {c["id"] for c in recalculated["state"]["planning"]["result"]["constructor"]["cubes"]}


def test_http_pending_conflict_success_and_cli_typed_delete(tmp_path, monkeypatch, capsys):
    work, store, note, cid = brainalot(tmp_path)
    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765")
    headers = {"Authorization": "Bearer "+store.token()}
    cmd = command(work, cid, expected_note_version=note["version"])
    def pause(name, row):
        if name == "after_prepare":
            raise OSError("pause")
    monkeypatch.setattr(work_delete, "_boundary", pause)
    response = client.post("/api/work/planning", headers=headers, json=cmd)
    assert response.status_code == 503 and response.json()["delete_result"]["status"] == "pending"
    response = client.get("/api/work/state", headers=headers)
    assert response.status_code == 200 and response.json()["deletion_state"]["pending"]
    monkeypatch.undo()
    response = client.post("/api/work/planning", headers=headers, json=cmd)
    assert response.status_code == 200 and response.json()["revision"] == cmd["expected_revision"]+1
    draft(work, "cli-piece")
    import json
    path = tmp_path / "command.json"
    path.write_text(json.dumps(command(work, "cli-piece")), encoding="utf-8")
    assert cli.main(["--root", str(tmp_path), "work", "plan", "command", "--file", str(path)]) == 0
    capsys.readouterr()
    assert "cli-piece" in work.read()["state"]["planning"]["cube_deletions"]


def test_unsafe_generic_restore_import_is_rejected_and_library_api_preserves_shape(tmp_path):
    work, store, note, cid = brainalot(tmp_path)
    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765")
    headers = {"Authorization": "Bearer "+store.token()}
    before = work.read()
    response = client.request("DELETE", "/api/notes/"+note["id"], headers=headers, json={"expected_version": note["version"]})
    assert response.status_code == 200 and response.json()["deleted"] is True
    assert client.request("DELETE", "/api/notes/"+note["id"], headers=headers, json={"expected_version": note["version"]}).json() == response.json()
    digest = upload_state(work, before["state"])
    with pytest.raises(ConflictError):
        work.commit({"request_id": "unsafe-import", "expected_revision": work.read()["revision"],
                     "state_blob": digest, "images": [], "restore_import": True})


@pytest.mark.parametrize("paused", [False, True])
def test_active_timer_blocks_api_cli_delete_then_stopped_fact_is_preserved(tmp_path, paused, capsys):
    from megamozg.time_tracking import TimeStore
    from test_time_tracking import FakeClock, cmd
    work = setup(tmp_path)
    op(work, "day", "constructor_day", {"date": DAY})
    draft(work, "timed", planned_date=DAY)
    op(work, "select", "constructor_select", {"cube_id": "timed", "date": DAY})
    clock = FakeClock()
    timer = TimeStore(tmp_path, lambda: clock.wall, lambda: clock.mono)
    running = cmd(timer, "start", 0, "start", task={"source": "work", "id": "work-cube:timed", "title": "Timed piece"})
    clock.advance(10_000)
    timer.checkpoint()
    if paused:
        running = cmd(timer, "pause", running["revision"], "pause")
    before = work.read()
    request = command(work, "timed")
    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765")
    headers = {"Authorization": "Bearer "+Store(tmp_path).token()}
    response = client.post("/api/work/planning", headers=headers, json=request)
    assert response.status_code == 409
    import json
    path = tmp_path / "timer-delete.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    assert cli.main(["--root", str(tmp_path), "work", "plan", "command", "--file", str(path)]) != 0
    capsys.readouterr()
    assert work.read() == before
    stopped = cmd(timer, "stop", running["revision"], "stop")
    assert stopped["status"] == "idle"
    intervals = timer.totals()["rows"]
    # Mirrors UI stopAndMeasure/recordActual: elapsed time does not imply progress.
    op(work, "record-actual", "cube_progress", {"cube_id": "timed", "percent": 0, "actual_minutes": 1})
    result = work.planning_operation(command(work, "timed"))
    report = result["state"]["planning"]["completed_cubes"]["timed"]
    assert (report["percent"], report["actual_minutes"]) == (0, 1)
    assert timer.totals()["rows"] == intervals


def test_pool_delete_marks_overallocated_forecasts_stale_without_touching_manual_work(tmp_path):
    document = planning()
    task = document["tasks"][0]
    document["tasks"] = [{**task, "estimate_minutes": {"lower": 120, "central": 120, "upper": 120}, "review_minutes": 0}]
    work = setup(tmp_path, document)
    initial = work.read()["state"]["planning"]
    entry = initial["result"]["task_pool"][0]
    segment = entry["segments"][0]
    pool_id = "pool:"+entry["task_id"]+":"+segment["id"]
    result = work.planning_operation(command(work, pool_id))
    plan = result["state"]["planning"]
    assert _remaining(plan)["a"] == 60
    forecasts = [cube for cube in plan["result"]["cubes"] if cube["task_id"] == "a"]
    assert sum(cube["minutes"] for cube in forecasts) == 120
    assert all(cube["allocation_stale"] for cube in forecasts)
    forecast = forecasts[0]
    with pytest.raises(ValidationError, match="устарел"):
        op(work, "select-stale", "constructor_cube", {"cube_id": "new-forecast", "kind": "task", "task_id": "a",
                                                     "title": "Stale", "minutes": forecast["minutes"], "blueprint_ref": forecast["id"]})
    recalculated = op(work, "explicit-replan", "recalculate", {})["state"]["planning"]
    assert sum(cube["minutes"] for cube in recalculated["result"]["cubes"] if cube["task_id"] == "a") <= 60


def test_timer_cannot_enter_pending_or_deleted_target_and_pending_undo_is_guarded(tmp_path, monkeypatch):
    from megamozg.time_tracking import TimeStore
    from test_time_tracking import FakeClock, cmd
    work, store, note, cid = brainalot(tmp_path)
    request = command(work, cid, expected_note_version=note["version"])
    def pause(name, row):
        if name == "after_note_delete":
            raise OSError("pause after real delete")
    monkeypatch.setattr(work_delete, "_boundary", pause)
    with pytest.raises(DeleteStateError):
        work.planning_operation(request)
    clock = FakeClock()
    timer = TimeStore(tmp_path, lambda: clock.wall, lambda: clock.mono)
    target = {"source": "work", "id": "work-cube:"+cid, "title": "Deleting"}
    with pytest.raises(ConflictError, match="удаляется"):
        cmd(timer, "start", 0, "cannot-start-pending", task=target)
    with pytest.raises(ConflictError, match="удаляется"):
        cmd(timer, "switch_task", 0, "cannot-switch-pending", task=target)
    delete_op = next(row for row in store.history() if row["kind"] == "delete")
    with pytest.raises(ConflictError, match="завершите"):
        store.undo(delete_op["id"])
    assert not (store.vault / note["path"]).exists()
    monkeypatch.undo()
    result = work.planning_operation(request)
    assert result["delete_result"]["status"] == "complete"
    with pytest.raises(ConflictError, match="удаляется"):
        cmd(timer, "start", 0, "cannot-start-deleted", task=target)


def test_legacy_active_timer_after_note_delete_stays_pending_until_stop_then_recovers(tmp_path, monkeypatch):
    import json
    from megamozg.time_tracking import TimeStore
    from test_time_tracking import FakeClock, cmd
    work, store, note, cid = brainalot(tmp_path)
    request = command(work, cid, expected_note_version=note["version"])
    def pause(name, row):
        if name == "after_note_delete":
            raise OSError("pause")
    monkeypatch.setattr(work_delete, "_boundary", pause)
    with pytest.raises(DeleteStateError):
        work.planning_operation(request)
    monkeypatch.undo()
    clock = FakeClock()
    timer = TimeStore(tmp_path, lambda: clock.wall, lambda: clock.mono)
    # Reproduce an already broken state from the older implementation, bypassing the new guard.
    with timer._connect() as db:
        db.execute("UPDATE timer SET status='paused',task=?,elapsed_ms=10000 WHERE id=1",
                   (json.dumps({"source": "work", "id": "work-cube:"+cid, "title": "Old active"}),))
    snapshot = work.read()
    assert snapshot["deletion_state"]["pending"] and not snapshot["deletion_state"]["conflicts"]
    assert snapshot["revision"] == request["expected_revision"]
    cmd(timer, "stop", 0, "stop-legacy")
    result = work.planning_operation(request)
    assert result["revision"] == request["expected_revision"]+1
    assert result["delete_result"]["status"] == "complete"


def test_authoritative_remaining_baseline_does_not_subtract_prior_cancellation_twice(tmp_path):
    document = planning()
    task = document["tasks"][0]
    document["tasks"] = [{**task, "estimate_minutes": {"lower": 120, "central": 120, "upper": 120}, "review_minutes": 0}]
    work = setup(tmp_path, document)
    draft(work, "cancelled")
    deleted = work.planning_operation(command(work, "cancelled"))["state"]["planning"]
    assert _remaining(deleted)["a"] == 60
    result = op(work, "new-remaining", "day_outcome", {"task_id": "a", "date": NEXT,
                                                       "remaining_minutes": 30, "done": False})
    plan = result["state"]["planning"]
    assert _remaining(plan)["a"] == 30
    assert plan["cube_deletions"] == deleted["cube_deletions"]
    assert plan["remaining_epochs"]["a"] == 1
    assert plan["result"]["tasks"][0]["cancelled_minutes"] == 60
    # An earlier dated report cannot reset the newer authoritative baseline/epoch.
    older = op(work, "older-report", "day_outcome", {"task_id": "a", "date": DAY,
                                                     "remaining_minutes": 100, "done": False})
    assert _remaining(older["state"]["planning"])["a"] == 30
    assert older["state"]["planning"]["remaining_epochs"]["a"] == 1
    imported = op(work, "import-same", "import", {"planning": document})
    assert _remaining(imported["state"]["planning"])["a"] == 30
    assert imported["state"]["planning"]["remaining_epochs"]["a"] == 1
    draft(work, "cancel-new", minutes=15)
    deleted_again = work.planning_operation(command(work, "cancel-new"))["state"]["planning"]
    assert _remaining(deleted_again)["a"] == 15
    # Repeating the same numeric explicit baseline is still a fresh owner's assertion.
    same_number = op(work, "assert-30-again", "day_outcome", {"task_id": "a", "date": NEXT,
                                                            "remaining_minutes": 30, "done": False})
    assert _remaining(same_number["state"]["planning"])["a"] == 30
    assert same_number["state"]["planning"]["remaining_epochs"]["a"] == 2


def test_imported_new_authoritative_remaining_advances_epoch_only_once(tmp_path):
    work = setup(tmp_path)
    draft(work, "cancelled")
    deleted = work.planning_operation(command(work, "cancelled"))["state"]["planning"]
    document = planning()
    document["day_outcomes"] = [{"task_id": "a", "date": NEXT, "remaining_minutes": 30, "done": False, "note": "Owner baseline"}]
    result = op(work, "new-imported-baseline", "import", {"planning": document})["state"]["planning"]
    assert _remaining(result)["a"] == 30
    assert result["cube_deletions"] == deleted["cube_deletions"]
    epoch = result["remaining_epochs"]["a"]
    result = op(work, "same-imported-baseline", "import", {"planning": document})["state"]["planning"]
    assert _remaining(result)["a"] == 30
    assert result["remaining_epochs"]["a"] == epoch
