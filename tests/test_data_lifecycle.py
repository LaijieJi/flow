"""Tests for the data-lifecycle commands: import, backup, prune."""

from __future__ import annotations

import io
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from flow import backup as flow_backup, db, export as flow_export, importer
from flow.cli import main
from flow.models import Completion, Habit


# ---- shared fixtures ---------------------------------------------------------


@pytest.fixture
def seeded_source(tmp_path: Path) -> Path:
    """Source DB used by import tests; matches export's JSON shape."""
    path = tmp_path / "src.db"
    today = date(2026, 4, 13)
    with db.session(path) as conn:
        ex = db.insert_habit(
            conn,
            Habit(name="Exercise", frequency="daily", created_at=today - timedelta(days=4)),
        )
        rd = db.insert_habit(
            conn,
            Habit(
                name="Read",
                frequency="weekdays",
                unit="pages",
                target=20.0,
                created_at=today - timedelta(days=4),
            ),
        )
        for i in range(3):
            db.upsert_completion(
                conn, Completion(habit_id=ex.id, date=today - timedelta(days=i))
            )
        db.upsert_completion(
            conn, Completion(habit_id=rd.id, date=today, value=15.0, note="n")
        )
    return path


def _export_payload(src: Path) -> dict:
    buf = io.StringIO()
    with db.session(src) as conn:
        flow_export.write_json(conn, buf, include_archived=True)
    return json.loads(buf.getvalue())


# ---- importer (round-trip + conflict modes) ---------------------------------


def test_import_into_empty_db_roundtrips(seeded_source: Path, tmp_path: Path) -> None:
    payload = _export_payload(seeded_source)
    dest = tmp_path / "dest.db"
    with db.session(dest) as conn:
        stats = importer.import_payload(conn, payload, conflict="skip")
    assert stats.habits_added == 2
    assert stats.completions_added == 4
    assert stats.habits_skipped == 0

    # Re-export from dest, compare habit-level equality.
    re_payload = _export_payload(dest)
    src_by_name = {h["name"]: h for h in payload["habits"]}
    dst_by_name = {h["name"]: h for h in re_payload["habits"]}
    assert set(src_by_name) == set(dst_by_name)
    for name, src_h in src_by_name.items():
        dst_h = dst_by_name[name]
        for k in ("frequency", "unit", "target", "alpha", "created_at", "archived_at"):
            assert src_h[k] == dst_h[k], f"{name}.{k} drift"
        assert src_h["completions"] == dst_h["completions"]


def test_import_skip_leaves_existing_untouched(seeded_source: Path, tmp_path: Path) -> None:
    payload = _export_payload(seeded_source)
    dest = tmp_path / "dest.db"
    # Pre-seed dest with a different Exercise (different alpha) and one completion.
    with db.session(dest) as conn:
        ex = db.insert_habit(
            conn, Habit(name="Exercise", frequency="weekly", alpha=0.8)
        )
        db.upsert_completion(
            conn, Completion(habit_id=ex.id, date=date(2020, 1, 1), note="old")
        )

    with db.session(dest) as conn:
        stats = importer.import_payload(conn, payload, conflict="skip")
        ex = db.find_habit_by_name(conn, "Exercise")
        comps = db.completions_for_habit(conn, ex.id)
        rd = db.find_habit_by_name(conn, "Read")

    assert stats.habits_skipped == 1
    assert stats.completions_skipped == 3
    assert stats.habits_added == 1  # Read is new
    assert ex.frequency == "weekly"
    assert ex.alpha == 0.8
    assert [c.note for c in comps] == ["old"]
    assert rd is not None  # new habit imported


def test_import_overwrite_replaces_metadata_and_completions(
    seeded_source: Path, tmp_path: Path
) -> None:
    payload = _export_payload(seeded_source)
    dest = tmp_path / "dest.db"
    with db.session(dest) as conn:
        ex = db.insert_habit(
            conn, Habit(name="Exercise", frequency="weekly", alpha=0.8)
        )
        db.upsert_completion(
            conn, Completion(habit_id=ex.id, date=date(2020, 1, 1), note="stale")
        )
    with db.session(dest) as conn:
        stats = importer.import_payload(conn, payload, conflict="overwrite")
        ex = db.find_habit_by_name(conn, "Exercise")
        comps = db.completions_for_habit(conn, ex.id)

    assert stats.habits_overwritten == 1
    assert stats.completions_added == 4  # 3 ex + 1 read
    assert ex.frequency == "daily"
    assert all(c.date >= date(2026, 4, 11) for c in comps)
    assert all(c.note != "stale" for c in comps)


def test_import_merge_preserves_existing_completions(
    seeded_source: Path, tmp_path: Path
) -> None:
    payload = _export_payload(seeded_source)
    dest = tmp_path / "dest.db"
    with db.session(dest) as conn:
        ex = db.insert_habit(conn, Habit(name="Exercise", frequency="weekly"))
        # Pre-seed a completion on the same date as an incoming one — must win.
        db.upsert_completion(
            conn,
            Completion(habit_id=ex.id, date=date(2026, 4, 13), note="LOCAL"),
        )
    with db.session(dest) as conn:
        stats = importer.import_payload(conn, payload, conflict="merge")
        ex = db.find_habit_by_name(conn, "Exercise")
        comps = db.completions_for_habit(conn, ex.id)

    assert stats.habits_merged == 1
    # Same-date kept local; other two ex dates added; Read habit added with 1 comp.
    assert stats.completions_skipped == 1
    assert stats.completions_added == 2 + 1
    assert ex.frequency == "weekly"  # metadata untouched
    same_day = next(c for c in comps if c.date == date(2026, 4, 13))
    assert same_day.note == "LOCAL"


def test_import_rejects_unknown_version() -> None:
    with pytest.raises(importer.FlowImportError, match="version"):
        importer.import_payload(
            sqlite3.connect(":memory:"), {"version": 99, "habits": []}
        )


def test_import_rejects_unknown_conflict_mode() -> None:
    with pytest.raises(importer.FlowImportError, match="conflict"):
        importer.import_payload(
            sqlite3.connect(":memory:"),
            {"version": 1, "habits": []},
            conflict="bogus",
        )


def test_import_rejects_invalid_frequency(tmp_path: Path) -> None:
    payload = {
        "version": 1,
        "habits": [
            {
                "name": "X",
                "frequency": "garbage",
                "created_at": "2026-01-01",
                "completions": [],
            }
        ],
    }
    with db.session(tmp_path / "x.db") as conn:
        with pytest.raises(importer.FlowImportError):
            importer.import_payload(conn, payload)


# ---- import CLI --------------------------------------------------------------


def test_cli_import_default_skip(
    seeded_source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps(_export_payload(seeded_source)))
    dest = tmp_path / "dest.db"
    monkeypatch.setenv("FLOW_DB_PATH", str(dest))

    runner = CliRunner()
    r = runner.invoke(main, ["import", str(snap)])
    assert r.exit_code == 0, r.output
    assert "imported" in r.output

    # Second run on populated DB should skip without error.
    r2 = runner.invoke(main, ["import", str(snap)])
    assert r2.exit_code == 0
    assert "skipped" in r2.output


def test_cli_import_invalid_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    monkeypatch.setenv("FLOW_DB_PATH", str(tmp_path / "x.db"))
    runner = CliRunner()
    r = runner.invoke(main, ["import", str(bad)])
    assert r.exit_code != 0
    assert "invalid json" in r.output.lower()


# ---- backup ------------------------------------------------------------------


def test_backup_creates_file_with_same_data(tmp_path: Path) -> None:
    src = tmp_path / "live.db"
    with db.session(src) as conn:
        db.insert_habit(conn, Habit(name="X", frequency="daily"))
    dest = tmp_path / "snap.db"
    out = flow_backup.snapshot(src_path=src, dest_path=dest)
    assert out == dest
    assert dest.exists()
    with db.session(dest) as conn:
        habits = db.list_habits(conn)
    assert [h.name for h in habits] == ["X"]


def test_backup_refuses_to_overwrite(tmp_path: Path) -> None:
    src = tmp_path / "live.db"
    with db.session(src) as conn:
        db.insert_habit(conn, Habit(name="X", frequency="daily"))
    dest = tmp_path / "snap.db"
    flow_backup.snapshot(src_path=src, dest_path=dest)
    with pytest.raises(FileExistsError):
        flow_backup.snapshot(src_path=src, dest_path=dest)


def test_backup_default_path_uses_today(tmp_path: Path) -> None:
    today = date(2026, 4, 25)
    p = flow_backup.default_backup_path(when=today, base=tmp_path)
    assert p == tmp_path / "habits-2026-04-25.db"


def test_cli_backup_writes_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "live.db"
    with db.session(src) as conn:
        db.insert_habit(conn, Habit(name="X", frequency="daily"))
    monkeypatch.setenv("FLOW_DB_PATH", str(src))
    dest = tmp_path / "out.db"
    r = CliRunner().invoke(main, ["backup", "-o", str(dest)])
    assert r.exit_code == 0, r.output
    assert dest.exists()
    assert "backup" in r.output


def test_cli_backup_missing_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "missing.db"
    # Don't open a session — file should not exist.
    monkeypatch.setenv("FLOW_DB_PATH", str(src))
    dest = tmp_path / "out.db"
    r = CliRunner().invoke(main, ["backup", "-o", str(dest)])
    assert r.exit_code != 0
    assert "does not exist" in r.output.lower()


# ---- prune -------------------------------------------------------------------


def _seed_for_prune(path: Path) -> tuple[int, int, int]:
    today = date.today()
    with db.session(path) as conn:
        recent = db.insert_habit(conn, Habit(name="Recent", frequency="daily"))
        old = db.insert_habit(conn, Habit(name="Old", frequency="daily"))
        active = db.insert_habit(conn, Habit(name="Active", frequency="daily"))
        db.upsert_completion(conn, Completion(habit_id=old.id, date=today - timedelta(days=200)))
        db.archive_habit(conn, recent.id, when=today - timedelta(days=10))
        db.archive_habit(conn, old.id, when=today - timedelta(days=200))
    return recent.id, old.id, active.id


def test_prune_dry_run_changes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "p.db"
    _, old_id, _ = _seed_for_prune(path)
    cutoff = date.today() - timedelta(days=90)
    with db.session(path) as conn:
        candidates = db.archived_habits_older_than(conn, cutoff)
    assert [h.id for h in candidates] == [old_id]
    # Confirm dry-run via CLI doesn't mutate.
    runner = CliRunner()
    r = runner.invoke(
        main, ["prune", "--days", "90", "--dry-run"], env={"FLOW_DB_PATH": str(path)}
    )
    assert r.exit_code == 0, r.output
    assert "Old" in r.output
    with db.session(path) as conn:
        assert db.find_habit_by_name(conn, "Old") is not None


def test_prune_deletes_old_archived(tmp_path: Path) -> None:
    path = tmp_path / "p.db"
    _, old_id, active_id = _seed_for_prune(path)
    runner = CliRunner()
    r = runner.invoke(
        main,
        ["prune", "--days", "90", "--yes"],
        env={"FLOW_DB_PATH": str(path)},
    )
    assert r.exit_code == 0, r.output
    with db.session(path) as conn:
        assert db.find_habit_by_name(conn, "Old") is None
        assert db.find_habit_by_name(conn, "Active") is not None
        assert db.find_habit_by_name(conn, "Recent") is not None  # within window
        # Cascade removed completions.
        rows = conn.execute(
            "SELECT COUNT(*) AS n FROM completions WHERE habit_id = ?", (old_id,)
        ).fetchone()
        assert rows["n"] == 0


def test_prune_zero_days_includes_all_archived(tmp_path: Path) -> None:
    path = tmp_path / "p.db"
    recent_id, old_id, _ = _seed_for_prune(path)
    runner = CliRunner()
    r = runner.invoke(
        main, ["prune", "--days", "0", "--yes"], env={"FLOW_DB_PATH": str(path)}
    )
    assert r.exit_code == 0, r.output
    with db.session(path) as conn:
        for hid in (recent_id, old_id):
            assert db.get_habit(conn, hid) is None


def test_prune_no_candidates_is_noop(tmp_path: Path) -> None:
    path = tmp_path / "p.db"
    with db.session(path) as conn:
        db.insert_habit(conn, Habit(name="Live", frequency="daily"))
    runner = CliRunner()
    r = runner.invoke(
        main, ["prune", "--days", "30", "--yes"], env={"FLOW_DB_PATH": str(path)}
    )
    assert r.exit_code == 0, r.output
    assert "nothing to prune" in r.output.lower()


def test_prune_confirm_no_aborts(tmp_path: Path) -> None:
    path = tmp_path / "p.db"
    _seed_for_prune(path)
    runner = CliRunner()
    r = runner.invoke(
        main,
        ["prune", "--days", "90"],
        input="n\n",
        env={"FLOW_DB_PATH": str(path)},
    )
    assert r.exit_code == 0, r.output
    assert "aborted" in r.output.lower()
    with db.session(path) as conn:
        assert db.find_habit_by_name(conn, "Old") is not None


def test_prune_negative_days_rejected(tmp_path: Path) -> None:
    path = tmp_path / "p.db"
    with db.session(path) as conn:
        db.insert_habit(conn, Habit(name="X", frequency="daily"))
    runner = CliRunner()
    r = runner.invoke(
        main, ["prune", "--days", "-1"], env={"FLOW_DB_PATH": str(path)}
    )
    assert r.exit_code != 0
