"""Short-form habit aliases for fast CLI use.

Stored as JSON at ~/.flow/aliases.json (override via FLOW_ALIASES_PATH). A
config file rather than a DB table because aliases are user-specific UX
preference — they don't need to round-trip through `flow export`."""

from __future__ import annotations

import json
import os
from pathlib import Path


DEFAULT_ALIASES_PATH = Path.home() / ".flow" / "aliases.json"


def _resolve_path(path: Path | str | None) -> Path:
    if path is not None:
        return Path(path)
    env = os.environ.get("FLOW_ALIASES_PATH")
    if env:
        return Path(env)
    return DEFAULT_ALIASES_PATH


def load(path: Path | str | None = None) -> dict[str, str]:
    """Return the alias → habit-name mapping. Missing file ⇒ empty dict.
    Keys are lowercased so lookup is case-insensitive."""
    p = _resolve_path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k).lower(): str(v) for k, v in data.items() if isinstance(v, str)}


def save(aliases: dict[str, str], path: Path | str | None = None) -> None:
    p = _resolve_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(aliases, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def resolve(query: str, path: Path | str | None = None) -> str | None:
    """Return the habit-name the alias points to, or None if no match."""
    return load(path).get(query.strip().lower())


def set_alias(
    alias: str, habit_name: str, path: Path | str | None = None
) -> None:
    aliases = load(path)
    aliases[alias.strip().lower()] = habit_name
    save(aliases, path)


def remove(alias: str, path: Path | str | None = None) -> bool:
    aliases = load(path)
    key = alias.strip().lower()
    if key not in aliases:
        return False
    del aliases[key]
    save(aliases, path)
    return True
