"""Textual App root. Entry screen is selected at construction time."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from textual.app import App

from .. import config as _config
from ..models import Habit


class FlowApp(App):
    """Top-level app shell. Holds the db path override and which screen to
    show first; the actual UI lives on the screens themselves."""

    TITLE = "flow"
    SUB_TITLE = "momentum habit tracker"

    def __init__(
        self,
        db_path: Path | None = None,
        initial: str = "check",
        today: date | None = None,
        focus_habit: str | None = None,
        detail_habit: Habit | None = None,
    ) -> None:
        super().__init__()
        self.db_path = db_path
        self.initial = initial
        self.today = today
        self.focus_habit = focus_habit
        self.detail_habit = detail_habit

    def on_mount(self) -> None:
        self._apply_theme_from_config()
        if self.initial == "check":
            from .screens.check import CheckScreen

            self.push_screen(CheckScreen(self.db_path, today=self.today))
        elif self.initial == "stats":
            from .screens.stats import StatsScreen

            self.push_screen(
                StatsScreen(
                    self.db_path, today=self.today, focus_habit=self.focus_habit
                )
            )
        elif self.initial == "detail":
            from .screens.detail import DetailScreen

            if self.detail_habit is None:
                raise ValueError("detail initial requires detail_habit")
            self.push_screen(
                DetailScreen(self.db_path, self.detail_habit, today=self.today)
            )
        else:
            raise ValueError(f"unknown initial screen: {self.initial!r}")

    # -- theme helpers ---------------------------------------------------------

    def _apply_theme_from_config(self) -> None:
        theme = _config.get("theme")
        self.dark = theme != "light"

    def toggle_theme(self) -> None:
        """Flip dark <-> light and persist so the choice survives restarts."""
        self.dark = not self.dark
        _config.set_value("theme", "dark" if self.dark else "light")
