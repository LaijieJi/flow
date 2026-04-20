"""Tests for flow.pomodoro — session merge logic and the `flow pomo` CLI surface."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from click.testing import CliRunner

from flow import db, pomodoro
from flow.cli import main
from flow.models import Completion, Habit
from flow.tui.screens.pomo_setup import PomodoroSettings, validate as validate_setup


# ---- merge_session ------------------------------------------------------------


def test_merge_first_session_sets_duration() -> None:
    h = Habit(id=1, name="Write", frequency="daily")
    out = pomodoro.merge_session(None, h, 1500, date(2026, 4, 20))
    assert out.duration_seconds == 1500
    assert out.value is None
    assert out.note is None


def test_merge_accumulates_across_sessions() -> None:
    h = Habit(id=1, name="Write", frequency="daily")
    today = date(2026, 4, 20)
    first = pomodoro.merge_session(None, h, 1500, today)
    second = pomodoro.merge_session(first, h, 1500, today)
    assert second.duration_seconds == 3000


def test_merge_derives_value_for_minute_unit() -> None:
    h = Habit(id=1, name="Focus", frequency="daily", unit="minutes", target=60.0)
    out = pomodoro.merge_session(None, h, 1500, date(2026, 4, 20))
    assert out.value == pytest.approx(25.0)
    assert out.duration_seconds == 1500


def test_merge_refreshes_value_across_sessions_for_time_habit() -> None:
    h = Habit(id=1, name="Focus", frequency="daily", unit="minutes", target=60.0)
    today = date(2026, 4, 20)
    first = pomodoro.merge_session(None, h, 1500, today)
    second = pomodoro.merge_session(first, h, 1500, today)
    assert second.value == pytest.approx(50.0)  # 50 minutes total


def test_merge_preserves_manual_value_that_diverges_from_derived() -> None:
    """User ran `flow done --value 10`; pomo should not overwrite that."""
    h = Habit(id=1, name="Focus", frequency="daily", unit="minutes", target=60.0)
    today = date(2026, 4, 20)
    manual = Completion(habit_id=h.id, date=today, value=10.0, duration_seconds=None)
    out = pomodoro.merge_session(manual, h, 1500, today)
    assert out.value == 10.0
    assert out.duration_seconds == 1500


def test_merge_does_not_derive_for_non_time_units() -> None:
    h = Habit(id=1, name="Read", frequency="daily", unit="pages", target=20.0)
    out = pomodoro.merge_session(None, h, 1500, date(2026, 4, 20))
    assert out.duration_seconds == 1500
    assert out.value is None


def test_merge_preserves_existing_note() -> None:
    h = Habit(id=1, name="Write", frequency="daily")
    today = date(2026, 4, 20)
    existing = Completion(habit_id=h.id, date=today, note="flowing")
    out = pomodoro.merge_session(existing, h, 1500, today)
    assert out.note == "flowing"
    assert out.duration_seconds == 1500


def test_merge_rejects_negative_session() -> None:
    h = Habit(id=1, name="Write", frequency="daily")
    with pytest.raises(ValueError):
        pomodoro.merge_session(None, h, -1, date(2026, 4, 20))


# ---- format_mmss --------------------------------------------------------------


def test_sound_play_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """sound.play must swallow failures so a missing audio util can't crash
    the TUI. Monkeypatch Popen to blow up on invocation and assert silence."""
    from flow.tui import sound

    def _boom(*a, **kw):
        raise RuntimeError("no audio")

    monkeypatch.setattr(sound.subprocess, "Popen", _boom)
    sound.play("phase")
    sound.play("done")
    sound.play("unknown-kind")  # falls through to default mapping


@pytest.mark.parametrize(
    "seconds, expected",
    [
        (0, "00:00"),
        (5, "00:05"),
        (65, "01:05"),
        (25 * 60, "25:00"),
        (3600, "1:00:00"),
        (3725, "1:02:05"),
        (-5, "00:00"),
    ],
)
def test_format_mmss(seconds: int, expected: str) -> None:
    assert pomodoro.format_mmss(seconds) == expected


# ---- setup-modal validator ----------------------------------------------------


def test_setup_defaults() -> None:
    s = validate_setup("25m", "5m", "1")
    assert s == PomodoroSettings(work_seconds=1500, break_seconds=300, cycles=1)


def test_setup_break_zero_variants() -> None:
    for raw in ["0", "0s", "0m", "", "  "]:
        s = validate_setup("25m", raw, "1")
        assert s.break_seconds == 0


def test_setup_custom_values() -> None:
    s = validate_setup("50m", "10m", "4")
    assert s.work_seconds == 3000
    assert s.break_seconds == 600
    assert s.cycles == 4


@pytest.mark.parametrize("work", ["", "banana", "-5m"])
def test_setup_rejects_bad_work(work: str) -> None:
    with pytest.raises(ValueError, match="work"):
        validate_setup(work, "5m", "1")


def test_setup_rejects_bad_cycles() -> None:
    with pytest.raises(ValueError, match="cycles"):
        validate_setup("25m", "5m", "0")
    with pytest.raises(ValueError, match="cycles"):
        validate_setup("25m", "5m", "abc")


def test_setup_rejects_bad_break() -> None:
    with pytest.raises(ValueError, match="break"):
        validate_setup("25m", "banana", "1")


# ---- CLI ----------------------------------------------------------------------


@pytest.fixture
def cli_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "pomo.db"
    monkeypatch.setenv("FLOW_DB_PATH", str(path))
    return path


def test_pomo_validates_habit(cli_db: Path) -> None:
    runner = CliRunner()
    runner.invoke(main, ["add", "Focus"])  # make sure the store isn't empty
    r = runner.invoke(main, ["pomo", "ghost"])
    assert r.exit_code != 0
    assert "no habit" in r.output.lower()


def test_pomo_rejects_bad_work_duration(cli_db: Path) -> None:
    runner = CliRunner()
    runner.invoke(main, ["add", "Focus"])
    r = runner.invoke(main, ["pomo", "Focus", "--work", "banana"])
    assert r.exit_code != 0
    assert "--work" in r.output


def test_pomo_rejects_zero_cycles(cli_db: Path) -> None:
    runner = CliRunner()
    runner.invoke(main, ["add", "Focus"])
    r = runner.invoke(main, ["pomo", "Focus", "--cycles", "0"])
    assert r.exit_code != 0


def test_pomo_rejects_archived_habit(cli_db: Path) -> None:
    runner = CliRunner()
    runner.invoke(main, ["add", "Focus"])
    runner.invoke(main, ["archive", "Focus"])
    r = runner.invoke(main, ["pomo", "Focus"])
    assert r.exit_code != 0


def test_pomo_free_mode_no_habit_required(
    cli_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bare `flow pomo` should launch the TUI without touching the DB."""
    captured: dict = {}

    class _FakeApp:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def run(self) -> None:
            captured["ran"] = True

    monkeypatch.setattr("flow.tui.app.FlowApp", _FakeApp, raising=True)

    runner = CliRunner()
    r = runner.invoke(main, ["pomo"])
    assert r.exit_code == 0, r.output
    assert captured.get("ran") is True
    assert captured["initial"] == "pomo"
    assert captured["pomo_habit"] is None


def test_free_mode_screen_mounts(tmp_path: Path) -> None:
    """A pomo without a habit must mount, show a countdown, and not touch the DB."""
    import asyncio

    from flow.tui.app import FlowApp
    from flow.tui.screens.pomo import PomodoroScreen

    db_path = tmp_path / "free.db"
    # seed something so we can verify no completions appear after the session.
    with db.session(db_path) as conn:
        db.insert_habit(conn, Habit(name="Untouched", frequency="daily"))

    async def _pilot() -> None:
        app = FlowApp(
            db_path=db_path,
            initial="pomo",
            pomo_habit=None,
            pomo_work_seconds=1,
            pomo_break_seconds=0,
            pomo_cycles=1,
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.screen, PomodoroScreen)
            assert app.screen.habit is None
            await pilot.press("escape")

    asyncio.run(_pilot())

    with db.session(db_path) as conn:
        habits = db.list_habits(conn)
        assert len(habits) == 1
        assert db.completions_for_habit(conn, habits[0].id) == []


def test_pomo_launches_flowapp(
    cli_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Don't actually run a TUI in the test — intercept FlowApp construction."""
    captured: dict = {}

    class _FakeApp:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def run(self) -> None:
            captured["ran"] = True

    import flow.cli as cli_mod

    monkeypatch.setattr("flow.tui.app.FlowApp", _FakeApp, raising=True)
    # ensure the import inside the command picks up the patched attribute
    monkeypatch.setitem(
        __import__("sys").modules, "flow.tui.app",
        __import__("flow.tui.app", fromlist=["FlowApp"]),
    )

    runner = CliRunner()
    runner.invoke(main, ["add", "Focus"])
    r = runner.invoke(
        main,
        ["pomo", "Focus", "--work", "1m", "--break", "30s", "--cycles", "2"],
    )
    assert r.exit_code == 0, r.output
    assert captured.get("ran") is True
    assert captured["initial"] == "pomo"
    assert captured["pomo_work_seconds"] == 60
    assert captured["pomo_break_seconds"] == 30
    assert captured["pomo_cycles"] == 2
    assert captured["pomo_habit"].name == "Focus"
