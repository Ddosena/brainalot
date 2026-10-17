"""A live WAL must be included when migrating each Brainalot SQLite store."""
from __future__ import annotations

import sqlite3
from contextlib import closing

from megamozg import paths


def test_migrate_backs_up_all_live_sqlite_stores(tmp_path):
    source, target = tmp_path / "old", tmp_path / "new"
    (source / "vault").mkdir(parents=True)
    state = source / "state"
    state.mkdir()
    names = ("journal.sqlite3", "shared.sqlite3", "time.sqlite3", "chats.sqlite3")
    open_connections = []
    try:
        for name in names:
            db = sqlite3.connect(state / name)
            open_connections.append(db)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA wal_autocheckpoint=0")
            db.execute("CREATE TABLE sample(value TEXT)")
            db.execute("INSERT INTO sample VALUES (?)", (name,))
            db.commit()
            assert (state / (name + "-wal")).exists()
        result = paths.migrate(source, target)
        assert set(names).issubset(result["copied_state"])
        for name in names:
            assert not (target / "state" / (name + "-wal")).exists()
            with closing(sqlite3.connect(target / "state" / name)) as db:
                assert db.execute("SELECT value FROM sample").fetchone() == (name,)
    finally:
        for db in open_connections:
            db.close()
