"""Modal form for editing an existing habit from the TUI."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from dateutil import parser as dateparser
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, Select, Static

from ... import db
from ...models import Habit, parse_frequency


FREQUENCY_OPTIONS: list[tuple[str, str]] = [
    ("Daily", "daily"),
    ("Weekdays (Mon–Fri)", "weekdays"),
    ("Weekly (Monday)", "weekly"),
    ("Custom days...", "custom"),
    ("Monthly...", "monthly"),
    ("Every N days...", "every"),
]

_KEYWORDS = {"daily", "weekdays", "weekly"}


def _classify_frequency(freq: str) -> tuple[str, str]:
    """Map a stored frequency string to (select_choice, secondary_value)."""
    f = freq.strip().lower()
    if f in _KEYWORDS:
        return f, ""
    if f == "monthly":
        return "monthly", "1"
    if f.startswith("monthly:"):
        return "monthly", f[len("monthly:"):]
    if f.startswith("every:"):
        return "every", f[len("every:"):]
    return "custom", freq


def _parse_date_input(value: str, flag: str) -> date | None:
    s = value.strip()
    if not s:
        return None
    try:
        return dateparser.parse(s).date()
    except (ValueError, TypeError, OverflowError) as e:
        raise ValueError(f"invalid {flag}: {e}")


class EditHabitScreen(ModalScreen[Habit | None]):
    """Edit a habit. Dismisses with the updated Habit on save, or None on cancel."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    EditHabitScreen {
        align: center middle;
    }
    #edit-form {
        background: $surface;
        border: thick $accent;
        padding: 1 2;
        width: 65;
        height: auto;
        max-height: 90%;
    }
    .field-label {
        margin-top: 1;
        color: $text;
    }
    #edit-custom-days-input,
    #edit-monthly-day-input,
    #edit-every-interval-input {
        display: none;
    }
    #edit-custom-days-input.visible,
    #edit-monthly-day-input.visible,
    #edit-every-interval-input.visible {
        display: block;
    }
    #edit-form-hint {
        margin-top: 1;
        color: $text-muted;
    }
    """

    def __init__(self, habit: Habit, db_path: Path | None = None) -> None:
        super().__init__()
        self.habit = habit
        self.db_path = db_path

    def compose(self) -> ComposeResult:
        h = self.habit
        freq_choice, secondary = _classify_frequency(h.frequency)

        with Vertical(id="edit-form"):
            yield Label(f"[bold]Edit habit[/bold] — {h.name}")

            yield Label("name", classes="field-label")
            yield Input(value=h.name, id="edit-name-input")

            yield Label("frequency", classes="field-label")
            yield Select(
                FREQUENCY_OPTIONS,
                value=freq_choice,
                id="edit-freq-select",
                allow_blank=False,
            )
            yield Input(
                value=secondary if freq_choice == "custom" else "",
                placeholder="e.g. mon,wed,fri",
                id="edit-custom-days-input",
                classes="visible" if freq_choice == "custom" else "",
            )
            yield Input(
                value=secondary if freq_choice == "monthly" else "",
                placeholder="1–31 or 'last'",
                id="edit-monthly-day-input",
                classes="visible" if freq_choice == "monthly" else "",
            )
            yield Input(
                value=secondary if freq_choice == "every" else "",
                placeholder="N days between occurrences",
                id="edit-every-interval-input",
                classes="visible" if freq_choice == "every" else "",
            )

            yield Label("unit [dim](optional)[/dim]", classes="field-label")
            yield Input(value=h.unit or "", id="edit-unit-input")

            yield Label(
                "target [dim](optional, requires unit)[/dim]",
                classes="field-label",
            )
            yield Input(
                value="" if h.target is None else f"{h.target:g}",
                id="edit-target-input",
            )

            yield Label(
                "description [dim](optional)[/dim]", classes="field-label"
            )
            yield Input(value=h.description or "", id="edit-desc-input")

            yield Label(
                "start date [dim](optional, YYYY-MM-DD)[/dim]",
                classes="field-label",
            )
            yield Input(
                value=h.start_date.isoformat() if h.start_date else "",
                placeholder="e.g. 2026-06-01",
                id="edit-start-date-input",
            )

            yield Label(
                "end date [dim](optional, YYYY-MM-DD)[/dim]",
                classes="field-label",
            )
            yield Input(
                value=h.end_date.isoformat() if h.end_date else "",
                placeholder="e.g. 2026-09-30",
                id="edit-end-date-input",
            )

            yield Static(
                "[dim]tab[/dim] next field   "
                "[dim]enter[/dim] save   "
                "[dim]escape[/dim] cancel",
                id="edit-form-hint",
            )

    def on_mount(self) -> None:
        self.query_one("#edit-name-input", Input).focus()

    def on_select_changed(self, event: Select.Changed) -> None:
        mapping = {
            "custom": "#edit-custom-days-input",
            "monthly": "#edit-monthly-day-input",
            "every": "#edit-every-interval-input",
        }
        for choice, selector in mapping.items():
            inp = self.query_one(selector, Input)
            if event.value == choice:
                inp.add_class("visible")
                inp.focus()
            else:
                inp.remove_class("visible")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._try_save()

    def _try_save(self) -> None:
        name = self.query_one("#edit-name-input", Input).value.strip()
        if not name:
            self.notify("name is required", severity="warning", timeout=3)
            self.query_one("#edit-name-input", Input).focus()
            return

        freq_value = self.query_one("#edit-freq-select", Select).value
        if freq_value == "custom":
            freq = self.query_one("#edit-custom-days-input", Input).value.strip()
            if not freq:
                self.notify(
                    "enter custom days (e.g. mon,wed,fri)",
                    severity="warning",
                    timeout=3,
                )
                self.query_one("#edit-custom-days-input", Input).focus()
                return
        elif freq_value == "monthly":
            payload = self.query_one("#edit-monthly-day-input", Input).value.strip()
            if not payload:
                self.notify(
                    "enter a day (1–31 or 'last')",
                    severity="warning",
                    timeout=3,
                )
                self.query_one("#edit-monthly-day-input", Input).focus()
                return
            freq = f"monthly:{payload}"
        elif freq_value == "every":
            payload = self.query_one("#edit-every-interval-input", Input).value.strip()
            if not payload:
                self.notify(
                    "enter the interval in days",
                    severity="warning",
                    timeout=3,
                )
                self.query_one("#edit-every-interval-input", Input).focus()
                return
            freq = f"every:{payload}"
        else:
            freq = str(freq_value)

        try:
            parse_frequency(freq)
        except ValueError as e:
            self.notify(str(e), severity="warning", timeout=3)
            return

        unit = self.query_one("#edit-unit-input", Input).value.strip() or None
        target_str = self.query_one("#edit-target-input", Input).value.strip()
        target: float | None = None
        if target_str:
            try:
                target = float(target_str)
            except ValueError:
                self.notify("target must be a number", severity="warning", timeout=3)
                self.query_one("#edit-target-input", Input).focus()
                return

        if target is not None and unit is None:
            self.notify("target requires unit", severity="warning", timeout=3)
            self.query_one("#edit-unit-input", Input).focus()
            return

        description = self.query_one("#edit-desc-input", Input).value.strip() or None

        try:
            start_date = _parse_date_input(
                self.query_one("#edit-start-date-input", Input).value, "start date"
            )
            end_date = _parse_date_input(
                self.query_one("#edit-end-date-input", Input).value, "end date"
            )
        except ValueError as e:
            self.notify(str(e), severity="warning", timeout=3)
            return

        if start_date is not None and end_date is not None and end_date < start_date:
            self.notify(
                "end date cannot be before start date",
                severity="warning",
                timeout=3,
            )
            self.query_one("#edit-end-date-input", Input).focus()
            return

        with db.session(self.db_path) as conn:
            if name.lower() != self.habit.name.lower():
                clash = db.find_habit_by_name(conn, name)
                if clash is not None and clash.id != self.habit.id:
                    self.notify(
                        f"habit '{name}' already exists",
                        severity="warning",
                        timeout=3,
                    )
                    self.query_one("#edit-name-input", Input).focus()
                    return
            self.habit.name = name
            self.habit.frequency = freq
            self.habit.unit = unit
            self.habit.target = target
            self.habit.description = description
            self.habit.start_date = start_date
            self.habit.end_date = end_date
            db.update_habit(conn, self.habit)

        self.dismiss(self.habit)

    def action_cancel(self) -> None:
        self.dismiss(None)
