"""Safe local backup and restore for the SQLite runtime databases.

Only ``*.db`` files are included. Settings, environment files, standalone
uploads and model outputs are outside this boundary; a database that already
stores asset text may therefore contain contract content and must be handled
as sensitive data by the backup destination.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


_MANIFEST = "manifest.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_name(value: str) -> str:
    path = Path(value)
    if path.name != value or path.suffix.lower() != ".db" or value in {"", ".", ".."}:
        raise ValueError("backup manifest contains an invalid database name")
    return value


def _sqlite_snapshot(source: Path, destination: Path) -> None:
    source_conn = sqlite3.connect(source)
    destination_conn = sqlite3.connect(destination)
    try:
        source_conn.backup(destination_conn)
        destination_conn.commit()
    finally:
        destination_conn.close()
        source_conn.close()


def backup_data(source_dir: Path | str, backup_dir: Path | str) -> dict[str, Any]:
    """Create a consistent, allowlisted SQLite backup and checksum manifest."""
    source = Path(source_dir)
    target = Path(backup_dir)
    if not source.is_dir():
        raise FileNotFoundError(f"data directory not found: {source}")
    target.mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    checksums: dict[str, str] = {}
    for database in sorted(source.glob("*.db")):
        if not database.is_file():
            continue
        name = _safe_name(database.name)
        temporary = target / f".{name}.{secrets.token_hex(6)}.tmp"
        try:
            _sqlite_snapshot(database, temporary)
            os.replace(temporary, target / name)
        finally:
            temporary.unlink(missing_ok=True)
        files.append(name)
        checksums[name] = _sha256(target / name)
    manifest = {
        "version": 1,
        "createdAt": datetime.now(UTC).isoformat(),
        "source": source.name,
        "files": files,
        "checksums": checksums,
    }
    (target / _MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def restore_data(backup_dir: Path | str, target_dir: Path | str) -> dict[str, Any]:
    """Verify a backup and restore it into a new directory atomically."""
    backup = Path(backup_dir)
    target = Path(target_dir)
    if not backup.is_dir():
        raise FileNotFoundError(f"backup directory not found: {backup}")
    if target.exists():
        raise FileExistsError(f"restore target already exists: {target}")
    try:
        manifest = json.loads((backup / _MANIFEST).read_text(encoding="utf-8"))
        files = manifest["files"]
        checksums = manifest["checksums"]
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError("backup manifest is invalid") from exc
    if not isinstance(files, list) or not isinstance(checksums, dict) or any(not isinstance(name, str) for name in files):
        raise ValueError("backup manifest is invalid")
    safe_files = [_safe_name(name) for name in files]
    if set(safe_files) != set(checksums):
        raise ValueError("backup manifest is invalid")
    for name in safe_files:
        path = backup / name
        if not path.is_file() or _sha256(path) != checksums[name]:
            raise ValueError(f"backup checksum mismatch: {name}")

    staging = target.parent / f".{target.name}.{secrets.token_hex(6)}.restore"
    staging.mkdir(parents=True)
    try:
        for name in safe_files:
            shutil.copy2(backup / name, staging / name)
        os.replace(staging, target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return {"files": safe_files, "verified": True}


__all__ = ["backup_data", "restore_data"]
