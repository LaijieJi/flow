"""Row widget for a single habit in the daily check-in list.

The row packs four signals into a single line so the user can scan today's
state without drilling into stats:

  [strip]  ●  Name (freq)  ▰▰▰▱▱  15 pages / 20  25m  — note

  - strip:    last 7 scheduled days, glyph per day (full/partial/miss/skip).
  - mark:     completion mark, color-tiered by strength (full/partial/zero).
  - bar:      proportional fill for habits with a target (target-only).
  - tail:     value/duration/note in their existing color codes.
"""

from __future__ import annotations

from datetime import date, timedelta

from textual.app import ComposeResult
from textual.widgets import ListItem, Static

from ...models import Completion, Habit, format_duration
from ...momentum import completion_strength


RECENT_DAYS = 7

# Strip glyphs — chosen so all are single-cell in common monospace fonts.
GLYPH_FULL = "▇"
GLYPH_PARTIAL = "▃"
GLYPH_MISS = "·"
GLYPH_SKIP = "▪"
GLYPH_OFF = " "

# Progress-bar glyphs.
BAR_FILLED = "▰"
BAR_EMPTY = "▱"
BAR_CELLS = 5


def _progress_bar(value: float | None, target: float, cells: int = BAR_CELLS) -> str:
    if target <= 0:
        return BAR_EMPTY * cells
    if value is None:
        ratio = 1.0
    else:
        ratio = max(0.0, min(value / target, 1.0))
    filled = round(ratio * cells)
    return BAR_FILLED * filled + BAR_EMPTY * (cells - filled)


class HabitRow(ListItem):
    """A single habit in the check list. Holds mutable completion state and
    re-renders its label when toggled."""

    def __init__(
        self,
        habit: Habit,
        today: date,
        completion: Completion | None = None,
        recent: list[Completion] | None = None,
    ) -> None:
        super().__init__()
        self.habit = habit
        self.today = today
        self.completion = completion
        self.recent = recent or []
        self.scheduled = habit.is_scheduled_on(today)
        self.add_class("unscheduled" if not self.scheduled else "scheduled")

    def compose(self) -> ComposeResult:
        yield Static(self._render_text(), markup=True, id="row-label")

    def refresh_text(self) -> None:
        self.query_one("#row-label", Static).update(self._render_text())
        c = self.completion
        is_done_state = c is not None and not c.is_skipped
        is_skipped_state = c is not None and c.is_skipped
        self.set_class(is_done_state, "done")
        self.set_class(is_skipped_state, "skipped")

    # -- rendering helpers -----------------------------------------------------

    def _render_strip(self) -> str:
        h = self.habit
        comps_by_date = {c.date: c for c in self.recent}
        cells: list[str] = []
        for i in range(RECENT_DAYS, 0, -1):
            d = self.today - timedelta(days=i)
            if d < h.created_at:
                cells.append(GLYPH_OFF)
                continue
            c = comps_by_date.get(d)
            if c is not None and c.is_skipped:
                cells.append(f"[yellow]{GLYPH_SKIP}[/yellow]")
                continue
            if not h.is_scheduled_on(d):
                cells.append(f"[dim]{GLYPH_MISS}[/dim]")
                continue
            if c is None:
                cells.append(f"[red dim]{GLYPH_MISS}[/red dim]")
                continue
            s = completion_strength(c.value, h.target)
            if s >= 1.0:
                cells.append(f"[green]{GLYPH_FULL}[/green]")
            elif s > 0:
                cells.append(f"[green dim]{GLYPH_PARTIAL}[/green dim]")
            else:
                cells.append(f"[red dim]{GLYPH_MISS}[/red dim]")
        return "".join(cells)

    def _render_text(self) -> str:
        h = self.habit
        strip = self._render_strip()
        if not self.scheduled:
            return (
                f"[dim]{strip}[/dim]  [dim]–  {h.name}[/dim]  "
                f"[dim italic]({h.frequency}) not scheduled[/dim italic]"
            )

        c = self.completion
        if c is not None and c.is_skipped:
            mark = "[yellow]⊘[/yellow]"
        elif c is not None:
            s = completion_strength(c.value, h.target)
            if s >= 1.0:
                mark = "[bold green]●[/bold green]"
            elif s > 0:
                mark = "[yellow]◐[/yellow]"
            else:
                mark = "[red]●[/red]"
        else:
            mark = "[dim]○[/dim]"

        parts: list[str] = [
            f"{strip}  {mark}  [bold]{h.name}[/bold]",
            f"  [dim]({h.frequency})[/dim]",
        ]

        if c is not None and c.is_skipped:
            parts.append("  [yellow]skipped[/yellow]")
            if c.note:
                note = c.note if len(c.note) <= 48 else c.note[:45] + "..."
                parts.append(f"  [dim italic]— {note}[/dim italic]")
            return "".join(parts)

        if c is not None:
            if c.value is not None:
                unit = f" {h.unit}" if h.unit else ""
                if h.target:
                    bar = _progress_bar(c.value, h.target)
                    parts.append(f"  [green]{bar}[/green]")
                    parts.append(f"  [green]{c.value:g}{unit} / {h.target:g}[/green]")
                else:
                    parts.append(f"  [green]{c.value:g}{unit}[/green]")
            else:
                if h.target:
                    bar = _progress_bar(None, h.target)
                    parts.append(f"  [green]{bar}[/green]")
                parts.append("  [green]✓ done[/green]")
            if c.duration_seconds is not None:
                parts.append(f"  [cyan]{format_duration(c.duration_seconds)}[/cyan]")
            if c.note:
                note = c.note if len(c.note) <= 48 else c.note[:45] + "..."
                parts.append(f"  [dim italic]— {note}[/dim italic]")
            return "".join(parts)

        # scheduled, not yet acted-on: show empty bar if there's a target so
        # the row width is stable across done/undone states.
        if h.target:
            bar = _progress_bar(0.0, h.target)
            unit = f" {h.unit}" if h.unit else ""
            parts.append(f"  [dim]{bar}[/dim]")
            parts.append(f"  [dim]0{unit} / {h.target:g}[/dim]")
        return "".join(parts)
