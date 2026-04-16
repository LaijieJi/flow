"""Modal form for adding a new habit from the TUI."""

from __future__ import annotations

from pathlib import Path

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
]


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
    #custom-days-input {
        display: none;
    }
    #custom-days-input.visible {
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
        custom_input = self.query_one("#custom-days-input", Input)
        if event.value == "custom":
            custom_input.add_class("visible")
            custom_input.focus()
        else:
            custom_input.remove_class("visible")
            custom_input.value = ""

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
            )
            db.insert_habit(conn, habit)

        self.dismiss(habit)

    def action_cancel(self) -> None:
        self.dismiss(None)
