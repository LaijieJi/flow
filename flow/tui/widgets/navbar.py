"""Top-of-screen navigation strip for the TUI.

Renders the three top-level screens (check / stats / log) as tab labels with
their activation key highlighted. The current screen is visually accented so
the user always knows where they are without reading the title bar.

Kept intentionally quiet — this is a location indicator, not a toolbar.
"""

from __future__ import annotations

from textual.widgets import Static


NAV_TABS: list[tuple[str, str, str]] = [
    # (target name, activation key, label)
    ("check", "c", "check"),
    ("stats", "s", "stats"),
    ("log", "l", "log"),
    ("review", "R", "review"),
]


def render_navbar(current: str) -> str:
    """Rich-markup tab strip with `current` highlighted."""
    cells: list[str] = []
    for target, key, label in NAV_TABS:
        if target == current:
            cells.append(
                f"[reverse b] {label} [/reverse b] [dim]\\[{key}][/dim]"
            )
        else:
            cells.append(f"[b]\\[{key}][/b] [dim]{label}[/dim]")
    return "   ".join(cells)


class NavBar(Static):
    """Static tab strip. Set `current` at construction time to pick which tab
    is highlighted."""

    DEFAULT_CSS = """
    NavBar {
        height: 1;
        padding: 0 2;
        margin: 0;
        background: $boost;
    }
    """

    def __init__(self, current: str, **kwargs) -> None:
        self.current = current
        self.rendered_text = render_navbar(current)
        super().__init__(self.rendered_text, markup=True, **kwargs)
