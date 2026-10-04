"""Shared base for top-level screens.

Centralizes the cross-screen actions every navigable screen needs (jump-to,
theme toggle, help reference, bindings hint). Each derived screen declares
its own `BINDINGS` — Textual reads BINDINGS from the derived class, not the
MRO chain, so bindings stay explicit per screen. Methods are inherited."""

from __future__ import annotations

from textual.screen import Screen


class BaseTopScreen(Screen):
    """Adds the cross-screen action handlers. Screens still own their own
    `BINDINGS` lists so users see exactly which keys their screen exposes."""

    def action_nav_check(self) -> None:
        self.app.navigate_to("check")

    def action_nav_stats(self) -> None:
        self.app.navigate_to("stats")

    def action_nav_log(self) -> None:
        self.app.navigate_to("log")

    def action_nav_review(self) -> None:
        self.app.navigate_to("review")

    def action_toggle_theme(self) -> None:
        self.app.toggle_theme()

    def action_help(self) -> None:
        # Late import — `.help` imports back into this package's screens
        # module during startup.
        from .help import HelpScreen

        self.app.push_screen(HelpScreen())

    def action_show_bindings(self) -> None:
        self.app.show_bindings(self)
