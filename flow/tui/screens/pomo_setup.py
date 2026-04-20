"""Modal that collects pomodoro settings before starting the timer.

Keeps three editable fields pre-filled with the current defaults so the common
case — "just start a standard pomo" — is Enter → Enter. Returns a plain dict
on save or None on cancel. Pure input validation lives in `validate` so it can
be unit-tested without spinning up a ModalScreen.
"""

from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, Static

from ... import pomodoro
from ...models import Habit, parse_duration


@dataclass(slots=True)
class PomodoroSettings:
    work_seconds: int
    break_seconds: int
    cycles: int


def validate(work_str: str, break_str: str, cycles_str: str) -> PomodoroSettings:
    """Parse + validate the three raw field values. Raises ValueError with a
    user-facing message on any problem."""
    try:
        work = parse_duration(work_str)
    except ValueError:
        raise ValueError(f"invalid work duration: {work_str!r}")
    if work < 1:
        raise ValueError("work duration must be > 0")

    br = break_str.strip()
    if br in {"", "0", "0s", "0m"}:
        break_seconds = 0
    else:
        try:
            break_seconds = parse_duration(br)
        except ValueError:
            raise ValueError(f"invalid break duration: {break_str!r}")
    if break_seconds < 0:
        raise ValueError("break duration must be >= 0")

    try:
        cycles = int(cycles_str.strip())
    except ValueError:
        raise ValueError(f"cycles must be an integer: {cycles_str!r}")
    if cycles < 1:
        raise ValueError("cycles must be >= 1")

    return PomodoroSettings(
        work_seconds=work, break_seconds=break_seconds, cycles=cycles
    )


class PomodoroSetupScreen(ModalScreen[PomodoroSettings | None]):
    """Three-field form for work / break / cycles. Enter on any field saves."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    PomodoroSetupScreen {
        align: center middle;
    }
    #pomo-setup-form {
        background: $surface;
        border: thick $accent;
        padding: 1 2;
        width: 52;
        height: auto;
    }
    .field-label {
        margin-top: 1;
    }
    #pomo-setup-hint {
        margin-top: 1;
        color: $text-muted;
    }
    """

    def __init__(
        self,
        habit: Habit | None = None,
        work_default: str = "25m",
        break_default: str = "5m",
        cycles_default: int = pomodoro.DEFAULT_CYCLES,
    ) -> None:
        super().__init__()
        self.habit = habit
        self.work_default = work_default
        self.break_default = break_default
        self.cycles_default = str(cycles_default)

    def compose(self) -> ComposeResult:
        with Vertical(id="pomo-setup-form"):
            title = "Start pomodoro"
            if self.habit is not None:
                title += f" — [bold]{self.habit.name}[/bold]"
            else:
                title += " [dim](free timer — nothing logged)[/dim]"
            yield Label(title)

            yield Label("work duration", classes="field-label")
            yield Input(value=self.work_default, id="work-input", placeholder="25m")

            yield Label("break duration", classes="field-label")
            yield Input(
                value=self.break_default, id="break-input", placeholder="5m (0 to skip)"
            )

            yield Label("cycles", classes="field-label")
            yield Input(value=self.cycles_default, id="cycles-input", placeholder="1")

            yield Static(
                "[dim]tab[/dim] next field   "
                "[dim]enter[/dim] start   "
                "[dim]escape[/dim] cancel",
                id="pomo-setup-hint",
            )

    def on_mount(self) -> None:
        self.query_one("#work-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._try_save()

    def _try_save(self) -> None:
        work = self.query_one("#work-input", Input).value
        brk = self.query_one("#break-input", Input).value
        cyc = self.query_one("#cycles-input", Input).value
        try:
            settings = validate(work, brk, cyc)
        except ValueError as e:
            self.notify(str(e), severity="warning", timeout=3)
            return
        self.dismiss(settings)

    def action_cancel(self) -> None:
        self.dismiss(None)
