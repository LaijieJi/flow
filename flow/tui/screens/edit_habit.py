"""Modal form for editing an existing habit from the TUI."""

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

_KEYWORDS = {"daily", "weekdays", "weekly"}


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
    #edit-custom-days-input {
        display: none;
    }
    #edit-custom-days-input.visible {
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
        freq_choice = h.frequency if h.frequency in _KEYWORDS else "custom"
        custom_initial = "" if h.frequency in _KEYWORDS else h.frequency

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
                value=custom_initial,
                placeholder="e.g. mon,wed,fri",
                id="edit-custom-days-input",
                classes="visible" if freq_choice == "custom" else "",
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

            yield Static(
                "[dim]tab[/dim] next field   "
                "[dim]enter[/dim] save   "
                "[dim]escape[/dim] cancel",
                id="edit-form-hint",
            )

    def on_mount(self) -> None:
        self.query_one("#edit-name-input", Input).focus()

    def on_select_changed(self, event: Select.Changed) -> None:
        custom_input = self.query_one("#edit-custom-days-input", Input)
        if event.value == "custom":
            custom_input.add_class("visible")
            custom_input.focus()
        else:
            custom_input.remove_class("visible")

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
            db.update_habit(conn, self.habit)

        self.dismiss(self.habit)

    def action_cancel(self) -> None:
        self.dismiss(None)
