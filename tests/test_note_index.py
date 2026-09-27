"""The parsed-note cache of Store must never hide a change on disk."""
import os

from megamozg.core import Store


def test_external_edit_is_seen_even_with_the_same_size_and_mtime(tmp_path):
    store = Store(tmp_path)
    note = store.capture("Первая мысль", kind="idea", source="test", external_id="a")
    assert store._find(note["id"])["title"] == "Первая мысль"
    path = store.vault / note["path"]
    stat = path.stat()
    text = path.read_text(encoding="utf-8")
    changed = text.replace("Первая мысль", "Вторая мысль", 1)
    assert len(changed.encode()) == len(text.encode())
    path.write_text(changed, encoding="utf-8", newline="\n")
    # Worst case for a mtime/size cache: Obsidian rewrote it within the same timestamp tick.
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert store._find(note["id"])["title"] == "Вторая мысль"


def test_deleted_and_new_files_are_picked_up(tmp_path):
    store = Store(tmp_path)
    first = store.capture("Раз", kind="idea", source="test", external_id="1")
    (store.vault / first["path"]).unlink()
    second = store.capture("Два", kind="idea", source="test", external_id="2")
    ids = {note["id"] for note in store.list_notes()}
    assert ids == {second["id"]}
    assert first["id"] not in store._index and all(entry[2] is None or entry[2]["id"] != first["id"]
                                                   for entry in store._index.values())


def test_callers_cannot_change_the_cached_notes(tmp_path):
    store = Store(tmp_path)
    note = store.capture("Мысль", kind="idea", source="test", external_id="x", tags=["one"])
    found = store._find(note["id"])
    found["title"] = "испорчено"
    found["tags"].append("two")
    again = store._find(note["id"])
    assert again["title"] == "Мысль" and again["tags"] == ["one"]


def test_broken_note_is_reported_and_recovers_after_a_fix(tmp_path):
    store = Store(tmp_path)
    note = store.capture("Мысль", kind="idea", source="test", external_id="y")
    path = store.vault / note["path"]
    good = path.read_text(encoding="utf-8")
    path.write_text("без свойств", encoding="utf-8")
    notes, warnings = store._scan()
    assert not notes and warnings
    path.write_text(good, encoding="utf-8", newline="\n")
    assert store._find(note["id"])["title"] == "Мысль"


def test_unchanged_old_files_are_served_from_the_cache(tmp_path, monkeypatch):
    store = Store(tmp_path)
    note = store.capture("Старая мысль", kind="idea", source="test", external_id="old")
    path = store.vault / note["path"]
    os.utime(path, ns=(10**18, 10**18))  # 2001: long past the racy window
    store._scan()
    reads = []
    original = store._read_note
    monkeypatch.setattr(store, "_read_note", lambda relative: reads.append(relative) or original(relative))
    assert store._find(note["id"])["title"] == "Старая мысль"
    assert reads == []


def test_cold_parallel_scan_keeps_all_notes_and_duplicate_warnings(tmp_path):
    store = Store(tmp_path)
    first = store.capture("Seed", kind="idea", external_id="seed")
    source = (store.vault / first["path"]).read_text(encoding="utf-8")
    directory = (store.vault / first["path"]).parent
    for index in range(1, 40):
        (directory / f"parallel-{index}.md").write_text(
            source.replace(first["id"], f"a{index:031x}"), encoding="utf-8")
    (directory / "duplicate.md").write_text(source, encoding="utf-8")

    notes, warnings = store._scan()
    assert len(notes) == 41
    assert len([warning for warning in warnings if warning.startswith("Повтор ID:")]) == 1
