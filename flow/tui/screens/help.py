"""Help modal — keybinding + command reference, same content as `flow help`."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from ...help_text import render_rich


class HelpScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close"),
        Binding("h", "close", "Close"),
    ]

    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
    }
    #help-box {
        background: $surface;
        border: thick $accent;
        padding: 1 2;
        width: 80%;
        max-width: 90;
        height: 80%;
    }
    #help-body {
        padding: 0 1;
    }
    #help-hint {
        margin-top: 1;
        color: $text-muted;
    }
    """

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="help-box"):
            yield Static(render_rich(), id="help-body", markup=True)
            yield Static(
                "[dim]escape / h / q to close[/dim]", id="help-hint", markup=True
            )

    def action_close(self) -> None:
        self.dismiss(None)
