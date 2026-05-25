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

from .models import Habit, VALID_COMPLETION_STATUS, parse_frequency


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


def check_empty_habit_names(conn: sqlite3.Connection) -> list[Issue]:
    """Names that are empty or whitespace-only — the CLI rejects these at
    insert, but a hand-edited DB or an import path bug could slip them in.
    Empty names break fuzzy resolution and the alias subsystem."""
    rows = conn.execute(
        "SELECT id, name FROM habits WHERE TRIM(COALESCE(name, '')) = ''"
    ).fetchall()
    if not rows:
        return []
    sample = ", ".join(f"id={r['id']}" for r in rows[:3])
    return [
        Issue(
            code="empty_habit_name",
            summary=f"{len(rows)} habits have an empty or whitespace-only name",
            detail=f"sample: {sample}",
        )
    ]


def check_invalid_seasonal_windows(conn: sqlite3.Connection) -> list[Issue]:
    """`end_date < start_date` corrupts scheduling: the habit is never
    in-season. Insert-time validation rejects this, but stored data can drift
    via hand edits or import."""
    rows = conn.execute(
        "SELECT id, name, start_date, end_date FROM habits "
        "WHERE start_date IS NOT NULL AND end_date IS NOT NULL "
        "AND end_date < start_date"
    ).fetchall()
    if not rows:
        return []
    sample = ", ".join(
        f"{r['name']} ({r['start_date']}→{r['end_date']})" for r in rows[:3]
    )
    return [
        Issue(
            code="invalid_seasonal_window",
            summary=f"{len(rows)} habits have end_date before start_date",
            detail=f"sample: {sample}",
        )
    ]


def check_alpha_out_of_range(conn: sqlite3.Connection) -> list[Issue]:
    """`alpha` must be in [ALPHA_MIN, ALPHA_MAX]. Outside that band, the EMA
    becomes nonsense (negative weighting, or all-or-nothing decay)."""
    rows = conn.execute(
        "SELECT id, name, alpha FROM habits WHERE alpha IS NOT NULL "
        "AND (alpha < ? OR alpha > ?)",
        (Habit.ALPHA_MIN, Habit.ALPHA_MAX),
    ).fetchall()
    if not rows:
        return []
    sample = ", ".join(f"{r['name']}={r['alpha']}" for r in rows[:3])
    return [
        Issue(
            code="alpha_out_of_range",
            summary=(
                f"{len(rows)} habits have alpha outside "
                f"[{Habit.ALPHA_MIN}, {Habit.ALPHA_MAX}]"
            ),
            detail=f"sample: {sample}",
        )
    ]


def check_stale_aliases(conn: sqlite3.Connection) -> list[Issue]:
    """Aliases pointing at habits that no longer exist. Stale aliases fall
    through to fuzzy match at runtime (so they aren't a hard error), but they
    waste mental space — the alias `r → Read` no longer doing what the user
    expects deserves a visible cleanup signal."""
    from . import aliases as _aliases

    aliases = _aliases.load()
    if not aliases:
        return []
    rows = conn.execute("SELECT name FROM habits").fetchall()
    habit_names = {r["name"].lower() for r in rows}
    stale = sorted(
        (short, target)
        for short, target in aliases.items()
        if target.lower() not in habit_names
    )
    if not stale:
        return []
    sample = ", ".join(f"{short}→{target}" for short, target in stale[:3])
    return [
        Issue(
            code="stale_alias",
            summary=f"{len(stale)} alias(es) point at habits that don't exist",
            detail=f"sample: {sample}",
            fixable=True,
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
    check_empty_habit_names,
    check_invalid_seasonal_windows,
    check_alpha_out_of_range,
    check_stale_aliases,
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
    of rows changed (or aliases removed, in the case of `stale_alias`)."""
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
        elif issue.code == "stale_alias":
            from . import aliases as _aliases

            aliases = _aliases.load()
            rows = conn.execute("SELECT name FROM habits").fetchall()
            habit_names = {r["name"].lower() for r in rows}
            keep = {
                short: target
                for short, target in aliases.items()
                if target.lower() in habit_names
            }
            removed = len(aliases) - len(keep)
            if removed:
                _aliases.save(keep)
            fixed[issue.code] = removed
    return fixed
