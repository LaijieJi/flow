"""Tests for TUI add-habit flow: AddHabitRow + AddHabitScreen."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from flow import db
from flow.models import Habit
from flow.tui.app import FlowApp
from flow.tui.screens.add_habit import AddHabitScreen
from flow.tui.screens.check import AddHabitRow, CheckScreen
from flow.tui.widgets.habit_row import HabitRow


@pytest.fixture
def empty_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "add.db"
    monkeypatch.setenv("FLOW_DB_PATH", str(path))
    with db.session(path):
        pass
    return path


@pytest.fixture
def seeded_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "add.db"
    monkeypatch.setenv("FLOW_DB_PATH", str(path))
    with db.session(path) as conn:
        db.insert_habit(conn, Habit(name="Exercise", frequency="daily"))
    return path


TODAY = date(2026, 4, 15)


# ---- "+" row presence ---------------------------------------------------------


async def test_add_row_at_top_of_list(seeded_db: Path) -> None:
    app = FlowApp(db_path=seeded_db, today=TODAY)
    async with app.run_test() as pilot:
        await pilot.pause()
        lv = app.screen.query_one("#rows")
        first = lv.children[0]
        assert isinstance(first, AddHabitRow)


async def test_add_row_present_on_empty_db(empty_db: Path) -> None:
    app = FlowApp(db_path=empty_db, today=TODAY)
    async with app.run_test() as pilot:
        await pilot.pause()
        lv = app.screen.query_one("#rows")
        assert len(lv.children) == 1
        assert isinstance(lv.children[0], AddHabitRow)


# ---- a key opens form ---------------------------------------------------------


async def test_a_key_opens_add_form(seeded_db: Path) -> None:
    app = FlowApp(db_path=seeded_db, today=TODAY)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        assert isinstance(app.screen, AddHabitScreen)


async def test_add_form_cancel_returns_to_check(seeded_db: Path) -> None:
    app = FlowApp(db_path=seeded_db, today=TODAY)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, CheckScreen)
        # No new habits
        with db.session(seeded_db) as conn:
            assert len(db.list_habits(conn)) == 1


# ---- form submit creates habit ------------------------------------------------


async def test_submit_minimal_habit(empty_db: Path) -> None:
    app = FlowApp(db_path=empty_db, today=TODAY)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        # Type name and enter (daily is default freq)
        for ch in "Meditate":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause()

    with db.session(empty_db) as conn:
        habits = db.list_habits(conn)
    assert len(habits) == 1
    assert habits[0].name == "Meditate"
    assert habits[0].frequency == "daily"


async def test_submit_and_list_reloads(seeded_db: Path) -> None:
    app = FlowApp(db_path=seeded_db, today=TODAY)
    async with app.run_test() as pilot:
        await pilot.pause()
        initial_count = len(list(app.screen.query(HabitRow)))
        await pilot.press("a")
        await pilot.pause()
        for ch in "Read":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause()
        # Back on check screen, list should have one more HabitRow
        assert isinstance(app.screen, CheckScreen)
        new_count = len(list(app.screen.query(HabitRow)))
        assert new_count == initial_count + 1


async def test_cursor_stays_on_add_row_after_submit(seeded_db: Path) -> None:
    app = FlowApp(db_path=seeded_db, today=TODAY)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        for ch in "New":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause()
        lv = app.screen.query_one("#rows")
        assert lv.index == 0
        assert isinstance(lv.children[0], AddHabitRow)


# ---- enter on "+" row opens form ----------------------------------------------


async def test_enter_on_add_row_opens_form(empty_db: Path) -> None:
    app = FlowApp(db_path=empty_db, today=TODAY)
    async with app.run_test() as pilot:
        await pilot.pause()
        lv = app.screen.query_one("#rows")
        lv.index = 0  # ensure on "+" row
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, AddHabitScreen)


# ---- space/v/n on "+" row are no-ops ------------------------------------------


async def test_space_on_add_row_is_noop(seeded_db: Path) -> None:
    app = FlowApp(db_path=seeded_db, today=TODAY)
    async with app.run_test() as pilot:
        await pilot.pause()
        lv = app.screen.query_one("#rows")
        lv.index = 0
        await pilot.press("space")
        await pilot.pause()
    with db.session(seeded_db) as conn:
        assert db.completions_on(conn, TODAY) == []


# ---- validation ---------------------------------------------------------------


async def test_empty_name_rejected(empty_db: Path) -> None:
    app = FlowApp(db_path=empty_db, today=TODAY)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        # Submit without typing name
        await pilot.press("enter")
        await pilot.pause()
        # Still on form
        assert isinstance(app.screen, AddHabitScreen)
    with db.session(empty_db) as conn:
        assert db.list_habits(conn) == []


async def test_duplicate_name_rejected(seeded_db: Path) -> None:
    app = FlowApp(db_path=seeded_db, today=TODAY)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        for ch in "Exercise":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause()
        # Still on form — duplicate rejected
        assert isinstance(app.screen, AddHabitScreen)
    with db.session(seeded_db) as conn:
        assert len(db.list_habits(conn)) == 1  # no new habit
