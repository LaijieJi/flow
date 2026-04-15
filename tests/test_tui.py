"""Pilot tests for the Textual TUI layer."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from flow import db
from flow.models import Habit, Completion
from flow.tui.app import FlowApp
from flow.tui.screens.check import CheckScreen
from flow.tui.screens.prompt import PromptScreen
from flow.tui.widgets.habit_row import HabitRow


@pytest.fixture
def seeded_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "tui.db"
    monkeypatch.setenv("FLOW_DB_PATH", str(path))
    with db.session(path) as conn:
        db.insert_habit(conn, Habit(name="Exercise", frequency="daily"))
        db.insert_habit(conn, Habit(name="Read", frequency="daily", unit="pages", target=20))
        db.insert_habit(conn, Habit(name="Language", frequency="mon,wed,fri"))
    return path


@pytest.mark.asyncio
async def test_app_mounts_with_habits(seeded_db: Path) -> None:
    app = FlowApp(db_path=seeded_db, today=date(2026, 4, 13))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, CheckScreen)
        rows = list(screen.query(HabitRow))
        assert len(rows) == 3
        assert {r.habit.name for r in rows} == {"Exercise", "Read", "Language"}


@pytest.mark.asyncio
async def test_toggle_persists_completion(seeded_db: Path) -> None:
    today = date(2026, 4, 13)  # Monday
    app = FlowApp(db_path=seeded_db, today=today)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()

    with db.session(seeded_db) as conn:
        comps = db.completions_on(conn, today)
    assert len(comps) == 1  # one habit toggled
    habit_id = comps[0].habit_id
    with db.session(seeded_db) as conn:
        h = db.get_habit(conn, habit_id)
    # Default cursor lands on first scheduled row — Exercise
    assert h.name == "Exercise"


@pytest.mark.asyncio
async def test_toggle_twice_clears_completion(seeded_db: Path) -> None:
    today = date(2026, 4, 13)
    app = FlowApp(db_path=seeded_db, today=today)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("space")
        await pilot.press("space")
        await pilot.pause()

    with db.session(seeded_db) as conn:
        comps = db.completions_on(conn, today)
    assert comps == []


@pytest.mark.asyncio
async def test_j_k_navigation_moves_cursor(seeded_db: Path) -> None:
    today = date(2026, 4, 13)
    app = FlowApp(db_path=seeded_db, today=today)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        lv = screen.query_one("#rows")
        start = lv.index
        await pilot.press("j")
        await pilot.pause()
        assert lv.index == start + 1
        await pilot.press("k")
        await pilot.pause()
        assert lv.index == start


@pytest.mark.asyncio
async def test_unscheduled_habit_not_toggled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "tui.db"
    monkeypatch.setenv("FLOW_DB_PATH", str(path))
    with db.session(path) as conn:
        db.insert_habit(conn, Habit(name="WeekendOnly", frequency="sat,sun"))

    # Render on a Monday — habit is unscheduled
    monday = date(2026, 4, 13)
    app = FlowApp(db_path=path, today=monday)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        rows = list(screen.query(HabitRow))
        assert len(rows) == 1
        assert not rows[0].scheduled
        # Toggling an unscheduled row should no-op — cursor won't land on it,
        # but even if called directly, it silently returns.
        screen.action_toggle()
        await pilot.pause()

    with db.session(path) as conn:
        assert db.completions_on(conn, monday) == []


@pytest.mark.asyncio
async def test_existing_completion_shown_on_mount(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "tui.db"
    monkeypatch.setenv("FLOW_DB_PATH", str(path))
    today = date(2026, 4, 13)
    with db.session(path) as conn:
        h = db.insert_habit(conn, Habit(name="Already", frequency="daily"))
        db.upsert_completion(conn, Completion(habit_id=h.id, date=today))

    app = FlowApp(db_path=path, today=today)
    async with app.run_test() as pilot:
        await pilot.pause()
        rows = list(app.screen.query(HabitRow))
        assert rows[0].completion is not None


@pytest.mark.asyncio
async def test_quit_exits(seeded_db: Path) -> None:
    app = FlowApp(db_path=seeded_db, today=date(2026, 4, 13))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
    assert app.return_value is None  # clean exit


@pytest.mark.asyncio
async def test_set_value_opens_prompt(seeded_db: Path) -> None:
    """Regression: pressing `v` must open PromptScreen via a worker.
    Previously crashed with NoActiveWorker because push_screen_wait wasn't
    run inside a worker context."""
    today = date(2026, 4, 13)
    app = FlowApp(db_path=seeded_db, today=today)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("v")
        await pilot.pause()
        assert isinstance(app.screen, PromptScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, CheckScreen)


@pytest.mark.asyncio
async def test_set_value_submits_and_persists(seeded_db: Path) -> None:
    today = date(2026, 4, 13)
    app = FlowApp(db_path=seeded_db, today=today)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("v")
        await pilot.pause()
        # PromptScreen focused on its Input. Type value + enter.
        for ch in "15":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause()

    with db.session(seeded_db) as conn:
        comps = db.completions_on(conn, today)
    assert len(comps) == 1
    assert comps[0].value == 15.0


@pytest.mark.asyncio
async def test_add_note_opens_prompt(seeded_db: Path) -> None:
    today = date(2026, 4, 13)
    app = FlowApp(db_path=seeded_db, today=today)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        assert isinstance(app.screen, PromptScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, CheckScreen)


def test_habit_row_render_unscheduled() -> None:
    h = Habit(name="X", frequency="mon,wed,fri", id=1)
    row = HabitRow(h, today=date(2026, 4, 14))  # Tuesday
    assert not row.scheduled
    text = row._render_text()
    assert "not scheduled" in text


def test_habit_row_render_done_with_value() -> None:
    h = Habit(name="Read", frequency="daily", unit="pages", target=20, id=1)
    c = Completion(habit_id=1, date=date(2026, 4, 13), value=15, note="focused")
    row = HabitRow(h, today=date(2026, 4, 13), completion=c)
    text = row._render_text()
    assert "15 pages" in text
    assert "/ 20" in text
    assert "focused" in text


def test_habit_row_render_truncates_long_note() -> None:
    h = Habit(name="X", frequency="daily", id=1)
    note = "a" * 100
    c = Completion(habit_id=1, date=date(2026, 4, 13), note=note)
    row = HabitRow(h, today=date(2026, 4, 13), completion=c)
    text = row._render_text()
    assert "..." in text
