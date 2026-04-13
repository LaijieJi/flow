"""Textual App root. Entry screen is selected at construction time."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from textual.app import App


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
    ) -> None:
        super().__init__()
        self.db_path = db_path
        self.initial = initial
        self.today = today

    def on_mount(self) -> None:
        if self.initial == "check":
            from .screens.check import CheckScreen

            self.push_screen(CheckScreen(self.db_path, today=self.today))
        else:
            raise ValueError(f"unknown initial screen: {self.initial!r}")
