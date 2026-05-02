"""Tests for flow.export + `flow export` CLI command."""

from __future__ import annotations

import csv
import io
import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from flow import db, export as flow_export
from flow.cli import main
from flow.models import Completion, Habit


@pytest.fixture
def seeded(tmp_path: Path) -> Path:
    path = tmp_path / "export.db"
    today = date(2026, 4, 13)
    with db.session(path) as conn:
        ex = db.insert_habit(
            conn, Habit(name="Exercise", frequency="daily", created_at=today - timedelta(days=5))
        )
        rd = db.insert_habit(
            conn,
            Habit(
                name="Read",
                description="fiction",
                frequency="weekdays",
                unit="pages",
                target=20.0,
                created_at=today - timedelta(days=5),
            ),
        )
        old = db.insert_habit(conn, Habit(name="Old", frequency="daily"))
        db.archive_habit(conn, old.id, when=today)

        for i in range(3):
            db.upsert_completion(
                conn, Completion(habit_id=ex.id, date=today - timedelta(days=i))
            )
        db.upsert_completion(
            conn,
            Completion(habit_id=rd.id, date=today, value=15.0, note="focused"),
        )
    return path


# ---- CSV writer ---------------------------------------------------------------


def test_csv_header_and_rows(seeded: Path) -> None:
    buf = io.StringIO()
    with db.session(seeded) as conn:
        n = flow_export.write_csv(conn, buf)
    buf.seek(0)
    reader = csv.reader(buf)
    header = next(reader)
    assert header == [
        "date",
        "habit",
        "frequency",
        "unit",
        "target",
        "value",
        "duration_seconds",
        "note",
        "status",
    ]
    rows = list(reader)
    assert len(rows) == n == 4  # 3 Exercise + 1 Read, Old is archived


def test_csv_row_shape(seeded: Path) -> None:
    buf = io.StringIO()
    with db.session(seeded) as conn:
        flow_export.write_csv(conn, buf)
    buf.seek(0)
    reader = csv.DictReader(buf)
    read_row = next(r for r in reader if r["habit"] == "Read")
    assert read_row["frequency"] == "weekdays"
    assert read_row["unit"] == "pages"
    assert read_row["target"] == "20"
    assert read_row["value"] == "15"
    assert read_row["note"] == "focused"


def test_csv_empty_returns_header_only(tmp_path: Path) -> None:
    path = tmp_path / "empty.db"
    buf = io.StringIO()
    with db.session(path) as conn:
        n = flow_export.write_csv(conn, buf)
    assert n == 0
    assert (
        buf.getvalue().strip()
        == "date,habit,frequency,unit,target,value,duration_seconds,note,status"
    )


def test_csv_include_archived(seeded: Path) -> None:
    buf = io.StringIO()
    with db.session(seeded) as conn:
        flow_export.write_csv(conn, buf, include_archived=True)
    content = buf.getvalue()
    assert "Old" not in content  # Old has no completions regardless


def test_csv_habit_filter(seeded: Path) -> None:
    buf = io.StringIO()
    with db.session(seeded) as conn:
        n = flow_export.write_csv(conn, buf, habit_filter="Read")
    assert n == 1
    buf.seek(0)
    reader = csv.DictReader(buf)
    names = {r["habit"] for r in reader}
    assert names == {"Read"}


def test_csv_escapes_notes_with_commas(tmp_path: Path) -> None:
    path = tmp_path / "commas.db"
    with db.session(path) as conn:
        h = db.insert_habit(conn, Habit(name="N", frequency="daily"))
        db.upsert_completion(
            conn,
            Completion(
                habit_id=h.id,
                date=date(2026, 4, 13),
                note='she said, "ok"',
            ),
        )
    buf = io.StringIO()
    with db.session(path) as conn:
        flow_export.write_csv(conn, buf)
    buf.seek(0)
    rows = list(csv.DictReader(buf))
    assert rows[0]["note"] == 'she said, "ok"'


# ---- JSON writer --------------------------------------------------------------


def test_json_structure(seeded: Path) -> None:
    buf = io.StringIO()
    with db.session(seeded) as conn:
        n = flow_export.write_json(
            conn, buf, exported_at=date(2026, 4, 13)
        )
    payload = json.loads(buf.getvalue())
    assert payload["exported_at"] == "2026-04-13"
    assert payload["version"] == 1
    assert n == len(payload["habits"]) == 2  # active only
    read = next(h for h in payload["habits"] if h["name"] == "Read")
    assert read["frequency"] == "weekdays"
    assert read["unit"] == "pages"
    assert read["target"] == 20.0
    assert len(read["completions"]) == 1
    assert read["completions"][0] == {
        "date": "2026-04-13",
        "value": 15.0,
        "duration_seconds": None,
        "note": "focused",
        "status": "done",
    }


def test_json_empty_has_empty_habits(tmp_path: Path) -> None:
    path = tmp_path / "empty.db"
    buf = io.StringIO()
    with db.session(path) as conn:
        flow_export.write_json(conn, buf)
    payload = json.loads(buf.getvalue())
    assert payload["habits"] == []


def test_json_includes_archived(seeded: Path) -> None:
    buf = io.StringIO()
    with db.session(seeded) as conn:
        flow_export.write_json(conn, buf, include_archived=True)
    payload = json.loads(buf.getvalue())
    names = {h["name"] for h in payload["habits"]}
    assert names == {"Exercise", "Read", "Old"}
    old = next(h for h in payload["habits"] if h["name"] == "Old")
    assert old["archived_at"] is not None


def test_json_unicode_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "uni.db"
    with db.session(path) as conn:
        h = db.insert_habit(conn, Habit(name="日记", frequency="daily"))
        db.upsert_completion(
            conn, Completion(habit_id=h.id, date=date(2026, 4, 13), note="✨ great")
        )
    buf = io.StringIO()
    with db.session(path) as conn:
        flow_export.write_json(conn, buf)
    payload = json.loads(buf.getvalue())
    assert payload["habits"][0]["name"] == "日记"
    assert payload["habits"][0]["completions"][0]["note"] == "✨ great"


# ---- CLI ----------------------------------------------------------------------


@pytest.fixture
def cli_env(
    seeded: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    monkeypatch.setenv("FLOW_DB_PATH", str(seeded))
    return seeded


def test_cli_export_csv_default(cli_env: Path) -> None:
    runner = CliRunner()
    r = runner.invoke(main, ["export"])
    assert r.exit_code == 0, r.output
    lines = r.output.strip().splitlines()
    assert lines[0] == "date,habit,frequency,unit,target,value,duration_seconds,note,status"
    assert any("Exercise" in ln for ln in lines[1:])


def test_cli_export_json(cli_env: Path) -> None:
    runner = CliRunner()
    r = runner.invoke(main, ["export", "--format", "json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert "habits" in payload
    assert payload["version"] == 1


def test_cli_export_to_file(cli_env: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    dest = tmp_path / "out.json"
    r = runner.invoke(main, ["export", "-F", "json", "-o", str(dest)])
    assert r.exit_code == 0, r.output
    payload = json.loads(dest.read_text())
    assert len(payload["habits"]) == 2


def test_cli_export_filter_unknown_habit(cli_env: Path) -> None:
    runner = CliRunner()
    r = runner.invoke(main, ["export", "--habit", "Ghost"])
    assert r.exit_code != 0
    assert "no habit" in r.output.lower()


def test_cli_export_filter_known_habit(cli_env: Path) -> None:
    runner = CliRunner()
    r = runner.invoke(main, ["export", "--habit", "Read"])
    assert r.exit_code == 0, r.output
    lines = r.output.strip().splitlines()
    # Only Read rows (plus header)
    assert len(lines) == 2
    assert "Read" in lines[1]


def test_cli_export_all_includes_archived(cli_env: Path) -> None:
    runner = CliRunner()
    r = runner.invoke(main, ["export", "-F", "json", "--all"])
    assert r.exit_code == 0
    payload = json.loads(r.output)
    names = {h["name"] for h in payload["habits"]}
    assert "Old" in names
