"""Tests for flow.db — schema, CRUD, constraints, migrations."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from flow import db
from flow.models import Completion, Habit


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
def conn(tmp_db: Path):
    c = db.connect(tmp_db)
    yield c
    c.close()


def test_connect_creates_parent_dir(tmp_path: Path) -> None:
    nested = tmp_path / "does" / "not" / "exist" / "flow.db"
    c = db.connect(nested)
    try:
        assert nested.parent.is_dir()
        assert nested.exists()
    finally:
        c.close()


def test_schema_has_habits_and_completions(conn: sqlite3.Connection) -> None:
    tables = {
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "habits" in tables
    assert "completions" in tables
    assert "schema_version" in tables


def test_migrations_apply_once(tmp_db: Path) -> None:
    c1 = db.connect(tmp_db)
    assert db.current_schema_version(c1) == len(db.MIGRATIONS)
    c1.close()

    c2 = db.connect(tmp_db)
    versions = [
        r["version"]
        for r in c2.execute("SELECT version FROM schema_version ORDER BY version").fetchall()
    ]
    assert versions == list(range(1, len(db.MIGRATIONS) + 1))
    c2.close()


def test_insert_and_get_habit(conn: sqlite3.Connection) -> None:
    h = Habit(name="Exercise", frequency="daily", unit="minutes", target=30.0)
    stored = db.insert_habit(conn, h)
    assert stored.id is not None

    loaded = db.get_habit(conn, stored.id)
    assert loaded is not None
    assert loaded.name == "Exercise"
    assert loaded.frequency == "daily"
    assert loaded.target == 30.0
    assert loaded.archived_at is None


def test_invalid_frequency_rejected(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        db.insert_habit(conn, Habit(name="Bad", frequency="sometimes"))


def test_list_habits_excludes_archived_by_default(conn: sqlite3.Connection) -> None:
    a = db.insert_habit(conn, Habit(name="A", frequency="daily"))
    b = db.insert_habit(conn, Habit(name="B", frequency="daily"))
    assert db.archive_habit(conn, a.id) is True

    active = db.list_habits(conn)
    assert [h.name for h in active] == ["B"]

    everything = db.list_habits(conn, include_archived=True)
    assert {h.name for h in everything} == {"A", "B"}
    archived = next(h for h in everything if h.name == "A")
    assert archived.archived_at is not None


def test_archive_is_idempotent(conn: sqlite3.Connection) -> None:
    h = db.insert_habit(conn, Habit(name="X", frequency="daily"))
    assert db.archive_habit(conn, h.id) is True
    assert db.archive_habit(conn, h.id) is False


def test_upsert_completion_enforces_uniqueness(conn: sqlite3.Connection) -> None:
    h = db.insert_habit(conn, Habit(name="Read", frequency="daily"))
    today = date.today()

    first = db.upsert_completion(conn, Completion(habit_id=h.id, date=today, value=10))
    second = db.upsert_completion(conn, Completion(habit_id=h.id, date=today, value=25))

    rows = db.completions_for_habit(conn, h.id)
    assert len(rows) == 1
    assert rows[0].value == 25
    assert first.id is not None
    assert second.id == first.id


def test_direct_duplicate_insert_raises(conn: sqlite3.Connection) -> None:
    h = db.insert_habit(conn, Habit(name="Dup", frequency="daily"))
    today = date.today()
    db.bulk_insert_completions(conn, [Completion(habit_id=h.id, date=today)])
    with pytest.raises(sqlite3.IntegrityError):
        db.bulk_insert_completions(conn, [Completion(habit_id=h.id, date=today)])


def test_completions_for_habit_date_range(conn: sqlite3.Connection) -> None:
    h = db.insert_habit(conn, Habit(name="Meditate", frequency="daily"))
    today = date.today()
    dates = [today - timedelta(days=i) for i in range(5)]
    db.bulk_insert_completions(
        conn, [Completion(habit_id=h.id, date=d, value=float(i)) for i, d in enumerate(dates)]
    )

    window = db.completions_for_habit(
        conn, h.id, since=today - timedelta(days=2), until=today
    )
    assert [c.date for c in window] == sorted([today - timedelta(days=i) for i in range(3)])


def test_delete_completion(conn: sqlite3.Connection) -> None:
    h = db.insert_habit(conn, Habit(name="Drop", frequency="daily"))
    today = date.today()
    db.upsert_completion(conn, Completion(habit_id=h.id, date=today))
    assert db.delete_completion(conn, h.id, today) is True
    assert db.delete_completion(conn, h.id, today) is False
    assert db.completions_for_habit(conn, h.id) == []


def test_cascade_delete_on_habit(conn: sqlite3.Connection) -> None:
    h = db.insert_habit(conn, Habit(name="Gone", frequency="daily"))
    db.upsert_completion(conn, Completion(habit_id=h.id, date=date.today()))
    conn.execute("DELETE FROM habits WHERE id = ?", (h.id,))
    remaining = conn.execute(
        "SELECT COUNT(*) AS n FROM completions WHERE habit_id = ?", (h.id,)
    ).fetchone()["n"]
    assert remaining == 0


def test_note_length_enforced_in_model() -> None:
    with pytest.raises(ValueError):
        Completion(habit_id=1, date=date.today(), note="x" * 281)


def test_habit_is_scheduled_on_weekdays() -> None:
    h = Habit(name="W", frequency="weekdays")
    monday = date(2026, 4, 13)  # Monday
    saturday = date(2026, 4, 11)  # Saturday
    assert h.is_scheduled_on(monday) is True
    assert h.is_scheduled_on(saturday) is False


def test_habit_is_scheduled_on_custom_days() -> None:
    h = Habit(name="W", frequency="mon,wed,fri")
    monday = date(2026, 4, 13)
    tuesday = date(2026, 4, 14)
    friday = date(2026, 4, 17)
    assert h.is_scheduled_on(monday) is True
    assert h.is_scheduled_on(tuesday) is False
    assert h.is_scheduled_on(friday) is True


def test_session_commits_on_success(tmp_db: Path) -> None:
    with db.session(tmp_db) as conn:
        db.insert_habit(conn, Habit(name="S", frequency="daily"))

    with db.session(tmp_db) as conn:
        assert len(db.list_habits(conn)) == 1


def test_insert_habit_with_seasonal_window(conn: sqlite3.Connection) -> None:
    h = db.insert_habit(
        conn,
        Habit(
            name="Summer",
            frequency="daily",
            start_date=date(2026, 6, 1),
            end_date=date(2026, 8, 31),
        ),
    )
    loaded = db.get_habit(conn, h.id)
    assert loaded.start_date == date(2026, 6, 1)
    assert loaded.end_date == date(2026, 8, 31)


def test_insert_habit_rejects_end_before_start(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        db.insert_habit(
            conn,
            Habit(
                name="Bad",
                frequency="daily",
                start_date=date(2026, 6, 1),
                end_date=date(2026, 5, 1),
            ),
        )


def test_update_habit_sets_and_clears_dates(conn: sqlite3.Connection) -> None:
    h = db.insert_habit(conn, Habit(name="S", frequency="daily"))
    h.start_date = date(2026, 3, 1)
    h.end_date = date(2026, 4, 1)
    db.update_habit(conn, h)
    loaded = db.get_habit(conn, h.id)
    assert loaded.start_date == date(2026, 3, 1)
    assert loaded.end_date == date(2026, 4, 1)

    loaded.start_date = None
    loaded.end_date = None
    db.update_habit(conn, loaded)
    again = db.get_habit(conn, h.id)
    assert again.start_date is None
    assert again.end_date is None


def test_update_habit_rejects_end_before_start(conn: sqlite3.Connection) -> None:
    h = db.insert_habit(conn, Habit(name="S", frequency="daily"))
    h.start_date = date(2026, 6, 1)
    h.end_date = date(2026, 5, 1)
    with pytest.raises(ValueError):
        db.update_habit(conn, h)


def test_monthly_and_every_frequencies_persist(conn: sqlite3.Connection) -> None:
    a = db.insert_habit(conn, Habit(name="A", frequency="monthly:15"))
    b = db.insert_habit(conn, Habit(name="B", frequency="every:5"))
    loaded_a = db.get_habit(conn, a.id)
    loaded_b = db.get_habit(conn, b.id)
    assert loaded_a.frequency == "monthly:15"
    assert loaded_b.frequency == "every:5"


def test_session_rolls_back_on_error(tmp_db: Path) -> None:
    with pytest.raises(RuntimeError):
        with db.session(tmp_db) as conn:
            db.insert_habit(conn, Habit(name="R", frequency="daily"))
            raise RuntimeError("boom")

    with db.session(tmp_db) as conn:
        assert db.list_habits(conn) == []
