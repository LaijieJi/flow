"""Context-aware keybindings modal.

Triggered by `?` on any top-level screen. Lists the *current* screen's bindings
(including ones hidden from the footer), grouped into navigation vs actions
so the footer's flat strip isn't the only discovery surface."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static


_NAV_KEYS = {"c", "s", "l", "R", "escape", "q", "t", "h", "?"}


class BindingsScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("question_mark", "close", "Close"),
        Binding("q", "close", "Close"),
    ]

    DEFAULT_CSS = """
    BindingsScreen {
        align: center middle;
    }
    #bindings-box {
        background: $surface;
        border: thick $accent;
        padding: 1 2;
        width: 70%;
        max-width: 70;
        height: 80%;
    }
    #bindings-body {
        padding: 0 1;
    }
    #bindings-hint {
        margin-top: 1;
        color: $text-muted;
    }
    """

    def __init__(self, source_bindings: list, source_name: str) -> None:
        super().__init__()
        self.source_bindings = source_bindings
        self.source_name = source_name

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="bindings-box"):
            yield Static(self._build_body(), id="bindings-body", markup=True)
            yield Static(
                "[dim]escape / ? to close[/dim]",
                id="bindings-hint",
                markup=True,
            )

    def _build_body(self) -> str:
        # Bindings may be stored as Binding instances or as raw tuples
        # depending on the screen's declaration style — normalise to a
        # uniform (key, description) shape.
        nav: list[tuple[str, str]] = []
        actions: list[tuple[str, str]] = []
        for b in self.source_bindings:
            key = getattr(b, "key", None) or (b[0] if isinstance(b, tuple) else None)
            desc = getattr(b, "description", "") or (
                b[2] if isinstance(b, tuple) and len(b) > 2 else ""
            )
            if not key or not desc:
                continue
            bucket = nav if key in _NAV_KEYS else actions
            bucket.append((key, desc))

        lines = [f"[bold]{self.source_name}[/bold] — keybindings", ""]
        if actions:
            lines.append("[bold cyan]actions[/bold cyan]")
            for key, desc in actions:
                lines.append(f"  [cyan]{key:<10}[/cyan] {desc}")
            lines.append("")
        if nav:
            lines.append("[bold cyan]navigation[/bold cyan]")
            for key, desc in nav:
                lines.append(f"  [cyan]{key:<10}[/cyan] {desc}")
        return "\n".join(lines)

    def action_close(self) -> None:
        self.dismiss(None)
