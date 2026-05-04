"""Tests for the YearHeatmap widget — pure render logic only."""

from __future__ import annotations

import re
from datetime import date, timedelta

from flow.models import Completion, Habit
from flow.tui.widgets.completion_grid import (
    GLYPH_FULL,
    GLYPH_MISS,
    GLYPH_NONE,
    GLYPH_SKIP,
    GLYPH_UNSCHEDULED,
)
from flow.tui.widgets.year_heatmap import render_year_rows


_MARKUP_RE = re.compile(r"\[/?[^\]]+\]")


def _strip(rows: list[str]) -> list[str]:
    """Remove Rich markup so cell positions can be indexed by character."""
    return [_MARKUP_RE.sub("", r) for r in rows]


class TestRenderYearRows:
    def test_shape_is_seven_rows_by_weeks_cells(self) -> None:
        today = date(2026, 5, 4)  # Mon
        h = Habit(name="X", frequency="daily", created_at=today - timedelta(days=400))
        rows = _strip(render_year_rows(h, [], today=today, weeks=53))
        assert len(rows) == 7
        for r in rows:
            assert len(r) == 53

    def test_today_lands_at_rightmost_column_of_its_weekday_row(self) -> None:
        # Wednesday → row index 2 (Mon=0). Rightmost col = weeks-1.
        today = date(2026, 5, 6)  # Wed
        start = today - timedelta(days=10)
        h = Habit(name="X", frequency="daily", created_at=start)
        comps = [Completion(habit_id=1, date=today)]
        rows = _strip(render_year_rows(h, comps, today=today, weeks=4))
        assert rows[2][-1] == GLYPH_FULL

    def test_future_days_in_current_week_are_blank(self) -> None:
        # Today = Wed (row 2). Thu/Fri/Sat/Sun in current week (rows 3..6,
        # rightmost col) are future and should be a single blank space.
        today = date(2026, 5, 6)  # Wed
        h = Habit(name="X", frequency="daily", created_at=today - timedelta(days=30))
        rows = _strip(render_year_rows(h, [], today=today, weeks=4))
        for r in (3, 4, 5, 6):
            assert rows[r][-1] == " "

    def test_cells_before_created_at_are_blank_glyph(self) -> None:
        today = date(2026, 5, 4)  # Mon
        # Habit created 8 days ago — older cells should render GLYPH_NONE.
        h = Habit(name="X", frequency="daily", created_at=today - timedelta(days=8))
        rows = _strip(render_year_rows(h, [], today=today, weeks=4))
        # Leftmost column is 3 weeks before current week's Monday, i.e. 21 days
        # before today. That's well before created_at.
        assert rows[0][0] == GLYPH_NONE

    def test_missed_scheduled_day_renders_miss_glyph(self) -> None:
        today = date(2026, 5, 4)  # Mon
        h = Habit(name="X", frequency="daily", created_at=today - timedelta(days=20))
        rows = _strip(render_year_rows(h, [], today=today, weeks=4))
        # Today is rightmost col, row 0 (Mon). No completion → miss.
        assert rows[0][-1] == GLYPH_MISS

    def test_skipped_day_renders_skip_glyph(self) -> None:
        today = date(2026, 5, 4)  # Mon
        h = Habit(name="X", frequency="daily", created_at=today - timedelta(days=20))
        comps = [Completion(habit_id=1, date=today, status="skipped")]
        rows = _strip(render_year_rows(h, comps, today=today, weeks=4))
        assert rows[0][-1] == GLYPH_SKIP

    def test_unscheduled_weekday_renders_unscheduled_glyph(self) -> None:
        today = date(2026, 5, 4)  # Mon
        h = Habit(
            name="MWF", frequency="mon,wed,fri", created_at=today - timedelta(days=30)
        )
        rows = _strip(render_year_rows(h, [], today=today, weeks=4))
        # Tue/Thu/Sat/Sun are unscheduled — for any past column those cells
        # should be the unscheduled-dot glyph.
        for r in (1, 3, 5, 6):
            # second-to-last column = previous week, so all 7 days are past
            assert rows[r][-2] == GLYPH_UNSCHEDULED
