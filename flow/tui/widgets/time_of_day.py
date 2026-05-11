"""Time-of-day distribution strip — 24 hour buckets, intensity = density.

Surfaces "morning person" / "evening person" patterns from `completed_at`
stamps. Backfilled completions (NULL stamp) and skip rows are excluded; only
real logged-now events contribute. Falls back to a placeholder when there's
not enough data yet."""

from __future__ import annotations

from typing import Iterable

from rich.text import Text
from textual.widgets import Static

from ...models import Completion


SPARK_LEVELS = " ▁▂▃▄▅▆▇█"
HOUR_BUCKETS = 24


def hour_buckets(completions: Iterable[Completion]) -> list[int]:
    """Count timestamped done-completions per hour-of-day (0..23)."""
    counts = [0] * HOUR_BUCKETS
    for c in completions:
        if not c.is_done or c.completed_at is None:
            continue
        counts[c.completed_at.hour] += 1
    return counts


def render_strip(buckets: list[int]) -> Text:
    """Render the 24-cell strip. Peak hour gets a green tint; the rest stay
    dim so the colour highlights the mode rather than competing with it."""
    out = Text()
    peak = max(buckets) if buckets else 0
    if peak == 0:
        out.append("(no time-of-day data yet)", style="dim")
        return out
    last = len(SPARK_LEVELS) - 1
    for h, n in enumerate(buckets):
        if n == 0:
            out.append(" ", style="dim")
            continue
        idx = max(1, min(last, round(n / peak * last)))
        glyph = SPARK_LEVELS[idx]
        if n == peak:
            out.append(glyph, style="green")
        else:
            out.append(glyph, style="green dim")
    return out


def render_axis() -> Text:
    """Hour labels under the strip. Trailing digit lands on the marker column,
    so '06' sits with its '6' under hour 6. The 24-mark spills one column past
    the strip width to signal end-of-day."""
    width = HOUR_BUCKETS + 1
    chars = [" "] * width
    for marker in (0, 6, 12, 18, 24):
        label = f"{marker:02d}"
        for i, ch in enumerate(label):
            col = marker - 1 + i
            if 0 <= col < width:
                chars[col] = ch
    return Text("".join(chars), style="dim")


def peak_label(buckets: list[int]) -> str:
    """Short subtitle for the panel — e.g. 'peak 09:00' or '' when empty."""
    if not buckets or max(buckets) == 0:
        return ""
    peak_hour = max(range(HOUR_BUCKETS), key=lambda h: buckets[h])
    return f"peak {peak_hour:02d}:00"


class TimeOfDayStrip(Static):
    """24-cell hourly density strip for one habit."""

    DEFAULT_CSS = """
    TimeOfDayStrip {
        height: auto;
        padding: 0 1;
        border-title-align: left;
        border-subtitle-align: right;
    }
    """

    def set_habit(self, habit_name: str, completions: Iterable[Completion]) -> None:
        comps = list(completions)
        buckets = hour_buckets(comps)
        body = Text()
        body.append(render_strip(buckets))
        body.append("\n")
        body.append(render_axis())
        self.border_title = f"{habit_name} — time of day"
        self.border_subtitle = peak_label(buckets)
        self.update(body)
