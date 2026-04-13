"""Modal input prompt used for value / note entry."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label


class PromptScreen(ModalScreen[str | None]):
    """Single-line modal input. Dismisses with the entered text on submit,
    or None on escape. Keeps the surface minimal — no validation, no submit
    button; just type and enter."""

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=True)]

    DEFAULT_CSS = """
    PromptScreen {
        align: center middle;
    }
    PromptScreen > Vertical {
        background: $surface;
        border: thick $accent;
        padding: 1 2;
        width: 60;
        height: auto;
    }
    PromptScreen Label {
        margin-bottom: 1;
    }
    """

    def __init__(
        self,
        title: str,
        initial: str = "",
        placeholder: str = "",
    ) -> None:
        super().__init__()
        self.title_text = title
        self.initial = initial
        self.placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self.title_text)
            yield Input(
                value=self.initial,
                placeholder=self.placeholder,
                id="prompt-input",
            )

    def on_mount(self) -> None:
        self.query_one("#prompt-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)
