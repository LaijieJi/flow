"""Tests for the TimeOfDayStrip widget — pure aggregation + render logic."""

from __future__ import annotations

from datetime import date, datetime

from flow.models import Completion
from flow.tui.widgets.time_of_day import (
    HOUR_BUCKETS,
    hour_buckets,
    peak_label,
    render_strip,
)


def _done(hour: int | None, day: int = 4) -> Completion:
    stamp = (
        None if hour is None else datetime(2026, 5, day, hour, 30, 0)
    )
    return Completion(
        habit_id=1, date=date(2026, 5, day), completed_at=stamp
    )


class TestHourBuckets:
    def test_empty_returns_zeros(self) -> None:
        assert hour_buckets([]) == [0] * HOUR_BUCKETS

    def test_single_completion_lands_in_correct_hour(self) -> None:
        buckets = hour_buckets([_done(9)])
        assert buckets[9] == 1
        assert sum(buckets) == 1

    def test_multiple_same_hour_accumulates(self) -> None:
        buckets = hour_buckets([_done(7, day=1), _done(7, day=2), _done(7, day=3)])
        assert buckets[7] == 3
        assert sum(buckets) == 3

    def test_null_completed_at_excluded(self) -> None:
        # Backfilled completions don't know the time of day.
        assert hour_buckets([_done(None)]) == [0] * HOUR_BUCKETS

    def test_skipped_excluded_even_with_stamp(self) -> None:
        skip = Completion(
            habit_id=1,
            date=date(2026, 5, 4),
            completed_at=datetime(2026, 5, 4, 9, 0, 0),
            status="skipped",
        )
        assert hour_buckets([skip]) == [0] * HOUR_BUCKETS

    def test_full_day_spread(self) -> None:
        comps = [_done(h, day=1 + h) for h in range(24)]
        buckets = hour_buckets(comps)
        assert buckets == [1] * 24


class TestPeakLabel:
    def test_empty_returns_blank(self) -> None:
        assert peak_label([0] * HOUR_BUCKETS) == ""

    def test_single_peak(self) -> None:
        buckets = [0] * HOUR_BUCKETS
        buckets[9] = 5
        assert peak_label(buckets) == "peak 09:00"

    def test_first_peak_wins_on_tie(self) -> None:
        buckets = [0] * HOUR_BUCKETS
        buckets[9] = 3
        buckets[18] = 3
        assert peak_label(buckets) == "peak 09:00"


class TestRenderStrip:
    def test_empty_shows_placeholder(self) -> None:
        out = render_strip([0] * HOUR_BUCKETS)
        assert "no time-of-day data yet" in out.plain

    def test_strip_width_matches_buckets_when_data_present(self) -> None:
        buckets = [0] * HOUR_BUCKETS
        buckets[9] = 1
        out = render_strip(buckets)
        assert len(out.plain) == HOUR_BUCKETS

    def test_zero_buckets_render_blank_when_at_least_one_nonzero(self) -> None:
        buckets = [0] * HOUR_BUCKETS
        buckets[9] = 1
        out = render_strip(buckets).plain
        assert out[0] == " "
        assert out[9] != " "
