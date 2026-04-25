"""Desktop notifications.

Best-effort wrapper over osascript (mac) and notify-send (linux). Mirrors
flow.tui.sound: never raises, exits silently if no notifier is on PATH so a
missing system util can't break a Pomodoro tick or a cron-fired reminder.
"""

from __future__ import annotations

import shutil
import subprocess
import sys


def send(title: str, message: str = "") -> bool:
    """Fire a desktop notification. Returns True if a notifier was invoked."""
    try:
        if sys.platform == "darwin":
            return _send_mac(title, message)
        if sys.platform.startswith("linux"):
            return _send_linux(title, message)
    except Exception:
        return False
    return False


def _osa_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _send_mac(title: str, message: str) -> bool:
    osa = shutil.which("osascript")
    if osa is None:
        return False
    script = (
        f'display notification "{_osa_escape(message)}" '
        f'with title "{_osa_escape(title)}"'
    )
    subprocess.Popen(
        [osa, "-e", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    return True


def _send_linux(title: str, message: str) -> bool:
    binary = shutil.which("notify-send")
    if binary is None:
        return False
    subprocess.Popen(
        [binary, title, message],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    return True
