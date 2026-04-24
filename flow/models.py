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
    alpha: float = 0.3

    NOTE_MAX: ClassVar[int] = 280
    ALPHA_DEFAULT: ClassVar[float] = 0.3
    ALPHA_MIN: ClassVar[float] = 0.01
    ALPHA_MAX: ClassVar[float] = 1.0

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
    duration_seconds: int | None = None

    def __post_init__(self) -> None:
        if self.note is not None and len(self.note) > Habit.NOTE_MAX:
            raise ValueError(f"note exceeds {Habit.NOTE_MAX} chars")
        if self.duration_seconds is not None and self.duration_seconds < 0:
            raise ValueError("duration_seconds must be >= 0")


_DURATION_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600}


def parse_duration(text: str) -> int:
    """Parse a human duration into seconds.

    Accepted forms:
      '25m' | '1h' | '1h30m' | '90s' | '1:30' (mm:ss) | '1:30:00' (h:mm:ss)
      '25' (bare int -> minutes, the habit-tracking default)

    Raises ValueError on empty / invalid input or negative totals.
    """
    if text is None:
        raise ValueError("empty duration")
    s = text.strip().lower()
    if not s:
        raise ValueError("empty duration")

    if ":" in s:
        parts = s.split(":")
        if len(parts) not in (2, 3):
            raise ValueError(f"invalid duration {text!r}")
        try:
            nums = [int(p) for p in parts]
        except ValueError:
            raise ValueError(f"invalid duration {text!r}")
        if any(n < 0 for n in nums):
            raise ValueError(f"invalid duration {text!r}")
        if len(nums) == 2:
            return nums[0] * 60 + nums[1]
        return nums[0] * 3600 + nums[1] * 60 + nums[2]

    if s.lstrip("-").isdigit():
        n = int(s)
        if n < 0:
            raise ValueError(f"invalid duration {text!r}")
        return n * 60

    total = 0
    num = ""
    seen_units: set[str] = set()
    for ch in s:
        if ch.isdigit():
            num += ch
            continue
        if ch in _DURATION_UNIT_SECONDS:
            if not num:
                raise ValueError(f"invalid duration {text!r}")
            if ch in seen_units:
                raise ValueError(f"invalid duration {text!r}")
            seen_units.add(ch)
            total += int(num) * _DURATION_UNIT_SECONDS[ch]
            num = ""
            continue
        raise ValueError(f"invalid duration {text!r}")
    if num:
        raise ValueError(f"invalid duration {text!r}")
    if not seen_units:
        raise ValueError(f"invalid duration {text!r}")
    return total


def format_duration(seconds: int | None) -> str:
    """Compact display (e.g. '25m', '1h30m', '45s'). Empty string if None."""
    if seconds is None:
        return ""
    if seconds < 0:
        return ""
    if seconds < 60:
        return f"{seconds}s"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    parts: list[str] = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    if s and not h:
        parts.append(f"{s}s")
    return "".join(parts) or f"{seconds}s"
