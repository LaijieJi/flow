"""Row widget for a single habit in the daily check-in list."""

from __future__ import annotations

from datetime import date

from textual.app import ComposeResult
from textual.widgets import ListItem, Static

from ...models import Completion, Habit, format_duration


class HabitRow(ListItem):
    """A single habit in the check list. Holds mutable completion state and
    re-renders its label when toggled."""

    def __init__(self, habit: Habit, today: date, completion: Completion | None = None) -> None:
        super().__init__()
        self.habit = habit
        self.today = today
        self.completion = completion
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

    def _render_text(self) -> str:
        h = self.habit
        if not self.scheduled:
            return (
                f"[dim]–  {h.name}[/dim]  [dim italic]({h.frequency}) "
                f"not scheduled[/dim italic]"
            )
        c = self.completion
        if c is not None and c.is_skipped:
            mark = "[yellow]⊘[/yellow]"
        elif c is not None:
            mark = "[green]●[/green]"
        else:
            mark = "○"
        parts: list[str] = [f"{mark}  {h.name}", f"  [dim]({h.frequency})[/dim]"]
        if c is not None and c.is_skipped:
            parts.append("  [yellow]skipped[/yellow]")
            if c.note:
                note = c.note if len(c.note) <= 48 else c.note[:45] + "..."
                parts.append(f"  [dim italic]— {note}[/dim italic]")
        elif c is not None:
            if c.value is not None:
                unit = f" {h.unit}" if h.unit else ""
                if h.target:
                    parts.append(f"  [green]{c.value:g}{unit} / {h.target:g}[/green]")
                else:
                    parts.append(f"  [green]{c.value:g}{unit}[/green]")
            else:
                parts.append("  [green]✓ done[/green]")
            if c.duration_seconds is not None:
                parts.append(f"  [cyan]{format_duration(c.duration_seconds)}[/cyan]")
            if c.note:
                note = c.note if len(c.note) <= 48 else c.note[:45] + "..."
                parts.append(f"  [dim italic]— {note}[/dim italic]")
        return "".join(parts)
