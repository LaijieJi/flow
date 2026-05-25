"""Shared help reference — used by both the CLI `flow help` command and the
TUI help modal. Keeping one source of truth preserves CLI/TUI parity."""

from __future__ import annotations


HELP_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "First-run setup",
        [
            ("flow init", "interactive wizard: pick starter habits from templates"),
            ("flow init --template reading", "install one or more bundled templates non-interactively"),
            ("flow templates", "list bundled starter templates (reading, meditation, workout, …)"),
            ("flow add --template <key>", "install one template, override fields with the usual flags"),
        ],
    ),
    (
        "CLI commands",
        [
            ("flow add <name>", "create a new habit"),
            ("flow done <habit> [VALUE]", "mark complete; positional `25m` or `15` routes to duration/value"),
            ("flow skip <habit>", "mark a day deliberately skipped (vacation/sick/rest)"),
            ("flow pause <habit>", "bulk skip a window (--until DATE | --days N)"),
            ("flow resume <habit>", "clear future skips for a paused habit"),
            ("flow why <habit>", "explain current momentum score with day-by-day breakdown"),
            ("flow undo [habit]", "reverse the most recent completion"),
            ("flow list", "show active habits with momentum"),
            ("flow edit <habit>", "rename / retarget / reschedule"),
            ("flow log", "completion history"),
            ("flow archive <habit>", "soft-delete a habit"),
            ("flow restore <habit>", "bring an archived habit back"),
            ("flow random", "pick a scheduled-but-undone habit"),
            ("flow today [--format count]", "one-line summary for shell prompts"),
            ("flow status [--format json] [--watch N]", "full status across habits; JSON or live-refresh"),
            ("flow alias set|remove|list", "short-form habit aliases (e.g. `flow done r`)"),
            ("flow export", "CSV/JSON dump"),
            ("flow import <file>", "load habits/completions from JSON (--conflict skip|overwrite|merge)"),
            ("flow backup", "atomic DB snapshot to ~/.flow/backups/"),
            ("flow doctor [--fix]", "scan DB for orphans / dupes / invariant violations"),
            ("flow prune --days N", "hard-delete habits archived > N days ago"),
            ("flow week / flow month", "condensed digest for current period"),
            ("flow summary --out week.md", "markdown digest for journaling"),
            ("flow correlations", "'when A is done, B is also done' pairs"),
            ("flow check", "interactive daily check-in (TUI)"),
            ("flow stats [habit] [--watch N]", "momentum dashboard; --watch auto-refreshes every N seconds"),
            ("flow pomo [habit]", "pomodoro timer; logs duration when a habit is given"),
            ("flow remind", "fire a desktop notification with today's summary (cron target)"),
            ("flow install-cron HH:MM", "install/remove a daily-reminder crontab entry"),
            ("flow config", "read/write persistent preferences (theme, notifications)"),
        ],
    ),
    (
        "Smart input on `flow done`",
        [
            ("flow done Read 15", "bare numeric → --value 15"),
            ("flow done Meditate 25m", "duration shape → --duration 25m (and derives value for time-unit habits)"),
            ("flow done r 20", "aliases resolve before fuzzy match"),
            ("(mutually exclusive)", "positional VALUE cannot be combined with --value / --duration"),
        ],
    ),
    (
        "Navigation (works on every top-level screen)",
        [
            ("c", "jump to check screen"),
            ("s", "jump to stats screen"),
            ("l", "jump to completion log"),
            ("R", "jump to review screen (week / month digest + correlations)"),
            ("t", "toggle light / dark theme"),
            ("h", "show this help (full reference)"),
            ("?", "show this screen's bindings only (compact)"),
            ("ctrl+\\", "open the command palette (fuzzy command finder)"),
            ("escape", "back / close modal"),
            ("q", "back, or quit from the home screen"),
        ],
    ),
    (
        "Check screen — TUI",
        [
            ("j / k", "move cursor down / up"),
            ("space", "toggle today's completion (done)"),
            ("S", "toggle skip (vacation/sick — doesn't decay momentum)"),
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
            ("e", "edit habit (alpha too)"),
            ("x", "archive / restore"),
            ("escape / q", "back"),
            ("(panels)", "score sparkline · 30-day grid · year heatmap · time-of-day strip · recent notes (newest in Markdown)"),
        ],
    ),
    (
        "Review screen — TUI",
        [
            ("w", "show week digest"),
            ("m", "show month digest"),
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
