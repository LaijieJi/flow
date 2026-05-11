"""Database integrity checks for `flow doctor`.

Each check is a pure function over a `sqlite3.Connection`, returning a list of
`Issue` records. The CLI layer aggregates them, prints a report, and optionally
applies fixes. Designed for post-sync conflict cleanup — git-syncing the DB
file can produce duplicate rows or orphans that the live app would never
write on its own."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date as _date
from typing import Callable

from .models import VALID_COMPLETION_STATUS, parse_frequency


@dataclass(frozen=True)
class Issue:
    code: str
    summary: str
    detail: str
    fixable: bool = False


CheckFn = Callable[[sqlite3.Connection], list[Issue]]


def check_schema_version(conn: sqlite3.Connection) -> list[Issue]:
    """Highest applied migration matches what the migration package ships.

    `db.session()` auto-migrates on entry, so this should never fire in
    practice — it's a defence against a half-applied schema after manual
    edits or a crash mid-migration."""
    from . import db as _db

    applied = conn.execute(
        "SELECT MAX(version) AS v FROM schema_version"
    ).fetchone()
    applied_v = applied["v"] if applied and applied["v"] is not None else 0
    latest_v = _db.MIGRATIONS[-1][0] if _db.MIGRATIONS else 0
    if applied_v < latest_v:
        return [
            Issue(
                code="schema_outdated",
                summary=f"schema is v{applied_v}, expected v{latest_v}",
                detail="run any flow command to apply pending migrations",
            )
        ]
    return []


def check_orphan_completions(conn: sqlite3.Connection) -> list[Issue]:
    """Completions whose habit_id no longer references a habits row.

    FK cascade should prevent this in the live app, but file-level merges
    (git, manual edits) can leave dangling rows."""
    rows = conn.execute(
        "SELECT c.id, c.habit_id, c.date FROM completions c "
        "LEFT JOIN habits h ON h.id = c.habit_id "
        "WHERE h.id IS NULL"
    ).fetchall()
    if not rows:
        return []
    sample = ", ".join(
        f"id={r['id']} habit_id={r['habit_id']} date={r['date']}"
        for r in rows[:3]
    )
    return [
        Issue(
            code="orphan_completions",
            summary=f"{len(rows)} completion rows point at missing habits",
            detail=f"sample: {sample}",
            fixable=True,
        )
    ]


def check_future_completions(
    conn: sqlite3.Connection, today: _date | None = None
) -> list[Issue]:
    today = today if today is not None else _date.today()
    rows = conn.execute(
        "SELECT c.id, c.date, h.name "
        "FROM completions c JOIN habits h ON h.id = c.habit_id "
        "WHERE c.date > ? ORDER BY c.date DESC",
        (today.isoformat(),),
    ).fetchall()
    if not rows:
        return []
    sample = ", ".join(f"{r['name']} on {r['date']}" for r in rows[:3])
    return [
        Issue(
            code="future_completions",
            summary=f"{len(rows)} completions dated after today ({today.isoformat()})",
            detail=f"sample: {sample}",
            fixable=True,
        )
    ]


def check_completions_before_creation(conn: sqlite3.Connection) -> list[Issue]:
    rows = conn.execute(
        "SELECT c.id, c.date, h.name, h.created_at "
        "FROM completions c JOIN habits h ON h.id = c.habit_id "
        "WHERE c.date < h.created_at"
    ).fetchall()
    if not rows:
        return []
    sample = ", ".join(
        f"{r['name']} on {r['date']} (created {r['created_at']})"
        for r in rows[:3]
    )
    return [
        Issue(
            code="completion_before_creation",
            summary=f"{len(rows)} completions predate their habit's created_at",
            detail=f"sample: {sample}",
        )
    ]


def check_duplicate_completions(conn: sqlite3.Connection) -> list[Issue]:
    """Schema enforces UNIQUE(habit_id, date), but a buggy import path or a
    raw-SQL edit could violate it. Belt-and-braces check."""
    rows = conn.execute(
        "SELECT habit_id, date, COUNT(*) AS n FROM completions "
        "GROUP BY habit_id, date HAVING n > 1"
    ).fetchall()
    if not rows:
        return []
    sample = ", ".join(
        f"habit_id={r['habit_id']} date={r['date']} count={r['n']}"
        for r in rows[:3]
    )
    return [
        Issue(
            code="duplicate_completions",
            summary=f"{len(rows)} (habit, date) pairs have duplicate rows",
            detail=f"sample: {sample}",
        )
    ]


def check_skip_rows_with_timestamp(conn: sqlite3.Connection) -> list[Issue]:
    """v6 convention: skip rows always have NULL completed_at."""
    rows = conn.execute(
        "SELECT c.id, c.date, h.name FROM completions c "
        "JOIN habits h ON h.id = c.habit_id "
        "WHERE c.status = 'skipped' AND c.completed_at IS NOT NULL"
    ).fetchall()
    if not rows:
        return []
    sample = ", ".join(f"{r['name']} on {r['date']}" for r in rows[:3])
    return [
        Issue(
            code="skip_with_timestamp",
            summary=f"{len(rows)} skip rows have a non-NULL completed_at",
            detail=f"sample: {sample}",
            fixable=True,
        )
    ]


def check_invalid_status(conn: sqlite3.Connection) -> list[Issue]:
    """CHECK constraint should prevent, but verify against the Python source
    of truth in case the DB was hand-edited."""
    valid = tuple(sorted(VALID_COMPLETION_STATUS))
    placeholders = ",".join("?" * len(valid))
    rows = conn.execute(
        f"SELECT id, habit_id, date, status FROM completions "
        f"WHERE status NOT IN ({placeholders})",
        valid,
    ).fetchall()
    if not rows:
        return []
    sample = ", ".join(
        f"id={r['id']} status={r['status']!r}" for r in rows[:3]
    )
    return [
        Issue(
            code="invalid_status",
            summary=f"{len(rows)} completions have a status outside {{done, skipped}}",
            detail=f"sample: {sample}",
        )
    ]


def check_invalid_frequencies(conn: sqlite3.Connection) -> list[Issue]:
    rows = conn.execute("SELECT id, name, frequency FROM habits").fetchall()
    bad: list[tuple[int, str, str]] = []
    for r in rows:
        try:
            parse_frequency(r["frequency"])
        except ValueError:
            bad.append((r["id"], r["name"], r["frequency"]))
    if not bad:
        return []
    sample = ", ".join(f"{name}={freq!r}" for _, name, freq in bad[:3])
    return [
        Issue(
            code="invalid_frequency",
            summary=f"{len(bad)} habits have an unparseable frequency",
            detail=f"sample: {sample}",
        )
    ]


ALL_CHECKS: tuple[CheckFn, ...] = (
    check_schema_version,
    check_orphan_completions,
    check_future_completions,
    check_completions_before_creation,
    check_duplicate_completions,
    check_skip_rows_with_timestamp,
    check_invalid_status,
    check_invalid_frequencies,
)


def run_checks(conn: sqlite3.Connection) -> list[Issue]:
    issues: list[Issue] = []
    for check in ALL_CHECKS:
        issues.extend(check(conn))
    return issues


def apply_fixes(
    conn: sqlite3.Connection, issues: list[Issue], today: _date | None = None
) -> dict[str, int]:
    """Apply safe fixes for the fixable issue codes. Returns a per-code count
    of rows changed."""
    today = today if today is not None else _date.today()
    fixed: dict[str, int] = {}
    for issue in issues:
        if not issue.fixable:
            continue
        if issue.code == "orphan_completions":
            cur = conn.execute(
                "DELETE FROM completions WHERE habit_id NOT IN "
                "(SELECT id FROM habits)"
            )
            fixed[issue.code] = cur.rowcount
        elif issue.code == "future_completions":
            cur = conn.execute(
                "DELETE FROM completions WHERE date > ?", (today.isoformat(),)
            )
            fixed[issue.code] = cur.rowcount
        elif issue.code == "skip_with_timestamp":
            cur = conn.execute(
                "UPDATE completions SET completed_at = NULL "
                "WHERE status = 'skipped' AND completed_at IS NOT NULL"
            )
            fixed[issue.code] = cur.rowcount
    return fixed
