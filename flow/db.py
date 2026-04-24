"""SQLite storage for flow.

Single connection per process. Schema is managed via a minimal migration
framework (a `schema_version` table + an ordered list of migration SQL
blocks). Applying migrations is idempotent.
"""

from __future__ import annotations

import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Iterable, Iterator

from .models import Habit, Completion, parse_frequency


DEFAULT_DB_DIR = Path.home() / ".flow"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "habits.db"

_MIGRATION_NAME_RE = re.compile(r"^(\d+)_[\w\-]+\.sql$")


def _adapt_date(d: date) -> str:
    return d.isoformat()


def _convert_date(b: bytes) -> date:
    return date.fromisoformat(b.decode())


sqlite3.register_adapter(date, _adapt_date)
sqlite3.register_converter("date", _convert_date)
sqlite3.register_converter("DATE", _convert_date)


def _discover_migrations() -> list[tuple[int, str, str]]:
    """Load migrations from the `flow.migrations` package.

    Returns `(version, filename, sql)` triples sorted by version. Enforces:
      - filenames match `NNN_description.sql`
      - versions form a gap-free sequence starting at 1
    Both rules catch the common "forgot to renumber" mistake early.
    """
    out: list[tuple[int, str, str]] = []
    for entry in resources.files("flow.migrations").iterdir():
        name = entry.name
        if not name.endswith(".sql"):
            continue
        match = _MIGRATION_NAME_RE.match(name)
        if not match:
            raise RuntimeError(
                f"migration filename {name!r} does not match NNN_description.sql"
            )
        version = int(match.group(1))
        sql = entry.read_text(encoding="utf-8")
        out.append((version, name, sql))
    out.sort(key=lambda t: t[0])

    seen: set[int] = set()
    for v, name, _ in out:
        if v in seen:
            raise RuntimeError(f"duplicate migration version {v} ({name})")
        seen.add(v)
    for expected, (v, name, _) in enumerate(out, start=1):
        if v != expected:
            raise RuntimeError(
                f"migration gap: expected v{expected}, found v{v} ({name})"
            )
    return out


MIGRATIONS: list[tuple[int, str, str]] = _discover_migrations()


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
    for version, _name, sql in MIGRATIONS:
        if version <= current:
            continue
        conn.executescript(sql)
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
    conn.commit()


# ---- row <-> dataclass helpers -------------------------------------------------


def _row_to_habit(row: sqlite3.Row) -> Habit:
    keys = row.keys()
    start_date = row["start_date"] if "start_date" in keys else None
    end_date = row["end_date"] if "end_date" in keys else None
    alpha = row["alpha"] if "alpha" in keys else Habit.ALPHA_DEFAULT
    return Habit(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        frequency=row["frequency"],
        unit=row["unit"],
        target=row["target"],
        created_at=row["created_at"],
        archived_at=row["archived_at"],
        start_date=start_date,
        end_date=end_date,
        alpha=alpha if alpha is not None else Habit.ALPHA_DEFAULT,
    )


def _row_to_completion(row: sqlite3.Row) -> Completion:
    keys = row.keys()
    duration = row["duration_seconds"] if "duration_seconds" in keys else None
    return Completion(
        id=row["id"],
        habit_id=row["habit_id"],
        date=row["date"],
        value=row["value"],
        note=row["note"],
        duration_seconds=duration,
    )


# ---- habit CRUD ----------------------------------------------------------------


def insert_habit(conn: sqlite3.Connection, habit: Habit) -> Habit:
    parse_frequency(habit.frequency)  # validate early
    if (
        habit.start_date is not None
        and habit.end_date is not None
        and habit.end_date < habit.start_date
    ):
        raise ValueError("end_date cannot be before start_date")
    if not Habit.ALPHA_MIN <= habit.alpha <= Habit.ALPHA_MAX:
        raise ValueError(
            f"alpha must be in [{Habit.ALPHA_MIN}, {Habit.ALPHA_MAX}]"
        )
    cur = conn.execute(
        "INSERT INTO habits ("
        "  name, description, frequency, unit, target, created_at, archived_at,"
        "  start_date, end_date, alpha"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            habit.name,
            habit.description,
            habit.frequency,
            habit.unit,
            habit.target,
            habit.created_at,
            habit.archived_at,
            habit.start_date,
            habit.end_date,
            habit.alpha,
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


def restore_habit(conn: sqlite3.Connection, habit_id: int) -> bool:
    cur = conn.execute(
        "UPDATE habits SET archived_at = NULL WHERE id = ? AND archived_at IS NOT NULL",
        (habit_id,),
    )
    return cur.rowcount > 0


def update_habit(conn: sqlite3.Connection, habit: Habit) -> Habit:
    """Persist edits to an existing habit. Validates frequency. Requires `habit.id`."""
    if habit.id is None:
        raise ValueError("update_habit requires habit.id")
    parse_frequency(habit.frequency)
    if (
        habit.start_date is not None
        and habit.end_date is not None
        and habit.end_date < habit.start_date
    ):
        raise ValueError("end_date cannot be before start_date")
    if not Habit.ALPHA_MIN <= habit.alpha <= Habit.ALPHA_MAX:
        raise ValueError(
            f"alpha must be in [{Habit.ALPHA_MIN}, {Habit.ALPHA_MAX}]"
        )
    cur = conn.execute(
        "UPDATE habits SET name = ?, description = ?, frequency = ?, unit = ?, "
        "target = ?, start_date = ?, end_date = ?, alpha = ? WHERE id = ?",
        (
            habit.name,
            habit.description,
            habit.frequency,
            habit.unit,
            habit.target,
            habit.start_date,
            habit.end_date,
            habit.alpha,
            habit.id,
        ),
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
        "INSERT INTO completions (habit_id, date, value, note, duration_seconds) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(habit_id, date) DO UPDATE SET "
        "value = excluded.value, note = excluded.note, "
        "duration_seconds = excluded.duration_seconds",
        (
            completion.habit_id,
            completion.date,
            completion.value,
            completion.note,
            completion.duration_seconds,
        ),
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
        "h.created_at AS h_created_at, h.archived_at AS h_archived_at, "
        "h.start_date AS h_start_date, h.end_date AS h_end_date, "
        "h.alpha AS h_alpha "
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
            duration_seconds=r["duration_seconds"],
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
            start_date=r["h_start_date"],
            end_date=r["h_end_date"],
            alpha=r["h_alpha"] if r["h_alpha"] is not None else Habit.ALPHA_DEFAULT,
        )
        out.append((completion, habit))
    return out


def most_recent_completion(
    conn: sqlite3.Connection, habit_id: int | None = None
) -> tuple[Completion, Habit] | None:
    """Most recent completion across all habits, or scoped to one. Tie-breaks
    on completion id so the true "last recorded" wins even on same-day pairs."""
    sql = (
        "SELECT c.*, h.name AS h_name, h.description AS h_description, "
        "h.frequency AS h_frequency, h.unit AS h_unit, h.target AS h_target, "
        "h.created_at AS h_created_at, h.archived_at AS h_archived_at, "
        "h.start_date AS h_start_date, h.end_date AS h_end_date, "
        "h.alpha AS h_alpha "
        "FROM completions c JOIN habits h ON h.id = c.habit_id"
    )
    params: list = []
    if habit_id is not None:
        sql += " WHERE c.habit_id = ?"
        params.append(habit_id)
    sql += " ORDER BY c.date DESC, c.id DESC LIMIT 1"
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return None
    completion = Completion(
        id=row["id"],
        habit_id=row["habit_id"],
        date=row["date"],
        value=row["value"],
        note=row["note"],
        duration_seconds=row["duration_seconds"],
    )
    habit = Habit(
        id=row["habit_id"],
        name=row["h_name"],
        description=row["h_description"],
        frequency=row["h_frequency"],
        unit=row["h_unit"],
        target=row["h_target"],
        created_at=row["h_created_at"],
        archived_at=row["h_archived_at"],
        start_date=row["h_start_date"],
        end_date=row["h_end_date"],
        alpha=row["h_alpha"] if row["h_alpha"] is not None else Habit.ALPHA_DEFAULT,
    )
    return completion, habit


def completions_on(conn: sqlite3.Connection, on: date) -> list[Completion]:
    rows = conn.execute(
        "SELECT * FROM completions WHERE date = ? ORDER BY habit_id", (on,)
    ).fetchall()
    return [_row_to_completion(r) for r in rows]


def bulk_insert_completions(
    conn: sqlite3.Connection, completions: Iterable[Completion]
) -> None:
    conn.executemany(
        "INSERT INTO completions (habit_id, date, value, note, duration_seconds) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (c.habit_id, c.date, c.value, c.note, c.duration_seconds)
            for c in completions
        ],
    )


def current_schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return row["v"] if row and row["v"] is not None else 0
