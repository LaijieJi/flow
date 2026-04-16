"""Daily check-in screen.

Primary interaction: see today's scheduled habits, toggle them, optionally
record a value or short note. Every action persists immediately so that the
screen can be exited at any point without losing state.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from ... import db
from ...models import Completion, Habit
from ..widgets.habit_row import HabitRow
from .add_habit import AddHabitScreen
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
        Binding("n", "add_note", "Note"),
        Binding("a", "add_habit", "Add"),
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
            note = row.completion.note if row.completion else None
            c = Completion(habit_id=h.id, date=self.today, value=value, note=note)
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
            value = row.completion.value if row.completion else None
            note = text or None
            c = Completion(habit_id=row.habit.id, date=self.today, value=value, note=note)
            db.upsert_completion(conn, c)
            row.completion = c
        row.refresh_text()

    def action_quit(self) -> None:
        self.app.exit()
