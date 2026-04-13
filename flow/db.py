"""SQLite storage for flow.

Single connection per process. Schema is managed via a minimal migration
framework (a `schema_version` table + an ordered list of migration SQL
blocks). Applying migrations is idempotent.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Iterable, Iterator

from .models import Habit, Completion, parse_frequency


DEFAULT_DB_DIR = Path.home() / ".flow"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "habits.db"


def _adapt_date(d: date) -> str:
    return d.isoformat()


def _convert_date(b: bytes) -> date:
    return date.fromisoformat(b.decode())


sqlite3.register_adapter(date, _adapt_date)
sqlite3.register_converter("date", _convert_date)
sqlite3.register_converter("DATE", _convert_date)


MIGRATIONS: list[str] = [
    # v1: initial schema
    """
    CREATE TABLE habits (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT NOT NULL,
        description TEXT,
        frequency   TEXT NOT NULL,
        unit        TEXT,
        target      REAL,
        created_at  DATE NOT NULL,
        archived_at DATE
    );
    CREATE TABLE completions (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        habit_id  INTEGER NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
        date      DATE NOT NULL,
        value     REAL,
        note      TEXT,
        UNIQUE(habit_id, date)
    );
    CREATE INDEX idx_completions_habit_date ON completions(habit_id, date);
    """,
]


def _resolve_db_path(path: Path | str | None) -> Path:
    if path is not None:
        return Path(path)
    env = os.environ.get("FLOW_DB_PATH")
    if env:
        return Path(env)
    return DEFAULT_DB_PATH


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """Open a SQLite connection, creating parent dir if needed and applying
    any pending migrations. Caller is responsible for closing."""
    db_path = _resolve_db_path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        db_path,
        detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _migrate(conn)
    return conn


@contextmanager
def session(path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    """Context-managed connection. Commits on success, rolls back on error."""
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "  version INTEGER PRIMARY KEY"
        ")"
    )
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    current = row["v"] if row and row["v"] is not None else 0
    for i, sql in enumerate(MIGRATIONS, start=1):
        if i <= current:
            continue
        conn.executescript(sql)
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (i,))
    conn.commit()


# ---- row <-> dataclass helpers -------------------------------------------------


def _row_to_habit(row: sqlite3.Row) -> Habit:
    return Habit(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        frequency=row["frequency"],
        unit=row["unit"],
        target=row["target"],
        created_at=row["created_at"],
        archived_at=row["archived_at"],
    )


def _row_to_completion(row: sqlite3.Row) -> Completion:
    return Completion(
        id=row["id"],
        habit_id=row["habit_id"],
        date=row["date"],
        value=row["value"],
        note=row["note"],
    )


# ---- habit CRUD ----------------------------------------------------------------


def insert_habit(conn: sqlite3.Connection, habit: Habit) -> Habit:
    parse_frequency(habit.frequency)  # validate early
    cur = conn.execute(
        "INSERT INTO habits (name, description, frequency, unit, target, created_at, archived_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            habit.name,
            habit.description,
            habit.frequency,
            habit.unit,
            habit.target,
            habit.created_at,
            habit.archived_at,
        ),
    )
    habit.id = cur.lastrowid
    return habit


def get_habit(conn: sqlite3.Connection, habit_id: int) -> Habit | None:
    row = conn.execute("SELECT * FROM habits WHERE id = ?", (habit_id,)).fetchone()
    return _row_to_habit(row) if row else None


def list_habits(conn: sqlite3.Connection, include_archived: bool = False) -> list[Habit]:
    if include_archived:
        rows = conn.execute("SELECT * FROM habits ORDER BY id").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM habits WHERE archived_at IS NULL ORDER BY id"
        ).fetchall()
    return [_row_to_habit(r) for r in rows]


def archive_habit(conn: sqlite3.Connection, habit_id: int, when: date | None = None) -> bool:
    when = when or date.today()
    cur = conn.execute(
        "UPDATE habits SET archived_at = ? WHERE id = ? AND archived_at IS NULL",
        (when, habit_id),
    )
    return cur.rowcount > 0


def update_habit(conn: sqlite3.Connection, habit: Habit) -> Habit:
    """Persist edits to an existing habit. Validates frequency. Requires `habit.id`."""
    if habit.id is None:
        raise ValueError("update_habit requires habit.id")
    parse_frequency(habit.frequency)
    cur = conn.execute(
        "UPDATE habits SET name = ?, description = ?, frequency = ?, unit = ?, target = ? "
        "WHERE id = ?",
        (habit.name, habit.description, habit.frequency, habit.unit, habit.target, habit.id),
    )
    if cur.rowcount == 0:
        raise LookupError(f"habit id {habit.id} not found")
    return habit


def find_habit_by_name(
    conn: sqlite3.Connection, name: str, include_archived: bool = True
) -> Habit | None:
    """Case-insensitive exact-name lookup. Returns first match or None."""
    sql = "SELECT * FROM habits WHERE LOWER(name) = LOWER(?)"
    if not include_archived:
        sql += " AND archived_at IS NULL"
    sql += " LIMIT 1"
    row = conn.execute(sql, (name,)).fetchone()
    return _row_to_habit(row) if row else None


# ---- completion CRUD -----------------------------------------------------------


def upsert_completion(conn: sqlite3.Connection, completion: Completion) -> Completion:
    """Insert or replace today's completion for a habit (enforced unique on
    (habit_id, date)). Returns the stored row."""
    cur = conn.execute(
        "INSERT INTO completions (habit_id, date, value, note) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(habit_id, date) DO UPDATE SET value = excluded.value, note = excluded.note",
        (completion.habit_id, completion.date, completion.value, completion.note),
    )
    if cur.lastrowid:
        completion.id = cur.lastrowid
    else:
        row = conn.execute(
            "SELECT id FROM completions WHERE habit_id = ? AND date = ?",
            (completion.habit_id, completion.date),
        ).fetchone()
        completion.id = row["id"] if row else None
    return completion


def delete_completion(conn: sqlite3.Connection, habit_id: int, on: date) -> bool:
    cur = conn.execute(
        "DELETE FROM completions WHERE habit_id = ? AND date = ?", (habit_id, on)
    )
    return cur.rowcount > 0


def completions_for_habit(
    conn: sqlite3.Connection,
    habit_id: int,
    since: date | None = None,
    until: date | None = None,
) -> list[Completion]:
    sql = "SELECT * FROM completions WHERE habit_id = ?"
    params: list = [habit_id]
    if since is not None:
        sql += " AND date >= ?"
        params.append(since)
    if until is not None:
        sql += " AND date <= ?"
        params.append(until)
    sql += " ORDER BY date"
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_completion(r) for r in rows]


def all_completions(
    conn: sqlite3.Connection,
    since: date | None = None,
    until: date | None = None,
    habit_id: int | None = None,
) -> list[tuple[Completion, Habit]]:
    """Completions across all habits, optionally filtered. Returns (completion,
    habit) pairs ordered by date descending then habit id."""
    sql = (
        "SELECT c.*, h.name AS h_name, h.description AS h_description, "
        "h.frequency AS h_frequency, h.unit AS h_unit, h.target AS h_target, "
        "h.created_at AS h_created_at, h.archived_at AS h_archived_at "
        "FROM completions c JOIN habits h ON h.id = c.habit_id"
    )
    clauses: list[str] = []
    params: list = []
    if since is not None:
        clauses.append("c.date >= ?")
        params.append(since)
    if until is not None:
        clauses.append("c.date <= ?")
        params.append(until)
    if habit_id is not None:
        clauses.append("c.habit_id = ?")
        params.append(habit_id)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY c.date DESC, c.habit_id ASC"
    rows = conn.execute(sql, params).fetchall()
    out: list[tuple[Completion, Habit]] = []
    for r in rows:
        completion = Completion(
            id=r["id"],
            habit_id=r["habit_id"],
            date=r["date"],
            value=r["value"],
            note=r["note"],
        )
        habit = Habit(
            id=r["habit_id"],
            name=r["h_name"],
            description=r["h_description"],
            frequency=r["h_frequency"],
            unit=r["h_unit"],
            target=r["h_target"],
            created_at=r["h_created_at"],
            archived_at=r["h_archived_at"],
        )
        out.append((completion, habit))
    return out


def completions_on(conn: sqlite3.Connection, on: date) -> list[Completion]:
    rows = conn.execute(
        "SELECT * FROM completions WHERE date = ? ORDER BY habit_id", (on,)
    ).fetchall()
    return [_row_to_completion(r) for r in rows]


def bulk_insert_completions(
    conn: sqlite3.Connection, completions: Iterable[Completion]
) -> None:
    conn.executemany(
        "INSERT INTO completions (habit_id, date, value, note) VALUES (?, ?, ?, ?)",
        [(c.habit_id, c.date, c.value, c.note) for c in completions],
    )


def current_schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return row["v"] if row and row["v"] is not None else 0
