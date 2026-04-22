"""Shared help reference — used by both the CLI `flow help` command and the
TUI help modal. Keeping one source of truth preserves CLI/TUI parity."""

from __future__ import annotations


HELP_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "CLI commands",
        [
            ("flow add <name>", "create a new habit"),
            ("flow done <habit>", "mark a habit complete today (--duration 25m logs time)"),
            ("flow undo [habit]", "reverse the most recent completion"),
            ("flow list", "show active habits with momentum"),
            ("flow edit <habit>", "rename / retarget / reschedule"),
            ("flow log", "completion history"),
            ("flow archive <habit>", "soft-delete a habit"),
            ("flow restore <habit>", "bring an archived habit back"),
            ("flow random", "pick a scheduled-but-undone habit"),
            ("flow today", "one-line summary for shell prompts"),
            ("flow export", "CSV/JSON dump"),
            ("flow check", "interactive daily check-in (TUI)"),
            ("flow stats [habit]", "momentum dashboard (TUI)"),
            ("flow pomo [habit]", "pomodoro timer; logs duration when a habit is given"),
            ("flow config", "read/write persistent preferences"),
        ],
    ),
    (
        "Navigation (works on every screen)",
        [
            ("c", "jump to check screen"),
            ("s", "jump to stats screen"),
            ("l", "jump to completion log"),
            ("t", "toggle light / dark theme"),
            ("h", "show this help"),
            ("escape", "back / close modal"),
            ("q", "back, or quit from the home screen"),
        ],
    ),
    (
        "Check screen — TUI",
        [
            ("j / k", "move cursor down / up"),
            ("space", "toggle today's completion"),
            ("v", "set numeric value"),
            ("d", "log time spent (e.g. 25m, 1h30m, 1:30)"),
            ("p", "pomodoro for highlighted habit (setup modal)"),
            ("P", "free pomodoro — no habit, nothing logged"),
            ("n", "add / edit note"),
            ("a", "add a new habit"),
            ("e", "edit highlighted habit (works on off-days too)"),
            ("x", "archive highlighted habit (soft-delete; restore via CLI)"),
            ("u", "undo most recent completion"),
            ("r", "jump to a random scheduled-undone habit"),
        ],
    ),
    (
        "Pomodoro screen — TUI",
        [
            ("space", "pause / resume countdown"),
            ("n", "skip current phase (does not log partial work)"),
            ("q / escape", "exit (completed sessions already saved)"),
        ],
    ),
    (
        "Frequency formats",
        [
            ("daily", "every day"),
            ("weekdays", "Mon–Fri"),
            ("weekly", "Mondays"),
            ("mon,wed,fri", "any subset of weekday tokens"),
            ("monthly", "1st of each month"),
            ("monthly:15", "Nth day (29–31 auto-clamp to month length)"),
            ("monthly:last", "last day of each month"),
            ("every:3", "every N days, anchored on created_at"),
        ],
    ),
    (
        "Seasonal windows",
        [
            ("--start-date", "habit is not scheduled before this date"),
            ("--end-date", "habit is not scheduled after this date"),
            ("TUI", "set start/end date fields in add or edit form"),
        ],
    ),
    (
        "Stats screen — TUI",
        [
            ("j / k", "move cursor"),
            ("enter", "drill into habit detail"),
            ("E", "export"),
            ("A", "toggle archived visibility"),
        ],
    ),
    (
        "Detail screen — TUI",
        [
            ("e", "edit habit"),
            ("x", "archive / restore"),
            ("escape / q", "back"),
        ],
    ),
]


def render_plain() -> str:
    """ASCII rendering suitable for CLI output."""
    out: list[str] = []
    for title, rows in HELP_SECTIONS:
        out.append(title)
        out.append("-" * len(title))
        width = max(len(k) for k, _ in rows)
        for key, desc in rows:
            out.append(f"  {key.ljust(width)}  {desc}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_rich() -> str:
    """Rich markup rendering for TUI / rich console."""
    out: list[str] = []
    for title, rows in HELP_SECTIONS:
        out.append(f"[bold]{title}[/bold]")
        width = max(len(k) for k, _ in rows)
        for key, desc in rows:
            out.append(f"  [cyan]{key.ljust(width)}[/cyan]  [dim]{desc}[/dim]")
        out.append("")
    return "\n".join(out).rstrip()
