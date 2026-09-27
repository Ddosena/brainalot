"""Data location, migration, port choice and Windows registration of the installed program."""
import json
import socket

import pytest

from megamozg import integrate, paths, runtime
from megamozg.core import Store, ValidationError


def test_checkout_keeps_data_next_to_the_code(monkeypatch):
    monkeypatch.delenv("BRAINALOT_HOME", raising=False)
    assert paths.default_root() == paths.project_root() / "data"


def test_installed_copy_keeps_data_out_of_the_program_folder(monkeypatch, tmp_path):
    monkeypatch.delenv("BRAINALOT_HOME", raising=False)
    monkeypatch.setattr(paths, "frozen", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(paths, "documents_folder", lambda: tmp_path / "Documents")
    root = paths.prepare_installed_root(paths.default_root())
    assert root == tmp_path / "local" / "Brainalot"
    store = Store(root)
    assert store.vault == (tmp_path / "Documents" / "Brainalot").resolve()
    assert (root / "state" / "journal.sqlite3").exists()
    note = store.capture("Мысль", kind="idea", source="test", external_id="1")
    assert (store.vault / note["path"]).exists()


def test_backup_names_vault_files_under_vault_even_when_it_lives_elsewhere(tmp_path):
    root = tmp_path / "root"
    paths.write_config(root, {"vault": str(tmp_path / "docs" / "Brainalot")})
    store = Store(root)
    store.capture("Мысль", kind="idea", source="test", external_id="1")
    import zipfile
    names = zipfile.ZipFile(store.backup(tmp_path / "backup.zip")).namelist()
    assert "state/journal.sqlite3" in names and any(name.startswith("vault/4 Идеи") for name in names)


def test_migrate_copies_notes_and_journal_and_refuses_to_mix(tmp_path):
    old = Store(tmp_path / "old")
    note = old.capture("Старая мысль", kind="idea", source="test", external_id="1")
    (old.state / "calendar.json").write_text("{}", encoding="utf-8")
    new_root = tmp_path / "new"
    Store(new_root)
    result = paths.migrate(tmp_path / "old", new_root)
    assert "journal.sqlite3" in result["copied_state"] and "calendar.json" in result["copied_state"]
    assert "writer.lock" not in result["copied_state"]
    moved = Store(new_root)
    assert moved._find(note["id"])["title"] == "Старая мысль"
    assert (tmp_path / "old" / "vault" / note["path"]).exists()  # the source stays
    with pytest.raises(ValidationError):
        paths.migrate(tmp_path / "old", new_root)


def test_pick_port_skips_a_busy_port():
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        assert runtime.pick_port(port, span=5) != port


def test_host_manifest_lists_the_extension_and_the_host_command(tmp_path):
    manifest = json.loads(integrate.write_host_manifest(tmp_path).read_text(encoding="utf-8"))
    assert manifest["name"] == integrate.HOST_NAME and manifest["type"] == "stdio"
    assert manifest["allowed_origins"] and all(o.startswith("chrome-extension://") and o.endswith("/")
                                               for o in manifest["allowed_origins"])
    assert manifest["path"].endswith(("native-host.cmd", "mmm.exe"))


def test_cli_answers_chrome_as_the_native_host(monkeypatch, capsysbinary):
    import io, struct
    from megamozg import cli, native_host
    payload = json.dumps({"action": "nonsense"}).encode()
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(struct.pack("<I", len(payload)) + payload)))
    assert cli.main(["chrome-extension://lbmdnalheigbldmodakfkmfjdhkpidch/"]) == 0
    out = capsysbinary.readouterr().out
    assert json.loads(out[4:]) == {"ok": False, "error": "unknown action"}


def test_a_backup_archive_moves_to_a_new_computer_with_migrate(tmp_path):
    import zipfile
    old = Store(tmp_path / "old")
    note = old.capture("Мысль со старого компьютера", kind="idea", source="test", external_id="1")
    archive = old.backup(tmp_path / "backup.zip")
    unpacked = tmp_path / "unpacked"
    zipfile.ZipFile(archive).extractall(unpacked)
    new_root = tmp_path / "new"
    Store(new_root)
    paths.migrate(unpacked, new_root)
    assert Store(new_root)._find(note["id"])["title"] == "Мысль со старого компьютера"
