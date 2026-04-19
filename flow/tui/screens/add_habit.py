"""Modal form for adding a new habit from the TUI."""

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


def _parse_date_input(value: str, flag: str) -> date | None:
    s = value.strip()
    if not s:
        return None
    try:
        return dateparser.parse(s).date()
    except (ValueError, TypeError, OverflowError) as e:
        raise ValueError(f"invalid {flag}: {e}")


class AddHabitScreen(ModalScreen[Habit | None]):
    """Multi-field form for creating a habit. Dismisses with the new Habit on
    save, or None on cancel. Validates inline and shows notifications."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    AddHabitScreen {
        align: center middle;
    }
    #add-form {
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
    #custom-days-input,
    #monthly-day-input,
    #every-interval-input {
        display: none;
    }
    #custom-days-input.visible,
    #monthly-day-input.visible,
    #every-interval-input.visible {
        display: block;
    }
    #form-hint {
        margin-top: 1;
        color: $text-muted;
    }
    """

    def __init__(self, db_path: Path | None = None) -> None:
        super().__init__()
        self.db_path = db_path

    def compose(self) -> ComposeResult:
        with Vertical(id="add-form"):
            yield Label("[bold]Add new habit[/bold]")

            yield Label("name", classes="field-label")
            yield Input(
                placeholder="e.g. Exercise, Read 20 pages",
                id="name-input",
            )

            yield Label("frequency", classes="field-label")
            yield Select(
                FREQUENCY_OPTIONS,
                value="daily",
                id="freq-select",
                allow_blank=False,
            )
            yield Input(
                placeholder="e.g. mon,wed,fri",
                id="custom-days-input",
            )
            yield Input(
                placeholder="1–31 or 'last'",
                id="monthly-day-input",
            )
            yield Input(
                placeholder="N days between occurrences",
                id="every-interval-input",
            )

            yield Label("unit [dim](optional)[/dim]", classes="field-label")
            yield Input(
                placeholder="e.g. pages, minutes, reps",
                id="unit-input",
            )

            yield Label(
                "target [dim](optional, requires unit)[/dim]",
                classes="field-label",
            )
            yield Input(placeholder="e.g. 20", id="target-input")

            yield Label(
                "description [dim](optional)[/dim]", classes="field-label"
            )
            yield Input(placeholder="short description", id="desc-input")

            yield Label(
                "start date [dim](optional, YYYY-MM-DD)[/dim]",
                classes="field-label",
            )
            yield Input(placeholder="e.g. 2026-06-01", id="start-date-input")

            yield Label(
                "end date [dim](optional, YYYY-MM-DD)[/dim]",
                classes="field-label",
            )
            yield Input(placeholder="e.g. 2026-09-30", id="end-date-input")

            yield Static(
                "[dim]tab[/dim] next field   "
                "[dim]enter[/dim] save   "
                "[dim]escape[/dim] cancel",
                id="form-hint",
            )

    def on_mount(self) -> None:
        self.query_one("#name-input", Input).focus()

    # -- frequency select toggle -----------------------------------------------

    def on_select_changed(self, event: Select.Changed) -> None:
        mapping = {
            "custom": "#custom-days-input",
            "monthly": "#monthly-day-input",
            "every": "#every-interval-input",
        }
        for choice, selector in mapping.items():
            inp = self.query_one(selector, Input)
            if event.value == choice:
                inp.add_class("visible")
                inp.focus()
            else:
                inp.remove_class("visible")
                inp.value = ""

    # -- submit ----------------------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._try_save()

    def _try_save(self) -> None:
        name = self.query_one("#name-input", Input).value.strip()
        if not name:
            self.notify("name is required", severity="warning", timeout=3)
            self.query_one("#name-input", Input).focus()
            return

        freq_value = self.query_one("#freq-select", Select).value
        if freq_value == "custom":
            freq = self.query_one("#custom-days-input", Input).value.strip()
            if not freq:
                self.notify(
                    "enter custom days (e.g. mon,wed,fri)",
                    severity="warning",
                    timeout=3,
                )
                self.query_one("#custom-days-input", Input).focus()
                return
        elif freq_value == "monthly":
            payload = self.query_one("#monthly-day-input", Input).value.strip()
            if not payload:
                self.notify(
                    "enter a day (1–31 or 'last')",
                    severity="warning",
                    timeout=3,
                )
                self.query_one("#monthly-day-input", Input).focus()
                return
            freq = f"monthly:{payload}"
        elif freq_value == "every":
            payload = self.query_one("#every-interval-input", Input).value.strip()
            if not payload:
                self.notify(
                    "enter the interval in days",
                    severity="warning",
                    timeout=3,
                )
                self.query_one("#every-interval-input", Input).focus()
                return
            freq = f"every:{payload}"
        else:
            freq = str(freq_value)

        try:
            parse_frequency(freq)
        except ValueError as e:
            self.notify(str(e), severity="warning", timeout=3)
            return

        unit = self.query_one("#unit-input", Input).value.strip() or None
        target_str = self.query_one("#target-input", Input).value.strip()
        target: float | None = None
        if target_str:
            try:
                target = float(target_str)
            except ValueError:
                self.notify("target must be a number", severity="warning", timeout=3)
                self.query_one("#target-input", Input).focus()
                return

        if target is not None and unit is None:
            self.notify("target requires unit", severity="warning", timeout=3)
            self.query_one("#unit-input", Input).focus()
            return

        description = self.query_one("#desc-input", Input).value.strip() or None

        try:
            start_date = _parse_date_input(
                self.query_one("#start-date-input", Input).value, "start date"
            )
            end_date = _parse_date_input(
                self.query_one("#end-date-input", Input).value, "end date"
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
            self.query_one("#end-date-input", Input).focus()
            return

        with db.session(self.db_path) as conn:
            existing = db.find_habit_by_name(conn, name)
            if existing is not None:
                self.notify(
                    f"habit '{name}' already exists",
                    severity="warning",
                    timeout=3,
                )
                self.query_one("#name-input", Input).focus()
                return
            habit = Habit(
                name=name,
                frequency=freq,
                unit=unit,
                target=target,
                description=description,
                start_date=start_date,
                end_date=end_date,
            )
            db.insert_habit(conn, habit)

        self.dismiss(habit)

    def action_cancel(self) -> None:
        self.dismiss(None)
