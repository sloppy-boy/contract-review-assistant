import json
import sqlite3

import pytest

from app.backup import backup_data, restore_data


def _make_db(path, value="ok"):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE facts(value TEXT NOT NULL)")
        conn.execute("INSERT INTO facts(value) VALUES (?)", (value,))


def test_backup_manifest_is_allowlisted_and_restore_verifies_checksums(tmp_path):
    source = tmp_path / "data"
    backup = tmp_path / "backup"
    restored = tmp_path / "restored"
    source.mkdir()
    _make_db(source / "review_runs.db", "kept")
    (source / "settings.json").write_text('{"secret":"must not copy"}', encoding="utf-8")

    manifest = backup_data(source, backup)
    assert manifest["files"] == ["review_runs.db"]
    assert not (backup / "settings.json").exists()

    result = restore_data(backup, restored)
    assert result["files"] == ["review_runs.db"]
    with sqlite3.connect(restored / "review_runs.db") as conn:
        assert conn.execute("SELECT value FROM facts").fetchone()[0] == "kept"


def test_restore_rejects_tampered_backup_and_existing_target(tmp_path):
    source = tmp_path / "data"
    backup = tmp_path / "backup"
    source.mkdir()
    _make_db(source / "runs.db")
    backup_data(source, backup)
    with open(backup / "runs.db", "ab") as stream:
        stream.write(b"tampered")

    with pytest.raises(ValueError, match="checksum"):
        restore_data(backup, tmp_path / "restored")

    clean_backup = tmp_path / "clean-backup"
    backup_data(source, clean_backup)
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(FileExistsError):
        restore_data(clean_backup, existing)
