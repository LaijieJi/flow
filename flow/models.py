"""Domain dataclasses for flow. Mirror the SQLite schema, no ORM."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import ClassVar


VALID_FREQUENCY_KEYWORDS = {"daily", "weekdays", "weekly"}
VALID_WEEKDAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}


def parse_frequency(frequency: str) -> set[int] | str:
    """Return either a keyword string ('daily'/'weekdays'/'weekly') or a set of
    weekday ints (0=Mon..6=Sun) for custom frequencies like 'mon,wed,fri'.
    Raises ValueError on invalid input."""
    freq = frequency.strip().lower()
    if freq in VALID_FREQUENCY_KEYWORDS:
        return freq
    parts = [p.strip() for p in freq.split(",") if p.strip()]
    if not parts:
        raise ValueError(f"empty frequency: {frequency!r}")
    weekday_index = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
    days: set[int] = set()
    for p in parts:
        if p not in VALID_WEEKDAYS:
            raise ValueError(f"invalid frequency token: {p!r}")
        days.add(weekday_index[p])
    return days


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

    NOTE_MAX: ClassVar[int] = 280

    def is_scheduled_on(self, day: date) -> bool:
        """Whether this habit is scheduled on the given date per its frequency."""
        parsed = parse_frequency(self.frequency)
        if parsed == "daily":
            return True
        if parsed == "weekdays":
            return day.weekday() < 5
        if parsed == "weekly":
            # Weekly habits are 'scheduled' every day of the week — caller
            # decides whether it's been done this week. Keep simple for v1.
            return True
        assert isinstance(parsed, set)
        return day.weekday() in parsed

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None


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
