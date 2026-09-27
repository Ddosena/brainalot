import base64
import json
import sqlite3
import zipfile

import pytest
from fastapi.testclient import TestClient

from megamozg.api import create_app
from megamozg.core import ConflictError, Store, ValidationError


PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR" + b"\x00" * 8 + b"\x00" * 4)


def upload(store, name="photo.png", data=PNG):
    return store.upload_attachment(name, base64.b64encode(data).decode("ascii"))


def test_upload_auth_bounds_mime_and_paths(tmp_path):
    store = Store(tmp_path)
    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765")
    auth = {"Authorization": "Bearer " + store.token(), "Origin": "chrome-extension://" + "a" * 32}
    data = {"name": "photo.png", "content_base64": base64.b64encode(PNG).decode("ascii")}
    assert client.post("/api/attachments", json=data).status_code == 401
    response = client.post("/api/attachments", headers=auth, json=data)
    assert response.status_code == 200
    descriptor = response.json()
    assert descriptor["mime"] == "image/png" and descriptor["size"] == len(PNG)
    assert descriptor["path"].startswith("8 Вложения/")
    assert client.post("/api/attachments", headers=auth, json=data).json()["id"] == descriptor["id"]
    got = client.get("/api/attachments/" + descriptor["id"], headers=auth)
    assert got.content == PNG and got.headers["x-content-type-options"] == "nosniff"
    assert got.headers["content-disposition"].startswith("attachment;")
    assert client.get("/api/attachments/" + descriptor["id"]).status_code == 401
    assert client.get("/api/attachments/..%2Fsecret", headers=auth).status_code in {404, 422}
    forged = upload(store, "evil.png", b"<svg onload=alert(1)></svg>")
    assert forged["mime"] == "application/octet-stream"
    assert client.get("/api/attachments/" + forged["id"], headers=auth).headers["content-type"].startswith("application/octet-stream")
    for name, content in (("../escape.txt", "eA=="), ("x.txt", "not base64")):
        assert client.post("/api/attachments", headers=auth, json={"name": name, "content_base64": content}).status_code == 422
    assert client.post("/api/attachments", headers=auth, json={"name": "huge.bin", "content_base64": base64.b64encode(b"x" * (10 * 1024 * 1024 + 1)).decode()}).status_code in {413, 422}
    assert client.post("/api/captures", headers=auth, content=b"x" * 262145).status_code == 413


def test_attachment_only_replay_update_move_and_undo(tmp_path):
    store = Store(tmp_path)
    image = upload(store, "a [cat].png")
    document = upload(store, "notes.md", b"# document\n")
    first = store.capture("", attachments=[image, document], external_id="clip-1")
    assert first["title"] == image["name"] and first["attachments"] == [image, document]
    assert first["body"].count("<!-- mmm:attachments -->") == 1
    assert "![a \\[cat\\].png](../8%20%D0%92%D0%BB%D0%BE%D0%B6%D0%B5%D0%BD%D0%B8%D1%8F/" in first["body"]
    assert "[notes.md](../8%20" in first["body"]
    assert store.capture("", attachments=[{"id": image["id"], "name": image["name"], "size": 0}, document], external_id="clip-1")["id"] == first["id"]
    with pytest.raises(ConflictError):
        store.capture("", attachments=[document], external_id="clip-1")
    with pytest.raises(ConflictError):
        store.update(first["id"], {"attachments": []}, "old")
    moved = store.update(first["id"], {"kind": "task"}, first["version"])
    assert moved["body"].count("<!-- mmm:attachments -->") == 1
    assert "../../8%20" in moved["body"]
    removed = store.update(first["id"], {"attachments": [], "body": "# Edited\n\nKept by user.\n"}, moved["version"])
    assert removed["attachments"] == [] and "mmm:attachments" not in removed["body"]
    assert "Kept by user" in removed["body"]
    restored = store.undo(store.history()[0]["id"])
    assert restored["attachments"] == [image, document]
    assert restored["body"].count("<!-- mmm:attachments -->") == 1
    assert (store.vault / image["path"]).is_file()


def test_invalid_refs_recovery_backup_and_markdown_exclusion(tmp_path, monkeypatch):
    store = Store(tmp_path / "data")
    document = upload(store, "story.md", b"# not a note\n")
    assert store.list_notes() == []
    with pytest.raises(ValidationError):
        store.capture("", attachments=[{"id": "../bad", "name": "x"}])
    with pytest.raises(ValidationError):
        store.capture("", attachments=[document] * 11)
    original_apply = store._apply
    monkeypatch.setattr(store, "_apply", lambda *_: (_ for _ in ()).throw(OSError("crash")))
    with pytest.raises(OSError):
        store.capture("", attachments=[document], external_id="pending")
    monkeypatch.setattr(store, "_apply", original_apply)
    recovered = Store(tmp_path / "data")
    note = recovered.capture("", attachments=[document], external_id="pending")
    assert note["attachments"] == [document]
    archive = recovered.backup(tmp_path / "backup.zip")
    with zipfile.ZipFile(archive) as bundle:
        assert "vault/" + document["path"] in bundle.namelist()
        bundle.extractall(tmp_path / "restore")
    restored = Store(tmp_path / "restore")
    assert restored.get_attachment(document["id"])[0] == b"# not a note\n"
    assert restored.list_notes()[0]["attachments"] == [document]


def test_symlink_escape_and_empty_document(tmp_path):
    root = tmp_path / "data"
    store = Store(root)
    blank = upload(store, "blank.txt", b"")
    assert store.get_attachment(blank["id"])[0] == b""
    target = tmp_path / "outside"
    target.mkdir()
    attachment_dir = store.vault / "8 Вложения"
    alias = attachment_dir / ("0" * 64 + ".bin")
    outside_file = target / "outside.bin"
    outside_file.write_bytes(b"outside")
    try:
        alias.symlink_to(outside_file)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks are unavailable on this host")
    with pytest.raises(ValidationError):
        store.get_attachment(alias.name)
    with pytest.raises(ValidationError):
        store.capture("text", attachments=[{"id": alias.name, "name": "outside.bin"}])


def test_repeated_refs_and_legacy_delivery_payload(tmp_path):
    store = Store(tmp_path)
    attachment = upload(store, "same.txt", b"same")
    repeated = store.capture("Two copies", attachments=[attachment, {**attachment, "name": "again.txt"}])
    assert len(repeated["attachments"]) == 2
    assert repeated["body"].count(attachment["id"]) == 2
    legacy = store.capture("Old delivery", external_id="old-1")
    with sqlite3.connect(store.db_path) as db:
        payload = json.loads(db.execute("SELECT payload FROM captures WHERE external_id='old-1'").fetchone()[0])
        del payload["attachments"]
        db.execute("UPDATE captures SET payload=? WHERE external_id='old-1'", (json.dumps(payload),))
    assert store.capture("Old delivery", external_id="old-1")["id"] == legacy["id"]
