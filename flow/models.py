"""Domain dataclasses for flow. Mirror the SQLite schema, no ORM."""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date
from typing import ClassVar


VALID_FREQUENCY_KEYWORDS = {"daily", "weekdays", "weekly"}
VALID_WEEKDAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
WEEKDAY_INDEX = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}


@dataclass(slots=True, frozen=True)
class FrequencySpec:
    """Parsed frequency descriptor. `kind` dispatches scheduling logic.

    kind values:
      - 'daily' / 'weekdays' / 'weekly' — keyword-based calendar pattern
      - 'custom' — arbitrary weekday list via `weekdays`
      - 'monthly' — scheduled on `day_of_month` (1..31 or 'last')
      - 'every' — every `interval_days` days, anchored on habit's created_at
    """

    kind: str
    weekdays: frozenset[int] | None = None
    day_of_month: int | str | None = None  # int 1..31 or literal 'last'
    interval_days: int | None = None


def parse_frequency(frequency: str) -> FrequencySpec:
    """Parse a frequency string. Accepted forms:

      daily | weekdays | weekly
      mon,wed,fri     (any subset of weekday tokens)
      monthly         (1st of each month)
      monthly:15      (15th of each month; 29-31 auto-clamp to month length)
      monthly:last    (last day of each month)
      every:3         (every 3 days, counted from habit.created_at)

    Raises ValueError on invalid input."""
    freq = frequency.strip().lower()
    if not freq:
        raise ValueError(f"empty frequency: {frequency!r}")

    if freq in VALID_FREQUENCY_KEYWORDS:
        return FrequencySpec(kind=freq)

    if freq == "monthly":
        return FrequencySpec(kind="monthly", day_of_month=1)

    if freq.startswith("monthly:"):
        payload = freq[len("monthly:"):].strip()
        if not payload:
            raise ValueError("monthly: needs a day (1-31 or 'last')")
        if payload == "last":
            return FrequencySpec(kind="monthly", day_of_month="last")
        try:
            n = int(payload)
        except ValueError:
            raise ValueError(f"invalid monthly day: {payload!r}")
        if not 1 <= n <= 31:
            raise ValueError(f"monthly day must be 1..31 (got {n})")
        return FrequencySpec(kind="monthly", day_of_month=n)

    if freq.startswith("every:"):
        payload = freq[len("every:"):].strip()
        try:
            n = int(payload)
        except ValueError:
            raise ValueError(f"invalid every interval: {payload!r}")
        if n < 1:
            raise ValueError(f"every interval must be >= 1 (got {n})")
        return FrequencySpec(kind="every", interval_days=n)

    parts = [p.strip() for p in freq.split(",") if p.strip()]
    if not parts:
        raise ValueError(f"empty frequency: {frequency!r}")
    days: set[int] = set()
    for p in parts:
        if p not in VALID_WEEKDAYS:
            raise ValueError(f"invalid frequency token: {p!r}")
        days.add(WEEKDAY_INDEX[p])
    return FrequencySpec(kind="custom", weekdays=frozenset(days))


def _last_day_of_month(d: date) -> int:
    return calendar.monthrange(d.year, d.month)[1]


@dataclass(slots=True)
class Habit:
    name: str
    frequency: str
    id: int | None = None
    description: str | None = None
    unit: str | None = None
    target: float | None = None
    created_at: date = field(default_factory=date.today)
    archived_at: date | None = None
    start_date: date | None = None
    end_date: date | None = None

    NOTE_MAX: ClassVar[int] = 280

    def is_scheduled_on(self, day: date) -> bool:
        """Whether this habit is scheduled on the given date per its frequency
        and optional seasonal window."""
        if self.start_date is not None and day < self.start_date:
            return False
        if self.end_date is not None and day > self.end_date:
            return False

        spec = parse_frequency(self.frequency)
        if spec.kind == "daily":
            return True
        if spec.kind == "weekdays":
            return day.weekday() < 5
        if spec.kind == "weekly":
            # v1: weekly habits are scheduled on Mondays only. Simple + makes
            # momentum scoring well-defined. Revisit when week-anchoring is needed.
            return day.weekday() == 0
        if spec.kind == "custom":
            assert spec.weekdays is not None
            return day.weekday() in spec.weekdays
        if spec.kind == "monthly":
            target = spec.day_of_month
            if target == "last":
                return day.day == _last_day_of_month(day)
            assert isinstance(target, int)
            eff = min(target, _last_day_of_month(day))
            return day.day == eff
        if spec.kind == "every":
            assert spec.interval_days is not None
            # Calendar-anchor from created_at. Documented deviation from the
            # original "anchor on last completion" idea: keeps is_scheduled_on
            # pure so momentum scoring stays straightforward. Revisit if users
            # want the drift-tolerant variant.
            if day < self.created_at:
                return False
            delta = (day - self.created_at).days
            return delta % spec.interval_days == 0
        raise ValueError(f"unknown frequency kind: {spec.kind!r}")

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    @property
    def is_in_season(self) -> bool:
        """Whether today's date falls within the seasonal window (if any)."""
        today = date.today()
        if self.start_date is not None and today < self.start_date:
            return False
        if self.end_date is not None and today > self.end_date:
            return False
        return True


@dataclass(slots=True)
class Completion:
    habit_id: int
    date: date
    id: int | None = None
    value: float | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        if self.note is not None and len(self.note) > Habit.NOTE_MAX:
            raise ValueError(f"note exceeds {Habit.NOTE_MAX} chars")
