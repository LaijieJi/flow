"""Stats overview screen — momentum dashboard for all active habits."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Label

from ... import db
from ...models import Completion, Habit
from ...momentum import completion_strength, compute_momentum, Momentum
from ..widgets.completion_grid import CompletionGrid
from ..widgets.navbar import NavBar


SPARK_LEVELS = "▁▂▃▄▅▆▇█"
SPARK_DAYS = 14


def _score_tier(score: float) -> str:
    if score >= 80:
        return "green"
    if score >= 40:
        return "yellow"
    return "red"


def _rate_tier(rate: float) -> str:
    if rate >= 0.8:
        return "green"
    if rate >= 0.4:
        return "yellow"
    return "red"


def _trend_color(arrow: str) -> str:
    return {"↗": "green", "↘": "red"}.get(arrow, "dim")


def render_sparkline(
    habit: Habit, completions: list[Completion], today: date, days: int = SPARK_DAYS
) -> Text:
    """Per-day sparkline for the last `days` days ending today.

    Glyph maps:
      - scheduled + done full     → █ (top level), tier-colored
      - scheduled + done partial  → mid level by strength
      - scheduled + missed        → · dim red
      - skipped                   → ▪ yellow
      - unscheduled               → · dim
      - before created            → space
    """
    start = today - timedelta(days=days - 1)
    by_date = {c.date: c for c in completions if start <= c.date <= today}
    text = Text()
    levels = SPARK_LEVELS
    last = len(levels) - 1
    for i in range(days):
        d = start + timedelta(days=i)
        if d < habit.created_at:
            text.append(" ")
            continue
        c = by_date.get(d)
        if c is not None and c.is_skipped:
            text.append("▪", style="yellow")
            continue
        if not habit.is_scheduled_on(d):
            text.append("·", style="dim")
            continue
        if c is None:
            text.append("·", style="red dim")
            continue
        s = completion_strength(c.value, habit.target)
        if s <= 0:
            text.append("·", style="red dim")
            continue
        # 0..1 → 1..last so any presence visibly clears the baseline.
        idx = max(1, min(last, round(s * last)))
        text.append(levels[idx], style="green" if s >= 1.0 else "green dim")
    return text


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
        margin: 1 2 1 2;
        border: round $accent;
        padding: 0 1;
    }
    """

    def __init__(
        self,
        db_path: Path | None = None,
        today: date | None = None,
        focus_habit: str | None = None,
        watch_interval: int | None = None,
    ) -> None:
        super().__init__()
        self.db_path = db_path
        self.today = today or date.today()
        self.focus_habit = focus_habit
        self.watch_interval = watch_interval
        self.include_archived = False
        self.habits: list[Habit] = []
        self._completions: dict[int, list[Completion]] = {}
        self._momentums: dict[int, Momentum] = {}
        self._watch_timer = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield NavBar(current="stats")
        yield Label("stats — last 30 days", id="stats-title")
        yield DataTable(id="stats-table", cursor_type="row", zebra_stripes=False)
        yield CompletionGrid(id="grid")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "flow stats"
        table = self.query_one(DataTable)
        table.add_columns("habit", f"last {SPARK_DAYS}", "score", "trend", "rate")
        self._load()
        if self.watch_interval is not None:
            self._watch_timer = self.set_interval(self.watch_interval, self._load)

    def _load(self) -> None:
        table = self.query_one(DataTable)
        prior_cursor = table.cursor_row if table.row_count else None

        with db.session(self.db_path) as conn:
            self.habits = db.list_habits(conn, include_archived=self.include_archived)
            self._completions = {
                h.id: db.completions_for_habit(conn, h.id) for h in self.habits
            }

        title = "stats — last 30 days"
        if self.include_archived:
            title += " [dim](incl. archived)[/dim]"
        if self.watch_interval is not None:
            title += f" [dim]· watching ({self.watch_interval}s)[/dim]"
        self.query_one("#stats-title", Label).update(title)

        table.clear()
        self._momentums.clear()

        for h in self.habits:
            mom = compute_momentum(h, self._completions[h.id], today=self.today)
            self._momentums[h.id] = mom
            if h.is_archived:
                name_cell = Text(f"{h.name} (archived)", style="dim")
            else:
                name_cell = Text(h.name)
            spark = render_sparkline(h, self._completions[h.id], today=self.today)
            score_cell = Text(f"{mom.score:.0f}", style=_score_tier(mom.score))
            trend_cell = Text(mom.trend, style=_trend_color(mom.trend))
            rate_cell = Text(
                f"{mom.completion_rate:.0%}", style=_rate_tier(mom.completion_rate)
            )
            table.add_row(
                name_cell,
                spark,
                score_cell,
                trend_cell,
                rate_cell,
                key=str(h.id),
            )

        if not self.habits:
            return

        target_idx = 0
        if prior_cursor is not None and 0 <= prior_cursor < len(self.habits):
            target_idx = prior_cursor
        elif self.focus_habit:
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
