"""Single-habit drill-down screen. Shows summary, 30-day grid, recent notes."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, Static

from ... import db
from ...models import Habit
from ...momentum import compute_momentum
from ...review import habit_score_sparkline
from ..widgets.completion_grid import CompletionGrid
from ..widgets.time_of_day import TimeOfDayStrip
from ..widgets.year_heatmap import YearHeatmap
from .edit_habit import EditHabitScreen


class DetailScreen(Screen):
    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("q", "go_back", "Back"),
        Binding("e", "edit", "Edit"),
        Binding("x", "archive_toggle", "Archive"),
        Binding("c", "nav_check", "Check", show=False),
        Binding("s", "nav_stats", "Stats", show=False),
        Binding("l", "nav_log", "Log", show=False),
        Binding("R", "nav_review", "Review", show=False),
        Binding("t", "toggle_theme", "Theme"),
        Binding("h", "help", "Help"),
        Binding("question_mark", "show_bindings", "Keys", show=False),
    ]

    DEFAULT_CSS = """
    DetailScreen {
        align: center top;
    }
    #detail-title,
    #detail-summary,
    #detail-sparkline,
    #notes-title {
        margin: 1 2 0 2;
    }
    CompletionGrid {
        margin: 1 2;
        border: round $accent;
        padding: 0 1;
    }
    YearHeatmap {
        margin: 0 2 1 2;
        border: round $accent;
        padding: 0 1;
    }
    TimeOfDayStrip {
        margin: 0 2 1 2;
        border: round $accent;
        padding: 0 1;
    }
    #notes-box {
        margin: 0 2 1 2;
        border: round $accent;
        padding: 0 1;
        height: auto;
        max-height: 40%;
    }
    """

    def __init__(
        self,
        db_path: Path | None,
        habit: Habit,
        today: date | None = None,
        watch_interval: int | None = None,
    ) -> None:
        super().__init__()
        self.db_path = db_path
        self.habit = habit
        self.today = today or date.today()
        self.watch_interval = watch_interval
        self.summary_text: str = ""
        self.notes_text: str = ""
        self._watch_timer = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Label("", id="detail-title")
        yield Label("", id="detail-summary")
        yield Label("", id="detail-sparkline")
        yield CompletionGrid(id="detail-grid")
        yield YearHeatmap(id="detail-year")
        yield TimeOfDayStrip(id="detail-tod")
        yield Label("recent notes", id="notes-title")
        with VerticalScroll(id="notes-box"):
            yield Static("", id="notes-body")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh()
        if self.watch_interval is not None:
            self._watch_timer = self.set_interval(self.watch_interval, self._refresh)

    def _refresh(self) -> None:
        self.title = f"flow — {self.habit.name}"

        with db.session(self.db_path) as conn:
            fresh = db.get_habit(conn, self.habit.id)
            if fresh is not None:
                self.habit = fresh
            comps = db.completions_for_habit(conn, self.habit.id)
            paused_until = db.paused_until_date(conn, self.habit.id, self.today)

        archived_tag = (
            f"  [red](archived {self.habit.archived_at.isoformat()})[/red]"
            if self.habit.is_archived
            else ""
        )
        paused_tag = (
            f"  [yellow](paused → {paused_until.isoformat()})[/yellow]"
            if paused_until is not None
            else ""
        )
        self.query_one("#detail-title", Label).update(
            f"[bold]{self.habit.name}[/bold]  [dim]— {self.habit.frequency}[/dim]"
            + archived_tag
            + paused_tag
        )

        mom = compute_momentum(self.habit, comps, today=self.today)
        last_done = next(
            (
                c
                for c in reversed(comps)
                if c.is_done and c.completed_at is not None
            ),
            None,
        )
        last_done_tag = ""
        if last_done is not None:
            stamp = last_done.completed_at
            if last_done.date == self.today:
                last_done_tag = f"  [dim]· last done {stamp.strftime('%H:%M')}[/dim]"
            else:
                last_done_tag = (
                    f"  [dim]· last done {last_done.date.isoformat()} "
                    f"{stamp.strftime('%H:%M')}[/dim]"
                )
        self.summary_text = (
            f"score [bold]{mom.score:.0f}[/bold]  {mom.trend}  "
            f"rate [bold]{mom.completion_rate:.0%}[/bold] "
            f"[dim]({mom.window_days}d)[/dim]"
            + last_done_tag
        )
        self.query_one("#detail-summary", Label).update(self.summary_text)

        spark, weekly = habit_score_sparkline(
            self.habit, comps, weeks=12, today=self.today, width=30
        )
        if spark and len(weekly) >= 2:
            spark_line = (
                f"[dim]score 12w[/dim] [cyan]{spark}[/cyan]  "
                f"[dim]{weekly[0]:.0f}→{weekly[-1]:.0f}[/dim]"
            )
        else:
            spark_line = "[dim]score history: not enough data yet[/dim]"
        self.query_one("#detail-sparkline", Label).update(spark_line)

        self.query_one(CompletionGrid).set_habit(self.habit, comps, today=self.today)
        self.query_one(YearHeatmap).set_habit(self.habit, comps, today=self.today)
        self.query_one(TimeOfDayStrip).set_habit(self.habit.name, comps)

        notes = sorted(
            (c for c in comps if c.note), key=lambda c: c.date, reverse=True
        )[:10]
        if notes:
            self.notes_text = "\n".join(
                f"[dim]{c.date.isoformat()}[/dim]  {c.note}" for c in notes
            )
        else:
            self.notes_text = "[dim]no notes yet[/dim]"
        self.query_one("#notes-body", Static).update(self.notes_text)

    def action_go_back(self) -> None:
        self.app.pop_screen()

    @work
    async def action_edit(self) -> None:
        result = await self.app.push_screen_wait(
            EditHabitScreen(self.habit, db_path=self.db_path)
        )
        if result is not None:
            self._refresh()
            self.notify(f"updated {result.name}", timeout=2)

    def action_toggle_theme(self) -> None:
        self.app.toggle_theme()

    def action_nav_check(self) -> None:
        self.app.navigate_to("check")

    def action_nav_stats(self) -> None:
        self.app.navigate_to("stats")

    def action_nav_log(self) -> None:
        self.app.navigate_to("log")

    def action_nav_review(self) -> None:
        self.app.navigate_to("review")

    def action_help(self) -> None:
        from .help import HelpScreen

        self.app.push_screen(HelpScreen())

    def action_show_bindings(self) -> None:
        self.app.show_bindings(self)

    def action_archive_toggle(self) -> None:
        with db.session(self.db_path) as conn:
            if self.habit.is_archived:
                if db.restore_habit(conn, self.habit.id):
                    msg = f"restored {self.habit.name}"
                else:
                    msg = "not archived"
            else:
                if db.archive_habit(conn, self.habit.id):
                    msg = f"archived {self.habit.name}"
                else:
                    msg = "already archived"
        self._refresh()
        self.notify(msg, timeout=2)
