"""Row widget for a single habit in the daily check-in list."""

from __future__ import annotations

from datetime import date

from textual.app import ComposeResult
from textual.widgets import ListItem, Static

from ...models import Completion, Habit


class HabitRow(ListItem):
    """A single habit in the check list. Holds mutable completion state and
    re-renders its label when toggled."""

    def __init__(self, habit: Habit, today: date, completion: Completion | None = None) -> None:
        super().__init__()
        self.habit = habit
        self.today = today
        self.completion = completion
        self.scheduled = habit.is_scheduled_on(today)
        if not self.scheduled:
            self.disabled = True
        self.add_class("unscheduled" if not self.scheduled else "scheduled")

    def compose(self) -> ComposeResult:
        yield Static(self._render_text(), markup=True, id="row-label")

    def refresh_text(self) -> None:
        self.query_one("#row-label", Static).update(self._render_text())
        done = self.completion is not None
        self.set_class(done, "done")

    def _render_text(self) -> str:
        h = self.habit
        if not self.scheduled:
            return (
                f"[dim]–  {h.name}[/dim]  [dim italic]({h.frequency}) "
                f"not scheduled[/dim italic]"
            )
        c = self.completion
        mark = "[green]●[/green]" if c else "○"
        parts: list[str] = [f"{mark}  {h.name}", f"  [dim]({h.frequency})[/dim]"]
        if c is not None:
            if c.value is not None:
                unit = f" {h.unit}" if h.unit else ""
                if h.target:
                    parts.append(f"  [green]{c.value:g}{unit} / {h.target:g}[/green]")
                else:
                    parts.append(f"  [green]{c.value:g}{unit}[/green]")
            else:
                parts.append("  [green]✓ done[/green]")
            if c.note:
                note = c.note if len(c.note) <= 48 else c.note[:45] + "..."
                parts.append(f"  [dim italic]— {note}[/dim italic]")
        return "".join(parts)
