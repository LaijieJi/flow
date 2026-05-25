"""Click-based CLI entry point for flow.

Commands routed here are the non-TUI surface: add / done / list / archive.
TUI-backed commands (check, stats) land in later phases. Every command is
scripting-friendly: exit codes are stable, output is plain when possible.
"""

from __future__ import annotations

import json as _json
import random as _random
import sys
from datetime import date, datetime, timedelta
from typing import Iterable

import click
from dateutil import parser as dateparser
from rich.console import Console
from rich.table import Table

from . import (
    aliases as _aliases,
    backup as _backup,
    config as _config,
    cron as _cron,
    db,
    doctor as _doctor,
    export as _export,
    help_text,
    importer as _importer,
    notify as _notify,
    review as _review,
    templates as _templates,
)
from .models import (
    COMPLETION_STATUS_DONE,
    COMPLETION_STATUS_SKIPPED,
    Completion,
    Habit,
    format_duration,
    parse_duration,
    parse_frequency,
)
from .momentum import (
    completion_strength,
    compute_momentum,
    compute_streaks,
    scheduled_days_between,
)


console = Console()
err_console = Console(stderr=True)


# ---- habit name resolution ----------------------------------------------------


def _resolve_habit(
    conn, query: str, include_archived: bool = False
) -> Habit:
    """Resolve a user-typed habit name to a single Habit.

    Matching order (case-insensitive): alias → exact → prefix → substring.
    Errors on zero or multiple matches at any tier. Aliases that point to a
    missing habit fall through to fuzzy matching instead of erroring, so a
    stale alias never silently blocks normal name resolution."""
    habits = db.list_habits(conn, include_archived=include_archived)
    if not habits:
        raise click.ClickException("no habits exist yet — try `flow add`")
    q = query.strip().lower()

    aliased = _aliases.resolve(q)
    if aliased is not None:
        for h in habits:
            if h.name.lower() == aliased.lower():
                return h

    def _pick(pool: Iterable[Habit], tier: str) -> Habit | None:
        matches = list(pool)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            names = ", ".join(h.name for h in matches)
            raise click.ClickException(
                f"ambiguous {query!r} ({tier} match): {names}"
            )
        return None

    hit = _pick((h for h in habits if h.name.lower() == q), "exact")
    if hit:
        return hit
    hit = _pick((h for h in habits if h.name.lower().startswith(q)), "prefix")
    if hit:
        return hit
    hit = _pick((h for h in habits if q in h.name.lower()), "substring")
    if hit:
        return hit
    raise click.ClickException(f"no habit matches {query!r}")


def _parse_date_flag(value: str | None) -> date:
    if not value:
        return date.today()
    try:
        parsed = dateparser.parse(value).date()
    except (ValueError, TypeError, OverflowError) as e:
        raise click.ClickException(f"invalid --date {value!r}: {e}")
    if parsed > date.today():
        raise click.ClickException(f"--date {parsed.isoformat()} is in the future")
    return parsed


# ---- commands -----------------------------------------------------------------


@click.group(
    help="flow — momentum-based habit tracker. Run `flow` (no args) to open the TUI.",
    invoke_without_command=True,
)
@click.version_option(package_name="flow")
@click.pass_context
def main(ctx: click.Context) -> None:
    if ctx.invoked_subcommand is None:
        from .tui.app import FlowApp

        FlowApp().run()


FREQUENCY_HELP = (
    "daily | weekdays | weekly | mon,wed,fri | "
    "monthly[:N|:last] | every:N"
)


def _parse_optional_date(value: str | None, flag: str) -> date | None:
    if value is None:
        return None
    if value == "":
        return None
    try:
        return dateparser.parse(value).date()
    except (ValueError, TypeError, OverflowError) as e:
        raise click.ClickException(f"invalid {flag} {value!r}: {e}")


@main.command(help="Add a new habit.")
@click.argument("name", required=False)
@click.option(
    "--frequency",
    "-f",
    default="daily",
    show_default=True,
    help=FREQUENCY_HELP,
)
@click.option("--unit", default=None, help="e.g. minutes, pages, reps")
@click.option(
    "--target", type=float, default=None, help="numeric target (requires --unit)"
)
@click.option("--description", "-d", default=None)
@click.option(
    "--start-date", default=None, help="seasonal start (YYYY-MM-DD, optional)"
)
@click.option(
    "--end-date", default=None, help="seasonal end (YYYY-MM-DD, optional)"
)
@click.option(
    "--alpha",
    type=float,
    default=None,
    help=f"momentum smoothing (0.01–1.0, default {Habit.ALPHA_DEFAULT})",
)
@click.option(
    "--template",
    "-t",
    "template_key",
    default=None,
    help="install a bundled template (see `flow templates`)",
)
@click.pass_context
def add(
    ctx: click.Context,
    name: str | None,
    frequency: str,
    unit: str | None,
    target: float | None,
    description: str | None,
    start_date: str | None,
    end_date: str | None,
    alpha: float | None,
    template_key: str | None,
) -> None:
    if template_key is not None:
        tpl = _templates.get_template(template_key)
        if tpl is None:
            raise click.ClickException(
                f"unknown template {template_key!r} — try `flow templates`"
            )
        # User-provided CLI values override the template; defaults yield to it.
        # We detect default vs user-supplied for `--frequency` via Click's
        # parameter-source API because both produce a non-None string.
        name = name or tpl.name
        freq_source = ctx.get_parameter_source("frequency")
        if freq_source is not None and freq_source.name == "DEFAULT":
            frequency = tpl.frequency
        unit = unit or tpl.unit
        target = target if target is not None else tpl.target
        description = description or tpl.description

    if name is None:
        raise click.ClickException(
            "NAME is required (or pass --template to use a bundled starter)"
        )

    try:
        parse_frequency(frequency)
    except ValueError as e:
        raise click.ClickException(str(e))
    if target is not None and unit is None:
        raise click.ClickException("--target requires --unit")

    start = _parse_optional_date(start_date, "--start-date")
    end = _parse_optional_date(end_date, "--end-date")
    if start is not None and end is not None and end < start:
        raise click.ClickException("--end-date cannot be before --start-date")

    if alpha is not None and not Habit.ALPHA_MIN <= alpha <= Habit.ALPHA_MAX:
        raise click.ClickException(
            f"--alpha must be in [{Habit.ALPHA_MIN}, {Habit.ALPHA_MAX}]"
        )

    with db.session() as conn:
        existing = [h for h in db.list_habits(conn, include_archived=True) if h.name.lower() == name.lower()]
        if existing:
            raise click.ClickException(f"habit named {name!r} already exists")
        habit = Habit(
            name=name,
            frequency=frequency,
            unit=unit,
            target=target,
            description=description,
            start_date=start,
            end_date=end,
            alpha=alpha if alpha is not None else Habit.ALPHA_DEFAULT,
        )
        db.insert_habit(conn, habit)

    summary = f"[green]added[/green] {name} [dim]({frequency}"
    if unit:
        summary += f", {target or ''} {unit}".rstrip()
    summary += ")[/dim]"
    console.print(summary)


_TIME_UNITS = {"minutes", "minute", "min", "mins", "hours", "hour", "hr", "hrs"}


def _parse_smart_value(raw: str) -> tuple[float | None, str | None]:
    """Parse a positional value/duration string and route it.

    Returns `(value, duration_str)` where exactly one is non-None:
      - bare numbers ("20", "1.5") → value
      - anything else (a duration shape: "25m", "1h30m", "1:30") → duration_str

    `float()` succeeds on bare numerics but rejects unit-suffixed strings, so
    using it as a first filter is enough to distinguish the two cases without
    duplicating `parse_duration`'s parsing rules."""
    raw = raw.strip()
    if not raw:
        raise click.ClickException("empty value")
    try:
        return float(raw), None
    except ValueError:
        pass
    # Validate as a duration here so the user gets a clearer error than the
    # generic "can't parse" one downstream.
    try:
        parse_duration(raw)
    except ValueError:
        raise click.ClickException(
            f"can't parse {raw!r} — expected number or duration (25m, 1h30m, 1:30)"
        )
    return None, raw


def _value_from_duration(unit: str | None, duration_seconds: int) -> float | None:
    """For time-unit habits, derive a numeric value from a recorded duration.

    'minutes'-style units → float minutes; 'hours'-style → float hours.
    Anything else: leave value untouched (caller supplies --value explicitly).
    """
    if unit is None:
        return None
    u = unit.lower()
    if u in {"minutes", "minute", "min", "mins"}:
        return duration_seconds / 60
    if u in {"hours", "hour", "hr", "hrs"}:
        return duration_seconds / 3600
    return None


@main.command(help="Mark a habit complete.")
@click.argument("habit")
@click.argument("smart", required=False, metavar="[VALUE]")
@click.option("--value", type=float, default=None, help="numeric value (for target habits)")
@click.option("--note", default=None, help="short reflection (max 280 chars)")
@click.option(
    "--duration",
    "duration_str",
    default=None,
    help="time spent (e.g. 25m, 1h30m, 90s, 1:30)",
)
@click.option("--date", "date_str", default=None, help="override completion date")
def done(
    habit: str,
    smart: str | None,
    value: float | None,
    note: str | None,
    duration_str: str | None,
    date_str: str | None,
) -> None:
    on = _parse_date_flag(date_str)
    if note is not None and len(note) > Habit.NOTE_MAX:
        raise click.ClickException(f"note too long (max {Habit.NOTE_MAX} chars)")

    # Smart positional: `flow done Read 20` or `flow done Meditate 25m` —
    # routes the input to --value or --duration based on shape. Explicit
    # flags win; if both flags and the positional are given, refuse rather
    # than guess which the user meant.
    if smart is not None:
        if value is not None or duration_str is not None:
            raise click.ClickException(
                "positional VALUE conflicts with --value/--duration"
            )
        smart_value, smart_duration = _parse_smart_value(smart)
        value = smart_value
        duration_str = smart_duration

    duration_seconds: int | None = None
    if duration_str is not None:
        try:
            duration_seconds = parse_duration(duration_str)
        except ValueError as e:
            raise click.ClickException(str(e))

    with db.session() as conn:
        h = _resolve_habit(conn, habit, include_archived=True)
        if h.is_archived:
            raise click.ClickException(f"{h.name} is archived — restore first")
        if value is None and duration_seconds is not None:
            derived = _value_from_duration(h.unit, duration_seconds)
            if derived is not None:
                value = derived
        if value is not None and h.target is None:
            err_console.print(
                f"[yellow]warn:[/yellow] {h.name} has no target; value recorded but won't scale momentum"
            )
        db.upsert_completion(
            conn,
            Completion(
                habit_id=h.id,
                date=on,
                value=value,
                note=note,
                duration_seconds=duration_seconds,
                completed_at=datetime.now() if date_str is None else None,
            ),
        )

    display = "today" if on == date.today() else on.isoformat()
    bits: list[str] = []
    if value is not None:
        bits.append(f"{value:g} {h.unit or ''}".strip())
    if duration_seconds is not None:
        bits.append(format_duration(duration_seconds))
    suffix = f" [dim]({', '.join(bits)})[/dim]" if bits else ""
    console.print(f"[green]✓[/green] {h.name}{suffix} [dim]— {display}[/dim]")


@main.command("list", help="List habits with momentum.")
@click.option("--all", "show_all", is_flag=True, help="include archived")
@click.option(
    "--window", type=int, default=14, show_default=True, help="rolling rate window (days)"
)
def list_cmd(show_all: bool, window: int) -> None:
    with db.session() as conn:
        habits = db.list_habits(conn, include_archived=show_all)
        if not habits:
            console.print("[dim]no habits yet — try `flow add`[/dim]")
            return

        table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
        table.add_column("id", justify="right", style="dim")
        table.add_column("habit")
        table.add_column("freq", style="dim")
        table.add_column("score", justify="right")
        table.add_column("trend", justify="center")
        table.add_column(f"rate ({window}d)", justify="right")
        if show_all:
            table.add_column("archived", style="red")

        today_ = date.today()
        for h in habits:
            comps = db.completions_for_habit(conn, h.id)
            mom = compute_momentum(h, comps, window_days=window)
            paused_until = db.paused_until_date(conn, h.id, today_)
            name_cell = h.name
            if paused_until is not None:
                name_cell += f" [yellow](paused → {paused_until.isoformat()})[/yellow]"
            row = [
                str(h.id),
                name_cell,
                h.frequency,
                f"{mom.score:.0f}",
                mom.trend,
                f"{mom.completion_rate:.0%}",
            ]
            if show_all:
                row.append(h.archived_at.isoformat() if h.archived_at else "")
            table.add_row(*row)

        console.print(table)


@main.command(help="Edit an existing habit. Pass empty string to clear a field.")
@click.argument("habit")
@click.option("--name", default=None, help="rename the habit")
@click.option("--frequency", "-f", default=None, help=FREQUENCY_HELP)
@click.option("--unit", default=None, help="e.g. minutes, pages (empty string clears)")
@click.option("--target", default=None, help="numeric target (empty string clears)")
@click.option("--description", "-d", default=None, help="short description (empty string clears)")
@click.option(
    "--start-date", default=None, help="seasonal start (YYYY-MM-DD, empty clears)"
)
@click.option(
    "--end-date", default=None, help="seasonal end (YYYY-MM-DD, empty clears)"
)
@click.option(
    "--alpha",
    default=None,
    help=f"momentum smoothing (0.01–1.0, empty resets to {Habit.ALPHA_DEFAULT})",
)
def edit(
    habit: str,
    name: str | None,
    frequency: str | None,
    unit: str | None,
    target: str | None,
    description: str | None,
    start_date: str | None,
    end_date: str | None,
    alpha: str | None,
) -> None:
    with db.session() as conn:
        h = _resolve_habit(conn, habit, include_archived=True)

        no_flags = all(
            v is None
            for v in (name, frequency, unit, target, description, start_date, end_date, alpha)
        )
        if no_flags:
            initial = h.description or ""
            edited = click.edit(text=initial, require_save=True, extension=".txt")
            if edited is None:
                console.print("[dim]no changes[/dim]")
                return
            h.description = edited.strip() or None
        else:
            if name is not None:
                if not name.strip():
                    raise click.ClickException("--name cannot be empty")
                if name.lower() != h.name.lower():
                    clash = db.find_habit_by_name(conn, name)
                    if clash is not None and clash.id != h.id:
                        raise click.ClickException(f"habit named {name!r} already exists")
                h.name = name
            if frequency is not None:
                try:
                    parse_frequency(frequency)
                except ValueError as e:
                    raise click.ClickException(str(e))
                h.frequency = frequency
            if unit is not None:
                h.unit = unit or None
            if target is not None:
                if target == "":
                    h.target = None
                else:
                    try:
                        h.target = float(target)
                    except ValueError:
                        raise click.ClickException(f"invalid --target {target!r}")
            if description is not None:
                h.description = description or None
            if start_date is not None:
                h.start_date = _parse_optional_date(start_date, "--start-date")
            if end_date is not None:
                h.end_date = _parse_optional_date(end_date, "--end-date")
            if alpha is not None:
                if alpha == "":
                    h.alpha = Habit.ALPHA_DEFAULT
                else:
                    try:
                        a = float(alpha)
                    except ValueError:
                        raise click.ClickException(f"invalid --alpha {alpha!r}")
                    if not Habit.ALPHA_MIN <= a <= Habit.ALPHA_MAX:
                        raise click.ClickException(
                            f"--alpha must be in [{Habit.ALPHA_MIN}, {Habit.ALPHA_MAX}]"
                        )
                    h.alpha = a
            if (
                h.start_date is not None
                and h.end_date is not None
                and h.end_date < h.start_date
            ):
                raise click.ClickException("--end-date cannot be before --start-date")
            if h.target is not None and h.unit is None:
                raise click.ClickException("--target requires --unit")

        db.update_habit(conn, h)

    console.print(f"[green]updated[/green] {h.name}")


@main.command(help="Completion history (newest first).")
@click.option("--habit", default=None, help="filter to a single habit")
@click.option(
    "--days", type=int, default=30, show_default=True, help="lookback window in days"
)
def log(habit: str | None, days: int) -> None:
    if days <= 0:
        raise click.ClickException("--days must be positive")
    since = date.today() - timedelta(days=days - 1)

    with db.session() as conn:
        habit_id: int | None = None
        if habit is not None:
            habit_id = _resolve_habit(conn, habit, include_archived=True).id
        pairs = db.all_completions(conn, since=since, habit_id=habit_id)

        if not pairs:
            console.print("[dim]no completions in window[/dim]")
            return

        table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
        table.add_column("date", style="dim")
        table.add_column("habit")
        table.add_column("value", justify="right")
        table.add_column("time", justify="right", style="dim")
        table.add_column("note", style="dim")

        for c, h in pairs:
            if c.is_skipped:
                val = "[yellow]⊘ skip[/yellow]"
            elif c.value is not None:
                val = f"{c.value:g}"
                if h.unit:
                    val += f" {h.unit}"
            else:
                val = "✓"
            table.add_row(
                c.date.isoformat(),
                h.name,
                val,
                format_duration(c.duration_seconds),
                c.note or "",
            )
        console.print(table)


@main.command(help="Interactive daily check-in (TUI).")
def check() -> None:
    from .tui.app import FlowApp

    FlowApp().run()


@main.command(help="Momentum dashboard (TUI). Pass a habit to drill in.")
@click.argument("habit", required=False)
@click.option(
    "--watch",
    "watch_interval",
    type=int,
    default=None,
    metavar="SECONDS",
    help="auto-refresh the dashboard every N seconds",
)
def stats(habit: str | None, watch_interval: int | None) -> None:
    from .tui.app import FlowApp

    if watch_interval is not None and watch_interval < 1:
        raise click.ClickException("--watch must be >= 1")

    if habit is None:
        FlowApp(initial="stats", watch_interval=watch_interval).run()
        return

    with db.session() as conn:
        h = _resolve_habit(conn, habit, include_archived=True)
    FlowApp(
        initial="detail", detail_habit=h, watch_interval=watch_interval
    ).run()


@main.command(
    help=(
        "Pomodoro timer (TUI). Pass a habit to auto-log each completed work "
        "session; omit it for a free-running timer that only rings the bell."
    ),
)
@click.argument("habit", required=False)
@click.option(
    "--work",
    "work_str",
    default="25m",
    show_default=True,
    help="work phase length (e.g. 25m, 50m, 1h)",
)
@click.option(
    "--break",
    "break_str",
    default="5m",
    show_default=True,
    help="break phase length (set to 0 to skip breaks)",
)
@click.option(
    "--cycles", type=int, default=1, show_default=True, help="number of work/break cycles"
)
def pomo(habit: str | None, work_str: str, break_str: str, cycles: int) -> None:
    if cycles < 1:
        raise click.ClickException("--cycles must be >= 1")
    try:
        work_seconds = parse_duration(work_str)
    except ValueError as e:
        raise click.ClickException(f"--work: {e}")
    if work_seconds < 1:
        raise click.ClickException("--work must be > 0")
    try:
        break_seconds = 0 if break_str.strip() in {"0", "0s", "0m"} else parse_duration(break_str)
    except ValueError as e:
        raise click.ClickException(f"--break: {e}")
    if break_seconds < 0:
        raise click.ClickException("--break must be >= 0")

    resolved: Habit | None = None
    if habit is not None:
        with db.session() as conn:
            resolved = _resolve_habit(conn, habit, include_archived=False)
            if resolved.is_archived:
                raise click.ClickException(f"{resolved.name} is archived — restore first")

    from .tui.app import FlowApp

    FlowApp(
        initial="pomo",
        pomo_habit=resolved,
        pomo_work_seconds=work_seconds,
        pomo_break_seconds=break_seconds,
        pomo_cycles=cycles,
    ).run()


@main.command(help="Export habit data (CSV or JSON). Stdout by default.")
@click.option(
    "--format",
    "-F",
    "fmt",
    type=click.Choice(["csv", "json"]),
    default="csv",
    show_default=True,
)
@click.option("--all", "include_archived", is_flag=True, help="include archived habits")
@click.option("--habit", default=None, help="filter to a single habit (exact name)")
@click.option(
    "--output",
    "-o",
    "output_path",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help="write to file instead of stdout",
)
def export(
    fmt: str,
    include_archived: bool,
    habit: str | None,
    output_path: str | None,
) -> None:
    if habit is not None:
        with db.session() as conn:
            match = db.find_habit_by_name(conn, habit, include_archived=True)
            if match is None:
                raise click.ClickException(f"no habit named {habit!r}")

    stream: object
    opened_file = None
    if output_path is None:
        stream = sys.stdout
    else:
        opened_file = open(output_path, "w", encoding="utf-8", newline="")
        stream = opened_file

    try:
        with db.session() as conn:
            if fmt == "csv":
                _export.write_csv(conn, stream, include_archived, habit)
            else:
                _export.write_json(conn, stream, include_archived, habit)
    finally:
        if opened_file is not None:
            opened_file.close()


@main.command("import", help="Import habits + completions from a JSON file.")
@click.argument("source", type=click.Path(dir_okay=False, exists=True, readable=True))
@click.option(
    "--conflict",
    type=click.Choice(list(_importer.CONFLICT_MODES)),
    default="skip",
    show_default=True,
    help="how to handle name collisions with existing habits",
)
def import_cmd(source: str, conflict: str) -> None:
    with open(source, "r", encoding="utf-8") as f:
        try:
            payload = _importer.read_json(f)
        except _importer.FlowImportError as e:
            raise click.ClickException(str(e))
    with db.session() as conn:
        try:
            stats = _importer.import_payload(conn, payload, conflict=conflict)
        except _importer.FlowImportError as e:
            raise click.ClickException(str(e))
    parts = [
        f"+{stats.habits_added} habits",
        f"~{stats.habits_overwritten} overwritten",
        f"~{stats.habits_merged} merged",
        f"·{stats.habits_skipped} skipped",
        f"+{stats.completions_added} completions",
        f"·{stats.completions_skipped} completions skipped",
    ]
    console.print(f"[green]imported[/green] " + " · ".join(parts))


@main.command(help="Snapshot the database to a dated file (cron-friendly).")
@click.option(
    "--output",
    "-o",
    "output_path",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help="explicit destination path (defaults to ~/.flow/backups/habits-DATE.db)",
)
def backup(output_path: str | None) -> None:
    try:
        dest = _backup.snapshot(dest_path=output_path)
    except FileExistsError as e:
        raise click.ClickException(str(e))
    except FileNotFoundError as e:
        raise click.ClickException(str(e))
    size_kb = dest.stat().st_size / 1024
    console.print(f"[green]backup[/green] {dest} [dim]({size_kb:.1f} KB)[/dim]")


@main.command(help="Hard-delete habits archived more than --days days ago.")
@click.option(
    "--days",
    type=int,
    default=90,
    show_default=True,
    help="minimum archived age before a habit is eligible for deletion",
)
@click.option("--dry-run", is_flag=True, help="list what would be deleted, change nothing")
@click.option("--yes", is_flag=True, help="skip the confirmation prompt")
def prune(days: int, dry_run: bool, yes: bool) -> None:
    if days < 0:
        raise click.ClickException("--days must be >= 0")
    cutoff = date.today() - timedelta(days=days)
    with db.session() as conn:
        candidates = db.archived_habits_older_than(conn, cutoff)
        if not candidates:
            console.print(
                f"[dim]nothing to prune (no habits archived on or before {cutoff.isoformat()})[/dim]"
            )
            return
        for h in candidates:
            console.print(
                f"  [red]-[/red] {h.name} [dim](archived {h.archived_at.isoformat()})[/dim]"
            )
        if dry_run:
            console.print(f"[dim]dry run — {len(candidates)} habit(s) would be deleted[/dim]")
            return
        if not yes and not click.confirm(
            f"Hard-delete {len(candidates)} habit(s) and all their completions?",
            default=False,
        ):
            console.print("[dim]aborted[/dim]")
            return
        for h in candidates:
            db.delete_habit(conn, h.id)
    console.print(f"[red]pruned[/red] {len(candidates)} habit(s)")


@main.group(invoke_without_command=True, help="Manage short-form habit aliases.")
@click.pass_context
def alias(ctx: click.Context) -> None:
    if ctx.invoked_subcommand is None:
        _alias_list()


@alias.command("list", help="Show all configured aliases.")
def alias_list_cmd() -> None:
    _alias_list()


def _alias_list() -> None:
    aliases = _aliases.load()
    if not aliases:
        console.print("[dim]no aliases yet — try `flow alias set <short> <habit>`[/dim]")
        return
    for short, target in sorted(aliases.items()):
        console.print(f"  [cyan]{short}[/cyan] → {target}")


@alias.command("set", help="Add or update an alias.")
@click.argument("short")
@click.argument("habit")
def alias_set_cmd(short: str, habit: str) -> None:
    # Verify the target habit exists right now so a typo surfaces immediately.
    # Aliases that go stale later (habit renamed/archived) fall back to fuzzy
    # match silently — see `_resolve_habit`.
    with db.session() as conn:
        h = _resolve_habit(conn, habit, include_archived=True)
    _aliases.set_alias(short, h.name)
    console.print(f"[green]aliased[/green] [cyan]{short}[/cyan] → {h.name}")


@alias.command("remove", help="Remove an alias.")
@click.argument("short")
def alias_remove_cmd(short: str) -> None:
    if _aliases.remove(short):
        console.print(f"[red]removed[/red] alias [cyan]{short}[/cyan]")
    else:
        raise click.ClickException(f"no alias named {short!r}")


@main.command(help="List bundled habit templates.")
def templates() -> None:
    for tpl in _templates.list_templates():
        bits = [f"[bold]{tpl.key}[/bold]", tpl.name, f"({tpl.frequency})"]
        if tpl.target and tpl.unit:
            bits.append(f"target {tpl.target:g} {tpl.unit}")
        elif tpl.unit:
            bits.append(f"unit {tpl.unit}")
        console.print("  " + "  ".join(bits))
        if tpl.description:
            console.print(f"      [dim]{tpl.description}[/dim]")


@main.command(help="First-time setup wizard. Picks starter habits from templates.")
@click.option(
    "--template",
    "-t",
    "preset_keys",
    multiple=True,
    help="install named template(s) non-interactively (repeatable)",
)
@click.option("--yes", is_flag=True, help="skip the confirmation prompt")
def init(preset_keys: tuple[str, ...], yes: bool) -> None:
    with db.session() as conn:
        existing = db.list_habits(conn, include_archived=True)

    if existing and not preset_keys:
        console.print(
            f"flow already has [bold]{len(existing)}[/bold] habit"
            f"{'s' if len(existing) != 1 else ''} — try `flow add` to add more"
        )
        return

    if preset_keys:
        chosen = _resolve_templates(preset_keys)
    else:
        chosen = _run_init_wizard(yes=yes)

    if not chosen:
        console.print("[dim]nothing installed[/dim]")
        return

    _install_templates(chosen)


def _resolve_templates(keys: tuple[str, ...]) -> list[_templates.Template]:
    out: list[_templates.Template] = []
    for key in keys:
        tpl = _templates.get_template(key)
        if tpl is None:
            raise click.ClickException(
                f"unknown template {key!r} — try `flow templates`"
            )
        out.append(tpl)
    return out


def _run_init_wizard(yes: bool) -> list[_templates.Template]:
    """Interactive picker. Returns the templates the user selected."""
    catalog = _templates.list_templates()
    console.print("[bold]Welcome to flow.[/bold]\n")
    console.print("Pick from these starter habits, or skip and add your own later:\n")
    for i, tpl in enumerate(catalog, start=1):
        bits = f"{tpl.name} [dim]({tpl.frequency}"
        if tpl.target and tpl.unit:
            bits += f", target {tpl.target:g} {tpl.unit}"
        bits += ")[/dim]"
        console.print(f"  {i:>2}. {bits}")
    console.print(
        "\n[dim]Enter numbers separated by commas (e.g. 1,3,5), "
        "'all', or empty to skip:[/dim]"
    )
    answer = click.prompt("choice", default="", show_default=False).strip()

    if not answer:
        return []
    if answer.lower() == "all":
        chosen = list(catalog)
    else:
        chosen = []
        for token in answer.split(","):
            token = token.strip()
            if not token:
                continue
            if not token.isdigit():
                raise click.ClickException(f"invalid selection: {token!r}")
            n = int(token)
            if not 1 <= n <= len(catalog):
                raise click.ClickException(
                    f"selection {n} out of range (1..{len(catalog)})"
                )
            chosen.append(catalog[n - 1])

    if not yes:
        names = ", ".join(t.name for t in chosen)
        if not click.confirm(f"Install {len(chosen)} habit(s): {names}?", default=True):
            return []
    return chosen


def _install_templates(chosen: list[_templates.Template]) -> None:
    """Insert templates, skipping any whose name collides case-insensitively
    with an existing habit. Collisions are warnings, not errors — the wizard
    should remain re-runnable after partial failures."""
    with db.session() as conn:
        existing_names = {
            h.name.lower()
            for h in db.list_habits(conn, include_archived=True)
        }
        installed: list[str] = []
        skipped: list[str] = []
        for tpl in chosen:
            if tpl.name.lower() in existing_names:
                skipped.append(tpl.name)
                continue
            db.insert_habit(
                conn,
                Habit(
                    name=tpl.name,
                    frequency=tpl.frequency,
                    unit=tpl.unit,
                    target=tpl.target,
                    description=tpl.description,
                ),
            )
            installed.append(tpl.name)
            existing_names.add(tpl.name.lower())

    if installed:
        console.print(
            f"[green]added[/green] {len(installed)} habit(s): {', '.join(installed)}"
        )
    if skipped:
        console.print(
            f"[yellow]skipped[/yellow] {len(skipped)} (already exist): "
            f"{', '.join(skipped)}"
        )
    if installed:
        console.print("[dim]Try `flow check` to start your first day.[/dim]")


@main.command(help="Sanity-check the database (orphan rows, dupes, invariants).")
@click.option(
    "--fix",
    is_flag=True,
    help="apply safe fixes (delete orphans, future-dated rows, clear stamps on skips)",
)
@click.option(
    "--quiet", is_flag=True, help="suppress output if no issues found"
)
def doctor(fix: bool, quiet: bool) -> None:
    # Exit code is deferred until after `db.session()` has committed any
    # applied fixes — `sys.exit()` raises SystemExit, which is a BaseException
    # the session's `except Exception:` doesn't catch, but it also bypasses
    # the post-`yield` `conn.commit()`, silently losing fixes. Set the flag
    # inside the block, raise the exit after.
    needs_failure_exit = False
    with db.session() as conn:
        issues = _doctor.run_checks(conn)

        if not issues:
            if not quiet:
                console.print("[green]✓[/green] no issues found")
            return

        for issue in issues:
            marker = "[yellow]⚠[/yellow]" if issue.fixable else "[red]✗[/red]"
            console.print(f"{marker} [bold]{issue.code}[/bold]: {issue.summary}")
            if issue.detail:
                console.print(f"  [dim]{issue.detail}[/dim]")

        if fix:
            fixed = _doctor.apply_fixes(conn, issues)
            for code, n in fixed.items():
                console.print(f"  [green]fixed {code}:[/green] {n} row(s)")
            unfixable = [i for i in issues if not i.fixable]
            if unfixable:
                console.print(
                    f"[yellow]{len(unfixable)} issue(s) need manual review.[/yellow]"
                )
                needs_failure_exit = True
        else:
            console.print(
                f"\n[red]{len(issues)} issue(s) found.[/red] "
                f"Re-run with --fix to apply safe repairs."
            )
            needs_failure_exit = True

    if needs_failure_exit:
        sys.exit(1)


@main.command(help="Archive a habit (soft delete, data preserved).")
@click.argument("habit")
def archive(habit: str) -> None:
    with db.session() as conn:
        h = _resolve_habit(conn, habit, include_archived=True)
        if not db.archive_habit(conn, h.id):
            raise click.ClickException(f"{h.name} is already archived")
    console.print(f"[yellow]archived[/yellow] {h.name}")


@main.command(help="Restore (unarchive) a habit.")
@click.argument("habit")
def restore(habit: str) -> None:
    with db.session() as conn:
        h = _resolve_habit(conn, habit, include_archived=True)
        if not db.restore_habit(conn, h.id):
            raise click.ClickException(f"{h.name} is not archived")
    console.print(f"[green]restored[/green] {h.name}")


@main.command(help="Pick a random scheduled-but-undone habit.")
@click.option("--seed", type=int, default=None, help="reproducible pick (testing)")
def random(seed: int | None) -> None:
    today_ = date.today()
    with db.session() as conn:
        habits = db.list_habits(conn)
        comps_today = db.completions_on(conn, today_)
        excluded_ids = {c.habit_id for c in comps_today}

    candidates = [
        h for h in habits if h.is_scheduled_on(today_) and h.id not in excluded_ids
    ]
    if not candidates:
        click.echo("nothing scheduled + undone today")
        return
    rng = _random.Random(seed) if seed is not None else _random
    pick = rng.choice(candidates)
    console.print(f"[bold]{pick.name}[/bold] [dim]({pick.frequency})[/dim]")


@main.command(help="Reverse the most recent completion.")
@click.option("--habit", default=None, help="scope to a single habit")
def undo(habit: str | None) -> None:
    with db.session() as conn:
        habit_id: int | None = None
        if habit is not None:
            habit_id = _resolve_habit(conn, habit, include_archived=True).id
        hit = db.most_recent_completion(conn, habit_id=habit_id)
        if hit is None:
            raise click.ClickException("no completions to undo")
        c, h = hit
        db.delete_completion(conn, h.id, c.date)

    console.print(f"[yellow]undone[/yellow] {h.name} [dim]({c.date.isoformat()})[/dim]")


@main.command(help="Mark a day deliberately skipped (vacation/sick/rest day).")
@click.argument("habit")
@click.option("--date", "date_str", default=None, help="override skip date (default: today)")
@click.option("--note", default=None, help="reason (max 280 chars)")
def skip(habit: str, date_str: str | None, note: str | None) -> None:
    on = _parse_date_flag(date_str)
    if note is not None and len(note) > Habit.NOTE_MAX:
        raise click.ClickException(f"note too long (max {Habit.NOTE_MAX} chars)")
    with db.session() as conn:
        h = _resolve_habit(conn, habit, include_archived=True)
        if h.is_archived:
            raise click.ClickException(f"{h.name} is archived — restore first")
        if not h.is_scheduled_on(on):
            raise click.ClickException(
                f"{h.name} is not scheduled on {on.isoformat()} — nothing to skip"
            )
        db.upsert_completion(
            conn,
            Completion(
                habit_id=h.id,
                date=on,
                value=None,
                note=note,
                duration_seconds=None,
                status=COMPLETION_STATUS_SKIPPED,
            ),
        )
    display = "today" if on == date.today() else on.isoformat()
    console.print(f"[yellow]skip[/yellow] {h.name} [dim]— {display}[/dim]")


@main.command(help="Pause a habit for a window. Skipped days don't decay momentum.")
@click.argument("habit")
@click.option("--until", "until_str", default=None, help="resume the day after (YYYY-MM-DD)")
@click.option("--days", "days", type=int, default=None, help="pause for N calendar days from today")
@click.option("--note", default=None, help="reason applied to each skip")
def pause(
    habit: str,
    until_str: str | None,
    days: int | None,
    note: str | None,
) -> None:
    if (until_str is None) == (days is None):
        raise click.ClickException("pass exactly one of --until or --days")
    today_ = date.today()
    if until_str is not None:
        until = _parse_optional_date(until_str, "--until")
        if until is None:
            raise click.ClickException("--until cannot be empty")
        if until < today_:
            raise click.ClickException(
                f"--until {until.isoformat()} is in the past"
            )
    else:
        if days is None or days < 1:
            raise click.ClickException("--days must be >= 1")
        until = today_ + timedelta(days=days - 1)
    if note is not None and len(note) > Habit.NOTE_MAX:
        raise click.ClickException(f"note too long (max {Habit.NOTE_MAX} chars)")

    with db.session() as conn:
        h = _resolve_habit(conn, habit, include_archived=True)
        if h.is_archived:
            raise click.ClickException(f"{h.name} is archived — restore first")
        scheduled = scheduled_days_between(h, today_, until)
        existing_dates = {
            c.date
            for c in db.completions_for_habit(conn, h.id, since=today_, until=until)
        }
        new_dates = [d for d in scheduled if d not in existing_dates]
        inserted = db.bulk_skip_dates(conn, h.id, new_dates, note=note)

    if inserted == 0 and not new_dates:
        if existing_dates:
            console.print(
                f"[dim]no new skips — all scheduled days through "
                f"{until.isoformat()} already have completions[/dim]"
            )
        else:
            console.print(
                f"[dim]{h.name} has no scheduled days through "
                f"{until.isoformat()}[/dim]"
            )
        return
    console.print(
        f"[yellow]paused[/yellow] {h.name} [dim]— "
        f"{inserted} skip(s) through {until.isoformat()}[/dim]"
    )


@main.command(help="Resume a paused habit by clearing future skips.")
@click.argument("habit")
def resume(habit: str) -> None:
    today_ = date.today()
    with db.session() as conn:
        h = _resolve_habit(conn, habit, include_archived=True)
        removed = db.clear_future_skips(conn, h.id, today_)
    if removed == 0:
        console.print(f"[dim]{h.name} has no active pause[/dim]")
        return
    console.print(
        f"[green]resumed[/green] {h.name} [dim]— cleared {removed} future skip(s)[/dim]"
    )


@main.command(help="Explain a habit's current momentum score.")
@click.argument("habit")
@click.option(
    "--days",
    type=int,
    default=14,
    show_default=True,
    help="recent-days breakdown window",
)
def why(habit: str, days: int) -> None:
    if days < 1:
        raise click.ClickException("--days must be >= 1")
    today_ = date.today()
    with db.session() as conn:
        h = _resolve_habit(conn, habit, include_archived=True)
        comps = db.completions_for_habit(conn, h.id)
        paused_until = db.paused_until_date(conn, h.id, today_)

    mom = compute_momentum(h, comps, today=today_)
    by_date: dict[date, Completion] = {c.date: c for c in comps}
    last_done = max(
        (c.date for c in comps if c.is_done), default=None
    )
    sched = scheduled_days_between(h, h.created_at, today_)
    last_miss = next(
        (d for d in reversed(sched) if d not in by_date),
        None,
    )

    console.print(
        f"[bold]{h.name}[/bold]  [dim]({h.frequency}, alpha {h.alpha:g})[/dim]"
    )
    if h.is_archived:
        console.print(f"  [red]archived {h.archived_at.isoformat()}[/red]")
    if paused_until is not None:
        console.print(f"  [yellow]paused until {paused_until.isoformat()}[/yellow]")
    console.print(
        f"  score [bold]{mom.score:.0f}[/bold]  trend {mom.trend}  "
        f"rate [bold]{mom.completion_rate:.0%}[/bold] "
        f"[dim]({mom.window_days}d)[/dim]"
    )
    console.print(
        f"  last done: [cyan]{last_done.isoformat() if last_done else 'never'}[/cyan]"
        f"   last miss: [red]{last_miss.isoformat() if last_miss else 'none'}[/red]"
    )

    # Per-day breakdown over the requested window. Shows D / M / S / -
    # so you can eyeball what's pulling the score in either direction.
    window_start = today_ - timedelta(days=days - 1)
    glyphs: list[str] = []
    for i in range(days):
        d = window_start + timedelta(days=i)
        if not h.is_scheduled_on(d):
            glyphs.append("[dim]·[/dim]")
            continue
        c = by_date.get(d)
        if c is None:
            glyphs.append("[red]M[/red]")
        elif c.is_skipped:
            glyphs.append("[yellow]S[/yellow]")
        else:
            strength = completion_strength(c.value, h.target)
            if strength >= 1.0:
                glyphs.append("[green]D[/green]")
            elif strength > 0:
                glyphs.append("[green]d[/green]")
            else:
                glyphs.append("[red]M[/red]")
    console.print(
        f"  last {days}d: {' '.join(glyphs)}  "
        f"[dim](D=done, d=partial, M=miss, S=skip, ·=off-day)[/dim]"
    )


@main.group(help="Read or write persistent preferences.")
def config() -> None:
    pass


@config.command("list", help="Show all config values.")
def config_list() -> None:
    cfg = _config.load()
    for k in sorted(cfg):
        console.print(f"{k} = {cfg[k]}")


@config.command("get", help="Print a single config value.")
@click.argument("key")
def config_get(key: str) -> None:
    try:
        click.echo(_config.get(key))
    except KeyError as e:
        raise click.ClickException(str(e).strip("'"))


@config.command("set", help="Set a config value.")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str) -> None:
    try:
        stored = _config.set_value(key, value)
    except (KeyError, ValueError) as e:
        raise click.ClickException(str(e).strip("'"))
    console.print(f"[green]set[/green] {key} = {stored}")


@main.command(help="Show keybinding + command reference.")
def help() -> None:
    click.echo(help_text.render_plain(), nl=False)


_COMPLETION_INSTRUCTIONS = {
    "bash": (
        "# Add to ~/.bashrc:\n"
        'eval "$(_FLOW_COMPLETE=bash_source flow)"\n'
    ),
    "zsh": (
        "# Add to ~/.zshrc:\n"
        'eval "$(_FLOW_COMPLETE=zsh_source flow)"\n'
    ),
    "fish": (
        "# Save to ~/.config/fish/completions/flow.fish:\n"
        "_FLOW_COMPLETE=fish_source flow | source\n"
    ),
}


@main.command(
    "completion",
    help="Print shell completion snippet. Eval it or add to your shell rc.",
)
@click.argument("shell", type=click.Choice(sorted(_COMPLETION_INSTRUCTIONS)))
def completion(shell: str) -> None:
    click.echo(_COMPLETION_INSTRUCTIONS[shell], nl=False)


@main.command(help="One-line summary of today's habits (for shell prompts).")
@click.option(
    "--format",
    "-F",
    "fmt",
    type=click.Choice(["text", "count"]),
    default="text",
    show_default=True,
    help="'text' = human summary, 'count' = 'done/total' only",
)
def today(fmt: str) -> None:
    message, done_count, total = _today_summary()
    if fmt == "count":
        click.echo(f"{done_count}/{total}")
        return
    if total == 0:
        click.echo("flow: nothing scheduled today")
        return
    if done_count == total:
        click.echo(f"flow: {done_count}/{total} ✓ all done")
        return
    # Trim the leading "N/M done — " from the shared message; `today` formats
    # its own prefix for shell-prompt compatibility.
    suffix = message.split(" — ", 1)[1] if " — " in message else message
    click.echo(f"flow: {done_count}/{total} — {suffix}")


def _today_summary() -> tuple[str, int, int]:
    """Body shared by `today` and `remind`. Returns (message, done, total).

    Skipped habits (active pauses) are removed from both numerator and
    denominator so a vacation doesn't leave you staring at a permanent
    'X/Y done' that you can't change."""
    today_ = date.today()
    with db.session() as conn:
        habits = db.list_habits(conn)
        comps_today = db.completions_on(conn, today_)
        done_ids = {c.habit_id for c in comps_today if not c.is_skipped}
        skipped_ids = {c.habit_id for c in comps_today if c.is_skipped}
        scheduled = [
            h for h in habits
            if h.is_scheduled_on(today_) and h.id not in skipped_ids
        ]

    total = len(scheduled)
    done_count = sum(1 for h in scheduled if h.id in done_ids)

    if total == 0:
        return ("nothing scheduled today", done_count, total)
    remaining = [h.name for h in scheduled if h.id not in done_ids]
    if not remaining:
        return (f"all {total} done — nice work", done_count, total)
    preview = ", ".join(remaining[:3])
    if len(remaining) > 3:
        preview += f", +{len(remaining) - 3} more"
    return (f"{done_count}/{total} done — {preview}", done_count, total)


@main.command(help="Full status across all habits (text or JSON for scripting).")
@click.option(
    "--format",
    "-F",
    "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
    help="'text' = human summary, 'json' = structured payload for scripts",
)
@click.option(
    "--watch",
    type=int,
    default=None,
    metavar="N",
    help="refresh every N seconds (text format only; ^C to exit)",
)
def status(fmt: str, watch: int | None) -> None:
    if watch is not None:
        if fmt == "json":
            raise click.ClickException("--watch is only supported with --format text")
        if watch < 1:
            raise click.ClickException("--watch interval must be >= 1 second")
        _watch_status(watch)
        return

    payload = _status_payload()
    if fmt == "json":
        click.echo(_json.dumps(payload, indent=2, ensure_ascii=False))
        return
    _print_status_text(payload)


def _watch_status(interval: int) -> None:
    """Refresh status in place every `interval` seconds. Uses Rich Live so the
    terminal scrollback stays clean across ticks. Exits on KeyboardInterrupt."""
    import time
    from rich.live import Live

    def _build():
        return _status_renderable(_status_payload(), watch_interval=interval)

    try:
        with Live(
            _build(), console=console, refresh_per_second=2, transient=False
        ) as live:
            while True:
                time.sleep(interval)
                live.update(_build())
    except KeyboardInterrupt:
        pass


def _status_payload(today: date | None = None) -> dict:
    """Aggregate every active habit's current state. Mirrors the stats-screen
    summary band but flattened for non-TUI consumption.

    Returns a JSON-safe dict. Skipped-today habits are excluded from the
    `scheduled_today` count — same rule as `flow today` and the check screen,
    so a vacation doesn't leave an unfinishable 'X/Y done' ratio."""
    today = today if today is not None else date.today()
    with db.session() as conn:
        habits = db.list_habits(conn)
        completions = {h.id: db.completions_for_habit(conn, h.id) for h in habits}

    def _today_row(hid: int) -> Completion | None:
        return next((c for c in completions[hid] if c.date == today), None)

    scheduled_today = 0
    done_today = 0
    skipped_today = 0
    at_risk = 0
    longest_streak = 0
    per_habit: list[dict] = []

    for h in habits:
        comps = completions[h.id]
        mom = compute_momentum(h, comps, today=today)
        cur_streak, best_streak = compute_streaks(h, comps, today=today)
        c = _today_row(h.id)
        is_sched = h.is_scheduled_on(today)
        is_skipped = c is not None and c.is_skipped
        is_done = (
            c is not None
            and not c.is_skipped
            and completion_strength(c.value, h.target) > 0
        )

        if is_sched and not is_skipped:
            scheduled_today += 1
            if is_done:
                done_today += 1
        if is_sched and is_skipped:
            skipped_today += 1
        if mom.trend == "↘":
            at_risk += 1
        if cur_streak > longest_streak:
            longest_streak = cur_streak

        per_habit.append(
            {
                "name": h.name,
                "frequency": h.frequency,
                "scheduled_today": is_sched,
                "done_today": is_done,
                "skipped_today": is_skipped,
                "value": c.value if c else None,
                "completed_at": (
                    c.completed_at.isoformat()
                    if c and c.completed_at is not None
                    else None
                ),
                "score": round(mom.score, 1),
                "trend": mom.trend,
                "completion_rate": round(mom.completion_rate, 3),
                "current_streak": cur_streak,
                "best_streak": best_streak,
            }
        )

    rate = (done_today / scheduled_today) if scheduled_today else 0.0
    return {
        "date": today.isoformat(),
        "habit_count": len(habits),
        "scheduled_today": scheduled_today,
        "done_today": done_today,
        "skipped_today": skipped_today,
        "completion_rate_today": round(rate, 3),
        "at_risk": at_risk,
        "longest_current_streak": longest_streak,
        "habits": per_habit,
    }


def _status_renderable(p: dict, watch_interval: int | None = None):
    """Compose a Rich renderable for a status payload — shared between the
    one-shot text path and `--watch`. When `watch_interval` is set, append a
    footer reminding the user how to exit."""
    from rich.console import Group
    from rich.text import Text

    header = f"[bold]flow status[/bold] [dim]— {p['date']}"
    if watch_interval is not None:
        header += f" · watching ({watch_interval}s, ^C to exit)"
    header += "[/dim]"
    lines: list[Text] = [Text.from_markup(header)]
    if p["habit_count"] == 0:
        lines.append(Text.from_markup("  [dim]no habits yet — try `flow add`[/dim]"))
        return Group(*lines)

    if p["scheduled_today"] == 0 and p["skipped_today"] == 0:
        lines.append(Text.from_markup("  [dim]nothing scheduled today[/dim]"))
    else:
        rate_pct = p["completion_rate_today"] * 100
        line = f"  today: [bold]{p['done_today']}/{p['scheduled_today']}[/bold] done"
        if p["scheduled_today"]:
            line += f" [dim]({rate_pct:.0f}%)[/dim]"
        if p["skipped_today"]:
            line += f" · [yellow]{p['skipped_today']} skipped[/yellow]"
        lines.append(Text.from_markup(line))

    risk_color = "red" if p["at_risk"] else "green"
    lines.append(
        Text.from_markup(
            f"  at risk: [{risk_color}]{p['at_risk']}[/{risk_color}] declining"
        )
    )
    streak = p["longest_current_streak"]
    if streak:
        unit = "day" if streak == 1 else "days"
        lines.append(
            Text.from_markup(
                f"  longest current streak: [bold]{streak}[/bold] {unit}"
            )
        )
    return Group(*lines)


def _print_status_text(p: dict) -> None:
    console.print(_status_renderable(p))


@main.command(help="Fire a desktop notification with today's summary (cron target).")
@click.option("--quiet", is_flag=True, help="suppress stdout (cron-friendly)")
def remind(quiet: bool) -> None:
    message, _done, _total = _today_summary()
    _notify.send("flow", message)
    if not quiet:
        click.echo(message)


@main.command(
    "install-cron",
    help="Install (or remove) a daily reminder cron entry. Time is HH:MM 24h.",
)
@click.argument("time_spec", required=False)
@click.option("--remove", "do_remove", is_flag=True, help="remove the existing entry")
@click.option("--dry-run", is_flag=True, help="print what would change")
def install_cron(time_spec: str | None, do_remove: bool, dry_run: bool) -> None:
    if do_remove:
        if time_spec is not None:
            raise click.ClickException("--remove takes no time argument")
        if dry_run:
            click.echo(f"would strip lines containing {_cron.MARKER!r}")
            return
        try:
            removed = _cron.remove()
        except RuntimeError as e:
            raise click.ClickException(str(e))
        if removed:
            console.print("[yellow]removed[/yellow] flow-reminder cron entry")
        else:
            console.print("[dim]no flow-reminder cron entry found[/dim]")
        return

    if time_spec is None:
        raise click.ClickException("missing time argument (e.g. `flow install-cron 22:00`)")
    try:
        schedule = _cron.parse_time(time_spec)
    except ValueError as e:
        raise click.ClickException(str(e))

    entry = _cron.build_entry(schedule)
    if dry_run:
        click.echo(entry)
        return

    try:
        _cron.install(schedule)
    except RuntimeError as e:
        raise click.ClickException(str(e))
    console.print(
        f"[green]installed[/green] daily reminder at {schedule.hhmm} "
        f"[dim]({entry})[/dim]"
    )


def _render_digest(digest: _review.Digest, title: str) -> None:
    header = (
        f"[bold]{title}[/bold]  [dim]{digest.start.isoformat()} → "
        f"{digest.end.isoformat()}[/dim]"
    )
    console.print(header)
    if not digest.rows:
        console.print("[dim]nothing scheduled in window[/dim]")
        return

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("habit")
    table.add_column("rate", justify="right")
    table.add_column("done", justify="right")
    table.add_column("sched", justify="right")
    table.add_column("time", justify="right", style="dim")
    table.add_column("notes", justify="right", style="dim")
    for r in digest.rows:
        time_str = format_duration(r.total_seconds) if r.total_seconds else ""
        table.add_row(
            r.habit.name,
            f"{r.rate:.0%}",
            f"{r.completed:g}",
            str(r.scheduled),
            time_str,
            str(r.notes) if r.notes else "",
        )
    console.print(table)
    console.print(
        f"[dim]overall[/dim] {digest.total_completed:g} / {digest.total_scheduled} "
        f"({digest.overall_rate:.0%})"
    )


def _load_digest_inputs(
    conn, start: date, end: date
) -> tuple[list[Habit], dict[int, list[Completion]]]:
    habits = db.list_habits(conn, include_archived=False)
    comps = {
        h.id: db.completions_for_habit(conn, h.id, since=start, until=end)
        for h in habits
    }
    return habits, comps


@main.command(help="Condensed digest for the current ISO week (Mon–Sun).")
def week() -> None:
    today_ = date.today()
    start, end = _review.week_bounds(today_)
    with db.session() as conn:
        habits, comps = _load_digest_inputs(conn, start, end)
    digest = _review.build_digest(habits, comps, start, end)
    _render_digest(digest, "this week")


@main.command(help="Condensed digest for the current calendar month.")
def month() -> None:
    today_ = date.today()
    start, end = _review.month_bounds(today_)
    with db.session() as conn:
        habits, comps = _load_digest_inputs(conn, start, end)
    digest = _review.build_digest(habits, comps, start, end)
    _render_digest(digest, "this month")


@main.command(
    help="Pairwise 'when A is done, B is also done' rates. Sorted by surprise.",
)
@click.option(
    "--days", type=int, default=60, show_default=True, help="lookback window"
)
@click.option(
    "--limit", type=int, default=15, show_default=True, help="max rows to show"
)
@click.option(
    "--min-shared",
    type=int,
    default=5,
    show_default=True,
    help="min overlapping scheduled days required",
)
def correlations(days: int, limit: int, min_shared: int) -> None:
    if days <= 0:
        raise click.ClickException("--days must be positive")
    today_ = date.today()
    since = today_ - timedelta(days=days - 1)
    with db.session() as conn:
        habits = db.list_habits(conn, include_archived=False)
        comps = {
            h.id: db.completions_for_habit(conn, h.id, since=since, until=today_)
            for h in habits
        }
    pairs = _review.correlations(habits, comps, since, today_, min_shared=min_shared)
    if not pairs:
        console.print("[dim]not enough shared data yet — try more days[/dim]")
        return

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("when")
    table.add_column("also", style="cyan")
    table.add_column("co-rate", justify="right")
    table.add_column("base", justify="right", style="dim")
    table.add_column("lift", justify="right")
    table.add_column("n", justify="right", style="dim")
    for p in pairs[:limit]:
        lift = p.co_rate - p.base_rate
        lift_cell = f"{lift:+.0%}"
        table.add_row(
            p.a.name, p.b.name,
            f"{p.co_rate:.0%}",
            f"{p.base_rate:.0%}",
            lift_cell,
            str(p.shared_days),
        )
    console.print(table)


@main.command(help="Markdown digest (defaults to the current week).")
@click.option(
    "--format",
    "-F",
    "fmt",
    type=click.Choice(["markdown"]),
    default="markdown",
    show_default=True,
)
@click.option(
    "--range",
    "range_",
    type=click.Choice(["week", "month"]),
    default="week",
    show_default=True,
)
@click.option(
    "--out",
    "-o",
    "output_path",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help="write to file instead of stdout",
)
def summary(fmt: str, range_: str, output_path: str | None) -> None:
    today_ = date.today()
    if range_ == "week":
        start, end = _review.week_bounds(today_)
    else:
        start, end = _review.month_bounds(today_)
    with db.session() as conn:
        habits = db.list_habits(conn, include_archived=False)
        comps = {
            h.id: db.completions_for_habit(conn, h.id, since=start, until=end)
            for h in habits
        }
    text = _review.summary_markdown(habits, comps, start, end)
    if output_path is None:
        click.echo(text, nl=False)
    else:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(text)
        console.print(f"[green]wrote[/green] {output_path}")


if __name__ == "__main__":
    main()
