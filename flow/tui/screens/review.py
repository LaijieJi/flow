"""Review screen — week/month digest + pairwise habit correlations.

Mirrors the CLI `flow week`, `flow month`, `flow correlations` commands so
everything scriptable stays reachable from the TUI.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from ... import db, review as _review
from ...models import format_duration
from ..widgets.navbar import NavBar


def _rate_tier(rate: float) -> str:
    if rate >= 0.8:
        return "green"
    if rate >= 0.4:
        return "yellow"
    return "red"


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
    #review-header {
        border: round $accent;
        margin: 1 2 0 2;
        padding: 0 1;
        color: $text;
        height: auto;
    }
    #digest-table {
        margin: 1 2 0 2;
        border: round $accent;
        padding: 0 1;
        height: auto;
        max-height: 40%;
        border-title-align: left;
    }
    #corr-table {
        margin: 1 2 1 2;
        border: round $accent;
        padding: 0 1;
        height: auto;
        max-height: 40%;
        border-title-align: left;
        border-subtitle-align: right;
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
        yield Static("", id="review-header", markup=True)
        yield DataTable(id="digest-table", cursor_type="row", zebra_stripes=False)
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

        digest_data = _review.build_digest(habits, dcomps, start, end)
        overall_pct = int(round(digest_data.overall_rate * 100))
        overall_tier = _rate_tier(digest_data.overall_rate)
        header = (
            f"[bold]{label}[/bold]   "
            f"[dim]{start.isoformat()} → {end.isoformat()}[/dim]   "
            f"[bold]{digest_data.total_completed:g}[/bold]"
            f"[dim]/{digest_data.total_scheduled}[/dim] done   "
            f"[{overall_tier}]{overall_pct}%[/{overall_tier}]   "
            f"[dim]· w=week · m=month[/dim]"
        )
        self.query_one("#review-header", Static).update(header)

        table = self.query_one("#digest-table", DataTable)
        table.border_title = f"digest · {label}"
        table.clear()
        if digest_data.rows:
            for r in digest_data.rows:
                time_str = format_duration(r.total_seconds) if r.total_seconds else ""
                rate_cell = Text(f"{r.rate:.0%}", style=_rate_tier(r.rate))
                table.add_row(
                    Text(r.habit.name),
                    rate_cell,
                    Text(f"{r.completed:g}", style="green"),
                    Text(str(r.scheduled), style="dim"),
                    Text(time_str, style="cyan") if time_str else Text(""),
                    Text(str(r.notes), style="dim") if r.notes else Text(""),
                )
        else:
            table.add_row(
                Text("—", style="dim"),
                Text("nothing scheduled", style="dim italic"),
                "",
                "",
                "",
                "",
            )

        pairs = _review.correlations(habits, ccomps, corr_since, self.today)
        corr = self.query_one("#corr-table", DataTable)
        corr.border_title = "correlations"
        corr.border_subtitle = "last 60 days"
        corr.clear()
        if pairs:
            for p in pairs[:10]:
                lift = p.co_rate - p.base_rate
                if lift > 0.05:
                    lift_style = "green"
                elif lift < -0.05:
                    lift_style = "red"
                else:
                    lift_style = "dim"
                corr.add_row(
                    Text(p.a.name),
                    Text(p.b.name),
                    Text(f"{p.co_rate:.0%}", style=_rate_tier(p.co_rate)),
                    Text(f"{p.base_rate:.0%}", style="dim"),
                    Text(f"{lift:+.0%}", style=lift_style),
                    Text(str(p.shared_days), style="dim"),
                )
        else:
            corr.add_row(
                Text("—", style="dim"),
                Text("not enough shared data yet", style="dim italic"),
                "",
                "",
                "",
                "",
            )

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
