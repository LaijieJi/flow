"""Chronological completion history, mirroring `flow log`."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from ... import db
from ...models import format_duration
from ..widgets.navbar import NavBar


class LogScreen(Screen):
    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("q", "go_back", "Back"),
        Binding("bracket_left", "shorter", "-7d"),
        Binding("bracket_right", "longer", "+7d"),
        Binding("c", "nav_check", "Check", show=False),
        Binding("s", "nav_stats", "Stats", show=False),
        Binding("R", "nav_review", "Review", show=False),
        Binding("t", "toggle_theme", "Theme"),
        Binding("h", "help", "Help"),
    ]

    DEFAULT_CSS = """
    LogScreen {
        align: center top;
    }
    #log-header {
        border: round $accent;
        margin: 1 2 0 2;
        padding: 0 1;
        color: $text;
        height: auto;
    }
    DataTable {
        margin: 1 2 1 2;
        height: auto;
        max-height: 80%;
    }
    """

    def __init__(
        self,
        db_path: Path | None = None,
        today: date | None = None,
        days: int = 30,
    ) -> None:
        super().__init__()
        self.db_path = db_path
        self.today = today or date.today()
        self.days = days

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield NavBar(current="log")
        yield Static("", id="log-header", markup=True)
        yield DataTable(id="log-table", cursor_type="row", zebra_stripes=False)
        yield Footer()

    def on_mount(self) -> None:
        self.title = "flow log"
        table = self.query_one(DataTable)
        table.add_columns("date", "habit", "value", "time", "note")
        self._load()

    def _load(self) -> None:
        since = self.today - timedelta(days=self.days - 1)
        with db.session(self.db_path) as conn:
            pairs = db.all_completions(conn, since=since)

        done = sum(1 for c, _ in pairs if not c.is_skipped)
        skipped = sum(1 for c, _ in pairs if c.is_skipped)
        header = (
            f"[bold]log[/bold]  "
            f"[dim]last {self.days} days · {since:%b %d} → {self.today:%b %d}[/dim]   "
            f"[green]●[/green] [bold]{done}[/bold] done   "
            f"[yellow]⊘[/yellow] [bold]{skipped}[/bold] skipped   "
            f"[dim]· {len(pairs)} entries[/dim]"
        )
        self.query_one("#log-header", Static).update(header)

        table = self.query_one(DataTable)
        table.clear()
        if not pairs:
            table.add_row(
                Text("—", style="dim"),
                Text("no completions in window", style="dim italic"),
                "",
                "",
                "",
            )
            return
        for c, h in pairs:
            if c.is_skipped:
                value_cell = Text("⊘ skip", style="yellow")
            elif c.value is not None:
                txt = f"{c.value:g}"
                if h.unit:
                    txt += f" {h.unit}"
                value_cell = Text(txt, style="green")
            else:
                value_cell = Text("✓", style="green")
            note = c.note or ""
            if len(note) > 60:
                note = note[:57] + "..."
            note_cell = Text(note, style="dim italic") if note else Text("")
            time_str = format_duration(c.duration_seconds)
            time_cell = Text(time_str, style="cyan") if time_str else Text("")
            table.add_row(
                Text(c.date.isoformat(), style="dim"),
                Text(h.name),
                value_cell,
                time_cell,
                note_cell,
            )

    def action_go_back(self) -> None:
        if len(self.app.screen_stack) > 1:
            self.app.pop_screen()
        else:
            self.app.navigate_to("check")

    def action_nav_check(self) -> None:
        self.app.navigate_to("check")

    def action_nav_stats(self) -> None:
        self.app.navigate_to("stats")

    def action_nav_review(self) -> None:
        self.app.navigate_to("review")

    def action_toggle_theme(self) -> None:
        self.app.toggle_theme()

    def action_help(self) -> None:
        from .help import HelpScreen

        self.app.push_screen(HelpScreen())

    def action_shorter(self) -> None:
        self.days = max(7, self.days - 7)
        self._load()

    def action_longer(self) -> None:
        self.days = min(365, self.days + 7)
        self._load()
