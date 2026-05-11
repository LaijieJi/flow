"""Tests for the bundled templates registry + `flow init`/`flow templates`
CLI surface."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from flow import db, templates as tpl
from flow.cli import main
from flow.models import Habit


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "flow.db"
    monkeypatch.setenv("FLOW_DB_PATH", str(path))
    return path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---- registry shape ----------------------------------------------------------


class TestRegistry:
    def test_list_returns_stable_alpha_order(self) -> None:
        names = [t.key for t in tpl.list_templates()]
        assert names == sorted(names)

    def test_get_template_is_case_insensitive(self) -> None:
        assert tpl.get_template("Reading") is not None
        assert tpl.get_template("READING") is not None
        assert tpl.get_template("reading") is tpl.get_template("READING")

    def test_get_template_unknown_returns_none(self) -> None:
        assert tpl.get_template("nope") is None

    def test_every_template_has_required_fields(self) -> None:
        for t in tpl.list_templates():
            assert t.key and t.name and t.frequency

    def test_targets_imply_units(self) -> None:
        # A target without a unit can't be entered via `flow add` (the CLI
        # rejects --target without --unit). Templates must satisfy the same
        # invariant or they'd break `flow init`.
        for t in tpl.list_templates():
            if t.target is not None:
                assert t.unit is not None, f"{t.key} has target but no unit"

    def test_every_frequency_parses(self) -> None:
        # Each bundled template must round-trip through parse_frequency or
        # `flow init` will reject its own preset at habit-creation time.
        from flow.models import parse_frequency

        for t in tpl.list_templates():
            parse_frequency(t.frequency)  # raises if invalid


# ---- flow templates ----------------------------------------------------------


def test_templates_cli_lists_catalog(runner: CliRunner, db_path: Path) -> None:
    r = runner.invoke(main, ["templates"])
    assert r.exit_code == 0
    assert "reading" in r.output
    assert "meditation" in r.output


# ---- flow init ---------------------------------------------------------------


def test_init_non_interactive_installs_templates(
    runner: CliRunner, db_path: Path
) -> None:
    r = runner.invoke(
        main, ["init", "--template", "reading", "--template", "meditation"]
    )
    assert r.exit_code == 0, r.output
    with db.session(db_path) as conn:
        names = {h.name for h in db.list_habits(conn)}
    assert names == {"Read", "Meditate"}


def test_init_rejects_unknown_template(runner: CliRunner, db_path: Path) -> None:
    r = runner.invoke(main, ["init", "--template", "bogus"])
    assert r.exit_code != 0
    assert "unknown template" in r.output


def test_init_with_existing_habits_is_noop(
    runner: CliRunner, db_path: Path
) -> None:
    runner.invoke(main, ["add", "X"])
    r = runner.invoke(main, ["init"])
    assert r.exit_code == 0
    assert "already has" in r.output
    with db.session(db_path) as conn:
        names = {h.name for h in db.list_habits(conn)}
    assert names == {"X"}


def test_init_template_collision_skips_not_errors(
    runner: CliRunner, db_path: Path
) -> None:
    # Pre-create "Read" (collides with reading template name).
    runner.invoke(main, ["add", "Read"])
    r = runner.invoke(
        main, ["init", "--template", "reading", "--template", "meditation"]
    )
    assert r.exit_code == 0, r.output
    assert "skipped" in r.output
    with db.session(db_path) as conn:
        names = {h.name for h in db.list_habits(conn)}
    # Existing Read is preserved (no overwrite); Meditate is added.
    assert names == {"Read", "Meditate"}


def test_init_interactive_picks_by_number(
    runner: CliRunner, db_path: Path
) -> None:
    # Two prompts: numeric choice, then a y/N confirmation.
    r = runner.invoke(main, ["init"], input="1\ny\n")
    assert r.exit_code == 0, r.output
    with db.session(db_path) as conn:
        habits = db.list_habits(conn)
    assert len(habits) == 1


def test_init_interactive_empty_choice_installs_nothing(
    runner: CliRunner, db_path: Path
) -> None:
    r = runner.invoke(main, ["init"], input="\n")
    assert r.exit_code == 0, r.output
    assert "nothing installed" in r.output
    with db.session(db_path) as conn:
        assert db.list_habits(conn) == []


def test_init_interactive_rejects_invalid_number(
    runner: CliRunner, db_path: Path
) -> None:
    r = runner.invoke(main, ["init"], input="99\n")
    assert r.exit_code != 0
    assert "out of range" in r.output


def test_init_interactive_rejects_non_numeric(
    runner: CliRunner, db_path: Path
) -> None:
    r = runner.invoke(main, ["init"], input="abc\n")
    assert r.exit_code != 0
    assert "invalid selection" in r.output


def test_init_interactive_decline_confirm_installs_nothing(
    runner: CliRunner, db_path: Path
) -> None:
    # Pick template 1, then decline the confirm prompt.
    r = runner.invoke(main, ["init"], input="1\nn\n")
    assert r.exit_code == 0, r.output
    assert "nothing installed" in r.output
    with db.session(db_path) as conn:
        assert db.list_habits(conn) == []


def test_init_yes_skips_confirm(
    runner: CliRunner, db_path: Path
) -> None:
    # With --yes the wizard auto-confirms; supply only the selection.
    r = runner.invoke(main, ["init", "--yes"], input="1\n")
    assert r.exit_code == 0, r.output
    with db.session(db_path) as conn:
        assert len(db.list_habits(conn)) == 1


# ---- flow add --template -----------------------------------------------------


def test_add_template_installs_with_defaults(
    runner: CliRunner, db_path: Path
) -> None:
    r = runner.invoke(main, ["add", "--template", "reading"])
    assert r.exit_code == 0, r.output
    with db.session(db_path) as conn:
        h = db.list_habits(conn)[0]
    assert h.name == "Read"
    assert h.unit == "pages"
    assert h.target == 20


def test_add_template_with_cli_override(
    runner: CliRunner, db_path: Path
) -> None:
    r = runner.invoke(
        main, ["add", "--template", "reading", "--target", "50"]
    )
    assert r.exit_code == 0, r.output
    with db.session(db_path) as conn:
        h = db.list_habits(conn)[0]
    assert h.target == 50  # CLI override beat the template default


def test_add_template_uses_template_frequency_when_default_used(
    runner: CliRunner, db_path: Path
) -> None:
    """Regression: --frequency has default="daily" (so --help shows it). If
    the implementation merged with `frequency or tpl.frequency` it would
    incorrectly let `"daily"` win over the workout template's `"mon,wed,fri"`.
    The parameter-source check fixes this."""
    r = runner.invoke(main, ["add", "--template", "workout"])
    assert r.exit_code == 0, r.output
    with db.session(db_path) as conn:
        h = db.list_habits(conn)[0]
    assert h.frequency == "mon,wed,fri"


def test_add_template_explicit_frequency_overrides_template(
    runner: CliRunner, db_path: Path
) -> None:
    r = runner.invoke(
        main, ["add", "--template", "workout", "--frequency", "weekly"]
    )
    assert r.exit_code == 0, r.output
    with db.session(db_path) as conn:
        h = db.list_habits(conn)[0]
    assert h.frequency == "weekly"


def test_add_template_with_name_override(
    runner: CliRunner, db_path: Path
) -> None:
    r = runner.invoke(main, ["add", "Books", "--template", "reading"])
    assert r.exit_code == 0, r.output
    with db.session(db_path) as conn:
        h = db.list_habits(conn)[0]
    assert h.name == "Books"
    assert h.unit == "pages"  # template's unit still applied


def test_add_template_unknown_errors(runner: CliRunner, db_path: Path) -> None:
    r = runner.invoke(main, ["add", "--template", "bogus"])
    assert r.exit_code != 0
    assert "unknown template" in r.output


def test_add_without_name_or_template_errors(
    runner: CliRunner, db_path: Path
) -> None:
    r = runner.invoke(main, ["add"])
    assert r.exit_code != 0
    assert "NAME is required" in r.output
