"""Tests for `flow doctor` checks + the CLI surface."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from flow import db, doctor
from flow.cli import main
from flow.models import Completion, Habit


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "flow.db"
    monkeypatch.setenv("FLOW_DB_PATH", str(path))
    return path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---- pure checks --------------------------------------------------------------


def test_run_checks_on_empty_db_is_clean(db_path: Path) -> None:
    with db.session(db_path) as conn:
        assert doctor.run_checks(conn) == []


def test_orphan_completions_detected(db_path: Path) -> None:
    with db.session(db_path) as conn:
        # Insert a row that points at a habit_id that doesn't exist.
        # Bypass the FK constraint via PRAGMA so we can stage the orphan.
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            "INSERT INTO completions (habit_id, date, status) "
            "VALUES (?, ?, 'done')",
            (999, "2026-05-01"),
        )
        conn.execute("PRAGMA foreign_keys=ON")
        issues = doctor.run_checks(conn)

    codes = {i.code for i in issues}
    assert "orphan_completions" in codes


def test_future_completions_detected(db_path: Path) -> None:
    today = date(2026, 5, 4)
    future = today + timedelta(days=2)
    with db.session(db_path) as conn:
        h = db.insert_habit(conn, Habit(name="X", frequency="daily"))
        conn.execute(
            "INSERT INTO completions (habit_id, date, status) "
            "VALUES (?, ?, 'done')",
            (h.id, future.isoformat()),
        )
        issues = doctor.check_future_completions(conn, today=today)
    assert len(issues) == 1
    assert issues[0].code == "future_completions"


def test_completion_before_creation_detected(db_path: Path) -> None:
    with db.session(db_path) as conn:
        h = db.insert_habit(
            conn,
            Habit(name="X", frequency="daily", created_at=date(2026, 5, 4)),
        )
        # Pre-creation completion — bypass app-side validation.
        conn.execute(
            "INSERT INTO completions (habit_id, date, status) "
            "VALUES (?, ?, 'done')",
            (h.id, "2026-04-01"),
        )
        issues = doctor.check_completions_before_creation(conn)
    assert len(issues) == 1
    assert issues[0].code == "completion_before_creation"


def test_skip_with_timestamp_detected(db_path: Path) -> None:
    with db.session(db_path) as conn:
        h = db.insert_habit(conn, Habit(name="X", frequency="daily"))
        # Skip rows should have NULL completed_at by v6 convention.
        conn.execute(
            "INSERT INTO completions (habit_id, date, status, completed_at) "
            "VALUES (?, ?, 'skipped', ?)",
            (h.id, "2026-05-04", datetime(2026, 5, 4, 9, 0, 0).isoformat()),
        )
        issues = doctor.check_skip_rows_with_timestamp(conn)
    assert len(issues) == 1
    assert issues[0].fixable


def test_invalid_status_detected(db_path: Path) -> None:
    with db.session(db_path) as conn:
        h = db.insert_habit(conn, Habit(name="X", frequency="daily"))
        # CHECK constraint blocks this normally; bypass via PRAGMA.
        conn.execute("PRAGMA ignore_check_constraints=ON")
        conn.execute(
            "INSERT INTO completions (habit_id, date, status) VALUES (?, ?, 'bogus')",
            (h.id, "2026-05-04"),
        )
        conn.execute("PRAGMA ignore_check_constraints=OFF")
        issues = doctor.check_invalid_status(conn)
    assert len(issues) == 1
    assert issues[0].code == "invalid_status"


def test_invalid_frequency_detected(db_path: Path) -> None:
    with db.session(db_path) as conn:
        h = db.insert_habit(conn, Habit(name="X", frequency="daily"))
        # Mutate row to bypass insert-time validation.
        conn.execute(
            "UPDATE habits SET frequency = 'never' WHERE id = ?", (h.id,)
        )
        issues = doctor.check_invalid_frequencies(conn)
    assert len(issues) == 1
    assert issues[0].code == "invalid_frequency"


def test_duplicate_completions_clean_db_is_silent(db_path: Path) -> None:
    # `check_duplicate_completions` defends against schema corruption that
    # bypasses the UNIQUE(habit_id, date) constraint. Constructing such a
    # state in a unit test would require rewriting the table schema, which
    # is more contortion than the value justifies. Smoke that it returns
    # an empty list on a clean DB.
    with db.session(db_path) as conn:
        db.insert_habit(conn, Habit(name="X", frequency="daily"))
        assert doctor.check_duplicate_completions(conn) == []


def test_empty_habit_name_detected(db_path: Path) -> None:
    with db.session(db_path) as conn:
        h = db.insert_habit(conn, Habit(name="Real", frequency="daily"))
        # Mutate the row to bypass model + insert validation. Mimics what a
        # bad import path or a hand-edited DB would produce.
        conn.execute("UPDATE habits SET name = '   ' WHERE id = ?", (h.id,))
        issues = doctor.check_empty_habit_names(conn)
    assert len(issues) == 1
    assert issues[0].code == "empty_habit_name"


def test_empty_name_check_ignores_real_habits(db_path: Path) -> None:
    with db.session(db_path) as conn:
        db.insert_habit(conn, Habit(name="Real", frequency="daily"))
        assert doctor.check_empty_habit_names(conn) == []


def test_invalid_seasonal_window_detected(db_path: Path) -> None:
    with db.session(db_path) as conn:
        h = db.insert_habit(conn, Habit(name="S", frequency="daily"))
        # Corrupt the dates directly — insert_habit and update_habit both
        # reject end_date < start_date.
        conn.execute(
            "UPDATE habits SET start_date = '2026-06-01', end_date = '2026-05-01' "
            "WHERE id = ?",
            (h.id,),
        )
        issues = doctor.check_invalid_seasonal_windows(conn)
    assert len(issues) == 1
    assert issues[0].code == "invalid_seasonal_window"


def test_alpha_out_of_range_detected(db_path: Path) -> None:
    with db.session(db_path) as conn:
        h = db.insert_habit(conn, Habit(name="A", frequency="daily"))
        conn.execute("UPDATE habits SET alpha = 2.5 WHERE id = ?", (h.id,))
        issues = doctor.check_alpha_out_of_range(conn)
    assert len(issues) == 1
    assert issues[0].code == "alpha_out_of_range"


def test_alpha_in_range_silent(db_path: Path) -> None:
    with db.session(db_path) as conn:
        db.insert_habit(conn, Habit(name="A", frequency="daily", alpha=0.3))
        assert doctor.check_alpha_out_of_range(conn) == []


# ---- stale alias checks (file-based) -----------------------------------------


def test_stale_alias_detected(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    aliases_path = tmp_path / "aliases.json"
    monkeypatch.setenv("FLOW_ALIASES_PATH", str(aliases_path))
    from flow import aliases as _aliases

    with db.session(db_path) as conn:
        db.insert_habit(conn, Habit(name="Read", frequency="daily"))
    _aliases.save({"r": "Read", "g": "Ghost"})

    with db.session(db_path) as conn:
        issues = doctor.check_stale_aliases(conn)

    assert len(issues) == 1
    assert issues[0].code == "stale_alias"
    assert issues[0].fixable


def test_stale_alias_silent_when_all_resolve(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    aliases_path = tmp_path / "aliases.json"
    monkeypatch.setenv("FLOW_ALIASES_PATH", str(aliases_path))
    from flow import aliases as _aliases

    with db.session(db_path) as conn:
        db.insert_habit(conn, Habit(name="Read", frequency="daily"))
    _aliases.save({"r": "Read"})

    with db.session(db_path) as conn:
        assert doctor.check_stale_aliases(conn) == []


def test_apply_fixes_prunes_stale_aliases(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    aliases_path = tmp_path / "aliases.json"
    monkeypatch.setenv("FLOW_ALIASES_PATH", str(aliases_path))
    from flow import aliases as _aliases

    with db.session(db_path) as conn:
        db.insert_habit(conn, Habit(name="Read", frequency="daily"))
    _aliases.save({"r": "Read", "g": "Ghost", "m": "Missing"})

    with db.session(db_path) as conn:
        issues = doctor.run_checks(conn)
        fixed = doctor.apply_fixes(conn, issues)

    assert fixed.get("stale_alias") == 2  # g and m removed
    remaining = _aliases.load()
    assert remaining == {"r": "Read"}


# ---- fix path ----------------------------------------------------------------


def test_apply_fixes_removes_orphans(db_path: Path) -> None:
    with db.session(db_path) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            "INSERT INTO completions (habit_id, date, status) "
            "VALUES (999, '2026-05-01', 'done')"
        )
        conn.execute("PRAGMA foreign_keys=ON")
        issues = doctor.run_checks(conn)
        fixed = doctor.apply_fixes(conn, issues)
        after = doctor.run_checks(conn)

    assert fixed.get("orphan_completions") == 1
    assert all(i.code != "orphan_completions" for i in after)


def test_apply_fixes_clears_stamp_on_skip(db_path: Path) -> None:
    with db.session(db_path) as conn:
        h = db.insert_habit(conn, Habit(name="X", frequency="daily"))
        conn.execute(
            "INSERT INTO completions (habit_id, date, status, completed_at) "
            "VALUES (?, ?, 'skipped', ?)",
            (h.id, "2026-05-04", datetime(2026, 5, 4, 9, 0, 0).isoformat()),
        )
        issues = doctor.run_checks(conn)
        doctor.apply_fixes(conn, issues)
        row = conn.execute(
            "SELECT completed_at FROM completions WHERE habit_id = ?", (h.id,)
        ).fetchone()

    assert row["completed_at"] is None


# ---- CLI ---------------------------------------------------------------------


def test_doctor_cli_clean_db_succeeds(
    runner: CliRunner, db_path: Path
) -> None:
    runner.invoke(main, ["add", "X"])
    r = runner.invoke(main, ["doctor"])
    assert r.exit_code == 0
    assert "no issues found" in r.output


def test_doctor_cli_reports_and_exits_nonzero(
    runner: CliRunner, db_path: Path
) -> None:
    runner.invoke(main, ["add", "X"])
    with db.session(db_path) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            "INSERT INTO completions (habit_id, date, status) "
            "VALUES (999, '2026-05-01', 'done')"
        )
    r = runner.invoke(main, ["doctor"])
    assert r.exit_code != 0
    assert "orphan_completions" in r.output


def test_doctor_cli_fix_removes_orphans(
    runner: CliRunner, db_path: Path
) -> None:
    runner.invoke(main, ["add", "X"])
    with db.session(db_path) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            "INSERT INTO completions (habit_id, date, status) "
            "VALUES (999, '2026-05-01', 'done')"
        )
    r = runner.invoke(main, ["doctor", "--fix"])
    assert r.exit_code == 0, r.output
    assert "fixed" in r.output
    # Subsequent check is clean.
    r2 = runner.invoke(main, ["doctor"])
    assert r2.exit_code == 0


def test_doctor_quiet_suppresses_clean_output(
    runner: CliRunner, db_path: Path
) -> None:
    runner.invoke(main, ["add", "X"])
    r = runner.invoke(main, ["doctor", "--quiet"])
    assert r.exit_code == 0
    assert r.output.strip() == ""


def test_doctor_fix_persists_when_unfixable_issues_remain(
    runner: CliRunner, db_path: Path
) -> None:
    """Regression guard: `sys.exit(1)` from inside the `db.session()` context
    would raise SystemExit (BaseException), bypass the post-yield commit, and
    silently discard any fixes we just applied. The CLI defers the exit until
    after the with-block so partial-fix mixed-result runs persist correctly."""
    runner.invoke(main, ["add", "X"])
    with db.session(db_path) as conn:
        h_id = db.list_habits(conn)[0].id
        # One fixable orphan, plus one unfixable pre-creation completion.
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            "INSERT INTO completions (habit_id, date, status) "
            "VALUES (999, '2026-05-01', 'done')"
        )
        conn.execute(
            "INSERT INTO completions (habit_id, date, status) "
            "VALUES (?, '2000-01-01', 'done')",
            (h_id,),
        )

    r = runner.invoke(main, ["doctor", "--fix"])
    # Unfixable issue keeps exit code non-zero.
    assert r.exit_code != 0
    assert "manual review" in r.output

    # But the orphan fix must have been persisted — re-running shows the
    # unfixable issue alone, with no orphans left.
    r2 = runner.invoke(main, ["doctor"])
    assert "orphan_completions" not in r2.output
    assert "completion_before_creation" in r2.output
