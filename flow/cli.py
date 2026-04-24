"""Click-based CLI entry point for flow.

Commands routed here are the non-TUI surface: add / done / list / archive.
TUI-backed commands (check, stats) land in later phases. Every command is
scripting-friendly: exit codes are stable, output is plain when possible.
"""

from __future__ import annotations

import random as _random
import sys
from datetime import date, timedelta
from typing import Iterable

import click
from dateutil import parser as dateparser
from rich.console import Console
from rich.table import Table

from . import (
    backup as _backup,
    config as _config,
    db,
    export as _export,
    help_text,
    importer as _importer,
    review as _review,
)
from .models import Completion, Habit, format_duration, parse_duration, parse_frequency
from .momentum import compute_momentum


console = Console()
err_console = Console(stderr=True)


# ---- habit name resolution ----------------------------------------------------


def _resolve_habit(
    conn, query: str, include_archived: bool = False
) -> Habit:
    """Resolve a user-typed habit name to a single Habit.

    Matching order (case-insensitive): exact → prefix → substring. Errors on
    zero or multiple matches at any tier.
    """
    habits = db.list_habits(conn, include_archived=include_archived)
    if not habits:
        raise click.ClickException("no habits exist yet — try `flow add`")
    q = query.strip().lower()

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
@click.argument("name")
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
def add(
    name: str,
    frequency: str,
    unit: str | None,
    target: float | None,
    description: str | None,
    start_date: str | None,
    end_date: str | None,
    alpha: float | None,
) -> None:
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
    value: float | None,
    note: str | None,
    duration_str: str | None,
    date_str: str | None,
) -> None:
    on = _parse_date_flag(date_str)
    if note is not None and len(note) > Habit.NOTE_MAX:
        raise click.ClickException(f"note too long (max {Habit.NOTE_MAX} chars)")

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

        for h in habits:
            comps = db.completions_for_habit(conn, h.id)
            mom = compute_momentum(h, comps, window_days=window)
            row = [
                str(h.id),
                h.name,
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
            if c.value is not None:
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
def stats(habit: str | None) -> None:
    from .tui.app import FlowApp

    if habit is None:
        FlowApp(initial="stats").run()
        return

    with db.session() as conn:
        h = _resolve_habit(conn, habit, include_archived=True)
    FlowApp(initial="detail", detail_habit=h).run()


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
        done_ids = {c.habit_id for c in db.completions_on(conn, today_)}

    candidates = [
        h for h in habits if h.is_scheduled_on(today_) and h.id not in done_ids
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
    today_ = date.today()
    with db.session() as conn:
        habits = db.list_habits(conn)
        scheduled = [h for h in habits if h.is_scheduled_on(today_)]
        done_ids = {c.habit_id for c in db.completions_on(conn, today_)}

    total = len(scheduled)
    done_count = sum(1 for h in scheduled if h.id in done_ids)

    if fmt == "count":
        click.echo(f"{done_count}/{total}")
        return

    if total == 0:
        click.echo("flow: nothing scheduled today")
        return

    remaining = [h.name for h in scheduled if h.id not in done_ids]
    if not remaining:
        click.echo(f"flow: {done_count}/{total} ✓ all done")
        return
    preview = ", ".join(remaining[:3])
    if len(remaining) > 3:
        preview += f", +{len(remaining) - 3} more"
    click.echo(f"flow: {done_count}/{total} — {preview}")


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
