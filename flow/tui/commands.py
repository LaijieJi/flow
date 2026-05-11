"""Custom command palette provider for flow.

Surfaces high-value actions (cross-screen navigation, theme toggle, help,
export, pomodoro) so users can find them by typing instead of memorizing
keybindings. Built on Textual's built-in palette (Ctrl+\\ by default)."""

from __future__ import annotations

from typing import Callable

from textual.command import Hit, Hits, Provider


def _commands(app) -> list[tuple[str, str, Callable[[], None]]]:
    """Return a flat list of (label, help, callback) entries for fuzzy search.

    `app` is the live FlowApp instance; callbacks close over it so they fire
    in-context when the user selects a hit."""

    def goto(target: str) -> Callable[[], None]:
        return lambda: app.navigate_to(target)

    def push_help() -> None:
        from .screens.help import HelpScreen

        app.push_screen(HelpScreen())

    def push_export() -> None:
        from .screens.export import ExportScreen

        app.push_screen(ExportScreen(app.db_path))

    def push_add_habit() -> None:
        from .screens.add_habit import AddHabitScreen

        app.push_screen(AddHabitScreen(db_path=app.db_path))

    def push_pomo() -> None:
        from .. import pomodoro as _pomo
        from .screens.pomo import PomodoroScreen

        app.push_screen(
            PomodoroScreen(
                habit=None,
                db_path=app.db_path,
                work_seconds=_pomo.DEFAULT_WORK_SECONDS,
                break_seconds=_pomo.DEFAULT_BREAK_SECONDS,
                cycles=_pomo.DEFAULT_CYCLES,
            )
        )

    return [
        ("Open check screen", "Daily check-in (c)", goto("check")),
        ("Open stats screen", "Momentum dashboard (s)", goto("stats")),
        ("Open log screen", "Completion history (l)", goto("log")),
        ("Open review screen", "Week / month digest (R)", goto("review")),
        ("Add habit", "Create a new habit (a on check)", push_add_habit),
        ("Toggle theme", "Switch light / dark (t)", app.toggle_theme),
        ("Show help", "Reference card of all bindings (h)", push_help),
        ("Export data", "CSV / JSON dump (E on stats)", push_export),
        ("Start pomodoro", "Free pomo timer (P on check)", push_pomo),
        ("Quit", "Exit flow", app.exit),
    ]


class FlowCommandProvider(Provider):
    """Fuzzy-searchable index over flow's high-leverage actions."""

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for label, help_text, callback in _commands(self.app):
            score = matcher.match(label)
            if score > 0:
                yield Hit(
                    score,
                    matcher.highlight(label),
                    callback,
                    help=help_text,
                )
