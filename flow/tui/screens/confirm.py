"""Minimal yes/no modal for destructive actions."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, Static


class ConfirmScreen(ModalScreen[bool]):
    """Dismisses with True on y/enter, False on n/escape."""

    BINDINGS = [
        Binding("y", "confirm", "Yes", show=True),
        Binding("enter", "confirm", "Yes", show=False),
        Binding("n", "cancel", "No", show=True),
        Binding("escape", "cancel", "No", show=False),
    ]

    DEFAULT_CSS = """
    ConfirmScreen {
        align: center middle;
    }
    ConfirmScreen > Vertical {
        background: $surface;
        border: thick $accent;
        padding: 1 2;
        width: 60;
        height: auto;
    }
    ConfirmScreen Label {
        margin-bottom: 1;
    }
    #confirm-hint {
        color: $text-muted;
    }
    """

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self.message)
            yield Static("[dim]y to confirm · n / escape to cancel[/dim]", id="confirm-hint", markup=True)

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
