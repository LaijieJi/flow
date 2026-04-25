"""crontab integration for daily reminders.

Single managed entry, identified by the trailing ``# flow-reminder`` marker.
Install/replace/remove operations always rewrite the entire crontab via
``crontab -`` so we don't fight with the user's other entries — we only
touch lines that carry our marker.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass


MARKER = "# flow-reminder"

_TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")


@dataclass(frozen=True)
class Schedule:
    minute: int
    hour: int

    @property
    def cron_fields(self) -> str:
        return f"{self.minute} {self.hour} * * *"

    @property
    def hhmm(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"


def parse_time(spec: str) -> Schedule:
    m = _TIME_RE.match(spec)
    if not m:
        raise ValueError(f"invalid time {spec!r}; expected HH:MM (24h)")
    hour, minute = int(m.group(1)), int(m.group(2))
    if not 0 <= hour <= 23:
        raise ValueError(f"hour out of range: {hour}")
    if not 0 <= minute <= 59:
        raise ValueError(f"minute out of range: {minute}")
    return Schedule(minute=minute, hour=hour)


def resolve_flow_command() -> str:
    """Best-effort absolute path to the flow CLI for the cron entry. Falls
    back to ``python -m flow.cli`` so the entry still works when ``flow``
    isn't on cron's PATH (cron has a famously sparse environment)."""
    binary = shutil.which("flow")
    if binary:
        return binary
    return f"{sys.executable} -m flow.cli"


def read_crontab(runner=None) -> str:
    """Return the user's current crontab text, or '' if none is installed."""
    if shutil.which("crontab") is None:
        raise RuntimeError("crontab(1) is not available on this system")
    result = (runner or subprocess.run)(
        ["crontab", "-l"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        # `no crontab for user` is a non-zero exit — treat as empty.
        return ""
    return result.stdout


def write_crontab(text: str, runner=None) -> None:
    if shutil.which("crontab") is None:
        raise RuntimeError("crontab(1) is not available on this system")
    if text and not text.endswith("\n"):
        text += "\n"
    result = (runner or subprocess.run)(
        ["crontab", "-"],
        input=text,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"crontab install failed: {result.stderr.strip() or 'unknown error'}"
        )


def strip_managed(current: str) -> str:
    """Drop any existing flow-managed lines (identified by MARKER)."""
    kept = [
        line for line in current.splitlines() if MARKER not in line
    ]
    return "\n".join(kept)


def build_entry(schedule: Schedule, command: str | None = None) -> str:
    cmd = command or f"{resolve_flow_command()} remind"
    return f"{schedule.cron_fields} {cmd}  {MARKER}"


def install(
    schedule: Schedule, *, command: str | None = None, runner=None
) -> str:
    current = read_crontab(runner=runner)
    stripped = strip_managed(current)
    entry = build_entry(schedule, command=command)
    new_text = (stripped + "\n" if stripped else "") + entry + "\n"
    write_crontab(new_text, runner=runner)
    return entry


def remove(*, runner=None) -> bool:
    """Remove the managed entry. Returns True if anything was removed."""
    current = read_crontab(runner=runner)
    stripped = strip_managed(current)
    if stripped == current.rstrip("\n"):
        return False
    write_crontab(stripped + ("\n" if stripped else ""), runner=runner)
    return True
