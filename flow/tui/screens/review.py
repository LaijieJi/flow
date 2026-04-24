"""Review screen — week/month digest + pairwise habit correlations.

Mirrors the CLI `flow week`, `flow month`, `flow correlations` commands so
everything scriptable stays reachable from the TUI.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Label

from ... import db, review as _review
from ...models import format_duration
from ..widgets.navbar import NavBar


class ReviewScreen(Screen):
    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("q", "go_back", "Back"),
        Binding("w", "set_week", "Week"),
        Binding("m", "set_month", "Month"),
        Binding("c", "nav_check", "Check", show=False),
        Binding("s", "nav_stats", "Stats", show=False),
        Binding("l", "nav_log", "Log", show=False),
        Binding("t", "toggle_theme", "Theme"),
        Binding("h", "help", "Help"),
    ]

    DEFAULT_CSS = """
    ReviewScreen {
        align: center top;
    }
    #review-title {
        margin: 1 2 0 2;
    }
    #review-overall {
        margin: 0 2 1 2;
        color: $text-muted;
    }
    #corr-title {
        margin: 1 2 0 2;
    }
    #digest-table, #corr-table {
        margin: 0 2;
        height: auto;
        max-height: 40%;
    }
    """

    def __init__(
        self,
        db_path: Path | None = None,
        today: date | None = None,
        range_: str = "week",
    ) -> None:
        super().__init__()
        self.db_path = db_path
        self.today = today or date.today()
        self.range_ = range_  # 'week' | 'month'

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield NavBar(current="review")
        yield Label("", id="review-title")
        yield DataTable(id="digest-table", cursor_type="row", zebra_stripes=False)
        yield Label("", id="review-overall")
        yield Label("", id="corr-title")
        yield DataTable(id="corr-table", cursor_type="row", zebra_stripes=False)
        yield Footer()

    def on_mount(self) -> None:
        self.title = "flow review"
        digest = self.query_one("#digest-table", DataTable)
        digest.add_columns("habit", "rate", "done", "sched", "time", "notes")
        corr = self.query_one("#corr-table", DataTable)
        corr.add_columns("when", "also", "co-rate", "base", "lift", "n")
        self._load()

    def _load(self) -> None:
        if self.range_ == "month":
            start, end = _review.month_bounds(self.today)
            label = "this month"
        else:
            start, end = _review.week_bounds(self.today)
            label = "this week"
        self.query_one("#review-title", Label).update(
            f"[bold]{label}[/bold]  [dim]{start.isoformat()} → {end.isoformat()}[/dim]"
        )

        with db.session(self.db_path) as conn:
            habits = db.list_habits(conn, include_archived=False)
            dcomps = {
                h.id: db.completions_for_habit(conn, h.id, since=start, until=end)
                for h in habits
            }
            corr_since = self.today - timedelta(days=59)
            ccomps = {
                h.id: db.completions_for_habit(
                    conn, h.id, since=corr_since, until=self.today
                )
                for h in habits
            }

        digest = _review.build_digest(habits, dcomps, start, end)
        table = self.query_one("#digest-table", DataTable)
        table.clear()
        if digest.rows:
            for r in digest.rows:
                time_str = format_duration(r.total_seconds) if r.total_seconds else ""
                table.add_row(
                    r.habit.name,
                    f"{r.rate:.0%}",
                    f"{r.completed:g}",
                    str(r.scheduled),
                    time_str,
                    str(r.notes) if r.notes else "",
                )
        else:
            table.add_row("—", "nothing scheduled", "", "", "", "")
        self.query_one("#review-overall", Label).update(
            f"overall {digest.total_completed:g} / {digest.total_scheduled} "
            f"({digest.overall_rate:.0%})"
        )

        pairs = _review.correlations(habits, ccomps, corr_since, self.today)
        self.query_one("#corr-title", Label).update(
            f"[bold]correlations[/bold]  [dim]last 60 days[/dim]"
        )
        corr = self.query_one("#corr-table", DataTable)
        corr.clear()
        if pairs:
            for p in pairs[:10]:
                lift = p.co_rate - p.base_rate
                corr.add_row(
                    p.a.name,
                    p.b.name,
                    f"{p.co_rate:.0%}",
                    f"{p.base_rate:.0%}",
                    f"{lift:+.0%}",
                    str(p.shared_days),
                )
        else:
            corr.add_row("—", "not enough shared data yet", "", "", "", "")

    def action_set_week(self) -> None:
        if self.range_ != "week":
            self.range_ = "week"
            self._load()

    def action_set_month(self) -> None:
        if self.range_ != "month":
            self.range_ = "month"
            self._load()

    def action_go_back(self) -> None:
        if len(self.app.screen_stack) > 1:
            self.app.pop_screen()
        else:
            self.app.navigate_to("check")

    def action_nav_check(self) -> None:
        self.app.navigate_to("check")

    def action_nav_stats(self) -> None:
        self.app.navigate_to("stats")

    def action_nav_log(self) -> None:
        self.app.navigate_to("log")

    def action_toggle_theme(self) -> None:
        self.app.toggle_theme()

    def action_help(self) -> None:
        from .help import HelpScreen

        self.app.push_screen(HelpScreen())
