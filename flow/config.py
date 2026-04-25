"""Persistent user preferences.

JSON at ~/.flow/config.json (override via FLOW_CONFIG_PATH). Missing file ==
defaults. Keys are validated against KNOWN_KEYS so typos surface early.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path.home() / ".flow" / "config.json"

DEFAULTS: dict[str, Any] = {
    "theme": "dark",  # "dark" | "light"
    "notifications": "true",  # desktop notifications on phase ends + reminders
}

KNOWN_KEYS = set(DEFAULTS.keys())

VALID_THEMES = {"dark", "light"}
VALID_BOOLS = {"true", "false"}


def _resolve_path(path: Path | str | None) -> Path:
    if path is not None:
        return Path(path)
    env = os.environ.get("FLOW_CONFIG_PATH")
    if env:
        return Path(env)
    return DEFAULT_CONFIG_PATH


def load(path: Path | str | None = None) -> dict[str, Any]:
    p = _resolve_path(path)
    if not p.exists():
        return dict(DEFAULTS)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULTS)
    merged = dict(DEFAULTS)
    if isinstance(data, dict):
        for k, v in data.items():
            if k in KNOWN_KEYS:
                merged[k] = v
    return merged


def save(cfg: dict[str, Any], path: Path | str | None = None) -> None:
    p = _resolve_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2, sort_keys=True), encoding="utf-8")


def get(key: str, path: Path | str | None = None) -> Any:
    if key not in KNOWN_KEYS:
        raise KeyError(f"unknown config key: {key!r}")
    return load(path).get(key, DEFAULTS[key])


def set_value(key: str, value: str, path: Path | str | None = None) -> Any:
    """Coerce + validate, persist, return stored value."""
    if key not in KNOWN_KEYS:
        raise KeyError(f"unknown config key: {key!r}")
    if key == "theme":
        if value not in VALID_THEMES:
            raise ValueError(
                f"invalid theme {value!r} (expected one of: {sorted(VALID_THEMES)})"
            )
    elif key == "notifications":
        if value not in VALID_BOOLS:
            raise ValueError(
                f"invalid notifications {value!r} (expected one of: {sorted(VALID_BOOLS)})"
            )
    cfg = load(path)
    cfg[key] = value
    save(cfg, path)
    return value
