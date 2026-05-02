"""Tests for skip + pause + resume + why.

Skip is the underlying mechanic: a completion row with status='skipped' that
neither boosts nor decays momentum. Pause is a bulk skip across a window;
resume clears future skips. `why` is a read-only score-explanation report.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from flow import db
from flow.cli import main
from flow.models import (
    COMPLETION_STATUS_DONE,
    COMPLETION_STATUS_SKIPPED,
    Completion,
    Habit,
)
from flow.momentum import compute_momentum


# ---- fixtures -----------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "skip.db"
    monkeypatch.setenv("FLOW_DB_PATH", str(path))
    return path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---- model layer --------------------------------------------------------------


def test_completion_default_status_is_done() -> None:
    c = Completion(habit_id=1, date=date(2026, 5, 1))
    assert c.status == "done"
    assert c.is_done
    assert not c.is_skipped


def test_completion_invalid_status_rejected() -> None:
    with pytest.raises(ValueError):
        Completion(habit_id=1, date=date(2026, 5, 1), status="bogus")


def test_completion_skipped_flag() -> None:
    c = Completion(
        habit_id=1, date=date(2026, 5, 1), status=COMPLETION_STATUS_SKIPPED
    )
    assert c.is_skipped
    assert not c.is_done


# ---- db helpers ---------------------------------------------------------------


def test_bulk_skip_dates_inserts_only_missing(db_path: Path) -> None:
    today = date(2026, 5, 1)
    with db.session(db_path) as conn:
        h = db.insert_habit(
            conn, Habit(name="X", frequency="daily", created_at=today)
        )
        # Pre-existing done completion on day 2 — bulk_skip must not clobber it.
        db.upsert_completion(
            conn,
            Completion(
                habit_id=h.id,
                date=today + timedelta(days=2),
                status=COMPLETION_STATUS_DONE,
            ),
        )
        dates = [today + timedelta(days=i) for i in range(5)]
        inserted = db.bulk_skip_dates(conn, h.id, dates, note="vacation")

        rows = db.completions_for_habit(conn, h.id)

    assert inserted == 4  # day 2 was already done, others are new skips
    by_date = {c.date: c for c in rows}
    assert by_date[today + timedelta(days=2)].is_done
    assert by_date[today].is_skipped
    assert by_date[today].note == "vacation"


def test_clear_future_skips_only_touches_future_skips(db_path: Path) -> None:
    today = date(2026, 5, 10)
    yesterday = today - timedelta(days=1)
    tomorrow = today + timedelta(days=1)
    with db.session(db_path) as conn:
        h = db.insert_habit(
            conn,
            Habit(name="X", frequency="daily", created_at=today - timedelta(days=10)),
        )
        # Past skip — must remain after resume (history is preserved).
        db.upsert_completion(
            conn,
            Completion(
                habit_id=h.id, date=yesterday, status=COMPLETION_STATUS_SKIPPED
            ),
        )
        # Future skip — must be removed.
        db.upsert_completion(
            conn,
            Completion(
                habit_id=h.id, date=tomorrow, status=COMPLETION_STATUS_SKIPPED
            ),
        )
        # Future done — must NOT be removed.
        db.upsert_completion(
            conn,
            Completion(
                habit_id=h.id,
                date=tomorrow + timedelta(days=1),
                status=COMPLETION_STATUS_DONE,
            ),
        )
        removed = db.clear_future_skips(conn, h.id, today)
        rows = db.completions_for_habit(conn, h.id)

    assert removed == 1
    dates = sorted(c.date for c in rows)
    assert yesterday in dates
    assert tomorrow not in dates
    assert tomorrow + timedelta(days=1) in dates


def test_paused_until_date_returns_max_future_skip(db_path: Path) -> None:
    today = date(2026, 6, 1)
    with db.session(db_path) as conn:
        h = db.insert_habit(
            conn, Habit(name="X", frequency="daily", created_at=today)
        )
        for i in range(3):
            db.upsert_completion(
                conn,
                Completion(
                    habit_id=h.id,
                    date=today + timedelta(days=i),
                    status=COMPLETION_STATUS_SKIPPED,
                ),
            )
        until = db.paused_until_date(conn, h.id, today)

    assert until == today + timedelta(days=2)


def test_paused_until_date_none_when_no_future_skip(db_path: Path) -> None:
    today = date(2026, 6, 1)
    with db.session(db_path) as conn:
        h = db.insert_habit(
            conn, Habit(name="X", frequency="daily", created_at=today - timedelta(days=10))
        )
        db.upsert_completion(
            conn,
            Completion(
                habit_id=h.id,
                date=today - timedelta(days=2),
                status=COMPLETION_STATUS_SKIPPED,
            ),
        )
        until = db.paused_until_date(conn, h.id, today)

    assert until is None


# ---- momentum -----------------------------------------------------------------


def test_skipped_day_scores_higher_than_missed_day() -> None:
    """A skip should leave the score better off than a miss. Same set of
    done completions, one day either missed or skipped — skip should yield
    a strictly higher score because it's removed from the denominator."""
    today = date(2026, 5, 20)
    h = Habit(
        name="X", frequency="daily", created_at=today - timedelta(days=14)
    )
    # All days 0..14 except day 7. Without anything for day 7 → it's a miss.
    miss_comps = [
        Completion(habit_id=1, date=h.created_at + timedelta(days=i))
        for i in range(15)
        if i != 7
    ]
    # Same, plus an explicit skip on day 7 → skip is excluded.
    skip_comps = list(miss_comps) + [
        Completion(
            habit_id=1,
            date=h.created_at + timedelta(days=7),
            status=COMPLETION_STATUS_SKIPPED,
        )
    ]

    m_miss = compute_momentum(h, miss_comps, today=today)
    m_skip = compute_momentum(h, skip_comps, today=today)

    assert m_skip.score > m_miss.score
    # With miss, denominator includes day 7 (15 sched, 14 done -> 14/15 ≈ 0.93).
    # With skip, day 7 drops from numerator AND denominator (14/14 = 1.0).
    assert m_skip.completion_rate > m_miss.completion_rate


def test_skipped_day_does_not_count_as_miss() -> None:
    """A bare skip (no other completions) should leave score at 0 — not push
    it negative. It also shouldn't be counted as a missed scheduled day."""
    today = date(2026, 5, 20)
    h = Habit(name="X", frequency="daily", created_at=today)
    comps = [
        Completion(habit_id=1, date=today, status=COMPLETION_STATUS_SKIPPED)
    ]
    m = compute_momentum(h, comps, today=today)
    assert m.score == 0.0
    assert m.completion_rate == 0.0  # zero scheduled in window after skip


# ---- CLI: skip ----------------------------------------------------------------


def test_cli_skip_today(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Run", "-f", "daily"])
    r = runner.invoke(main, ["skip", "Run"])
    assert r.exit_code == 0, r.output
    assert "skip" in r.output.lower()

    with db.session(db_path) as conn:
        comps = db.completions_for_habit(conn, 1)
    assert len(comps) == 1
    assert comps[0].is_skipped


def test_cli_skip_with_date_and_note(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Run", "-f", "daily"])
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    r = runner.invoke(
        main, ["skip", "Run", "--date", yesterday, "--note", "sick"]
    )
    assert r.exit_code == 0, r.output
    with db.session(db_path) as conn:
        comps = db.completions_for_habit(conn, 1)
    assert len(comps) == 1
    assert comps[0].is_skipped
    assert comps[0].note == "sick"
    assert comps[0].date.isoformat() == yesterday


def test_cli_skip_rejects_unscheduled_day(
    runner: CliRunner, db_path: Path
) -> None:
    # mon,wed,fri only.
    runner.invoke(main, ["add", "Mtg", "-f", "tue,thu"])
    today = date.today()
    # Find a non-Tue/Thu day in the past.
    target = today - timedelta(days=1)
    while target.weekday() in {1, 3}:
        target -= timedelta(days=1)
    r = runner.invoke(
        main, ["skip", "Mtg", "--date", target.isoformat()]
    )
    assert r.exit_code != 0
    assert "not scheduled" in r.output


def test_cli_skip_rejects_archived(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Run"])
    runner.invoke(main, ["archive", "Run"])
    r = runner.invoke(main, ["skip", "Run"])
    assert r.exit_code != 0
    assert "archived" in r.output


def test_cli_skip_then_done_promotes_to_done(
    runner: CliRunner, db_path: Path
) -> None:
    runner.invoke(main, ["add", "Run"])
    runner.invoke(main, ["skip", "Run"])
    r = runner.invoke(main, ["done", "Run"])
    assert r.exit_code == 0, r.output
    with db.session(db_path) as conn:
        comps = db.completions_for_habit(conn, 1)
    assert len(comps) == 1
    assert comps[0].is_done


# ---- CLI: pause ---------------------------------------------------------------


def test_cli_pause_with_until(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Run", "-f", "daily"])
    until = (date.today() + timedelta(days=4)).isoformat()
    r = runner.invoke(main, ["pause", "Run", "--until", until])
    assert r.exit_code == 0, r.output
    assert "paused" in r.output.lower()

    with db.session(db_path) as conn:
        comps = db.completions_for_habit(conn, 1)
    skips = [c for c in comps if c.is_skipped]
    assert len(skips) == 5  # today + next 4 days inclusive
    assert all(c.is_skipped for c in skips)


def test_cli_pause_with_days(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Run", "-f", "daily"])
    r = runner.invoke(main, ["pause", "Run", "--days", "7"])
    assert r.exit_code == 0, r.output
    with db.session(db_path) as conn:
        comps = db.completions_for_habit(conn, 1)
    assert len([c for c in comps if c.is_skipped]) == 7


def test_cli_pause_requires_one_of_until_days(
    runner: CliRunner, db_path: Path
) -> None:
    runner.invoke(main, ["add", "Run"])
    r = runner.invoke(main, ["pause", "Run"])
    assert r.exit_code != 0
    assert "exactly one" in r.output


def test_cli_pause_rejects_both_until_and_days(
    runner: CliRunner, db_path: Path
) -> None:
    runner.invoke(main, ["add", "Run"])
    until = (date.today() + timedelta(days=2)).isoformat()
    r = runner.invoke(
        main, ["pause", "Run", "--until", until, "--days", "3"]
    )
    assert r.exit_code != 0


def test_cli_pause_rejects_past_until(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Run"])
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    r = runner.invoke(main, ["pause", "Run", "--until", yesterday])
    assert r.exit_code != 0
    assert "past" in r.output.lower()


def test_cli_pause_preserves_existing_done(
    runner: CliRunner, db_path: Path
) -> None:
    runner.invoke(main, ["add", "Run", "-f", "daily"])
    # Mark today done first, THEN pause for 3 days.
    runner.invoke(main, ["done", "Run"])
    r = runner.invoke(main, ["pause", "Run", "--days", "3"])
    assert r.exit_code == 0, r.output

    with db.session(db_path) as conn:
        comps = db.completions_for_habit(conn, 1)
    today_comp = next(c for c in comps if c.date == date.today())
    assert today_comp.is_done  # not clobbered
    skips = [c for c in comps if c.is_skipped]
    assert len(skips) == 2  # only days 2 and 3 got skips


def test_cli_pause_skips_only_scheduled_days(
    runner: CliRunner, db_path: Path
) -> None:
    runner.invoke(main, ["add", "Mtg", "-f", "mon,wed,fri"])
    r = runner.invoke(main, ["pause", "Mtg", "--days", "14"])
    assert r.exit_code == 0, r.output
    with db.session(db_path) as conn:
        comps = db.completions_for_habit(conn, 1)
    # Two weeks → exactly 6 mon/wed/fri occurrences.
    assert len([c for c in comps if c.is_skipped]) == 6


# ---- CLI: resume --------------------------------------------------------------


def test_cli_resume_clears_future_skips(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Run", "-f", "daily"])
    runner.invoke(main, ["pause", "Run", "--days", "5"])
    r = runner.invoke(main, ["resume", "Run"])
    assert r.exit_code == 0, r.output
    assert "resumed" in r.output.lower()

    with db.session(db_path) as conn:
        comps = db.completions_for_habit(conn, 1)
    assert len(comps) == 0


def test_cli_resume_when_not_paused(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Run"])
    r = runner.invoke(main, ["resume", "Run"])
    assert r.exit_code == 0, r.output
    assert "no active pause" in r.output


# ---- CLI: why -----------------------------------------------------------------


def test_cli_why_basic(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Run", "-f", "daily"])
    runner.invoke(main, ["done", "Run"])
    r = runner.invoke(main, ["why", "Run"])
    assert r.exit_code == 0, r.output
    assert "Run" in r.output
    assert "score" in r.output
    assert "alpha" in r.output
    assert "last done" in r.output


def test_cli_why_shows_paused_until(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Run", "-f", "daily"])
    runner.invoke(main, ["pause", "Run", "--days", "3"])
    r = runner.invoke(main, ["why", "Run"])
    assert r.exit_code == 0, r.output
    assert "paused until" in r.output


def test_cli_why_unknown_habit(runner: CliRunner, db_path: Path) -> None:
    r = runner.invoke(main, ["why", "Unknown"])
    assert r.exit_code != 0


# ---- summary integration ------------------------------------------------------


def test_cli_today_excludes_paused_from_total(
    runner: CliRunner, db_path: Path
) -> None:
    runner.invoke(main, ["add", "Run", "-f", "daily"])
    runner.invoke(main, ["add", "Read", "-f", "daily"])
    runner.invoke(main, ["pause", "Run", "--days", "1"])

    r = runner.invoke(main, ["today", "--format", "count"])
    assert r.exit_code == 0
    # Only Read should count: 0/1, not 0/2.
    assert r.output.strip() == "0/1"


def test_cli_random_excludes_paused(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Run", "-f", "daily"])
    runner.invoke(main, ["pause", "Run", "--days", "1"])
    r = runner.invoke(main, ["random"])
    assert r.exit_code == 0
    # Only habit is paused → no candidate.
    assert "nothing scheduled" in r.output.lower()


def test_cli_list_shows_paused_marker(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Run", "-f", "daily"])
    runner.invoke(main, ["pause", "Run", "--days", "3"])
    r = runner.invoke(main, ["list"])
    assert r.exit_code == 0, r.output
    assert "paused" in r.output


# ---- export / import roundtrip ------------------------------------------------


def test_export_import_roundtrips_skip_status(
    runner: CliRunner, tmp_path: Path, db_path: Path
) -> None:
    runner.invoke(main, ["add", "Run", "-f", "daily"])
    runner.invoke(main, ["skip", "Run", "--note", "sick"])

    export_path = tmp_path / "out.json"
    r = runner.invoke(
        main, ["export", "--format", "json", "-o", str(export_path)]
    )
    assert r.exit_code == 0, r.output

    payload = json.loads(export_path.read_text())
    completion = payload["habits"][0]["completions"][0]
    assert completion["status"] == "skipped"
    assert completion["note"] == "sick"

    # Wipe and re-import.
    db_path.unlink()
    r = runner.invoke(
        main, ["import", str(export_path), "--conflict", "overwrite"]
    )
    assert r.exit_code == 0, r.output

    with db.session(db_path) as conn:
        comps = db.completions_for_habit(conn, 1)
    assert len(comps) == 1
    assert comps[0].is_skipped
    assert comps[0].note == "sick"
