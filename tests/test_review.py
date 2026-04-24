"""Tests for flow.review — digest / sparkline / correlations / markdown."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from flow import db, review as r
from flow.cli import main
from flow.models import Completion, Habit


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "review.db"
    monkeypatch.setenv("FLOW_DB_PATH", str(path))
    return path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---- bounds helpers -----------------------------------------------------------


class TestBounds:
    def test_week_bounds_is_monday_to_sunday(self) -> None:
        # 2026-04-24 is a Friday
        start, end = r.week_bounds(date(2026, 4, 24))
        assert start == date(2026, 4, 20)
        assert end == date(2026, 4, 26)
        assert start.weekday() == 0
        assert end.weekday() == 6

    def test_month_bounds(self) -> None:
        start, end = r.month_bounds(date(2026, 2, 15))
        assert start == date(2026, 2, 1)
        assert end == date(2026, 2, 28)


# ---- digest -------------------------------------------------------------------


class TestDigest:
    def test_skips_archived(self) -> None:
        h = Habit(
            name="A",
            frequency="daily",
            id=1,
            created_at=date(2026, 4, 20),
            archived_at=date(2026, 4, 21),
        )
        digest = r.build_digest([h], {1: []}, date(2026, 4, 20), date(2026, 4, 26))
        assert digest.rows == []

    def test_partial_strengths_fractional(self) -> None:
        start, end = date(2026, 4, 20), date(2026, 4, 26)
        h = Habit(
            name="Read",
            frequency="daily",
            id=1,
            unit="pages",
            target=20,
            created_at=start,
        )
        comps = [
            Completion(habit_id=1, date=start + timedelta(days=i), value=10)
            for i in range(3)
        ]
        digest = r.build_digest([h], {1: comps}, start, end)
        assert digest.rows[0].scheduled == 7
        # Three half-completions -> 1.5 completed
        assert digest.rows[0].completed == pytest.approx(1.5)
        assert digest.rows[0].rate == pytest.approx(1.5 / 7)

    def test_time_and_notes_aggregate(self) -> None:
        start, end = date(2026, 4, 20), date(2026, 4, 26)
        h = Habit(name="A", frequency="daily", id=1, created_at=start)
        comps = [
            Completion(habit_id=1, date=start, duration_seconds=600, note="ok"),
            Completion(habit_id=1, date=start + timedelta(days=1), duration_seconds=300),
        ]
        digest = r.build_digest([h], {1: comps}, start, end)
        assert digest.rows[0].total_seconds == 900
        assert digest.rows[0].notes == 1

    def test_drops_rows_with_zero_scheduled(self) -> None:
        start, end = date(2026, 4, 20), date(2026, 4, 26)
        # Habit created after window -> zero scheduled
        h = Habit(
            name="New", frequency="daily", id=1, created_at=date(2026, 5, 1)
        )
        digest = r.build_digest([h], {1: []}, start, end)
        assert digest.rows == []


# ---- sparkline ----------------------------------------------------------------


class TestSparkline:
    def test_empty(self) -> None:
        assert r.sparkline([]) == ""

    def test_flat_series(self) -> None:
        s = r.sparkline([50.0, 50.0, 50.0])
        # All equal -> mid block
        assert len(set(s)) == 1
        assert len(s) == 3

    def test_rising(self) -> None:
        s = r.sparkline([0, 20, 40, 60, 80, 100])
        # First char is the smallest block, last is the biggest
        assert s[0] == "▁"
        assert s[-1] == "█"

    def test_downsamples_to_width(self) -> None:
        s = r.sparkline(list(range(100)), width=10)
        assert len(s) == 10


# ---- correlations -------------------------------------------------------------


class TestCorrelations:
    def test_perfect_co_occurrence(self) -> None:
        start = date(2026, 4, 1)
        today = date(2026, 4, 30)
        a = Habit(name="A", frequency="daily", id=1, created_at=start)
        b = Habit(name="B", frequency="daily", id=2, created_at=start)
        # Both completed on the same 20 days
        days = [start + timedelta(days=i) for i in range(20)]
        comps = {
            1: [Completion(habit_id=1, date=d) for d in days],
            2: [Completion(habit_id=2, date=d) for d in days],
        }
        pairs = r.correlations([a, b], comps, start, today)
        assert len(pairs) == 2
        # Each direction: given A done, B done 100%
        assert all(p.co_rate == 1.0 for p in pairs)

    def test_filters_low_overlap(self) -> None:
        start = date(2026, 4, 1)
        today = date(2026, 4, 30)
        a = Habit(name="A", frequency="daily", id=1, created_at=start)
        b = Habit(name="B", frequency="daily", id=2, created_at=start)
        comps = {
            1: [Completion(habit_id=1, date=start)],
            2: [Completion(habit_id=2, date=start)],
        }
        pairs = r.correlations([a, b], comps, start, today, min_shared=50)
        assert pairs == []

    def test_lift_sorted_first(self) -> None:
        start = date(2026, 4, 1)
        today = date(2026, 4, 30)
        a = Habit(name="A", frequency="daily", id=1, created_at=start)
        b = Habit(name="B", frequency="daily", id=2, created_at=start)
        c = Habit(name="C", frequency="daily", id=3, created_at=start)
        a_days = [start + timedelta(days=i) for i in range(10)]
        comps = {
            1: [Completion(habit_id=1, date=d) for d in a_days],
            # B perfectly overlaps A
            2: [Completion(habit_id=2, date=d) for d in a_days],
            # C never overlaps A (done only on non-A days)
            3: [
                Completion(habit_id=3, date=start + timedelta(days=i))
                for i in range(15, 25)
            ],
        }
        pairs = r.correlations([a, b, c], comps, start, today)
        # Highest lift pair first
        assert (pairs[0].a.name, pairs[0].b.name) == ("A", "B")
        assert pairs[0].co_rate == 1.0


# ---- markdown summary ---------------------------------------------------------


class TestMarkdownSummary:
    def test_contains_header_and_rows(self) -> None:
        start, end = date(2026, 4, 20), date(2026, 4, 26)
        h = Habit(name="Exercise", frequency="daily", id=1, created_at=start)
        comps = {1: [Completion(habit_id=1, date=start, note="hard run")]}
        md = r.summary_markdown([h], comps, start, end)
        assert md.startswith("# flow — 2026-04-20 to 2026-04-26")
        assert "| Exercise |" in md
        assert "hard run" in md
        assert "## Notes" in md


# ---- CLI integration ----------------------------------------------------------


class TestCliReview:
    def test_week(self, runner: CliRunner, db_path: Path) -> None:
        runner.invoke(main, ["add", "Exercise"])
        runner.invoke(main, ["done", "Exercise"])
        r_ = runner.invoke(main, ["week"])
        assert r_.exit_code == 0, r_.output
        assert "this week" in r_.output
        assert "Exercise" in r_.output

    def test_month(self, runner: CliRunner, db_path: Path) -> None:
        runner.invoke(main, ["add", "Meditate"])
        r_ = runner.invoke(main, ["month"])
        assert r_.exit_code == 0, r_.output
        assert "this month" in r_.output

    def test_correlations_runs(self, runner: CliRunner, db_path: Path) -> None:
        runner.invoke(main, ["add", "A"])
        runner.invoke(main, ["add", "B"])
        r_ = runner.invoke(main, ["correlations", "--days", "30"])
        assert r_.exit_code == 0, r_.output

    def test_summary_to_file(
        self, runner: CliRunner, db_path: Path, tmp_path: Path
    ) -> None:
        runner.invoke(main, ["add", "Read"])
        runner.invoke(main, ["done", "Read"])
        out = tmp_path / "wk.md"
        r_ = runner.invoke(main, ["summary", "--out", str(out)])
        assert r_.exit_code == 0, r_.output
        text = out.read_text()
        assert text.startswith("# flow — ")
        assert "Read" in text

    def test_alpha_flag_on_add(self, runner: CliRunner, db_path: Path) -> None:
        r_ = runner.invoke(main, ["add", "Focused", "--alpha", "0.6"])
        assert r_.exit_code == 0, r_.output
        with db.session(db_path) as conn:
            h = db.find_habit_by_name(conn, "Focused")
        assert h is not None
        assert h.alpha == pytest.approx(0.6)

    def test_alpha_flag_rejects_out_of_range(
        self, runner: CliRunner, db_path: Path
    ) -> None:
        r_ = runner.invoke(main, ["add", "X", "--alpha", "2.0"])
        assert r_.exit_code != 0
        assert "alpha" in r_.output.lower()

    def test_edit_alpha_resets_on_empty(
        self, runner: CliRunner, db_path: Path
    ) -> None:
        runner.invoke(main, ["add", "X", "--alpha", "0.9"])
        r_ = runner.invoke(main, ["edit", "X", "--alpha", ""])
        assert r_.exit_code == 0, r_.output
        with db.session(db_path) as conn:
            h = db.find_habit_by_name(conn, "X")
        assert h.alpha == pytest.approx(Habit.ALPHA_DEFAULT)


# ---- momentum uses habit alpha ------------------------------------------------


class TestAlphaIsPerHabit:
    def test_compute_momentum_uses_habit_alpha(self) -> None:
        from flow.momentum import compute_momentum

        today = date(2026, 4, 20)
        start = today - timedelta(days=9)
        h_fast = Habit(
            name="F", frequency="daily", id=1, created_at=start, alpha=0.6
        )
        h_slow = Habit(
            name="S", frequency="daily", id=2, created_at=start, alpha=0.1
        )
        comps = [
            Completion(habit_id=1, date=start + timedelta(days=i))
            for i in range(10)
        ]
        comps_slow = [
            Completion(habit_id=2, date=start + timedelta(days=i))
            for i in range(10)
        ]
        mom_fast = compute_momentum(h_fast, comps, today=today)
        mom_slow = compute_momentum(h_slow, comps_slow, today=today)
        assert mom_fast.score > mom_slow.score
