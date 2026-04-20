"""Daily check-in screen.

Primary interaction: see today's scheduled habits, toggle them, optionally
record a value or short note. Every action persists immediately so that the
screen can be exited at any point without losing state.
"""

from __future__ import annotations

import random
from datetime import date
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from ... import db
from ...models import Completion, Habit, parse_duration
from ..widgets.habit_row import HabitRow
from ..widgets.navbar import NavBar
from .add_habit import AddHabitScreen
from .edit_habit import EditHabitScreen
from .prompt import PromptScreen


class AddHabitRow(ListItem):
    """Placeholder row at the top of the list for creating a new habit."""

    DEFAULT_CSS = """
    AddHabitRow {
        padding: 0 1;
    }
    """

    def compose(self):
        yield Static("[bold]+[/bold]  Add new habit...", markup=True)


class CheckScreen(Screen):
    BINDINGS = [
        Binding("j", "cursor_down", "Down"),
        Binding("k", "cursor_up", "Up"),
        Binding("space", "toggle", "Toggle"),
        Binding("v", "set_value", "Value"),
        Binding("d", "set_duration", "Time"),
        Binding("n", "add_note", "Note"),
        Binding("a", "add_habit", "Add"),
        Binding("e", "edit_habit", "Edit"),
        Binding("u", "undo", "Undo"),
        Binding("r", "random", "Random"),
        Binding("s", "nav_stats", "Stats", show=False),
        Binding("l", "nav_log", "Log", show=False),
        Binding("escape", "back", "Back", show=False),
        Binding("t", "toggle_theme", "Theme"),
        Binding("h", "help", "Help"),
        Binding("q", "quit", "Quit"),
    ]

    DEFAULT_CSS = """
    CheckScreen {
        align: center top;
    }
    #title {
        margin: 1 2;
        color: $text;
    }
    #rows {
        border: round $accent;
        margin: 0 2 1 2;
        padding: 0 1;
        height: auto;
        max-height: 80%;
    }
    HabitRow.done #row-label {
        color: $text-muted;
    }
    HabitRow.unscheduled {
        background: transparent;
    }
    """

    def __init__(self, db_path: Path | None = None, today: date | None = None) -> None:
        super().__init__()
        self.db_path = db_path
        self.today = today or date.today()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield NavBar(current="check")
        yield Label(
            f"Today's habits — {self.today:%A, %b %d}", id="title"
        )
        yield ListView(id="rows")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "flow"
        self._reload()

    # -- data ------------------------------------------------------------------

    def _reload(self, focus_add_row: bool = False) -> None:
        with db.session(self.db_path) as conn:
            habits = db.list_habits(conn)
            completions = db.completions_on(conn, self.today)
        comp_map: dict[int, Completion] = {c.habit_id: c for c in completions}

        lv = self.query_one("#rows", ListView)
        lv.clear()

        # "+" row always first
        lv.append(AddHabitRow())

        for h in habits:
            lv.append(HabitRow(h, self.today, comp_map.get(h.id)))

        if focus_add_row or not habits:
            lv.index = 0
        else:
            # Focus first scheduled habit row (index 1+)
            for i, item in enumerate(lv.children):
                if isinstance(item, HabitRow) and item.scheduled:
                    lv.index = i
                    break

    def _current_row(self) -> HabitRow | None:
        lv = self.query_one("#rows", ListView)
        item = lv.highlighted_child
        return item if isinstance(item, HabitRow) else None

    # -- add habit -------------------------------------------------------------

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Enter key on the "+" row opens the add-habit form."""
        if isinstance(event.item, AddHabitRow):
            self.action_add_habit()

    @work
    async def action_add_habit(self) -> None:
        result = await self.app.push_screen_wait(
            AddHabitScreen(db_path=self.db_path)
        )
        if result is not None:
            self._reload(focus_add_row=True)

    @work
    async def action_edit_habit(self) -> None:
        """Edit the highlighted habit regardless of scheduled state.

        Editing is habit-level (frequency, name, window), so it's allowed on
        days the habit is not scheduled — the ban on logging from an off-day
        does not apply to metadata changes.
        """
        row = self._current_row()
        if row is None:
            return
        result = await self.app.push_screen_wait(
            EditHabitScreen(row.habit, db_path=self.db_path)
        )
        if result is not None:
            self._reload()

    # -- toggle / value / note -------------------------------------------------

    def action_cursor_down(self) -> None:
        self.query_one("#rows", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#rows", ListView).action_cursor_up()

    def action_toggle(self) -> None:
        row = self._current_row()
        if row is None or not row.scheduled:
            return
        with db.session(self.db_path) as conn:
            if row.completion is not None:
                db.delete_completion(conn, row.habit.id, self.today)
                row.completion = None
            else:
                c = Completion(habit_id=row.habit.id, date=self.today)
                db.upsert_completion(conn, c)
                row.completion = c
        row.refresh_text()

    @work
    async def action_set_value(self) -> None:
        row = self._current_row()
        if row is None or not row.scheduled:
            return
        h = row.habit
        initial = ""
        if row.completion is not None and row.completion.value is not None:
            initial = f"{row.completion.value:g}"
        title = f"value for {h.name}"
        if h.unit:
            title += f" ({h.unit}"
            if h.target:
                title += f", target {h.target:g}"
            title += ")"
        answer = await self.app.push_screen_wait(
            PromptScreen(title, initial=initial, placeholder="numeric value")
        )
        if answer is None:
            return
        text = answer.strip()
        if not text:
            return
        try:
            value = float(text)
        except ValueError:
            self.notify("invalid number", severity="warning", timeout=3)
            return

        with db.session(self.db_path) as conn:
            existing = row.completion
            note = existing.note if existing else None
            dur = existing.duration_seconds if existing else None
            c = Completion(
                habit_id=h.id,
                date=self.today,
                value=value,
                note=note,
                duration_seconds=dur,
            )
            db.upsert_completion(conn, c)
            row.completion = c
        row.refresh_text()

    @work
    async def action_set_duration(self) -> None:
        row = self._current_row()
        if row is None or not row.scheduled:
            return
        h = row.habit
        initial = ""
        if row.completion is not None and row.completion.duration_seconds is not None:
            from ...models import format_duration

            initial = format_duration(row.completion.duration_seconds)
        answer = await self.app.push_screen_wait(
            PromptScreen(
                f"time for {h.name}",
                initial=initial,
                placeholder="25m | 1h30m | 90s | 1:30",
            )
        )
        if answer is None:
            return
        text = answer.strip()
        if not text:
            return
        try:
            seconds = parse_duration(text)
        except ValueError as e:
            self.notify(str(e), severity="warning", timeout=3)
            return

        with db.session(self.db_path) as conn:
            existing = row.completion
            value = existing.value if existing else None
            note = existing.note if existing else None
            c = Completion(
                habit_id=h.id,
                date=self.today,
                value=value,
                note=note,
                duration_seconds=seconds,
            )
            db.upsert_completion(conn, c)
            row.completion = c
        row.refresh_text()

    @work
    async def action_add_note(self) -> None:
        row = self._current_row()
        if row is None or not row.scheduled:
            return
        initial = row.completion.note if row.completion and row.completion.note else ""
        answer = await self.app.push_screen_wait(
            PromptScreen(
                f"note for {row.habit.name}",
                initial=initial,
                placeholder=f"max {Habit.NOTE_MAX} chars",
            )
        )
        if answer is None:
            return
        text = answer.strip()
        if len(text) > Habit.NOTE_MAX:
            self.notify(f"note too long (max {Habit.NOTE_MAX})", severity="warning")
            return

        with db.session(self.db_path) as conn:
            existing = row.completion
            value = existing.value if existing else None
            dur = existing.duration_seconds if existing else None
            note = text or None
            c = Completion(
                habit_id=row.habit.id,
                date=self.today,
                value=value,
                note=note,
                duration_seconds=dur,
            )
            db.upsert_completion(conn, c)
            row.completion = c
        row.refresh_text()

    def action_random(self) -> None:
        from textual.widgets import ListView

        lv = self.query_one("#rows", ListView)
        candidates = [
            i
            for i, item in enumerate(lv.children)
            if isinstance(item, HabitRow) and item.scheduled and item.completion is None
        ]
        if not candidates:
            self.notify("nothing scheduled + undone", severity="warning", timeout=2)
            return
        lv.index = random.choice(candidates)

    def action_undo(self) -> None:
        with db.session(self.db_path) as conn:
            hit = db.most_recent_completion(conn)
            if hit is None:
                self.notify("nothing to undo", severity="warning", timeout=2)
                return
            c, h = hit
            db.delete_completion(conn, h.id, c.date)
        self.notify(f"undone {h.name} ({c.date.isoformat()})", timeout=2)
        self._reload()

    def action_toggle_theme(self) -> None:
        self.app.toggle_theme()

    def action_nav_stats(self) -> None:
        self.app.navigate_to("stats")

    def action_nav_log(self) -> None:
        self.app.navigate_to("log")

    def action_back(self) -> None:
        if len(self.app.screen_stack) > 1:
            self.app.pop_screen()

    def action_help(self) -> None:
        from .help import HelpScreen

        self.app.push_screen(HelpScreen())

    def action_quit(self) -> None:
        self.app.exit()
