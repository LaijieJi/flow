"""Shared pytest fixtures + helpers for the flow test suite.

Anything multiple test files need lands here so we keep one source of truth
for the isolated-DB / isolated-CLI runner setup."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated SQLite DB path per test. Sets FLOW_DB_PATH so CLI invocations
    operate on it without touching the user's real ~/.flow/habits.db."""
    path = tmp_path / "flow.db"
    monkeypatch.setenv("FLOW_DB_PATH", str(path))
    return path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Combined DB + aliases isolation per test — used wherever both subsystems
    need to be sandboxed (alias-related tests, doctor stale-alias checks)."""
    monkeypatch.setenv("FLOW_DB_PATH", str(tmp_path / "flow.db"))
    monkeypatch.setenv("FLOW_ALIASES_PATH", str(tmp_path / "aliases.json"))
    return tmp_path


def renderable_of(widget):
    """Pull the underlying renderable out of a Static-derived Textual widget.

    Textual 8.x dropped the public `.renderable` accessor. `widget.visual`
    is either a `RichVisual` wrapping a Rich object (exposed via the private
    `_renderable` slot) or a `Content` (text/markup, exposing `.plain`).
    Centralised here so a Textual update only needs one fix-up site."""
    v = widget.visual
    inner = getattr(v, "_renderable", None)
    return inner if inner is not None else v
