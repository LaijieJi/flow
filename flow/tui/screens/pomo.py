"""Pomodoro timer screen.

Runs a sequence of work/break phases for a single habit. When a work phase
completes in full, its duration is rolled into the habit's completion for
today via flow.pomodoro.merge_session. Partial / skipped work sessions are
not logged — only the disciplined time counts.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, Static

from ... import config as _config, db, notify, pomodoro
from ...models import Completion, Habit
from .. import sound


_PROGRESS_WIDTH = 40


class PomodoroScreen(Screen):
    BINDINGS = [
        Binding("space", "toggle_pause", "Pause"),
        Binding("n", "skip_phase", "Skip"),
        Binding("escape", "back", "Back"),
        Binding("q", "back", "Back", show=False),
        Binding("h", "help", "Help"),
    ]

    DEFAULT_CSS = """
    PomodoroScreen {
        align: center middle;
    }
    PomodoroScreen > Vertical {
        border: thick $accent;
        padding: 2 4;
        width: 64;
        height: auto;
        align: center middle;
    }
    PomodoroScreen.break > Vertical {
        border: thick $success;
    }
    PomodoroScreen.done > Vertical {
        border: thick $success;
    }
    #pomo-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }
    #pomo-phase {
        text-align: center;
        margin-bottom: 1;
        color: $text-muted;
    }
    #pomo-time {
        text-align: center;
        text-style: bold;
        color: $accent;
        margin: 1 0;
    }
    PomodoroScreen.break #pomo-time {
        color: $success;
    }
    PomodoroScreen.done #pomo-time {
        color: $success;
    }
    #pomo-bar {
        text-align: center;
        color: $accent;
        margin-bottom: 1;
    }
    PomodoroScreen.break #pomo-bar {
        color: $success;
    }
    #pomo-cycles {
        text-align: center;
        margin-bottom: 1;
        color: $text-muted;
    }
    #pomo-status {
        text-align: center;
        color: $text-muted;
    }
    """

    def __init__(
        self,
        habit: Habit | None = None,
        db_path: Path | None = None,
        today: date | None = None,
        work_seconds: int = pomodoro.DEFAULT_WORK_SECONDS,
        break_seconds: int = pomodoro.DEFAULT_BREAK_SECONDS,
        cycles: int = pomodoro.DEFAULT_CYCLES,
    ) -> None:
        super().__init__()
        if cycles < 1:
            raise ValueError("cycles must be >= 1")
        if work_seconds < 1:
            raise ValueError("work_seconds must be >= 1")
        # habit is optional: None = free-timer mode, no logging on phase end.
        self.habit = habit
        self.db_path = db_path
        self.today = today or date.today()
        self.work_seconds = work_seconds
        self.break_seconds = break_seconds
        self.total_cycles = cycles

        self.phase: str = "work"  # 'work' | 'break' | 'done'
        self.cycle: int = 1
        self.remaining: int = work_seconds
        self.paused: bool = False
        self._timer = None

    # -- layout ---------------------------------------------------------------

    def compose(self) -> ComposeResult:
        title = "Pomodoro"
        if self.habit is not None:
            title += f" — {self.habit.name}"
        yield Header(show_clock=False)
        with Vertical():
            yield Static(title, id="pomo-title")
            yield Label("", id="pomo-phase")
            yield Static("", id="pomo-time")
            yield Static("", id="pomo-bar", markup=True)
            yield Label("", id="pomo-cycles", markup=True)
            yield Label("", id="pomo-status")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "flow pomo"
        self._refresh_labels()
        self._timer = self.set_interval(1.0, self._tick)

    # -- tick loop ------------------------------------------------------------

    def _tick(self) -> None:
        if self.paused or self.phase == "done":
            return
        self.remaining -= 1
        if self.remaining <= 0:
            self._advance_phase()
        self._refresh_labels()

    def _advance_phase(self) -> None:
        """Called when the current phase's countdown hits zero."""
        if self.phase == "work":
            if self.habit is not None:
                self._log_work_session(self.work_seconds)
            if self.break_seconds > 0:
                self._ring("phase", "Work done — break time")
                self.phase = "break"
                self.remaining = self.break_seconds
            else:
                # Suppress the phase notification when the very next call
                # will fire the final 'done' notification (last cycle).
                if self.cycle < self.total_cycles:
                    self._ring("phase", "Work done")
                self._start_next_cycle_or_finish()
        elif self.phase == "break":
            if self.cycle < self.total_cycles:
                self._ring("phase", "Break over — back to work")
            self._start_next_cycle_or_finish()

    def _start_next_cycle_or_finish(self) -> None:
        if self.cycle >= self.total_cycles:
            self.phase = "done"
            self.remaining = 0
            if self._timer is not None:
                self._timer.pause()
            done_msg = (
                f"Pomodoro complete — {self.habit.name}"
                if self.habit is not None
                else "Pomodoro complete"
            )
            self._ring("done", done_msg)
            return
        self.cycle += 1
        self.phase = "work"
        self.remaining = self.work_seconds

    def _ring(self, kind: str, message: str = "") -> None:
        """Audible + visual cue. System sound, terminal bell, and (when the
        notifications config flag is on) a desktop notification."""
        sound.play(kind)
        self.app.bell()
        if message and _config.get("notifications") == "true":
            title = "flow pomo"
            if self.habit is not None and kind != "done":
                title = f"flow pomo — {self.habit.name}"
            notify.send(title, message)

    # -- logging --------------------------------------------------------------

    def _log_work_session(self, seconds: int) -> None:
        assert self.habit is not None  # callers guard on habit is not None
        with db.session(self.db_path) as conn:
            existing = self._load_existing(conn)
            merged = pomodoro.merge_session(
                existing, self.habit, seconds, self.today, completed_at=datetime.now()
            )
            db.upsert_completion(conn, merged)
        self.notify(
            f"✓ {self.habit.name} +{seconds // 60}m logged", timeout=2
        )

    def _load_existing(self, conn) -> Completion | None:
        assert self.habit is not None
        rows = db.completions_for_habit(conn, self.habit.id, since=self.today, until=self.today)
        return rows[0] if rows else None

    # -- rendering ------------------------------------------------------------

    def _refresh_labels(self) -> None:
        phase_label = self.query_one("#pomo-phase", Label)
        time_label = self.query_one("#pomo-time", Static)
        bar = self.query_one("#pomo-bar", Static)
        cycles = self.query_one("#pomo-cycles", Label)
        status = self.query_one("#pomo-status", Label)

        # Phase-driven border/accent colors via screen class toggle.
        self.set_class(self.phase == "break", "break")
        self.set_class(self.phase == "done", "done")

        cycles.update(self._render_cycle_dots())

        if self.phase == "done":
            action = "logged" if self.habit is not None else "complete"
            phase_label.update(
                f"[green]Done — {self.total_cycles}× {self.work_seconds // 60}m {action}[/green]"
            )
            time_label.update(self._big_time("✓"))
            bar.update("█" * _PROGRESS_WIDTH)
            status.update("[dim]press q or escape to exit[/dim]")
            return

        if self.phase == "work":
            label = "[bold red]WORK[/bold red]"
        else:
            label = "[bold green]BREAK[/bold green]"
        phase_label.update(
            f"{label}   cycle {self.cycle}/{self.total_cycles}"
        )
        time_label.update(self._big_time(pomodoro.format_mmss(self.remaining)))
        bar.update(self._render_bar())

        tag = "[yellow]PAUSED[/yellow]   " if self.paused else ""
        status.update(f"{tag}[dim]space pause  ·  n skip  ·  q back[/dim]")

    def _render_bar(self) -> str:
        total = self.work_seconds if self.phase == "work" else self.break_seconds
        if total <= 0:
            return "░" * _PROGRESS_WIDTH
        elapsed = max(0, total - self.remaining)
        filled = min(_PROGRESS_WIDTH, int(_PROGRESS_WIDTH * elapsed / total))
        return "█" * filled + "░" * (_PROGRESS_WIDTH - filled)

    def _render_cycle_dots(self) -> str:
        """Cycles rendered as filled/empty dots. Current cycle shown as a
        larger marker so the user can see phase progress at a glance."""
        parts: list[str] = []
        for i in range(1, self.total_cycles + 1):
            if i < self.cycle or self.phase == "done":
                parts.append("[green]●[/green]")
            elif i == self.cycle and self.phase != "done":
                parts.append("[bold]◉[/bold]")
            else:
                parts.append("[dim]○[/dim]")
        return "  ".join(parts)

    @staticmethod
    def _big_time(text: str) -> str:
        """Pad the time string so it reads as a focal element on screen."""
        return f"\n  {text}  \n"

    # -- actions --------------------------------------------------------------

    def action_toggle_pause(self) -> None:
        if self.phase == "done":
            return
        self.paused = not self.paused
        self._refresh_labels()

    def action_skip_phase(self) -> None:
        """Skip the current phase without logging partial work."""
        if self.phase == "done":
            return
        if self.phase == "work":
            self._ring("phase")
            if self.break_seconds > 0:
                self.phase = "break"
                self.remaining = self.break_seconds
            else:
                self._start_next_cycle_or_finish()
        else:
            self._start_next_cycle_or_finish()
        self._refresh_labels()

    def action_back(self) -> None:
        if self._timer is not None:
            self._timer.pause()
        # Textual seats a blank default Screen at stack[0]; popping onto it
        # strands the app on an empty screen. Exit instead when we were the
        # only meaningful screen (e.g. launched via `flow pomo`).
        stack = self.app.screen_stack
        if len(stack) > 1 and type(stack[-2]) is not Screen:
            self.app.pop_screen()
        else:
            self.app.exit()

    def action_help(self) -> None:
        from .help import HelpScreen

        self.app.push_screen(HelpScreen())
