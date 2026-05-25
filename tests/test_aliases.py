"""Tests for the aliases module + `flow alias` CLI surface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from flow import aliases, db
from flow.cli import main
from flow.models import Habit


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated DB + aliases file per test."""
    db_path = tmp_path / "flow.db"
    aliases_path = tmp_path / "aliases.json"
    monkeypatch.setenv("FLOW_DB_PATH", str(db_path))
    monkeypatch.setenv("FLOW_ALIASES_PATH", str(aliases_path))
    return tmp_path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---- module surface ----------------------------------------------------------


class TestAliasesModule:
    def test_load_missing_file_returns_empty(self, env: Path) -> None:
        assert aliases.load() == {}

    def test_save_round_trips(self, env: Path) -> None:
        aliases.save({"r": "Read", "m": "Meditate"})
        loaded = aliases.load()
        assert loaded == {"r": "Read", "m": "Meditate"}

    def test_save_keys_lowercased_on_load(self, env: Path) -> None:
        # Direct file write with mixed case — load() normalises.
        path = env / "aliases.json"
        path.write_text(json.dumps({"R": "Read"}))
        loaded = aliases.load()
        assert loaded == {"r": "Read"}

    def test_resolve_returns_target(self, env: Path) -> None:
        aliases.set_alias("r", "Read")
        assert aliases.resolve("r") == "Read"
        assert aliases.resolve("R") == "Read"  # case-insensitive lookup
        assert aliases.resolve("nope") is None

    def test_remove_returns_true_when_present(self, env: Path) -> None:
        aliases.set_alias("r", "Read")
        assert aliases.remove("r") is True
        assert aliases.remove("r") is False

    def test_load_tolerates_corrupt_file(self, env: Path) -> None:
        (env / "aliases.json").write_text("{not json")
        assert aliases.load() == {}

    def test_load_tolerates_wrong_shape(self, env: Path) -> None:
        (env / "aliases.json").write_text('["wrong shape"]')
        assert aliases.load() == {}

    def test_load_skips_non_string_values(self, env: Path) -> None:
        (env / "aliases.json").write_text('{"r": 123, "m": "Meditate"}')
        assert aliases.load() == {"m": "Meditate"}


# ---- _resolve_habit integration ---------------------------------------------


def test_alias_resolves_via_done_command(
    env: Path, runner: CliRunner
) -> None:
    runner.invoke(main, ["add", "Read"])
    runner.invoke(main, ["alias", "set", "r", "Read"])
    r = runner.invoke(main, ["done", "r"])
    assert r.exit_code == 0, r.output
    with db.session() as conn:
        h = db.list_habits(conn)[0]
        comps = db.completions_for_habit(conn, h.id)
    assert len(comps) == 1


def test_stale_alias_falls_through_to_fuzzy(
    env: Path, runner: CliRunner
) -> None:
    """Alias pointing at a non-existent habit shouldn't block fuzzy match."""
    runner.invoke(main, ["add", "Read"])
    aliases.save({"r": "GhostHabit"})
    # "r" alias is stale; fuzzy prefix match on "r" should still find Read.
    r = runner.invoke(main, ["done", "r"])
    assert r.exit_code == 0, r.output


# ---- CLI subcommand surface --------------------------------------------------


def test_alias_set_unknown_habit_errors(
    env: Path, runner: CliRunner
) -> None:
    r = runner.invoke(main, ["alias", "set", "r", "Ghost"])
    assert r.exit_code != 0
    assert "no habit" in r.output.lower() or "no habits exist" in r.output.lower()


def test_alias_remove_unknown_errors(env: Path, runner: CliRunner) -> None:
    r = runner.invoke(main, ["alias", "remove", "nope"])
    assert r.exit_code != 0
    assert "no alias" in r.output


def test_alias_list_empty(env: Path, runner: CliRunner) -> None:
    r = runner.invoke(main, ["alias", "list"])
    assert r.exit_code == 0
    assert "no aliases" in r.output


def test_alias_list_shows_configured(
    env: Path, runner: CliRunner
) -> None:
    runner.invoke(main, ["add", "Read"])
    runner.invoke(main, ["alias", "set", "r", "Read"])
    r = runner.invoke(main, ["alias", "list"])
    assert r.exit_code == 0
    assert "r" in r.output
    assert "Read" in r.output


def test_alias_bare_invocation_lists(
    env: Path, runner: CliRunner
) -> None:
    """`flow alias` with no subcommand should print the list — same as
    `flow alias list`. Discoverable shorthand."""
    runner.invoke(main, ["add", "Read"])
    runner.invoke(main, ["alias", "set", "r", "Read"])
    r = runner.invoke(main, ["alias"])
    assert r.exit_code == 0
    assert "r" in r.output
