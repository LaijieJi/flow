"""Momentum engine — pure functions, no DB dependency.

The primary metric is a weighted recency score computed as an exponential
moving average (EMA) over a habit's *scheduled* days. A day where the habit
is not scheduled does not decay the score; only missed scheduled days do.

score_{t} = alpha * strength_t + (1 - alpha) * score_{t-1}

  - strength_t is in [0, 1]:
      * no target set: binary (completed = 1.0, missed = 0.0)
      * target set:    min(value / target, 1.0); missing value on a done
                       entry is treated as full completion (1.0)

Scores are returned on a 0..100 scale for display.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Mapping

from .models import Completion, Habit


ALPHA = 0.3
DEFAULT_WINDOW_DAYS = 14
TREND_LOOKBACK = 3
TREND_FLAT_THRESHOLD = 2.0  # score points; below this delta counts as flat


# ---- strength -----------------------------------------------------------------


def completion_strength(value: float | None, target: float | None) -> float:
    """Return strength of a single completion in [0, 1].

    Rules:
      - No target: presence of a row = full completion (1.0).
      - Target set, value provided: clamped ratio value/target.
      - Target set, value None: treat as full completion (user explicitly
        marked done without specifying a value).
    """
    if target is None or target <= 0:
        return 1.0
    if value is None:
        return 1.0
    if value <= 0:
        return 0.0
    return min(value / target, 1.0)


def strengths_by_date(
    completions: Iterable[Completion], target: float | None
) -> dict[date, float]:
    return {c.date: completion_strength(c.value, target) for c in completions}


# ---- scheduling ---------------------------------------------------------------


def scheduled_days_between(habit: Habit, start: date, end: date) -> list[date]:
    """All dates in [start, end] where the habit is scheduled, inclusive."""
    if start > end:
        return []
    days: list[date] = []
    d = start
    one = timedelta(days=1)
    while d <= end:
        if habit.is_scheduled_on(d):
            days.append(d)
        d += one
    return days


# ---- EMA score ----------------------------------------------------------------


def score_history(
    strengths: Mapping[date, float],
    scheduled_days: list[date],
    alpha: float = ALPHA,
) -> list[float]:
    """Score (0..100) after each scheduled day, in chronological order."""
    score = 0.0
    history: list[float] = []
    for d in sorted(scheduled_days):
        s = strengths.get(d, 0.0)
        score = alpha * s + (1 - alpha) * score
        history.append(score * 100)
    return history


def ema_score(
    strengths: Mapping[date, float],
    scheduled_days: list[date],
    alpha: float = ALPHA,
) -> float:
    """Final EMA score (0..100). 0 if no scheduled days yet."""
    hist = score_history(strengths, scheduled_days, alpha=alpha)
    return hist[-1] if hist else 0.0


# ---- trend --------------------------------------------------------------------


def trend(
    history: list[float],
    lookback: int = TREND_LOOKBACK,
    flat_threshold: float = TREND_FLAT_THRESHOLD,
) -> str:
    """Return '↗', '↘', or '→' based on score change over the last `lookback`
    scheduled days. Requires at least 2 history points; otherwise flat."""
    if len(history) < 2:
        return "→"
    n = min(lookback, len(history) - 1)
    delta = history[-1] - history[-1 - n]
    if delta > flat_threshold:
        return "↗"
    if delta < -flat_threshold:
        return "↘"
    return "→"


# ---- rolling rate -------------------------------------------------------------


def rolling_completion_rate(
    strengths: Mapping[date, float],
    scheduled_days: list[date],
    window_days: int = DEFAULT_WINDOW_DAYS,
    ref_date: date | None = None,
) -> float:
    """Fraction of scheduled occurrences completed in the last `window_days`
    calendar days ending at `ref_date` (inclusive). Returns 0..1.

    Partial completions contribute their strength (so a 50% target entry counts
    as 0.5 of a completion). Returns 0.0 when no scheduled days fall in window."""
    if not scheduled_days:
        return 0.0
    ref = ref_date if ref_date is not None else max(scheduled_days)
    cutoff = ref - timedelta(days=window_days - 1)
    in_window = [d for d in scheduled_days if cutoff <= d <= ref]
    if not in_window:
        return 0.0
    total = sum(strengths.get(d, 0.0) for d in in_window)
    return total / len(in_window)


# ---- aggregate ----------------------------------------------------------------


@dataclass(slots=True)
class Momentum:
    score: float  # 0..100, EMA of completion strength over scheduled days
    trend: str  # '↗' | '→' | '↘'
    completion_rate: float  # 0..1, over rolling window
    window_days: int


def compute_momentum(
    habit: Habit,
    completions: Iterable[Completion],
    today: date | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    alpha: float | None = None,
) -> Momentum:
    """Bundle score + trend + rate for one habit. `today` defaults to
    `date.today()`; pass it explicitly for determinism in tests. `alpha`
    defaults to the habit's configured smoothing factor."""
    today = today if today is not None else date.today()
    start = habit.created_at
    scheduled = scheduled_days_between(habit, start, today)
    strengths = strengths_by_date(completions, habit.target)

    effective_alpha = alpha if alpha is not None else habit.alpha
    history = score_history(strengths, scheduled, alpha=effective_alpha)
    final_score = history[-1] if history else 0.0
    rate = rolling_completion_rate(strengths, scheduled, window_days=window_days, ref_date=today)
    direction = trend(history)

    return Momentum(
        score=final_score,
        trend=direction,
        completion_rate=rate,
        window_days=window_days,
    )
