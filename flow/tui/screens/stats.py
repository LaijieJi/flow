"""Stats overview screen — momentum dashboard for all active habits."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Label

from ... import db
from ...models import Completion, Habit
from ...momentum import compute_momentum, Momentum
from ..widgets.completion_grid import CompletionGrid
from ..widgets.navbar import NavBar


class StatsScreen(Screen):
    BINDINGS = [
        Binding("j", "cursor_down", "Down"),
        Binding("k", "cursor_up", "Up"),
        Binding("enter", "drill_down", "Details"),
        Binding("c", "nav_check", "Check", show=False),
        Binding("l", "nav_log", "Log", show=False),
        Binding("R", "nav_review", "Review", show=False),
        Binding("E", "open_export", "Export"),
        Binding("A", "toggle_archived", "Toggle archived"),
        Binding("t", "toggle_theme", "Theme"),
        Binding("h", "help", "Help"),
        Binding("escape", "back", "Back", show=False),
        Binding("q", "quit", "Quit"),
    ]

    DEFAULT_CSS = """
    StatsScreen {
        align: center top;
    }
    #stats-title {
        margin: 1 2;
    }
    DataTable {
        margin: 0 2;
        height: auto;
        max-height: 55%;
    }
    CompletionGrid {
        margin: 1 2 0 2;
        border: round $accent;
        padding: 0 1;
    }
    #grid-caption {
        margin: 0 2 1 2;
        color: $text-muted;
    }
    """

    def __init__(
        self,
        db_path: Path | None = None,
        today: date | None = None,
        focus_habit: str | None = None,
    ) -> None:
        super().__init__()
        self.db_path = db_path
        self.today = today or date.today()
        self.focus_habit = focus_habit
        self.include_archived = False
        self.habits: list[Habit] = []
        self._completions: dict[int, list[Completion]] = {}
        self._momentums: dict[int, Momentum] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield NavBar(current="stats")
        yield Label("stats — last 30 days", id="stats-title")
        yield DataTable(id="stats-table", cursor_type="row", zebra_stripes=False)
        yield CompletionGrid(id="grid")
        yield Label("", id="grid-caption")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "flow stats"
        table = self.query_one(DataTable)
        table.add_columns("habit", "score", "trend", "rate")
        self._load()

    def _load(self) -> None:
        with db.session(self.db_path) as conn:
            self.habits = db.list_habits(conn, include_archived=self.include_archived)
            self._completions = {
                h.id: db.completions_for_habit(conn, h.id) for h in self.habits
            }

        title = "stats — last 30 days"
        if self.include_archived:
            title += " [dim](incl. archived)[/dim]"
        self.query_one("#stats-title", Label).update(title)

        table = self.query_one(DataTable)
        table.clear()
        self._momentums.clear()

        for h in self.habits:
            mom = compute_momentum(h, self._completions[h.id], today=self.today)
            self._momentums[h.id] = mom
            name = (
                f"[dim]{h.name} (archived)[/dim]" if h.is_archived else h.name
            )
            table.add_row(
                name,
                f"{mom.score:.0f}",
                mom.trend,
                f"{mom.completion_rate:.0%}",
                key=str(h.id),
            )

        if not self.habits:
            return

        target_idx = 0
        if self.focus_habit:
            needle = self.focus_habit.lower()
            for i, h in enumerate(self.habits):
                if h.name.lower() == needle:
                    target_idx = i
                    break
        table.move_cursor(row=target_idx)
        self._refresh_grid()

    def _refresh_grid(self) -> None:
        table = self.query_one(DataTable)
        if not self.habits:
            return
        row = table.cursor_row
        if row is None or row < 0 or row >= len(self.habits):
            return
        h = self.habits[row]
        self.query_one(CompletionGrid).set_habit(
            h, self._completions[h.id], today=self.today
        )
        self.query_one("#grid-caption", Label).update(
            f"{h.name} — past 30 days"
        )

    # -- actions ---------------------------------------------------------------

    def action_cursor_down(self) -> None:
        self.query_one(DataTable).action_cursor_down()
        self._refresh_grid()

    def action_cursor_up(self) -> None:
        self.query_one(DataTable).action_cursor_up()
        self._refresh_grid()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._refresh_grid()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_drill_down()

    @work
    async def action_drill_down(self) -> None:
        table = self.query_one(DataTable)
        row = table.cursor_row
        if row is None or row < 0 or row >= len(self.habits):
            return
        from .detail import DetailScreen

        h = self.habits[row]
        await self.app.push_screen_wait(
            DetailScreen(self.db_path, h, today=self.today)
        )
        self._load()

    def action_nav_check(self) -> None:
        self.app.navigate_to("check")

    def action_nav_log(self) -> None:
        self.app.navigate_to("log")

    def action_nav_review(self) -> None:
        self.app.navigate_to("review")

    def action_back(self) -> None:
        if len(self.app.screen_stack) > 1:
            self.app.pop_screen()

    @work
    async def action_open_export(self) -> None:
        from .export import ExportScreen

        await self.app.push_screen_wait(ExportScreen(self.db_path))

    def action_toggle_archived(self) -> None:
        self.include_archived = not self.include_archived
        self._load()

    def action_toggle_theme(self) -> None:
        self.app.toggle_theme()

    def action_help(self) -> None:
        from .help import HelpScreen

        self.app.push_screen(HelpScreen())

    def action_quit(self) -> None:
        self.app.exit()
