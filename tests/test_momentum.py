"""Tests for flow.momentum — the critical path of the app."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from flow import momentum as m
from flow.models import Completion, Habit


# ---- completion_strength ------------------------------------------------------


class TestCompletionStrength:
    def test_no_target_is_binary(self) -> None:
        assert m.completion_strength(value=None, target=None) == 1.0
        assert m.completion_strength(value=123, target=None) == 1.0

    def test_target_with_full_value(self) -> None:
        assert m.completion_strength(value=30, target=30) == 1.0

    def test_target_with_partial_value(self) -> None:
        assert m.completion_strength(value=15, target=30) == 0.5

    def test_target_caps_at_one(self) -> None:
        assert m.completion_strength(value=60, target=30) == 1.0

    def test_target_with_missing_value_is_full(self) -> None:
        assert m.completion_strength(value=None, target=30) == 1.0

    def test_zero_or_negative_value_is_zero(self) -> None:
        assert m.completion_strength(value=0, target=30) == 0.0
        assert m.completion_strength(value=-5, target=30) == 0.0


# ---- scheduling ---------------------------------------------------------------


class TestScheduledDays:
    def test_daily_includes_every_day(self) -> None:
        h = Habit(name="X", frequency="daily")
        start, end = date(2026, 4, 1), date(2026, 4, 7)
        assert m.scheduled_days_between(h, start, end) == [
            start + timedelta(days=i) for i in range(7)
        ]

    def test_weekdays_skips_weekend(self) -> None:
        h = Habit(name="X", frequency="weekdays")
        start = date(2026, 4, 13)  # Monday
        end = date(2026, 4, 19)  # Sunday
        days = m.scheduled_days_between(h, start, end)
        assert len(days) == 5
        assert all(d.weekday() < 5 for d in days)

    def test_custom_days(self) -> None:
        h = Habit(name="X", frequency="mon,wed,fri")
        start = date(2026, 4, 13)
        end = date(2026, 4, 19)
        days = m.scheduled_days_between(h, start, end)
        assert [d.weekday() for d in days] == [0, 2, 4]

    def test_weekly_is_monday_only(self) -> None:
        h = Habit(name="X", frequency="weekly")
        start = date(2026, 4, 13)
        end = date(2026, 4, 26)  # Two full weeks
        days = m.scheduled_days_between(h, start, end)
        assert len(days) == 2
        assert all(d.weekday() == 0 for d in days)

    def test_empty_range(self) -> None:
        h = Habit(name="X", frequency="daily")
        assert m.scheduled_days_between(h, date(2026, 4, 2), date(2026, 4, 1)) == []


# ---- EMA score ----------------------------------------------------------------


class TestEmaScore:
    def _days(self, n: int, start: date = date(2026, 1, 1)) -> list[date]:
        return [start + timedelta(days=i) for i in range(n)]

    def test_empty_history_is_zero(self) -> None:
        assert m.ema_score({}, []) == 0.0
        assert m.score_history({}, []) == []

    def test_all_completed_converges_toward_hundred(self) -> None:
        days = self._days(30)
        strengths = {d: 1.0 for d in days}
        score = m.ema_score(strengths, days)
        assert 95.0 < score <= 100.0

    def test_all_missed_is_zero(self) -> None:
        days = self._days(30)
        assert m.ema_score({}, days) == 0.0

    def test_single_miss_small_dent_on_high_score(self) -> None:
        """Core principle: one miss barely moves a built-up score."""
        days = self._days(30)
        # Build up with 29 completions then miss the last day
        strengths = {d: 1.0 for d in days[:-1]}
        score_with_miss = m.ema_score(strengths, days)
        score_without_miss = m.ema_score({d: 1.0 for d in days}, days)

        dent = score_without_miss - score_with_miss
        assert 0 < dent < 35  # one miss drops by ~30 * alpha ratio, still clearly > 0
        # After continuing to complete, the dent should recover
        recovery_days = self._days(5, start=days[-1] + timedelta(days=1))
        all_days = days + recovery_days
        recovered = m.ema_score(
            {**strengths, **{d: 1.0 for d in recovery_days}}, all_days
        )
        assert recovered > score_with_miss

    def test_partial_completion_contributes_proportionally(self) -> None:
        days = self._days(20)
        full = m.ema_score({d: 1.0 for d in days}, days)
        half = m.ema_score({d: 0.5 for d in days}, days)
        assert half == pytest.approx(full / 2, rel=1e-9)

    def test_score_history_length_matches_scheduled_days(self) -> None:
        days = self._days(10)
        hist = m.score_history({d: 1.0 for d in days}, days)
        assert len(hist) == 10
        # Monotonically rising when every day completed
        assert hist == sorted(hist)

    def test_score_is_bounded(self) -> None:
        days = self._days(100)
        # Even over-target entries should cap at strength 1.0 via clamping
        strengths = {d: 1.0 for d in days}
        assert m.ema_score(strengths, days) <= 100.0

    def test_alpha_controls_smoothing(self) -> None:
        days = self._days(10)
        strengths = {d: 1.0 for d in days}
        fast = m.ema_score(strengths, days, alpha=0.6)
        slow = m.ema_score(strengths, days, alpha=0.1)
        # Higher alpha rises faster -> higher after same input
        assert fast > slow


# ---- trend --------------------------------------------------------------------


class TestTrend:
    def test_short_history_is_flat(self) -> None:
        assert m.trend([]) == "→"
        assert m.trend([50.0]) == "→"

    def test_rising_returns_up_arrow(self) -> None:
        hist = [10.0, 20.0, 40.0, 70.0]
        assert m.trend(hist) == "↗"

    def test_falling_returns_down_arrow(self) -> None:
        hist = [80.0, 70.0, 50.0, 30.0]
        assert m.trend(hist) == "↘"

    def test_flat_within_threshold(self) -> None:
        hist = [50.0, 50.5, 51.0, 50.3]
        assert m.trend(hist) == "→"

    def test_lookback_clamps_to_history(self) -> None:
        # lookback=3 but only 2 entries -> compare last to first
        hist = [0.0, 30.0]
        assert m.trend(hist) == "↗"


# ---- rolling completion rate --------------------------------------------------


class TestRollingRate:
    def test_empty_returns_zero(self) -> None:
        assert m.rolling_completion_rate({}, []) == 0.0

    def test_all_complete_window(self) -> None:
        today = date(2026, 4, 13)
        days = [today - timedelta(days=i) for i in range(14)]
        strengths = {d: 1.0 for d in days}
        assert m.rolling_completion_rate(
            strengths, days, window_days=14, ref_date=today
        ) == 1.0

    def test_half_complete_window(self) -> None:
        today = date(2026, 4, 13)
        days = [today - timedelta(days=i) for i in range(14)]
        strengths = {d: 1.0 for i, d in enumerate(days) if i % 2 == 0}
        rate = m.rolling_completion_rate(
            strengths, days, window_days=14, ref_date=today
        )
        assert rate == pytest.approx(7 / 14)

    def test_partial_strength_counts_fractionally(self) -> None:
        today = date(2026, 4, 13)
        days = [today - timedelta(days=i) for i in range(10)]
        strengths = {d: 0.5 for d in days}
        rate = m.rolling_completion_rate(
            strengths, days, window_days=10, ref_date=today
        )
        assert rate == pytest.approx(0.5)

    def test_window_bounds_exclude_older_days(self) -> None:
        today = date(2026, 4, 13)
        old = [today - timedelta(days=30 + i) for i in range(5)]
        recent = [today - timedelta(days=i) for i in range(5)]
        days = old + recent
        strengths = {d: 1.0 for d in old}  # only old completions
        rate = m.rolling_completion_rate(strengths, days, window_days=7, ref_date=today)
        assert rate == 0.0

    def test_no_scheduled_in_window_returns_zero(self) -> None:
        today = date(2026, 4, 13)
        days = [today - timedelta(days=30)]
        strengths = {days[0]: 1.0}
        assert m.rolling_completion_rate(
            strengths, days, window_days=7, ref_date=today
        ) == 0.0


# ---- compute_momentum integration --------------------------------------------


class TestComputeMomentum:
    def test_new_habit_no_scheduled_days_yet(self) -> None:
        today = date(2026, 4, 13)
        h = Habit(name="New", frequency="daily", created_at=today + timedelta(days=1))
        mom = m.compute_momentum(h, [], today=today)
        assert mom.score == 0.0
        assert mom.trend == "→"
        assert mom.completion_rate == 0.0

    def test_perfect_record(self) -> None:
        today = date(2026, 4, 13)
        start = today - timedelta(days=29)
        h = Habit(name="Perfect", frequency="daily", created_at=start)
        completions = [
            Completion(habit_id=1, date=start + timedelta(days=i)) for i in range(30)
        ]
        mom = m.compute_momentum(h, completions, today=today)
        assert mom.score > 95.0
        assert mom.completion_rate == 1.0
        assert mom.trend in {"↗", "→"}

    def test_recovery_trend_after_gap(self) -> None:
        today = date(2026, 4, 13)
        start = today - timedelta(days=29)
        h = Habit(name="Rec", frequency="daily", created_at=start)
        # Miss first 25 days, complete last 5 -> score is actively climbing
        completions = [
            Completion(habit_id=1, date=start + timedelta(days=i))
            for i in range(25, 30)
        ]
        mom = m.compute_momentum(h, completions, today=today)
        assert mom.trend == "↗"

    def test_declining_trend(self) -> None:
        today = date(2026, 4, 13)
        start = today - timedelta(days=29)
        h = Habit(name="Dec", frequency="daily", created_at=start)
        # Complete first 20, miss last 10
        completions = [
            Completion(habit_id=1, date=start + timedelta(days=i)) for i in range(20)
        ]
        mom = m.compute_momentum(h, completions, today=today)
        assert mom.trend == "↘"

    def test_target_habit_partial_entry(self) -> None:
        today = date(2026, 4, 13)
        start = today - timedelta(days=9)
        h = Habit(name="Read", frequency="daily", unit="pages", target=20, created_at=start)
        # 10 days of half-target entries
        completions = [
            Completion(habit_id=1, date=start + timedelta(days=i), value=10)
            for i in range(10)
        ]
        mom = m.compute_momentum(h, completions, today=today)
        assert mom.completion_rate == pytest.approx(0.5)
        # EMA score for steady 0.5 strength approaches 50
        assert 40.0 < mom.score < 55.0

    def test_monthly_first(self) -> None:
        h = Habit(name="M", frequency="monthly")
        assert h.is_scheduled_on(date(2026, 4, 1)) is True
        assert h.is_scheduled_on(date(2026, 4, 2)) is False

    def test_monthly_specific_day(self) -> None:
        h = Habit(name="M", frequency="monthly:15")
        assert h.is_scheduled_on(date(2026, 4, 15)) is True
        assert h.is_scheduled_on(date(2026, 4, 16)) is False

    def test_monthly_day_clamps_to_month_length(self) -> None:
        h = Habit(name="M", frequency="monthly:31")
        # Feb 2026 has 28 days -> clamped to Feb 28
        assert h.is_scheduled_on(date(2026, 2, 28)) is True
        assert h.is_scheduled_on(date(2026, 2, 27)) is False
        # April has 30 days -> clamped to the 30th
        assert h.is_scheduled_on(date(2026, 4, 30)) is True
        assert h.is_scheduled_on(date(2026, 4, 29)) is False

    def test_monthly_last(self) -> None:
        h = Habit(name="M", frequency="monthly:last")
        assert h.is_scheduled_on(date(2026, 2, 28)) is True
        assert h.is_scheduled_on(date(2026, 3, 31)) is True
        assert h.is_scheduled_on(date(2026, 4, 30)) is True
        assert h.is_scheduled_on(date(2026, 4, 29)) is False

    def test_every_n_days_calendar_anchored(self) -> None:
        start = date(2026, 4, 1)
        h = Habit(name="E", frequency="every:3", created_at=start)
        assert h.is_scheduled_on(start) is True
        assert h.is_scheduled_on(start + timedelta(days=3)) is True
        assert h.is_scheduled_on(start + timedelta(days=2)) is False
        assert h.is_scheduled_on(start + timedelta(days=9)) is True
        # Before created_at -> never scheduled
        assert h.is_scheduled_on(start - timedelta(days=1)) is False

    def test_seasonal_window_before_start_not_scheduled(self) -> None:
        h = Habit(
            name="Summer",
            frequency="daily",
            start_date=date(2026, 6, 1),
            end_date=date(2026, 8, 31),
        )
        assert h.is_scheduled_on(date(2026, 5, 31)) is False
        assert h.is_scheduled_on(date(2026, 6, 1)) is True
        assert h.is_scheduled_on(date(2026, 8, 31)) is True
        assert h.is_scheduled_on(date(2026, 9, 1)) is False

    def test_seasonal_window_only_start(self) -> None:
        h = Habit(name="S", frequency="daily", start_date=date(2026, 4, 15))
        assert h.is_scheduled_on(date(2026, 4, 14)) is False
        assert h.is_scheduled_on(date(2026, 4, 15)) is True
        assert h.is_scheduled_on(date(2030, 1, 1)) is True

    def test_monthly_in_scheduled_days_between(self) -> None:
        h = Habit(name="M", frequency="monthly:1")
        days = m.scheduled_days_between(h, date(2026, 1, 1), date(2026, 4, 30))
        assert days == [
            date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1), date(2026, 4, 1),
        ]

    def test_every_in_scheduled_days_between(self) -> None:
        h = Habit(name="E", frequency="every:2", created_at=date(2026, 4, 1))
        days = m.scheduled_days_between(h, date(2026, 4, 1), date(2026, 4, 8))
        assert days == [
            date(2026, 4, 1), date(2026, 4, 3), date(2026, 4, 5), date(2026, 4, 7),
        ]

    def test_respects_frequency_no_decay_on_unscheduled_days(self) -> None:
        today = date(2026, 4, 13)  # Monday
        start = today - timedelta(days=27)  # Two weeks prior, Tuesday
        h = Habit(
            name="MWF", frequency="mon,wed,fri", created_at=start
        )
        # Complete every scheduled day
        scheduled = m.scheduled_days_between(h, start, today)
        completions = [Completion(habit_id=1, date=d) for d in scheduled]
        mom = m.compute_momentum(h, completions, today=today)
        assert mom.score > 90.0
        assert mom.completion_rate == 1.0


# ---- compute_streaks ----------------------------------------------------------


class TestComputeStreaks:
    def test_no_scheduled_days(self) -> None:
        today = date(2026, 4, 13)
        h = Habit(name="X", frequency="daily", created_at=today + timedelta(days=1))
        assert m.compute_streaks(h, [], today=today) == (0, 0)

    def test_perfect_record(self) -> None:
        today = date(2026, 4, 13)
        start = today - timedelta(days=9)
        h = Habit(name="P", frequency="daily", created_at=start)
        comps = [Completion(habit_id=1, date=start + timedelta(days=i)) for i in range(10)]
        assert m.compute_streaks(h, comps, today=today) == (10, 10)

    def test_today_scheduled_but_not_done_does_not_reset(self) -> None:
        today = date(2026, 4, 13)
        start = today - timedelta(days=9)
        h = Habit(name="P", frequency="daily", created_at=start)
        # Done 9 days, today scheduled but no completion yet
        comps = [Completion(habit_id=1, date=start + timedelta(days=i)) for i in range(9)]
        cur, best = m.compute_streaks(h, comps, today=today)
        assert cur == 9
        assert best == 9

    def test_miss_breaks_streak(self) -> None:
        today = date(2026, 4, 13)
        start = today - timedelta(days=9)
        h = Habit(name="P", frequency="daily", created_at=start)
        # Miss day index 4, complete the rest
        comps = [
            Completion(habit_id=1, date=start + timedelta(days=i))
            for i in range(10) if i != 4
        ]
        cur, best = m.compute_streaks(h, comps, today=today)
        # After the miss: days 5..9 = 5 in a row including today
        assert cur == 5
        # Best run: days 0..3 (4) or 5..9 (5) -> 5
        assert best == 5

    def test_skipped_day_does_not_break_streak(self) -> None:
        today = date(2026, 4, 13)
        start = today - timedelta(days=4)
        h = Habit(name="P", frequency="daily", created_at=start)
        comps = [
            Completion(habit_id=1, date=start + timedelta(days=0)),
            Completion(habit_id=1, date=start + timedelta(days=1)),
            Completion(habit_id=1, date=start + timedelta(days=2), status="skipped"),
            Completion(habit_id=1, date=start + timedelta(days=3)),
            Completion(habit_id=1, date=start + timedelta(days=4)),
        ]
        cur, best = m.compute_streaks(h, comps, today=today)
        assert cur == 4
        assert best == 4

    def test_target_zero_value_breaks_streak(self) -> None:
        today = date(2026, 4, 13)
        start = today - timedelta(days=4)
        h = Habit(name="Read", frequency="daily", target=20, created_at=start)
        comps = [
            Completion(habit_id=1, date=start + timedelta(days=0), value=20),
            Completion(habit_id=1, date=start + timedelta(days=1), value=20),
            Completion(habit_id=1, date=start + timedelta(days=2), value=0),
            Completion(habit_id=1, date=start + timedelta(days=3), value=20),
            Completion(habit_id=1, date=start + timedelta(days=4), value=20),
        ]
        cur, best = m.compute_streaks(h, comps, today=today)
        assert cur == 2
        assert best == 2

    def test_partial_target_counts_toward_streak(self) -> None:
        today = date(2026, 4, 13)
        start = today - timedelta(days=2)
        h = Habit(name="Read", frequency="daily", target=20, created_at=start)
        comps = [
            Completion(habit_id=1, date=start + timedelta(days=i), value=5)
            for i in range(3)
        ]
        cur, best = m.compute_streaks(h, comps, today=today)
        assert cur == 3
        assert best == 3

    def test_best_exceeds_current(self) -> None:
        today = date(2026, 4, 13)
        start = today - timedelta(days=9)
        h = Habit(name="P", frequency="daily", created_at=start)
        # Days 0..6 done (7-day run), miss 7, done 8 and 9 (2-day current run)
        comps = [
            Completion(habit_id=1, date=start + timedelta(days=i))
            for i in [0, 1, 2, 3, 4, 5, 6, 8, 9]
        ]
        cur, best = m.compute_streaks(h, comps, today=today)
        assert cur == 2
        assert best == 7

    def test_only_unscheduled_days_excluded(self) -> None:
        # MWF habit. Only Mon/Wed/Fri count.
        today = date(2026, 4, 13)  # Monday
        start = today - timedelta(days=13)  # Tuesday two weeks prior
        h = Habit(name="MWF", frequency="mon,wed,fri", created_at=start)
        scheduled = m.scheduled_days_between(h, start, today)
        comps = [Completion(habit_id=1, date=d) for d in scheduled]
        cur, best = m.compute_streaks(h, comps, today=today)
        assert cur == len(scheduled)
        assert best == len(scheduled)
