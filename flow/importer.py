"""JSON import for flow.

Round-trips with `flow.export.write_json`. Matches habits by case-insensitive
name. Conflict strategies decide what happens when a name already exists:

  - skip:      leave the existing habit and its completions untouched; nothing
               from the import for that habit is applied.
  - overwrite: replace the existing habit's metadata and wipe its completions,
               then load the import's completions.
  - merge:     keep the existing habit's metadata; for each completion in the
               import, insert it only if (habit_id, date) is not already
               present (existing data wins).

New habits (no name match) are always inserted with all their completions.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, TextIO

from . import db
from .models import (
    COMPLETION_STATUS_DONE,
    VALID_COMPLETION_STATUS,
    Completion,
    Habit,
    parse_frequency,
)


CONFLICT_MODES = ("skip", "overwrite", "merge")
SUPPORTED_VERSIONS = {1}


@dataclass(slots=True)
class ImportStats:
    habits_added: int = 0
    habits_overwritten: int = 0
    habits_merged: int = 0
    habits_skipped: int = 0
    completions_added: int = 0
    completions_skipped: int = 0


class FlowImportError(Exception):
    pass


def _parse_date(value: str | None, field: str, ctx: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError) as e:
        raise FlowImportError(f"{ctx}: invalid {field} {value!r}: {e}")


def _required_date(value: Any, field: str, ctx: str) -> date:
    parsed = _parse_date(value, field, ctx)
    if parsed is None:
        raise FlowImportError(f"{ctx}: missing {field}")
    return parsed


def _habit_from_payload(raw: dict[str, Any], ctx: str) -> Habit:
    if not isinstance(raw, dict):
        raise FlowImportError(f"{ctx}: habit must be an object")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise FlowImportError(f"{ctx}: habit missing 'name'")
    frequency = raw.get("frequency")
    if not isinstance(frequency, str):
        raise FlowImportError(f"{ctx} {name!r}: missing 'frequency'")
    try:
        parse_frequency(frequency)
    except ValueError as e:
        raise FlowImportError(f"{ctx} {name!r}: invalid frequency: {e}")

    target = raw.get("target")
    if target is not None and not isinstance(target, (int, float)):
        raise FlowImportError(f"{ctx} {name!r}: target must be number or null")

    alpha = raw.get("alpha", Habit.ALPHA_DEFAULT)
    if not isinstance(alpha, (int, float)):
        raise FlowImportError(f"{ctx} {name!r}: alpha must be number")
    if not Habit.ALPHA_MIN <= alpha <= Habit.ALPHA_MAX:
        raise FlowImportError(
            f"{ctx} {name!r}: alpha {alpha} outside [{Habit.ALPHA_MIN}, "
            f"{Habit.ALPHA_MAX}]"
        )

    created_at = _required_date(raw.get("created_at"), "created_at", f"{ctx} {name!r}")
    archived_at = _parse_date(raw.get("archived_at"), "archived_at", f"{ctx} {name!r}")
    start_date = _parse_date(raw.get("start_date"), "start_date", f"{ctx} {name!r}")
    end_date = _parse_date(raw.get("end_date"), "end_date", f"{ctx} {name!r}")

    description = raw.get("description")
    if description is not None and not isinstance(description, str):
        raise FlowImportError(f"{ctx} {name!r}: description must be string or null")
    unit = raw.get("unit")
    if unit is not None and not isinstance(unit, str):
        raise FlowImportError(f"{ctx} {name!r}: unit must be string or null")

    return Habit(
        name=name,
        description=description,
        frequency=frequency,
        unit=unit,
        target=float(target) if target is not None else None,
        created_at=created_at,
        archived_at=archived_at,
        start_date=start_date,
        end_date=end_date,
        alpha=float(alpha),
    )


def _completion_from_payload(
    raw: dict[str, Any], habit_id: int, ctx: str
) -> Completion:
    if not isinstance(raw, dict):
        raise FlowImportError(f"{ctx}: completion must be an object")
    on = _required_date(raw.get("date"), "date", ctx)
    value = raw.get("value")
    if value is not None and not isinstance(value, (int, float)):
        raise FlowImportError(f"{ctx}: value must be number or null")
    duration = raw.get("duration_seconds")
    if duration is not None and not isinstance(duration, int):
        raise FlowImportError(f"{ctx}: duration_seconds must be int or null")
    note = raw.get("note")
    if note is not None and not isinstance(note, str):
        raise FlowImportError(f"{ctx}: note must be string or null")
    status = raw.get("status", COMPLETION_STATUS_DONE)
    if not isinstance(status, str) or status not in VALID_COMPLETION_STATUS:
        raise FlowImportError(
            f"{ctx}: status must be one of {sorted(VALID_COMPLETION_STATUS)}"
        )
    completed_at_raw = raw.get("completed_at")
    completed_at = None
    if completed_at_raw is not None:
        if not isinstance(completed_at_raw, str):
            raise FlowImportError(f"{ctx}: completed_at must be string or null")
        try:
            completed_at = datetime.fromisoformat(completed_at_raw)
        except ValueError as e:
            raise FlowImportError(f"{ctx}: invalid completed_at: {e}")
    return Completion(
        habit_id=habit_id,
        date=on,
        value=float(value) if value is not None else None,
        note=note,
        duration_seconds=duration,
        status=status,
        completed_at=completed_at,
    )


def import_payload(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    conflict: str = "skip",
) -> ImportStats:
    if conflict not in CONFLICT_MODES:
        raise FlowImportError(
            f"unknown conflict mode {conflict!r} (expected: {', '.join(CONFLICT_MODES)})"
        )
    if not isinstance(payload, dict):
        raise FlowImportError("payload must be a JSON object")
    version = payload.get("version")
    if version not in SUPPORTED_VERSIONS:
        raise FlowImportError(
            f"unsupported export version {version!r} (supported: "
            f"{sorted(SUPPORTED_VERSIONS)})"
        )
    raw_habits = payload.get("habits")
    if not isinstance(raw_habits, list):
        raise FlowImportError("payload.habits must be a list")

    stats = ImportStats()

    for raw in raw_habits:
        incoming = _habit_from_payload(raw, "habit")
        existing = db.find_habit_by_name(conn, incoming.name, include_archived=True)
        raw_completions = raw.get("completions") or []
        if not isinstance(raw_completions, list):
            raise FlowImportError(
                f"habit {incoming.name!r}: completions must be a list"
            )

        if existing is None:
            inserted = db.insert_habit(conn, incoming)
            stats.habits_added += 1
            for c_raw in raw_completions:
                completion = _completion_from_payload(
                    c_raw, inserted.id, f"habit {incoming.name!r}"
                )
                db.insert_completion(conn, completion)
                stats.completions_added += 1
            continue

        if conflict == "skip":
            stats.habits_skipped += 1
            stats.completions_skipped += len(raw_completions)
            continue

        if conflict == "overwrite":
            incoming.id = existing.id
            db.update_habit(conn, incoming)
            db.delete_completions_for_habit(conn, existing.id)
            stats.habits_overwritten += 1
            for c_raw in raw_completions:
                completion = _completion_from_payload(
                    c_raw, existing.id, f"habit {incoming.name!r}"
                )
                db.insert_completion(conn, completion)
                stats.completions_added += 1
            continue

        # merge — keep existing habit metadata, add only missing completions
        stats.habits_merged += 1
        for c_raw in raw_completions:
            completion = _completion_from_payload(
                c_raw, existing.id, f"habit {incoming.name!r}"
            )
            if db.get_completion(conn, existing.id, completion.date) is not None:
                stats.completions_skipped += 1
                continue
            db.insert_completion(conn, completion)
            stats.completions_added += 1

    return stats


def read_json(stream: TextIO) -> dict[str, Any]:
    try:
        return json.load(stream)
    except json.JSONDecodeError as e:
        raise FlowImportError(f"invalid JSON: {e}")
