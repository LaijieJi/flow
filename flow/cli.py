"""Click-based CLI entry point for flow.

Commands routed here are the non-TUI surface: add / done / list / archive.
TUI-backed commands (check, stats) land in later phases. Every command is
scripting-friendly: exit codes are stable, output is plain when possible.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable

import click
from dateutil import parser as dateparser
from rich.console import Console
from rich.table import Table

from . import db
from .models import Completion, Habit, parse_frequency
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


@click.group(help="flow — momentum-based habit tracker.")
@click.version_option(package_name="flow")
def main() -> None:
    pass


@main.command(help="Add a new habit.")
@click.argument("name")
@click.option(
    "--frequency",
    "-f",
    default="daily",
    show_default=True,
    help="daily | weekdays | weekly | comma-list like 'mon,wed,fri'",
)
@click.option("--unit", default=None, help="e.g. minutes, pages, reps")
@click.option(
    "--target", type=float, default=None, help="numeric target (requires --unit)"
)
@click.option("--description", "-d", default=None)
def add(
    name: str,
    frequency: str,
    unit: str | None,
    target: float | None,
    description: str | None,
) -> None:
    try:
        parse_frequency(frequency)
    except ValueError as e:
        raise click.ClickException(str(e))
    if target is not None and unit is None:
        raise click.ClickException("--target requires --unit")

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
        )
        db.insert_habit(conn, habit)

    summary = f"[green]added[/green] {name} [dim]({frequency}"
    if unit:
        summary += f", {target or ''} {unit}".rstrip()
    summary += ")[/dim]"
    console.print(summary)


@main.command(help="Mark a habit complete.")
@click.argument("habit")
@click.option("--value", type=float, default=None, help="numeric value (for target habits)")
@click.option("--note", default=None, help="short reflection (max 280 chars)")
@click.option("--date", "date_str", default=None, help="override completion date")
def done(
    habit: str,
    value: float | None,
    note: str | None,
    date_str: str | None,
) -> None:
    on = _parse_date_flag(date_str)
    if note is not None and len(note) > Habit.NOTE_MAX:
        raise click.ClickException(f"note too long (max {Habit.NOTE_MAX} chars)")

    with db.session() as conn:
        h = _resolve_habit(conn, habit, include_archived=True)
        if h.is_archived:
            raise click.ClickException(f"{h.name} is archived — restore first")
        if value is not None and h.target is None:
            err_console.print(
                f"[yellow]warn:[/yellow] {h.name} has no target; value recorded but won't scale momentum"
            )
        db.upsert_completion(
            conn, Completion(habit_id=h.id, date=on, value=value, note=note)
        )

    display = "today" if on == date.today() else on.isoformat()
    val_str = f" [dim]({value} {h.unit or ''})[/dim]" if value is not None else ""
    console.print(f"[green]✓[/green] {h.name}{val_str} [dim]— {display}[/dim]")


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
@click.option("--frequency", "-f", default=None, help="daily | weekdays | weekly | mon,wed,fri")
@click.option("--unit", default=None, help="e.g. minutes, pages (empty string clears)")
@click.option("--target", default=None, help="numeric target (empty string clears)")
@click.option("--description", "-d", default=None, help="short description (empty string clears)")
def edit(
    habit: str,
    name: str | None,
    frequency: str | None,
    unit: str | None,
    target: str | None,
    description: str | None,
) -> None:
    with db.session() as conn:
        h = _resolve_habit(conn, habit, include_archived=True)

        no_flags = all(
            v is None for v in (name, frequency, unit, target, description)
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
        table.add_column("note", style="dim")

        for c, h in pairs:
            if c.value is not None:
                val = f"{c.value:g}"
                if h.unit:
                    val += f" {h.unit}"
            else:
                val = "✓"
            table.add_row(c.date.isoformat(), h.name, val, c.note or "")
        console.print(table)


@main.command(help="Archive a habit (soft delete, data preserved).")
@click.argument("habit")
def archive(habit: str) -> None:
    with db.session() as conn:
        h = _resolve_habit(conn, habit, include_archived=True)
        if not db.archive_habit(conn, h.id):
            raise click.ClickException(f"{h.name} is already archived")
    console.print(f"[yellow]archived[/yellow] {h.name}")


if __name__ == "__main__":
    main()
