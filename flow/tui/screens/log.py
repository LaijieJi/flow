"""Chronological completion history, mirroring `flow log`."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Label

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
        Binding("t", "toggle_theme", "Theme"),
        Binding("h", "help", "Help"),
    ]

    DEFAULT_CSS = """
    LogScreen {
        align: center top;
    }
    #log-title {
        margin: 1 2;
    }
    DataTable {
        margin: 0 2 1 2;
        height: auto;
        max-height: 85%;
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
        yield Label("", id="log-title")
        yield DataTable(id="log-table", cursor_type="row", zebra_stripes=False)
        yield Footer()

    def on_mount(self) -> None:
        self.title = "flow log"
        table = self.query_one(DataTable)
        table.add_columns("date", "habit", "value", "time", "note")
        self._load()

    def _load(self) -> None:
        since = self.today - timedelta(days=self.days - 1)
        self.query_one("#log-title", Label).update(
            f"log — last {self.days} days"
        )
        with db.session(self.db_path) as conn:
            pairs = db.all_completions(conn, since=since)

        table = self.query_one(DataTable)
        table.clear()
        if not pairs:
            table.add_row("—", "no completions in window", "", "", "")
            return
        for c, h in pairs:
            if c.value is not None:
                val = f"{c.value:g}"
                if h.unit:
                    val += f" {h.unit}"
            else:
                val = "✓"
            note = c.note or ""
            if len(note) > 60:
                note = note[:57] + "..."
            table.add_row(
                c.date.isoformat(),
                h.name,
                val,
                format_duration(c.duration_seconds),
                note,
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
