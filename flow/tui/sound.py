"""Audible feedback for timer events.

Textual's `App.bell()` writes `\\a` to the terminal, which most modern
terminals silence by default. We spawn a real sound player as a non-blocking
subprocess so the user actually hears a chime when a pomodoro phase ends.
Falls through silently when no player is available — we never want a missing
sound util to crash the TUI.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


# Kind -> (mac aiff candidate, generic wav candidate)
_SOUNDS: dict[str, tuple[str, str]] = {
    "phase": ("Tink.aiff", "bell.oga"),
    "done": ("Glass.aiff", "complete.oga"),
}


_MAC_SYSTEM_SOUNDS = Path("/System/Library/Sounds")
_LINUX_OGA_DIRS = [
    Path("/usr/share/sounds/freedesktop/stereo"),
    Path("/usr/share/sounds/gnome/default/alerts"),
]


def play(kind: str = "phase") -> None:
    """Fire a short chime. Never raises — best-effort only."""
    try:
        mac_name, oga_name = _SOUNDS.get(kind, _SOUNDS["phase"])
        if sys.platform == "darwin":
            _play_mac(mac_name)
            return
        if sys.platform.startswith("linux"):
            _play_linux(oga_name)
            return
    except Exception:
        pass


def _play_mac(filename: str) -> None:
    path = _MAC_SYSTEM_SOUNDS / filename
    if not path.exists():
        return
    afplay = shutil.which("afplay")
    if afplay is None:
        return
    subprocess.Popen(
        [afplay, str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )


def _play_linux(filename: str) -> None:
    for base in _LINUX_OGA_DIRS:
        candidate = base / filename
        if not candidate.exists():
            continue
        for player in ("paplay", "ogg123", "aplay"):
            binary = shutil.which(player)
            if binary is None:
                continue
            subprocess.Popen(
                [binary, str(candidate)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
            return
