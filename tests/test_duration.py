"""Tests for time-tracking: parse_duration, DB round-trip, CLI + export wiring."""

from __future__ import annotations

import csv
import io
import json
from datetime import date
from pathlib import Path

import pytest
from click.testing import CliRunner

from flow import db, export as flow_export
from flow.cli import main
from flow.models import (
    Completion,
    Habit,
    format_duration,
    parse_duration,
)


# ---- parse_duration -----------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("25m", 25 * 60),
        ("1h", 3600),
        ("1h30m", 90 * 60),
        ("90s", 90),
        ("2h15m30s", 2 * 3600 + 15 * 60 + 30),
        ("1:30", 90),  # mm:ss
        ("0:45", 45),
        ("1:30:00", 5400),  # h:mm:ss
        ("25", 25 * 60),  # bare int = minutes
        ("  45M  ", 45 * 60),  # whitespace + case
    ],
)
def test_parse_duration_accepts(text: str, expected: int) -> None:
    assert parse_duration(text) == expected


@pytest.mark.parametrize(
    "text",
    ["", "   ", "abc", "25x", "h30m", "1h30", "25m25m", "-5m", "1:2:3:4", "::"],
)
def test_parse_duration_rejects(text: str) -> None:
    with pytest.raises(ValueError):
        parse_duration(text)


def test_format_duration_roundtrip() -> None:
    assert format_duration(None) == ""
    assert format_duration(45) == "45s"
    assert format_duration(60) == "1m"
    assert format_duration(1500) == "25m"
    assert format_duration(3600) == "1h"
    assert format_duration(5400) == "1h30m"


# ---- DB round-trip ------------------------------------------------------------


def test_completion_persists_duration(tmp_path: Path) -> None:
    path = tmp_path / "dur.db"
    with db.session(path) as conn:
        h = db.insert_habit(conn, Habit(name="Run", frequency="daily"))
        db.upsert_completion(
            conn,
            Completion(habit_id=h.id, date=date(2026, 4, 20), duration_seconds=1500),
        )
    with db.session(path) as conn:
        comps = db.completions_for_habit(conn, h.id)
    assert comps[0].duration_seconds == 1500


def test_upsert_overwrites_duration(tmp_path: Path) -> None:
    path = tmp_path / "dur.db"
    today = date(2026, 4, 20)
    with db.session(path) as conn:
        h = db.insert_habit(conn, Habit(name="Run", frequency="daily"))
        db.upsert_completion(
            conn, Completion(habit_id=h.id, date=today, duration_seconds=600)
        )
        db.upsert_completion(
            conn, Completion(habit_id=h.id, date=today, duration_seconds=1800)
        )
    with db.session(path) as conn:
        comps = db.completions_for_habit(conn, h.id)
    assert len(comps) == 1
    assert comps[0].duration_seconds == 1800


def test_negative_duration_rejected_in_model() -> None:
    with pytest.raises(ValueError):
        Completion(habit_id=1, date=date.today(), duration_seconds=-1)


def test_all_completions_returns_duration(tmp_path: Path) -> None:
    path = tmp_path / "dur.db"
    with db.session(path) as conn:
        h = db.insert_habit(conn, Habit(name="Run", frequency="daily"))
        db.upsert_completion(
            conn,
            Completion(habit_id=h.id, date=date(2026, 4, 20), duration_seconds=777),
        )
        pairs = db.all_completions(conn)
    assert pairs[0][0].duration_seconds == 777


# ---- CLI `done --duration` ----------------------------------------------------


@pytest.fixture
def cli_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "cli.db"
    monkeypatch.setenv("FLOW_DB_PATH", str(path))
    return path


def test_done_records_duration(cli_db: Path) -> None:
    runner = CliRunner()
    assert runner.invoke(main, ["add", "Focus"]).exit_code == 0
    r = runner.invoke(main, ["done", "Focus", "--duration", "25m"])
    assert r.exit_code == 0, r.output
    with db.session(cli_db) as conn:
        h = db.list_habits(conn)[0]
        comps = db.completions_for_habit(conn, h.id)
    assert comps[0].duration_seconds == 1500


def test_done_invalid_duration_rejected(cli_db: Path) -> None:
    runner = CliRunner()
    runner.invoke(main, ["add", "Focus"])
    r = runner.invoke(main, ["done", "Focus", "--duration", "banana"])
    assert r.exit_code != 0
    assert "duration" in r.output.lower()


def test_done_duration_derives_value_for_minute_unit(cli_db: Path) -> None:
    """Habit with unit=minutes and target=30 auto-derives value from --duration."""
    runner = CliRunner()
    runner.invoke(
        main,
        ["add", "Focus", "--unit", "minutes", "--target", "30"],
    )
    r = runner.invoke(main, ["done", "Focus", "--duration", "25m"])
    assert r.exit_code == 0, r.output
    with db.session(cli_db) as conn:
        h = db.list_habits(conn)[0]
        comps = db.completions_for_habit(conn, h.id)
    assert comps[0].duration_seconds == 1500
    assert comps[0].value == pytest.approx(25.0)


def test_done_duration_does_not_override_explicit_value(cli_db: Path) -> None:
    runner = CliRunner()
    runner.invoke(main, ["add", "Focus", "--unit", "minutes", "--target", "30"])
    r = runner.invoke(
        main,
        ["done", "Focus", "--duration", "25m", "--value", "10"],
    )
    assert r.exit_code == 0, r.output
    with db.session(cli_db) as conn:
        h = db.list_habits(conn)[0]
        comps = db.completions_for_habit(conn, h.id)
    assert comps[0].value == 10.0
    assert comps[0].duration_seconds == 1500


def test_done_duration_no_value_derivation_for_pages(cli_db: Path) -> None:
    runner = CliRunner()
    runner.invoke(main, ["add", "Read", "--unit", "pages", "--target", "20"])
    r = runner.invoke(main, ["done", "Read", "--duration", "30m"])
    assert r.exit_code == 0, r.output
    with db.session(cli_db) as conn:
        h = db.list_habits(conn)[0]
        comps = db.completions_for_habit(conn, h.id)
    assert comps[0].duration_seconds == 1800
    assert comps[0].value is None  # unit is not time → no derivation


def test_log_output_shows_time_column(cli_db: Path) -> None:
    runner = CliRunner()
    runner.invoke(main, ["add", "Focus"])
    runner.invoke(main, ["done", "Focus", "--duration", "25m"])
    r = runner.invoke(main, ["log"])
    assert r.exit_code == 0, r.output
    assert "time" in r.output
    assert "25m" in r.output


# ---- Export -------------------------------------------------------------------


def test_csv_export_includes_duration(tmp_path: Path) -> None:
    path = tmp_path / "dur.db"
    with db.session(path) as conn:
        h = db.insert_habit(conn, Habit(name="Run", frequency="daily"))
        db.upsert_completion(
            conn,
            Completion(habit_id=h.id, date=date(2026, 4, 20), duration_seconds=1500),
        )
    buf = io.StringIO()
    with db.session(path) as conn:
        flow_export.write_csv(conn, buf)
    buf.seek(0)
    row = next(csv.DictReader(buf))
    assert row["duration_seconds"] == "1500"


def test_json_export_includes_duration(tmp_path: Path) -> None:
    path = tmp_path / "dur.db"
    with db.session(path) as conn:
        h = db.insert_habit(conn, Habit(name="Run", frequency="daily"))
        db.upsert_completion(
            conn,
            Completion(habit_id=h.id, date=date(2026, 4, 20), duration_seconds=1500),
        )
    buf = io.StringIO()
    with db.session(path) as conn:
        flow_export.write_json(conn, buf)
    payload = json.loads(buf.getvalue())
    comp = payload["habits"][0]["completions"][0]
    assert comp["duration_seconds"] == 1500
