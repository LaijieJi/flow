"""DB snapshot helper.

Uses sqlite3's online backup API so a copy taken while the source is being
written remains internally consistent. Default destination is
``~/.flow/backups/habits-YYYY-MM-DD.db``; callers can override the directory
or the full output path.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from . import db as _db


DEFAULT_BACKUP_DIR = _db.DEFAULT_DB_DIR / "backups"


def default_backup_path(when: date | None = None, base: Path | None = None) -> Path:
    when = when or date.today()
    root = base or DEFAULT_BACKUP_DIR
    return root / f"habits-{when.isoformat()}.db"


def snapshot(
    src_path: Path | str | None = None,
    dest_path: Path | str | None = None,
    when: date | None = None,
) -> Path:
    """Write an atomic snapshot of the source DB to ``dest_path``.

    Returns the destination path. Refuses to overwrite an existing file so
    repeated same-day runs don't silently clobber yesterday's checkpoint —
    callers should choose a new path or delete the stale one first.
    """
    src = _db._resolve_db_path(src_path)
    if not src.exists():
        raise FileNotFoundError(f"source DB does not exist: {src}")

    dest = Path(dest_path) if dest_path is not None else default_backup_path(when)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise FileExistsError(f"backup already exists: {dest}")

    src_conn = sqlite3.connect(src)
    dest_conn = sqlite3.connect(dest)
    try:
        with dest_conn:
            src_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        src_conn.close()
    return dest
