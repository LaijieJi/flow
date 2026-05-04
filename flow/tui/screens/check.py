"""Daily check-in screen.

Primary interaction: see today's scheduled habits, toggle them, optionally
record a value or short note. Every action persists immediately so that the
screen can be exited at any point without losing state.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header, ListItem, ListView, Static

from ... import db
from ...models import (
    COMPLETION_STATUS_DONE,
    COMPLETION_STATUS_SKIPPED,
    Completion,
    Habit,
    parse_duration,
)
from ..widgets.habit_row import RECENT_DAYS, HabitRow
from ..widgets.navbar import NavBar
from .add_habit import AddHabitScreen
from .edit_habit import EditHabitScreen
from .prompt import PromptScreen


def _stamp_for_edit(existing: Completion | None, status: str) -> datetime | None:
    """Pick the `completed_at` value for an interactive edit on the check screen.

    Preserves an existing stamp so editing a note or value later doesn't move
    the recorded log time. Stamps `now()` when transitioning a fresh row to
    `done`. Skip rows always get NULL — the convention enforced by the v6
    migration."""
    if existing is not None:
        return existing.completed_at
    if status == COMPLETION_STATUS_DONE:
        return datetime.now()
    return None


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
        # Shown in the footer — the primary check-in verbs.
        Binding("space", "toggle", "Toggle"),
        Binding("v", "set_value", "Value"),
        Binding("d", "set_duration", "Time"),
        Binding("p", "pomodoro", "Pomo"),
        Binding("a", "add_habit", "Add"),
        Binding("e", "edit_habit", "Edit"),
        Binding("x", "archive_habit", "Archive"),
        Binding("h", "help", "Help"),
        # Hidden from the footer to keep it readable — all documented in `h`.
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("S", "toggle_skip", "Skip", show=False),
        Binding("P", "pomodoro_free", "Free pomo", show=False),
        Binding("n", "add_note", "Note", show=False),
        Binding("u", "undo", "Undo", show=False),
        Binding("r", "random", "Random", show=False),
        Binding("s", "nav_stats", "Stats", show=False),
        Binding("l", "nav_log", "Log", show=False),
        Binding("R", "nav_review", "Review", show=False),
        Binding("escape", "back", "Back", show=False),
        Binding("t", "toggle_theme", "Theme", show=False),
        Binding("q", "quit", "Quit", show=False),
    ]

    DEFAULT_CSS = """
    CheckScreen {
        align: center top;
    }
    #header {
        border: round $accent;
        margin: 1 2 0 2;
        padding: 0 1;
        color: $text;
        height: auto;
    }
    #rows {
        border: round $accent;
        margin: 1 2 1 2;
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
        yield Static("", id="header", markup=True)
        yield ListView(id="rows")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "flow"
        self._reload()

    def on_screen_resume(self) -> None:
        """Re-read state when returning from a sub-screen (e.g. pomo, detail)
        so any completions logged elsewhere show up immediately."""
        self._reload()

    # -- data ------------------------------------------------------------------

    def _reload(self, focus_add_row: bool = False) -> None:
        recent_since = self.today - timedelta(days=RECENT_DAYS)
        recent_until = self.today - timedelta(days=1)
        with db.session(self.db_path) as conn:
            habits = db.list_habits(conn)
            completions = db.completions_on(conn, self.today)
            recent_by_habit: dict[int, list[Completion]] = {
                h.id: db.completions_for_habit(
                    conn, h.id, since=recent_since, until=recent_until
                )
                for h in habits
            }
        comp_map: dict[int, Completion] = {c.habit_id: c for c in completions}

        self._update_header(habits, comp_map)

        lv = self.query_one("#rows", ListView)
        lv.clear()

        # "+" row always first
        lv.append(AddHabitRow())

        for h in habits:
            lv.append(
                HabitRow(
                    h,
                    self.today,
                    comp_map.get(h.id),
                    recent=recent_by_habit.get(h.id, []),
                )
            )

        target_index = 0
        if not focus_add_row and habits:
            for i, h in enumerate(habits, start=1):
                if h.is_scheduled_on(self.today):
                    target_index = i
                    break

        # ListView.clear()/append() schedule mount work on the next refresh,
        # so setting `index` here races with child mounting — the assignment
        # would clamp to None and the highlight wouldn't render until a
        # keypress forced a redraw. Defer until after the refresh cycle so the
        # children exist, then focus the list so the highlight is visible.
        def _apply_initial_focus() -> None:
            lv.index = target_index
            lv.focus()

        self.call_after_refresh(_apply_initial_focus)

    def _current_row(self) -> HabitRow | None:
        lv = self.query_one("#rows", ListView)
        item = lv.highlighted_child
        return item if isinstance(item, HabitRow) else None

    def _update_header(
        self, habits: list[Habit], comp_map: dict[int, Completion]
    ) -> None:
        """Render today's progress summary above the habit list. Skipped
        habits are excluded from the denominator — they're explicitly off the
        hook for the day, not an outstanding TODO."""
        scheduled = [h for h in habits if h.is_scheduled_on(self.today)]
        active: list[Habit] = []
        skipped = 0
        done = 0
        for h in scheduled:
            c = comp_map.get(h.id)
            if c is not None and c.is_skipped:
                skipped += 1
                continue
            active.append(h)
            if c is not None:
                done += 1
        total = len(active)
        remaining = total - done

        date_str = f"{self.today:%A, %b %d}"
        if total == 0:
            if skipped:
                summary = f"[dim]{skipped} skipped · nothing else scheduled[/dim]"
            else:
                summary = "[dim]no habits scheduled today[/dim]"
            text = f"[bold]{date_str}[/bold]   {summary}"
        else:
            dots = "[green]●[/green]" * done + "[dim]○[/dim]" * remaining
            pct = int(round(done / total * 100))
            tier = (
                "green"
                if pct >= 80
                else "yellow"
                if pct >= 40
                else "red"
            )
            tail = ""
            if skipped:
                tail = f"   [yellow dim]· {skipped} skipped[/yellow dim]"
            text = (
                f"[bold]{date_str}[/bold]   "
                f"{dots}   "
                f"[bold]{done}[/bold][dim]/{total}[/dim] done   "
                f"[{tier}]{pct}%[/{tier}]"
                f"{tail}"
            )
        self.query_one("#header", Static).update(text)

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
    async def action_archive_habit(self) -> None:
        """Soft-delete the highlighted habit after a y/n confirm. Data is
        preserved; restore via `flow restore <name>` from the CLI."""
        row = self._current_row()
        if row is None:
            return
        from .confirm import ConfirmScreen

        ok = await self.app.push_screen_wait(
            ConfirmScreen(f"archive [bold]{row.habit.name}[/bold]?")
        )
        if not ok:
            return
        with db.session(self.db_path) as conn:
            if not db.archive_habit(conn, row.habit.id):
                self.notify("already archived", severity="warning", timeout=2)
                return
        self.notify(f"archived {row.habit.name}", timeout=2)
        self._reload()

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
            existing = row.completion
            # Three-way: nothing → done → off; skipped → done (overwrite the
            # skip, since space means "I did it after all").
            if existing is not None and not existing.is_skipped:
                db.delete_completion(conn, row.habit.id, self.today)
                row.completion = None
            else:
                c = Completion(
                    habit_id=row.habit.id,
                    date=self.today,
                    status=COMPLETION_STATUS_DONE,
                    completed_at=_stamp_for_edit(None, COMPLETION_STATUS_DONE),
                )
                db.upsert_completion(conn, c)
                row.completion = c
        row.refresh_text()

    def action_toggle_skip(self) -> None:
        row = self._current_row()
        if row is None or not row.scheduled:
            return
        with db.session(self.db_path) as conn:
            existing = row.completion
            if existing is not None and existing.is_skipped:
                db.delete_completion(conn, row.habit.id, self.today)
                row.completion = None
            else:
                # Replace any prior 'done' with a skip — this is the user's
                # explicit "actually, skipping today" override.
                c = Completion(
                    habit_id=row.habit.id,
                    date=self.today,
                    status=COMPLETION_STATUS_SKIPPED,
                )
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
                completed_at=_stamp_for_edit(existing, COMPLETION_STATUS_DONE),
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
                completed_at=_stamp_for_edit(existing, COMPLETION_STATUS_DONE),
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
            status = existing.status if existing else COMPLETION_STATUS_DONE
            note = text or None
            c = Completion(
                habit_id=row.habit.id,
                date=self.today,
                value=value,
                note=note,
                duration_seconds=dur,
                status=status,
                completed_at=_stamp_for_edit(existing, status),
            )
            db.upsert_completion(conn, c)
            row.completion = c
        row.refresh_text()

    @work
    async def action_pomodoro(self) -> None:
        """Start a pomo tied to the highlighted habit. Shows a setup modal
        pre-filled with defaults; Enter-through starts a standard 25/5."""
        row = self._current_row()
        if row is None or not row.scheduled:
            return
        await self._launch_pomo(row.habit)

    @work
    async def action_pomodoro_free(self) -> None:
        """Start a free-running pomodoro with no habit attached — the timer
        just rings the bell; nothing is logged."""
        await self._launch_pomo(None)

    async def _launch_pomo(self, habit: Habit | None) -> None:
        from .pomo import PomodoroScreen
        from .pomo_setup import PomodoroSetupScreen

        settings = await self.app.push_screen_wait(PomodoroSetupScreen(habit=habit))
        if settings is None:
            return
        self.app.push_screen(
            PomodoroScreen(
                habit=habit,
                db_path=self.db_path,
                today=self.today,
                work_seconds=settings.work_seconds,
                break_seconds=settings.break_seconds,
                cycles=settings.cycles,
            )
        )

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

    def action_nav_review(self) -> None:
        self.app.navigate_to("review")

    def action_back(self) -> None:
        if len(self.app.screen_stack) > 1:
            self.app.pop_screen()

    def action_help(self) -> None:
        from .help import HelpScreen

        self.app.push_screen(HelpScreen())

    def action_quit(self) -> None:
        self.app.exit()
