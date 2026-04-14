"""Tests for CompletionGrid widget + Stats/Detail screens."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from flow import db
from flow.models import Completion, Habit
from flow.tui.app import FlowApp
from flow.tui.screens.detail import DetailScreen
from flow.tui.screens.stats import StatsScreen
from flow.tui.widgets.completion_grid import (
    GLYPH_FULL,
    GLYPH_MISS,
    GLYPH_NONE,
    GLYPH_PARTIAL,
    GLYPH_UNSCHEDULED,
    render_block,
)


# ---- CompletionGrid block rendering ------------------------------------------


class TestRenderBlock:
    def _habit(self, **kw) -> Habit:
        kw.setdefault("name", "X")
        kw.setdefault("frequency", "daily")
        kw.setdefault("created_at", date(2026, 1, 1))
        return Habit(**kw)

    def test_before_creation_is_blank(self) -> None:
        h = self._habit(created_at=date(2026, 4, 10))
        out = render_block(h, date(2026, 4, 1), {})
        assert out == GLYPH_NONE

    def test_unscheduled_day_shows_dot(self) -> None:
        h = self._habit(frequency="mon,wed,fri")
        tuesday = date(2026, 4, 14)
        out = render_block(h, tuesday, {})
        assert GLYPH_UNSCHEDULED in out

    def test_full_strength_shows_full_block(self) -> None:
        h = self._habit()
        out = render_block(h, date(2026, 4, 10), {date(2026, 4, 10): 1.0})
        assert out == GLYPH_FULL

    def test_partial_strength_shows_partial(self) -> None:
        h = self._habit()
        out = render_block(h, date(2026, 4, 10), {date(2026, 4, 10): 0.5})
        assert GLYPH_PARTIAL in out

    def test_missed_scheduled_day_shows_miss(self) -> None:
        h = self._habit()
        out = render_block(h, date(2026, 4, 10), {})
        assert GLYPH_MISS in out


# ---- Stats screen pilot tests ------------------------------------------------


@pytest.fixture
def seeded_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "stats.db"
    monkeypatch.setenv("FLOW_DB_PATH", str(path))
    today = date(2026, 4, 13)
    start = today - timedelta(days=29)
    with db.session(path) as conn:
        ex = db.insert_habit(
            conn, Habit(name="Exercise", frequency="daily", created_at=start)
        )
        rd = db.insert_habit(
            conn,
            Habit(
                name="Read",
                frequency="daily",
                unit="pages",
                target=20,
                created_at=start,
            ),
        )
        # Exercise: strong record, last 28 days
        for i in range(2, 30):
            db.upsert_completion(
                conn, Completion(habit_id=ex.id, date=start + timedelta(days=i))
            )
        # Read: half-target throughout, with notes on a couple of days
        for i in range(30):
            note = "focused" if i == 28 else None
            db.upsert_completion(
                conn,
                Completion(
                    habit_id=rd.id,
                    date=start + timedelta(days=i),
                    value=10,
                    note=note,
                ),
            )
    return path


async def test_stats_screen_mounts_and_populates(seeded_db: Path) -> None:
    app = FlowApp(db_path=seeded_db, initial="stats", today=date(2026, 4, 13))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, StatsScreen)
        assert len(screen.habits) == 2
        assert {h.name for h in screen.habits} == {"Exercise", "Read"}


async def test_stats_cursor_navigation(seeded_db: Path) -> None:
    app = FlowApp(db_path=seeded_db, initial="stats", today=date(2026, 4, 13))
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("DataTable")
        start = table.cursor_row
        await pilot.press("j")
        await pilot.pause()
        assert table.cursor_row == (start + 1) % 2
        await pilot.press("k")
        await pilot.pause()
        assert table.cursor_row == start


async def test_stats_drill_down_pushes_detail(seeded_db: Path) -> None:
    app = FlowApp(db_path=seeded_db, initial="stats", today=date(2026, 4, 13))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, DetailScreen)


async def test_stats_focus_habit_selects_row(seeded_db: Path) -> None:
    app = FlowApp(
        db_path=seeded_db,
        initial="stats",
        today=date(2026, 4, 13),
        focus_habit="Read",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("DataTable")
        habit = app.screen.habits[table.cursor_row]
        assert habit.name == "Read"


async def test_stats_empty_db_mounts_without_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "empty.db"
    monkeypatch.setenv("FLOW_DB_PATH", str(path))
    with db.session(path):
        pass  # just create the schema

    app = FlowApp(db_path=path, initial="stats", today=date(2026, 4, 13))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, StatsScreen)
        assert app.screen.habits == []


async def test_stats_quit(seeded_db: Path) -> None:
    app = FlowApp(db_path=seeded_db, initial="stats", today=date(2026, 4, 13))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()


# ---- Detail screen pilot tests -----------------------------------------------


async def test_detail_screen_shows_summary_and_notes(seeded_db: Path) -> None:
    today = date(2026, 4, 13)
    with db.session(seeded_db) as conn:
        read = [h for h in db.list_habits(conn) if h.name == "Read"][0]

    app = FlowApp(db_path=seeded_db, initial="detail", today=today, detail_habit=read)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, DetailScreen)
        assert "score" in app.screen.summary_text
        assert "rate" in app.screen.summary_text
        assert "focused" in app.screen.notes_text


async def test_detail_screen_no_notes_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "nn.db"
    monkeypatch.setenv("FLOW_DB_PATH", str(path))
    today = date(2026, 4, 13)
    with db.session(path) as conn:
        h = db.insert_habit(conn, Habit(name="Plain", frequency="daily"))
        db.upsert_completion(conn, Completion(habit_id=h.id, date=today))

    with db.session(path) as conn:
        plain = db.list_habits(conn)[0]

    app = FlowApp(db_path=path, initial="detail", today=today, detail_habit=plain)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "no notes" in app.screen.notes_text


async def test_detail_screen_escape_returns_to_stats(seeded_db: Path) -> None:
    app = FlowApp(db_path=seeded_db, initial="stats", today=date(2026, 4, 13))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, DetailScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, StatsScreen)


# ---- CLI wiring --------------------------------------------------------------


def test_stats_cli_rejects_missing_habit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from click.testing import CliRunner

    from flow.cli import main

    path = tmp_path / "c.db"
    monkeypatch.setenv("FLOW_DB_PATH", str(path))
    with db.session(path) as conn:
        db.insert_habit(conn, Habit(name="Exists", frequency="daily"))

    runner = CliRunner()
    r = runner.invoke(main, ["stats", "ghost"])
    assert r.exit_code != 0
    assert "no habit matches" in r.output
