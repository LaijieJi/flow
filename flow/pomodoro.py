"""Pomodoro work/break timing — pure logic.

The Textual screen drives the clock and renders the UI; this module owns the
rules for merging a completed work session into the day's existing completion
(duration accumulation + value derivation for time-unit habits). Keeping it
here means the behavior is testable without spinning up a TUI.
"""

from __future__ import annotations

from datetime import date as _date

from .models import Completion, Habit


DEFAULT_WORK_SECONDS = 25 * 60
DEFAULT_BREAK_SECONDS = 5 * 60
DEFAULT_CYCLES = 1

_TIME_UNITS_MINUTES = {"minutes", "minute", "min", "mins"}
_TIME_UNITS_HOURS = {"hours", "hour", "hr", "hrs"}


def merge_session(
    existing: Completion | None,
    habit: Habit,
    session_seconds: int,
    on: _date,
) -> Completion:
    """Return a Completion that rolls `session_seconds` into `existing` (if any).

    - Duration accumulates across same-day sessions.
    - For time-unit habits (unit in 'minutes'/'hours') with no explicit value,
      the derived value is recomputed from the *total* accumulated duration so
      momentum tracks the entire day's focus time, not just the last session.
    - Notes and explicit numeric values are preserved untouched.
    """
    if session_seconds < 0:
        raise ValueError("session_seconds must be >= 0")

    prior_duration = existing.duration_seconds if existing else None
    total_duration = (prior_duration or 0) + session_seconds

    value = existing.value if existing else None
    derived = _value_from_duration(habit.unit, total_duration)
    if derived is not None:
        if value is None or _looks_derived(existing, habit):
            value = derived

    return Completion(
        habit_id=habit.id,
        date=on,
        value=value,
        note=existing.note if existing else None,
        duration_seconds=total_duration if total_duration > 0 else None,
    )


def _value_from_duration(unit: str | None, duration_seconds: int) -> float | None:
    if unit is None:
        return None
    u = unit.lower()
    if u in _TIME_UNITS_MINUTES:
        return duration_seconds / 60
    if u in _TIME_UNITS_HOURS:
        return duration_seconds / 3600
    return None


def _looks_derived(existing: Completion | None, habit: Habit) -> bool:
    """True if the existing value appears to match what we'd derive from the
    existing duration — i.e. user never set a custom --value, so we're free to
    refresh the derived value after adding a new session. If the user has
    manually set a value that diverges from the derived number, leave it alone.
    """
    if existing is None or existing.value is None:
        return True
    if existing.duration_seconds is None:
        return False
    derived_prior = _value_from_duration(habit.unit, existing.duration_seconds)
    if derived_prior is None:
        return False
    return abs(existing.value - derived_prior) < 1e-6


def format_mmss(seconds: int) -> str:
    """Clock-face MM:SS. Negative clamps to zero. Hours render as H:MM:SS."""
    if seconds < 0:
        seconds = 0
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"
