"""Export habit data in CSV or JSON.

Writers accept an already-open text stream and an open DB connection so they
stay composable and easy to test. The CLI layer handles file vs. stdout and
the db.session lifecycle.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from datetime import date
from typing import TextIO

from . import db
from .models import Habit


def _select_habits(
    conn: sqlite3.Connection,
    include_archived: bool,
    habit_filter: str | None,
) -> list[Habit]:
    habits = db.list_habits(conn, include_archived=include_archived)
    if habit_filter:
        needle = habit_filter.lower()
        habits = [h for h in habits if h.name.lower() == needle]
    return habits


def write_csv(
    conn: sqlite3.Connection,
    out: TextIO,
    include_archived: bool = False,
    habit_filter: str | None = None,
) -> int:
    """Flat completion rows. Returns number of data rows written."""
    writer = csv.writer(out)
    writer.writerow(
        [
            "date",
            "habit",
            "frequency",
            "unit",
            "target",
            "value",
            "duration_seconds",
            "note",
            "status",
        ]
    )
    rows = 0
    for h in _select_habits(conn, include_archived, habit_filter):
        for c in db.completions_for_habit(conn, h.id):
            writer.writerow(
                [
                    c.date.isoformat(),
                    h.name,
                    h.frequency,
                    h.unit or "",
                    "" if h.target is None else f"{h.target:g}",
                    "" if c.value is None else f"{c.value:g}",
                    "" if c.duration_seconds is None else str(c.duration_seconds),
                    c.note or "",
                    c.status,
                ]
            )
            rows += 1
    return rows


def write_json(
    conn: sqlite3.Connection,
    out: TextIO,
    include_archived: bool = False,
    habit_filter: str | None = None,
    exported_at: date | None = None,
) -> int:
    """Structured export with nested completions. Returns habit count."""
    habits = _select_habits(conn, include_archived, habit_filter)
    payload = {
        "exported_at": (exported_at or date.today()).isoformat(),
        "version": 1,
        "habits": [
            {
                "id": h.id,
                "name": h.name,
                "description": h.description,
                "frequency": h.frequency,
                "unit": h.unit,
                "target": h.target,
                "alpha": h.alpha,
                "created_at": h.created_at.isoformat(),
                "archived_at": h.archived_at.isoformat() if h.archived_at else None,
                "completions": [
                    {
                        "date": c.date.isoformat(),
                        "value": c.value,
                        "duration_seconds": c.duration_seconds,
                        "note": c.note,
                        "status": c.status,
                    }
                    for c in db.completions_for_habit(conn, h.id)
                ],
            }
            for h in habits
        ],
    }
    json.dump(payload, out, indent=2, ensure_ascii=False)
    out.write("\n")
    return len(habits)
