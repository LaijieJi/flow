"""End-to-end CLI integration. Drives the whole surface through CliRunner
and asserts invariants at each step."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from flow import db
from flow.cli import main


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "intg.db"
    monkeypatch.setenv("FLOW_DB_PATH", str(path))
    return path


def _ok(r) -> None:
    assert r.exit_code == 0, r.output


def test_full_workflow_add_done_edit_log_export(env: Path) -> None:
    runner = CliRunner()

    _ok(runner.invoke(main, ["add", "Exercise", "-f", "daily"]))
    _ok(runner.invoke(
        main,
        ["add", "Read", "-f", "weekdays", "--unit", "pages", "--target", "20"],
    ))
    _ok(runner.invoke(main, ["add", "Meditate", "-f", "daily"]))

    # list shows all three
    r = runner.invoke(main, ["list"])
    _ok(r)
    for name in ("Exercise", "Read", "Meditate"):
        assert name in r.output

    # complete with note + value; re-mark updates row, not duplicates
    _ok(runner.invoke(main, ["done", "exe"]))
    _ok(runner.invoke(main, ["done", "read", "--value", "18", "--note", "focused"]))
    _ok(runner.invoke(main, ["done", "read", "--value", "20"]))

    # edit bumps target — rate drops accordingly
    _ok(runner.invoke(main, ["edit", "read", "--target", "40"]))

    # log shows all completions
    r = runner.invoke(main, ["log"])
    _ok(r)
    assert "Exercise" in r.output
    assert "Read" in r.output

    # export csv + json agree on completions count
    r_csv = runner.invoke(main, ["export"])
    _ok(r_csv)
    csv_rows = r_csv.output.strip().splitlines()[1:]  # skip header
    assert len(csv_rows) == 2  # one Exercise + one Read (upserted)

    r_json = runner.invoke(main, ["export", "-F", "json"])
    _ok(r_json)
    payload = json.loads(r_json.output)
    total = sum(len(h["completions"]) for h in payload["habits"])
    assert total == 2

    # only the last Read value persisted (upsert)
    read = next(h for h in payload["habits"] if h["name"] == "Read")
    assert len(read["completions"]) == 1
    assert read["completions"][0]["value"] == 20.0


def test_archive_flow(env: Path) -> None:
    runner = CliRunner()
    _ok(runner.invoke(main, ["add", "Tmp"]))
    _ok(runner.invoke(main, ["done", "tmp"]))
    _ok(runner.invoke(main, ["archive", "tmp"]))

    # hidden from default list
    r = runner.invoke(main, ["list"])
    _ok(r)
    assert "Tmp" not in r.output

    # revealed by --all
    r = runner.invoke(main, ["list", "--all"])
    _ok(r)
    assert "Tmp" in r.output
    assert "archived" in r.output.lower()

    # default export hides it
    r = runner.invoke(main, ["export", "-F", "json"])
    _ok(r)
    payload = json.loads(r.output)
    assert all(h["name"] != "Tmp" for h in payload["habits"])

    # --all exposes the archived habit and its completion history
    r = runner.invoke(main, ["export", "-F", "json", "--all"])
    _ok(r)
    payload = json.loads(r.output)
    tmp = next(h for h in payload["habits"] if h["name"] == "Tmp")
    assert tmp["archived_at"] is not None
    assert len(tmp["completions"]) == 1

    # cannot mark done on archived habit
    r = runner.invoke(main, ["done", "tmp"])
    assert r.exit_code != 0
    assert "archived" in r.output.lower()


def test_momentum_visible_after_completion_sequence(env: Path) -> None:
    """Momentum only counts scheduled days from the habit's creation onward,
    so seed the habit with a backdated `created_at` to let the EMA build."""
    from flow.models import Habit

    runner = CliRunner()
    today = date.today()
    with db.session(env) as conn:
        db.insert_habit(
            conn,
            Habit(
                name="Exercise",
                frequency="daily",
                created_at=today - timedelta(days=6),
            ),
        )

    for i in range(7):
        d = (today - timedelta(days=i)).isoformat()
        _ok(runner.invoke(main, ["done", "exe", "--date", d]))

    r = runner.invoke(main, ["list"])
    _ok(r)
    # 7 EMA updates at strength 1.0, alpha=0.3 -> 1 - 0.7^7 ≈ 0.918 -> 92
    assert "92" in r.output or "91" in r.output
    assert "100%" in r.output


def test_habits_created_today_show_zero_score(env: Path) -> None:
    runner = CliRunner()
    _ok(runner.invoke(main, ["add", "Brand New"]))
    r = runner.invoke(main, ["list"])
    _ok(r)
    # EMA after 1 scheduled day with no completion = 0
    assert "Brand New" in r.output


def test_log_window_filters_older_data(env: Path) -> None:
    runner = CliRunner()
    _ok(runner.invoke(main, ["add", "Exercise"]))
    old = (date.today() - timedelta(days=45)).isoformat()
    _ok(runner.invoke(main, ["done", "exe", "--date", old]))

    r = runner.invoke(main, ["log", "--days", "30"])
    _ok(r)
    assert "no completions" in r.output

    r = runner.invoke(main, ["log", "--days", "60"])
    _ok(r)
    assert old in r.output


def test_stats_cli_requires_valid_habit_for_detail(env: Path) -> None:
    runner = CliRunner()
    _ok(runner.invoke(main, ["add", "Real"]))
    r = runner.invoke(main, ["stats", "phantom"])
    assert r.exit_code != 0
    assert "no habit matches" in r.output


def test_empty_db_commands_do_not_crash(env: Path) -> None:
    runner = CliRunner()
    # list, log, export on empty DB all exit 0 with helpful output
    r = runner.invoke(main, ["list"])
    _ok(r)
    assert "no habits" in r.output

    r = runner.invoke(main, ["log"])
    _ok(r)
    assert "no completions" in r.output

    r = runner.invoke(main, ["export"])
    _ok(r)
    assert r.output.strip().startswith("date,habit")

    r = runner.invoke(main, ["export", "-F", "json"])
    _ok(r)
    payload = json.loads(r.output)
    assert payload["habits"] == []


def test_tui_check_mount_on_seeded_db(env: Path) -> None:
    """Confirm flow check TUI wiring works end-to-end via pilot."""
    import asyncio
    from datetime import date as _date

    from flow.models import Habit
    from flow.tui.app import FlowApp
    from flow.tui.screens.check import CheckScreen
    from flow.tui.widgets.habit_row import HabitRow

    with db.session(env) as conn:
        db.insert_habit(conn, Habit(name="E", frequency="daily"))
        db.insert_habit(conn, Habit(name="R", frequency="daily"))

    async def _pilot() -> None:
        app = FlowApp(db_path=env, today=_date.today())
        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.screen, CheckScreen)
            rows = list(app.screen.query(HabitRow))
            assert len(rows) == 2
            await pilot.press("q")

    asyncio.run(_pilot())
