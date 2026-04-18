"""Shared help reference — used by both the CLI `flow help` command and the
TUI help modal. Keeping one source of truth preserves CLI/TUI parity."""

from __future__ import annotations


HELP_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "CLI commands",
        [
            ("flow add <name>", "create a new habit"),
            ("flow done <habit>", "mark a habit complete today"),
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
            ("flow config", "read/write persistent preferences"),
        ],
    ),
    (
        "Check screen — TUI",
        [
            ("j / k", "move cursor down / up"),
            ("space", "toggle today's completion"),
            ("v", "set numeric value"),
            ("n", "add / edit note"),
            ("a", "add a new habit"),
            ("u", "undo most recent completion"),
            ("r", "jump to a random scheduled-undone habit"),
            ("t", "toggle light / dark theme"),
            ("h", "show this help"),
            ("q", "quit"),
        ],
    ),
    (
        "Stats screen — TUI",
        [
            ("j / k", "move cursor"),
            ("enter", "drill into habit detail"),
            ("l", "completion log"),
            ("E", "export"),
            ("A", "toggle archived visibility"),
            ("t", "toggle light / dark theme"),
            ("h", "show this help"),
            ("q", "quit"),
        ],
    ),
    (
        "Detail screen — TUI",
        [
            ("e", "edit habit"),
            ("x", "archive / restore"),
            ("h", "show this help"),
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
