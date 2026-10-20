"""Conditional reads must remain fresh after direct vault edits."""
import os

from fastapi.testclient import TestClient

from megamozg.api import create_app
from megamozg.core import Store


def test_dashboard_and_notes_etags_follow_external_edits(tmp_path):
    store = Store(tmp_path)
    note = store.capture("Alpha", kind="idea", title="Alpha", external_id="conditional")
    path = store.vault / note["path"]
    # Exercise a real warm-cache hit deterministically, outside the two-second
    # racy timestamp window, rather than depending on the suite's execution time.
    aged_ns = 10**18
    os.utime(path, ns=(aged_ns, aged_ns))
    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765")
    origin = "chrome-extension://" + "a" * 32
    headers = {"Authorization": "Bearer " + store.token(), "Origin": origin}

    etags = {}
    for endpoint in ("/api/dashboard", "/api/notes"):
        first = client.get(endpoint, headers=headers)
        assert first.status_code == 200
        assert first.headers["etag"].startswith('"')
        etags[endpoint] = first.headers["etag"]
        assert first.headers["access-control-expose-headers"] == "ETag"
        unchanged = client.get(endpoint, headers={**headers, "If-None-Match": first.headers["etag"]})
        assert unchanged.status_code == 304 and unchanged.content == b""
        assert unchanged.headers["etag"] == first.headers["etag"]

    before = path.stat()
    assert before.st_mtime_ns == aged_ns
    original = path.read_text(encoding="utf-8")
    changed = original.replace("Alpha", "Bravo", 1)
    assert len(changed.encode("utf-8")) == len(original.encode("utf-8"))
    path.write_text(changed, encoding="utf-8", newline="\n")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))

    for endpoint, list_key in (("/api/dashboard", "library"), ("/api/notes", "notes")):
        # A stale ETag must not turn a same-size, same-mtime Obsidian edit into 304.
        fresh = client.get(endpoint, headers={**headers, "If-None-Match": etags[endpoint]})
        assert fresh.status_code == 200
        assert fresh.headers["etag"] != etags[endpoint]
        payload = fresh.json()
        assert payload[list_key][0]["title"] == "Bravo"

    assert client.get("/api/dashboard", headers={"If-None-Match": "*"}).status_code == 401
    preflight = client.options("/api/dashboard", headers={"Origin": origin})
    assert "If-None-Match" in preflight.headers["access-control-allow-headers"]


def test_conditional_validation_reads_content_but_reuses_unchanged_yaml(tmp_path, monkeypatch):
    import megamozg.core as core

    store = Store(tmp_path)
    note = store.capture("Cached", kind="idea", external_id="validated-cache")
    path = store.vault / note["path"]
    os.utime(path, ns=(10**18, 10**18))
    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765")
    headers = {"Authorization": "Bearer " + store.token()}
    etags = {endpoint: client.get(endpoint, headers=headers).headers["etag"]
             for endpoint in ("/api/dashboard", "/api/notes")}
    reads = []
    original = Store._content

    def read(self, relative):
        reads.append(relative)
        return original(self, relative)

    def unexpected_parse(_content):
        raise AssertionError("Unchanged validated content must reuse parsed YAML")

    monkeypatch.setattr(Store, "_content", read)
    monkeypatch.setattr(core, "decode_note", unexpected_parse)
    for endpoint, etag in etags.items():
        response = client.get(endpoint, headers={**headers, "If-None-Match": etag})
        assert response.status_code == 304
    assert reads == [note["path"], note["path"]]
