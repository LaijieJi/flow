"""Year completion heatmap — 53-week grid, rows = weekdays, cols = weeks.

Right-anchored on the current week. Future days within the current week render
blank. Reuses `render_block` from CompletionGrid so glyph/tier semantics stay
in lock-step with the 30-day view."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable

from textual.widgets import Static

from ...models import Completion, Habit
from ...momentum import completion_strength
from .completion_grid import render_block


def render_year_rows(
    habit: Habit,
    completions: Iterable[Completion],
    today: date | None = None,
    weeks: int = 53,
) -> list[str]:
    """Build the 7 row strings (one per weekday, Mon top) for the heatmap.

    Cells before `habit.created_at` and cells after `today` (future days in
    the current week) render as a single blank space."""
    today = today or date.today()
    comps_list = list(completions)
    strengths = {
        c.date: completion_strength(c.value, habit.target)
        for c in comps_list
        if not c.is_skipped
    }
    skips = {c.date for c in comps_list if c.is_skipped}

    monday_this_week = today - timedelta(days=today.weekday())
    rows: list[str] = []
    for r in range(7):
        cells: list[str] = []
        for c in range(weeks):
            weeks_back = (weeks - 1) - c
            d = monday_this_week - timedelta(weeks=weeks_back) + timedelta(days=r)
            if d > today:
                cells.append(" ")
                continue
            cells.append(render_block(habit, d, strengths, skips))
        rows.append("".join(cells))
    return rows


class YearHeatmap(Static):
    """53-week × 7-day completion grid for a single habit."""

    DEFAULT_CSS = """
    YearHeatmap {
        height: auto;
        padding: 0 1;
        border-title-align: left;
        border-subtitle-align: right;
    }
    """

    DEFAULT_WEEKS = 53
    LEGEND = "▓ done  ▒ partial  ░ miss  ▪ skip"

    def set_habit(
        self,
        habit: Habit,
        completions: Iterable[Completion],
        weeks: int = DEFAULT_WEEKS,
        today: date | None = None,
    ) -> None:
        rows = render_year_rows(habit, completions, today=today, weeks=weeks)
        self.border_title = f"{habit.name} — past year"
        self.border_subtitle = self.LEGEND
        self.update("\n".join(rows))
