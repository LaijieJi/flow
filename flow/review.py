"""Review & insight — digest / correlation / sparkline / markdown summary.

Pure functions on (habit, completions) collections. No DB or IO dependency;
the CLI and TUI layers handle rendering.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Mapping

from .models import Completion, Habit, format_duration
from .momentum import (
    completion_strength,
    rolling_completion_rate,
    scheduled_days_between,
    score_history,
)


# ---- digest -------------------------------------------------------------------


@dataclass(slots=True)
class DigestRow:
    habit: Habit
    scheduled: int
    completed: float  # sum of strengths (partials count fractionally)
    total_seconds: int
    notes: int

    @property
    def rate(self) -> float:
        return self.completed / self.scheduled if self.scheduled else 0.0


@dataclass(slots=True)
class Digest:
    start: date
    end: date
    rows: list[DigestRow]

    @property
    def total_scheduled(self) -> int:
        return sum(r.scheduled for r in self.rows)

    @property
    def total_completed(self) -> float:
        return sum(r.completed for r in self.rows)

    @property
    def overall_rate(self) -> float:
        total = self.total_scheduled
        return (self.total_completed / total) if total else 0.0


def _digest_row(habit: Habit, completions: Iterable[Completion], start: date, end: date) -> DigestRow:
    comps = [c for c in completions if start <= c.date <= end]
    scheduled = scheduled_days_between(habit, max(habit.created_at, start), end)
    by_date: dict[date, Completion] = {c.date: c for c in comps}
    # Skipped days are deliberate not-counted: drop them from the denominator
    # so the digest rate matches the momentum score's view of the window.
    scheduled = [d for d in scheduled if not (by_date.get(d) and by_date[d].is_skipped)]
    completed = 0.0
    for d in scheduled:
        c = by_date.get(d)
        if c is not None and not c.is_skipped:
            completed += completion_strength(c.value, habit.target)
    total_seconds = sum(
        (c.duration_seconds or 0) for c in comps if not c.is_skipped
    )
    notes = sum(1 for c in comps if c.note)
    return DigestRow(
        habit=habit,
        scheduled=len(scheduled),
        completed=completed,
        total_seconds=total_seconds,
        notes=notes,
    )


def build_digest(
    habits: list[Habit],
    completions_by_habit: Mapping[int, list[Completion]],
    start: date,
    end: date,
) -> Digest:
    rows = [
        _digest_row(h, completions_by_habit.get(h.id, []), start, end)
        for h in habits
        if not h.is_archived
    ]
    # Drop rows with zero scheduled occurrences in the window — they crowd the
    # digest without information (habit didn't apply this week/month).
    rows = [r for r in rows if r.scheduled > 0]
    return Digest(start=start, end=end, rows=rows)


def week_bounds(today: date) -> tuple[date, date]:
    """ISO-ish week: Monday..Sunday containing `today`."""
    start = today - timedelta(days=today.weekday())
    return start, start + timedelta(days=6)


def month_bounds(today: date) -> tuple[date, date]:
    """First..last day of `today`'s calendar month."""
    from calendar import monthrange

    start = today.replace(day=1)
    end = today.replace(day=monthrange(today.year, today.month)[1])
    return start, end


# ---- sparkline ----------------------------------------------------------------


_SPARK_BLOCKS = "▁▂▃▄▅▆▇█"


def sparkline(values: list[float], width: int | None = None) -> str:
    """Unicode block sparkline for a 0..100 series. Downsamples to `width`
    buckets (mean per bucket) when the series is longer. Empty series → ''."""
    if not values:
        return ""
    series = list(values)
    if width is not None and width > 0 and len(series) > width:
        step = len(series) / width
        buckets: list[float] = []
        for i in range(width):
            lo = int(i * step)
            hi = int((i + 1) * step) if i < width - 1 else len(series)
            chunk = series[lo:hi] or [series[lo]]
            buckets.append(sum(chunk) / len(chunk))
        series = buckets
    lo = min(series)
    hi = max(series)
    span = hi - lo
    n = len(_SPARK_BLOCKS) - 1
    if span < 1e-9:
        return _SPARK_BLOCKS[n // 2] * len(series)
    out: list[str] = []
    for v in series:
        idx = int(round((v - lo) / span * n))
        out.append(_SPARK_BLOCKS[max(0, min(n, idx))])
    return "".join(out)


def habit_score_sparkline(
    habit: Habit,
    completions: Iterable[Completion],
    weeks: int = 12,
    today: date | None = None,
    width: int | None = None,
) -> tuple[str, list[float]]:
    """Sparkline of end-of-week score values over the last `weeks` weeks.
    Returns (ascii, weekly_scores) so callers can also label endpoints."""
    today = today or date.today()
    start = today - timedelta(days=weeks * 7 - 1)
    start = max(start, habit.created_at)
    comps_list = list(completions)
    scheduled = scheduled_days_between(habit, start, today)
    skips = {c.date for c in comps_list if c.is_skipped}
    if skips:
        scheduled = [d for d in scheduled if d not in skips]
    strengths = {
        c.date: completion_strength(c.value, habit.target)
        for c in comps_list
        if not c.is_skipped
    }
    history = score_history(strengths, scheduled, alpha=habit.alpha)
    if not history:
        return "", []
    by_day = dict(zip(scheduled, history))
    weekly: list[float] = []
    for w in range(weeks):
        week_end = today - timedelta(days=(weeks - 1 - w) * 7)
        # Use the most recent scheduled-day score on or before week_end.
        best: float | None = None
        for d in scheduled:
            if d <= week_end:
                best = by_day[d]
            else:
                break
        if best is not None:
            weekly.append(best)
    if not weekly:
        return "", []
    return sparkline(weekly, width=width), weekly


# ---- correlations -------------------------------------------------------------


@dataclass(slots=True)
class CorrelationPair:
    a: Habit
    b: Habit
    # P(B done | A done) over days where both are scheduled.
    co_rate: float
    base_rate: float  # P(B done) over shared scheduled window
    shared_days: int


def correlations(
    habits: list[Habit],
    completions_by_habit: Mapping[int, list[Completion]],
    since: date,
    today: date,
    min_shared: int = 5,
) -> list[CorrelationPair]:
    """Pairwise "given A done, how often is B done" over the shared scheduled
    window since `since`. Symmetric pairs (A→B and B→A) are both returned so
    directional reading is preserved. Filters out pairs with < `min_shared`
    overlapping scheduled days."""
    active = [h for h in habits if not h.is_archived]
    done_sets: dict[int, set[date]] = {}
    skip_sets: dict[int, set[date]] = {}
    for h in active:
        comps = completions_by_habit.get(h.id, [])
        done_sets[h.id] = {
            c.date
            for c in comps
            if since <= c.date <= today
            and not c.is_skipped
            and completion_strength(c.value, h.target) > 0.0
        }
        skip_sets[h.id] = {
            c.date for c in comps if since <= c.date <= today and c.is_skipped
        }
    sched_sets: dict[int, set[date]] = {
        h.id: set(scheduled_days_between(h, max(h.created_at, since), today))
        - skip_sets[h.id]
        for h in active
    }

    out: list[CorrelationPair] = []
    for a in active:
        a_done_days = done_sets[a.id]
        if not a_done_days:
            continue
        for b in active:
            if a.id == b.id:
                continue
            shared = sched_sets[a.id] & sched_sets[b.id]
            if len(shared) < min_shared:
                continue
            a_done_in_shared = a_done_days & shared
            if not a_done_in_shared:
                continue
            b_done = done_sets[b.id]
            co = len(a_done_in_shared & b_done) / len(a_done_in_shared)
            base = (len(b_done & shared) / len(shared)) if shared else 0.0
            out.append(
                CorrelationPair(
                    a=a, b=b, co_rate=co, base_rate=base, shared_days=len(shared)
                )
            )
    # Most "surprising" pairs first: biggest lift above base rate.
    out.sort(key=lambda p: (p.co_rate - p.base_rate), reverse=True)
    return out


# ---- markdown summary ---------------------------------------------------------


def summary_markdown(
    habits: list[Habit],
    completions_by_habit: Mapping[int, list[Completion]],
    start: date,
    end: date,
) -> str:
    digest = build_digest(habits, completions_by_habit, start, end)
    lines: list[str] = []
    lines.append(f"# flow — {start.isoformat()} to {end.isoformat()}")
    lines.append("")
    lines.append(
        f"**Overall:** {digest.total_completed:.1f} / {digest.total_scheduled} "
        f"({digest.overall_rate:.0%})"
    )
    lines.append("")
    if digest.rows:
        lines.append("| habit | rate | done | sched | time | notes |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for r in digest.rows:
            time_str = format_duration(r.total_seconds) if r.total_seconds else ""
            lines.append(
                f"| {r.habit.name} | {r.rate:.0%} | {r.completed:g} | "
                f"{r.scheduled} | {time_str} | {r.notes} |"
            )
        lines.append("")

    note_rows: list[tuple[date, str, str]] = []
    for h in habits:
        if h.is_archived:
            continue
        for c in completions_by_habit.get(h.id, []):
            if c.note and start <= c.date <= end:
                note_rows.append((c.date, h.name, c.note))
    if note_rows:
        lines.append("## Notes")
        lines.append("")
        for d, name, note in sorted(note_rows):
            lines.append(f"- **{d.isoformat()}** _{name}_ — {note}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
