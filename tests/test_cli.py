"""Tests for the Click CLI surface. Uses FLOW_DB_PATH env var to redirect
storage into a per-test temp file."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from flow import db
from flow.cli import main
from flow.models import Completion, Habit


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "cli.db"
    monkeypatch.setenv("FLOW_DB_PATH", str(path))
    return path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---- add ----------------------------------------------------------------------


def test_add_minimal(runner: CliRunner, db_path: Path) -> None:
    r = runner.invoke(main, ["add", "Exercise"])
    assert r.exit_code == 0, r.output
    assert "added" in r.output
    with db.session(db_path) as conn:
        habits = db.list_habits(conn)
    assert len(habits) == 1
    assert habits[0].name == "Exercise"
    assert habits[0].frequency == "daily"


def test_add_with_target_and_unit(runner: CliRunner, db_path: Path) -> None:
    r = runner.invoke(
        main,
        ["add", "Read", "-f", "weekdays", "--unit", "pages", "--target", "20"],
    )
    assert r.exit_code == 0, r.output
    with db.session(db_path) as conn:
        h = db.list_habits(conn)[0]
    assert h.target == 20
    assert h.unit == "pages"
    assert h.frequency == "weekdays"


def test_add_target_without_unit_rejected(runner: CliRunner, db_path: Path) -> None:
    r = runner.invoke(main, ["add", "Bad", "--target", "10"])
    assert r.exit_code != 0
    assert "--target requires --unit" in r.output


def test_add_invalid_frequency_rejected(runner: CliRunner, db_path: Path) -> None:
    r = runner.invoke(main, ["add", "Weird", "-f", "sometimes"])
    assert r.exit_code != 0
    assert "frequency" in r.output.lower()


def test_add_duplicate_name_rejected(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Same"])
    r = runner.invoke(main, ["add", "same"])  # case-insensitive dup
    assert r.exit_code != 0
    assert "already exists" in r.output


def test_add_custom_days_frequency(runner: CliRunner, db_path: Path) -> None:
    r = runner.invoke(main, ["add", "Lang", "-f", "mon,wed,fri"])
    assert r.exit_code == 0, r.output


# ---- list ---------------------------------------------------------------------


def test_list_empty(runner: CliRunner, db_path: Path) -> None:
    r = runner.invoke(main, ["list"])
    assert r.exit_code == 0
    assert "no habits" in r.output


def test_list_shows_habits(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "A"])
    runner.invoke(main, ["add", "B", "-f", "weekdays"])
    r = runner.invoke(main, ["list"])
    assert r.exit_code == 0
    assert "A" in r.output
    assert "B" in r.output
    assert "score" in r.output
    assert "trend" in r.output


def test_list_hides_archived_by_default(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Visible"])
    runner.invoke(main, ["add", "Hidden"])
    runner.invoke(main, ["archive", "Hidden"])
    r = runner.invoke(main, ["list"])
    assert "Visible" in r.output
    assert "Hidden" not in r.output


def test_list_all_includes_archived(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "A"])
    runner.invoke(main, ["add", "B"])
    runner.invoke(main, ["archive", "B"])
    r = runner.invoke(main, ["list", "--all"])
    assert r.exit_code == 0
    assert "A" in r.output
    assert "B" in r.output
    assert "archived" in r.output.lower()


# ---- done ---------------------------------------------------------------------


def test_done_exact_match(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Exercise"])
    r = runner.invoke(main, ["done", "Exercise"])
    assert r.exit_code == 0, r.output
    assert "✓" in r.output

    with db.session(db_path) as conn:
        h = db.list_habits(conn)[0]
        comps = db.completions_for_habit(conn, h.id)
    assert len(comps) == 1
    assert comps[0].date == date.today()


def test_done_prefix_match(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Exercise"])
    r = runner.invoke(main, ["done", "exe"])
    assert r.exit_code == 0, r.output


def test_done_substring_match(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Morning Run"])
    r = runner.invoke(main, ["done", "run"])
    assert r.exit_code == 0, r.output


def test_done_ambiguous_name(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Read"])
    runner.invoke(main, ["add", "Relax"])
    r = runner.invoke(main, ["done", "re"])
    assert r.exit_code != 0
    assert "ambiguous" in r.output.lower()


def test_done_no_match(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Exercise"])
    r = runner.invoke(main, ["done", "xyz"])
    assert r.exit_code != 0
    assert "no habit matches" in r.output


def test_done_with_value_and_note(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Read", "--unit", "pages", "--target", "20"])
    r = runner.invoke(main, ["done", "Read", "--value", "15", "--note", "short session"])
    assert r.exit_code == 0, r.output

    with db.session(db_path) as conn:
        h = db.list_habits(conn)[0]
        c = db.completions_for_habit(conn, h.id)[0]
    assert c.value == 15.0
    assert c.note == "short session"


def test_done_with_backdate(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Exercise"])
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    r = runner.invoke(main, ["done", "Exercise", "--date", yesterday])
    assert r.exit_code == 0, r.output

    with db.session(db_path) as conn:
        h = db.list_habits(conn)[0]
        c = db.completions_for_habit(conn, h.id)[0]
    assert c.date.isoformat() == yesterday


def test_done_future_date_rejected(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Exercise"])
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    r = runner.invoke(main, ["done", "Exercise", "--date", tomorrow])
    assert r.exit_code != 0
    assert "future" in r.output.lower()


def test_done_invalid_date_rejected(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Exercise"])
    r = runner.invoke(main, ["done", "Exercise", "--date", "not-a-date"])
    assert r.exit_code != 0
    assert "invalid" in r.output.lower()


def test_done_long_note_rejected(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Exercise"])
    r = runner.invoke(main, ["done", "Exercise", "--note", "x" * 281])
    assert r.exit_code != 0
    assert "note too long" in r.output


def test_done_is_idempotent(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Exercise"])
    runner.invoke(main, ["done", "Exercise", "--value", "10", "--note", "first"])
    runner.invoke(main, ["add", "Read", "--unit", "pages", "--target", "20"])
    r = runner.invoke(main, ["done", "Read", "--value", "15"])
    assert r.exit_code == 0

    # Re-mark the same habit today -> overwrites same row
    r = runner.invoke(main, ["done", "Read", "--value", "20"])
    assert r.exit_code == 0

    with db.session(db_path) as conn:
        read = [h for h in db.list_habits(conn) if h.name == "Read"][0]
        comps = db.completions_for_habit(conn, read.id)
    assert len(comps) == 1
    assert comps[0].value == 20.0


def test_done_on_archived_habit_rejected(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Old"])
    runner.invoke(main, ["archive", "Old"])
    r = runner.invoke(main, ["done", "Old"])
    assert r.exit_code != 0
    assert "archived" in r.output.lower()


# ---- archive ------------------------------------------------------------------


def test_archive_marks_habit(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Gone"])
    r = runner.invoke(main, ["archive", "Gone"])
    assert r.exit_code == 0, r.output

    with db.session(db_path) as conn:
        habits = db.list_habits(conn, include_archived=True)
    assert habits[0].archived_at is not None


def test_archive_twice_errors(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Gone"])
    runner.invoke(main, ["archive", "Gone"])
    r = runner.invoke(main, ["archive", "Gone"])
    assert r.exit_code != 0
    assert "already archived" in r.output


def test_archive_nonexistent_errors(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Real"])
    r = runner.invoke(main, ["archive", "Imaginary"])
    assert r.exit_code != 0


# ---- restore ------------------------------------------------------------------


def test_restore_unarchives(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Back"])
    runner.invoke(main, ["archive", "Back"])
    r = runner.invoke(main, ["restore", "Back"])
    assert r.exit_code == 0, r.output
    assert "restored" in r.output.lower()
    with db.session(db_path) as conn:
        h = db.list_habits(conn)[0]
    assert h.archived_at is None


def test_restore_active_errors(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Active"])
    r = runner.invoke(main, ["restore", "Active"])
    assert r.exit_code != 0
    assert "not archived" in r.output


def test_restore_nonexistent_errors(runner: CliRunner, db_path: Path) -> None:
    r = runner.invoke(main, ["restore", "Nope"])
    assert r.exit_code != 0


# ---- edit ---------------------------------------------------------------------


def test_edit_rename(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Exercise"])
    r = runner.invoke(main, ["edit", "Exercise", "--name", "Morning Exercise"])
    assert r.exit_code == 0, r.output
    with db.session(db_path) as conn:
        h = db.list_habits(conn)[0]
    assert h.name == "Morning Exercise"


def test_edit_rename_same_name_allowed(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Exercise"])
    r = runner.invoke(main, ["edit", "Exercise", "--name", "exercise"])
    assert r.exit_code == 0


def test_edit_rename_to_duplicate_rejected(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "A"])
    runner.invoke(main, ["add", "B"])
    r = runner.invoke(main, ["edit", "A", "--name", "B"])
    assert r.exit_code != 0
    assert "already exists" in r.output


def test_edit_frequency(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "A"])
    r = runner.invoke(main, ["edit", "A", "-f", "weekdays"])
    assert r.exit_code == 0, r.output
    with db.session(db_path) as conn:
        h = db.list_habits(conn)[0]
    assert h.frequency == "weekdays"


def test_edit_invalid_frequency(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "A"])
    r = runner.invoke(main, ["edit", "A", "-f", "nonsense"])
    assert r.exit_code != 0


def test_edit_unit_and_target(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Read"])
    r = runner.invoke(
        main, ["edit", "Read", "--unit", "pages", "--target", "20"]
    )
    assert r.exit_code == 0, r.output
    with db.session(db_path) as conn:
        h = db.list_habits(conn)[0]
    assert h.unit == "pages"
    assert h.target == 20.0


def test_edit_clear_target_and_unit(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Read", "--unit", "pages", "--target", "20"])
    r = runner.invoke(main, ["edit", "Read", "--target", "", "--unit", ""])
    assert r.exit_code == 0, r.output
    with db.session(db_path) as conn:
        h = db.list_habits(conn)[0]
    assert h.unit is None
    assert h.target is None


def test_edit_target_without_unit_rejected(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Read"])
    r = runner.invoke(main, ["edit", "Read", "--target", "10"])
    assert r.exit_code != 0
    assert "--target requires --unit" in r.output


def test_edit_description_inline(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Exercise"])
    r = runner.invoke(
        main, ["edit", "Exercise", "--description", "30 min daily"]
    )
    assert r.exit_code == 0, r.output
    with db.session(db_path) as conn:
        h = db.list_habits(conn)[0]
    assert h.description == "30 min daily"


def test_edit_no_flags_opens_editor(
    runner: CliRunner, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner.invoke(main, ["add", "Exercise"])
    monkeypatch.setattr("click.edit", lambda *a, **kw: "edited via $EDITOR\n")
    r = runner.invoke(main, ["edit", "Exercise"])
    assert r.exit_code == 0, r.output
    with db.session(db_path) as conn:
        h = db.list_habits(conn)[0]
    assert h.description == "edited via $EDITOR"


def test_edit_editor_no_save_preserves(
    runner: CliRunner, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner.invoke(main, ["add", "Exercise", "-d", "original"])
    monkeypatch.setattr("click.edit", lambda *a, **kw: None)
    r = runner.invoke(main, ["edit", "Exercise"])
    assert r.exit_code == 0
    with db.session(db_path) as conn:
        h = db.list_habits(conn)[0]
    assert h.description == "original"


def test_edit_ambiguous_habit(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Read"])
    runner.invoke(main, ["add", "Relax"])
    r = runner.invoke(main, ["edit", "re", "-f", "weekdays"])
    assert r.exit_code != 0
    assert "ambiguous" in r.output.lower()


# ---- log ----------------------------------------------------------------------


def test_log_empty(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Exercise"])
    r = runner.invoke(main, ["log"])
    assert r.exit_code == 0
    assert "no completions" in r.output


def test_log_all_completions(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "A"])
    runner.invoke(main, ["add", "B"])
    runner.invoke(main, ["done", "A"])
    runner.invoke(main, ["done", "B"])
    r = runner.invoke(main, ["log"])
    assert r.exit_code == 0
    assert "A" in r.output
    assert "B" in r.output


def test_log_filter_by_habit(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "A"])
    runner.invoke(main, ["add", "B"])
    runner.invoke(main, ["done", "A"])
    runner.invoke(main, ["done", "B"])
    r = runner.invoke(main, ["log", "--habit", "A"])
    assert r.exit_code == 0
    assert "A" in r.output
    # 'B' only appears as the filter miss; verify via row count approximation
    # by checking the habit name column doesn't surface the other
    assert r.output.count("B") == 0


def test_log_window_filters_old_entries(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Exercise"])
    old = (date.today() - timedelta(days=40)).isoformat()
    runner.invoke(main, ["done", "Exercise", "--date", old])
    r = runner.invoke(main, ["log", "--days", "30"])
    assert r.exit_code == 0
    assert "no completions" in r.output
    r = runner.invoke(main, ["log", "--days", "60"])
    assert r.exit_code == 0
    assert old in r.output


def test_log_rejects_nonpositive_days(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Exercise"])
    r = runner.invoke(main, ["log", "--days", "0"])
    assert r.exit_code != 0


def test_log_with_value_and_note(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(main, ["add", "Read", "--unit", "pages", "--target", "20"])
    runner.invoke(main, ["done", "Read", "--value", "18", "--note", "focused"])
    r = runner.invoke(main, ["log"])
    assert r.exit_code == 0
    assert "18" in r.output
    assert "pages" in r.output
    assert "focused" in r.output
