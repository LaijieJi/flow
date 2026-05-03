"""30-day completion grid widget.

Renders a horizontal run of block glyphs, one per day, as qualitative signal
for consistency. Not color-coded shame — just subtle dimming of misses vs.
completions. Exposed as a Static so it slots into any screen layout.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable

from textual.widgets import Static

from ...models import Completion, Habit
from ...momentum import completion_strength


GLYPH_FULL = "▓"
GLYPH_PARTIAL = "▒"
GLYPH_MISS = "░"
GLYPH_SKIP = "▪"
GLYPH_UNSCHEDULED = "·"
GLYPH_NONE = " "


class CompletionGrid(Static):
    """Visual 30-day (or N-day) completion grid for a single habit.

    Sets its own border title/subtitle so the grid reads as a labeled panel
    when the host screen wraps it in a border (the typical layout)."""

    DEFAULT_CSS = """
    CompletionGrid {
        height: auto;
        padding: 0 1;
        border-title-align: left;
        border-subtitle-align: right;
    }
    """

    DEFAULT_WINDOW = 30
    LEGEND = "▓ done  ▒ partial  ░ miss  ▪ skip"

    def set_habit(
        self,
        habit: Habit,
        completions: Iterable[Completion],
        window: int = DEFAULT_WINDOW,
        today: date | None = None,
    ) -> None:
        today = today or date.today()
        start = today - timedelta(days=window - 1)
        comps_list = list(completions)
        strengths = {
            c.date: completion_strength(c.value, habit.target)
            for c in comps_list
            if not c.is_skipped
        }
        skips = {c.date for c in comps_list if c.is_skipped}
        blocks = [
            render_block(habit, start + timedelta(days=i), strengths, skips)
            for i in range(window)
        ]
        self.border_title = f"{habit.name} — past {window} days"
        self.border_subtitle = self.LEGEND
        self.update("".join(blocks))


def render_block(
    habit: Habit,
    day: date,
    strengths: dict[date, float],
    skips: set[date] | None = None,
) -> str:
    """Return the markup fragment for a single day's block."""
    if day < habit.created_at:
        return GLYPH_NONE
    if not habit.is_scheduled_on(day):
        return f"[dim]{GLYPH_UNSCHEDULED}[/dim]"
    if skips and day in skips:
        return f"[yellow]{GLYPH_SKIP}[/yellow]"
    s = strengths.get(day, 0.0)
    if s >= 1.0:
        return GLYPH_FULL
    if s > 0:
        return f"[dim]{GLYPH_PARTIAL}[/dim]"
    return f"[dim]{GLYPH_MISS}[/dim]"
