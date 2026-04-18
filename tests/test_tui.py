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


# ---- help modal --------------------------------------------------------------


@pytest.mark.asyncio
async def test_help_opens_and_closes(seeded_db: Path) -> None:
    from flow.tui.screens.help import HelpScreen

    app = FlowApp(db_path=seeded_db, today=date(2026, 4, 13))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("h")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, CheckScreen)


# ---- undo (u key) ------------------------------------------------------------


@pytest.mark.asyncio
async def test_undo_removes_last_completion(seeded_db: Path) -> None:
    today = date(2026, 4, 13)
    with db.session(seeded_db) as conn:
        ex = db.list_habits(conn)[0]
        db.upsert_completion(conn, Completion(habit_id=ex.id, date=today))

    app = FlowApp(db_path=seeded_db, today=today)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("u")
        await pilot.pause()

    with db.session(seeded_db) as conn:
        assert db.completions_on(conn, today) == []


@pytest.mark.asyncio
async def test_undo_with_no_completions_no_crash(seeded_db: Path) -> None:
    today = date(2026, 4, 13)
    app = FlowApp(db_path=seeded_db, today=today)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("u")
        await pilot.pause()
        assert isinstance(app.screen, CheckScreen)


# ---- random (r key) ----------------------------------------------------------


@pytest.mark.asyncio
async def test_random_moves_cursor_to_scheduled_undone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After r, cursor should land on a scheduled + undone habit row."""
    from flow.tui.widgets.habit_row import HabitRow

    path = tmp_path / "r.db"
    monkeypatch.setenv("FLOW_DB_PATH", str(path))
    with db.session(path) as conn:
        db.insert_habit(conn, Habit(name="A", frequency="daily"))
        db.insert_habit(conn, Habit(name="B", frequency="daily"))
        db.insert_habit(conn, Habit(name="C", frequency="daily"))

    today = date(2026, 4, 13)
    app = FlowApp(db_path=path, today=today)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        lv = app.screen.query_one("#rows")
        landed = lv.children[lv.index]
        assert isinstance(landed, HabitRow)
        assert landed.scheduled
        assert landed.completion is None


# ---- theme toggle (t key) ----------------------------------------------------


@pytest.mark.asyncio
async def test_theme_toggle_persists_to_config(
    seeded_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from flow import config as _config

    cfg_path = tmp_path / "config.json"
    monkeypatch.setenv("FLOW_CONFIG_PATH", str(cfg_path))
    # Start explicitly dark so a single toggle lands on light
    _config.save({"theme": "dark"})

    app = FlowApp(db_path=seeded_db, today=date(2026, 4, 13))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.dark is True
        await pilot.press("t")
        await pilot.pause()
        assert app.dark is False

    assert _config.get("theme") == "light"


# ---- navbar ------------------------------------------------------------------


def test_navbar_highlights_current_tab() -> None:
    from flow.tui.widgets.navbar import render_navbar

    out = render_navbar("stats")
    assert "reverse" in out  # current tab uses reverse styling
    # All three tabs should appear
    assert "check" in out and "stats" in out and "log" in out
    # Activation keys visible for non-current
    assert "[c]" in out and "[l]" in out


@pytest.mark.asyncio
async def test_navbar_rendered_on_check(seeded_db: Path) -> None:
    from flow.tui.widgets.navbar import NavBar

    app = FlowApp(db_path=seeded_db, today=date(2026, 4, 13))
    async with app.run_test() as pilot:
        await pilot.pause()
        bars = list(app.screen.query(NavBar))
        assert len(bars) == 1


@pytest.mark.asyncio
async def test_navbar_current_tab_updates_per_screen(seeded_db: Path) -> None:
    from flow.tui.widgets.navbar import NavBar

    app = FlowApp(db_path=seeded_db, initial="stats", today=date(2026, 4, 13))
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.screen.query_one(NavBar)
        assert bar.current == "stats"
        assert "reverse" in bar.rendered_text
        assert " stats " in bar.rendered_text


# ---- cross-screen navigation -------------------------------------------------


@pytest.mark.asyncio
async def test_check_to_stats_via_s_key(seeded_db: Path) -> None:
    from flow.tui.screens.stats import StatsScreen

    app = FlowApp(db_path=seeded_db, today=date(2026, 4, 13))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, StatsScreen)


@pytest.mark.asyncio
async def test_check_to_log_via_l_key(seeded_db: Path) -> None:
    from flow.tui.screens.log import LogScreen

    app = FlowApp(db_path=seeded_db, today=date(2026, 4, 13))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("l")
        await pilot.pause()
        assert isinstance(app.screen, LogScreen)


@pytest.mark.asyncio
async def test_stats_to_check_via_c_key(seeded_db: Path) -> None:
    app = FlowApp(db_path=seeded_db, initial="stats", today=date(2026, 4, 13))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, CheckScreen)


@pytest.mark.asyncio
async def test_log_to_stats_via_s_key(seeded_db: Path) -> None:
    from flow.tui.screens.log import LogScreen
    from flow.tui.screens.stats import StatsScreen

    app = FlowApp(db_path=seeded_db, initial="stats", today=date(2026, 4, 13))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("l")
        await pilot.pause()
        assert isinstance(app.screen, LogScreen)
        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, StatsScreen)


@pytest.mark.asyncio
async def test_repeat_navigation_keeps_stack_shallow(seeded_db: Path) -> None:
    """Bouncing c→s→c→s must not grow the screen stack unboundedly."""
    app = FlowApp(db_path=seeded_db, today=date(2026, 4, 13))
    async with app.run_test() as pilot:
        await pilot.pause()
        for key in ("s", "c", "s", "c", "s", "c"):
            await pilot.press(key)
            await pilot.pause()
        assert len(app.screen_stack) <= 3  # base + at most one extra


@pytest.mark.asyncio
async def test_navigate_to_same_screen_is_noop(seeded_db: Path) -> None:
    """Pressing c while already on check shouldn't push a duplicate."""
    app = FlowApp(db_path=seeded_db, today=date(2026, 4, 13))
    async with app.run_test() as pilot:
        await pilot.pause()
        before = len(app.screen_stack)
        assert isinstance(app.screen, CheckScreen)
        # Check doesn't bind 'c' but navigating via the API is a no-op too:
        app.navigate_to("check")
        await pilot.pause()
        assert len(app.screen_stack) == before


@pytest.mark.asyncio
async def test_detail_to_check_via_c_key(seeded_db: Path) -> None:
    """From a drill-in screen we can still jump to a top-level screen."""
    today = date(2026, 4, 13)
    with db.session(seeded_db) as conn:
        read = [h for h in db.list_habits(conn) if h.name == "Read"][0]

    app = FlowApp(db_path=seeded_db, initial="detail", today=today, detail_habit=read)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, CheckScreen)


@pytest.mark.asyncio
async def test_theme_light_is_applied_on_mount(
    seeded_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from flow import config as _config

    cfg_path = tmp_path / "config.json"
    monkeypatch.setenv("FLOW_CONFIG_PATH", str(cfg_path))
    _config.save({"theme": "light"})

    app = FlowApp(db_path=seeded_db, today=date(2026, 4, 13))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.dark is False
