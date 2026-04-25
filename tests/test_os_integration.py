"""Tests for the OS-integration surface: notify wrapper, install-cron, remind,
and `flow stats --watch`."""

from __future__ import annotations

import asyncio
import subprocess
from datetime import date
from pathlib import Path

import pytest
from click.testing import CliRunner

from flow import config as _config, cron, db, notify
from flow.cli import main
from flow.models import Completion, Habit


# ---- notify ------------------------------------------------------------------


def test_notify_send_swallows_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """notify.send must never raise — a missing notifier or a Popen blow-up
    can't be allowed to crash a Pomodoro tick or a cron-fired reminder."""

    def _boom(*a, **kw):
        raise RuntimeError("nope")

    monkeypatch.setattr(notify.subprocess, "Popen", _boom)
    # Whether True or False is returned depends on which notifier is on PATH;
    # the contract under test is "doesn't raise".
    notify.send("flow", "hello")


def test_notify_send_returns_false_when_no_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(notify.shutil, "which", lambda _: None)
    assert notify.send("flow", "hello") is False


def test_notify_mac_invokes_osascript(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notify.sys, "platform", "darwin")
    monkeypatch.setattr(notify.shutil, "which", lambda name: "/usr/bin/osascript")
    captured: dict = {}

    class _FakeProc:
        def __init__(self, args, **kwargs):
            captured["args"] = args

    monkeypatch.setattr(notify.subprocess, "Popen", _FakeProc)
    assert notify.send("flow", 'msg with "quote"') is True
    assert captured["args"][0] == "/usr/bin/osascript"
    # Quote correctly escaped so AppleScript doesn't break.
    script = captured["args"][2]
    assert '\\"quote\\"' in script
    assert script.startswith('display notification "msg with')


def test_notify_linux_invokes_notify_send(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notify.sys, "platform", "linux")
    monkeypatch.setattr(notify.shutil, "which", lambda name: "/usr/bin/notify-send")
    captured: dict = {}

    class _FakeProc:
        def __init__(self, args, **kwargs):
            captured["args"] = args

    monkeypatch.setattr(notify.subprocess, "Popen", _FakeProc)
    assert notify.send("flow", "msg") is True
    assert captured["args"] == ["/usr/bin/notify-send", "flow", "msg"]


# ---- cron --------------------------------------------------------------------


def test_parse_time_accepts_24h() -> None:
    s = cron.parse_time("22:00")
    assert s.hour == 22 and s.minute == 0
    assert s.cron_fields == "0 22 * * *"


def test_parse_time_pads_single_digit_hour() -> None:
    s = cron.parse_time("9:05")
    assert s.hour == 9 and s.minute == 5
    assert s.hhmm == "09:05"


@pytest.mark.parametrize("bad", ["", "25:00", "12:60", "noon", "12-30", "12:5"])
def test_parse_time_rejects_garbage(bad: str) -> None:
    with pytest.raises(ValueError):
        cron.parse_time(bad)


def test_strip_managed_drops_marker_lines() -> None:
    text = (
        "# user comment\n"
        "0 9 * * * other  # not ours\n"
        f"0 22 * * * /usr/local/bin/flow remind  {cron.MARKER}\n"
        "0 8 * * 1 weekly\n"
    )
    out = cron.strip_managed(text)
    assert cron.MARKER not in out
    assert "user comment" in out
    assert "other" in out
    assert "weekly" in out


def test_install_replaces_existing_managed_entry() -> None:
    """Installing twice must leave a single managed entry, never duplicates."""
    state = {"text": "0 8 * * 1 something  # mine\n"}

    def fake_runner(args, **kwargs):
        if args == ["crontab", "-l"]:
            return subprocess.CompletedProcess(args, 0, stdout=state["text"])
        if args == ["crontab", "-"]:
            state["text"] = kwargs["input"]
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected {args!r}")

    schedule = cron.parse_time("22:00")
    cron.install(schedule, command="/bin/flow remind", runner=fake_runner)
    cron.install(schedule, command="/bin/flow remind", runner=fake_runner)

    occurrences = state["text"].count(cron.MARKER)
    assert occurrences == 1
    assert "0 22 * * * /bin/flow remind" in state["text"]
    assert "something  # mine" in state["text"]


def test_remove_returns_false_when_no_managed_entry() -> None:
    state = {"text": "0 8 * * 1 something\n"}

    def fake_runner(args, **kwargs):
        if args == ["crontab", "-l"]:
            return subprocess.CompletedProcess(args, 0, stdout=state["text"])
        if args == ["crontab", "-"]:
            state["text"] = kwargs["input"]
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        raise AssertionError

    assert cron.remove(runner=fake_runner) is False


def test_remove_strips_managed_entry() -> None:
    state = {
        "text": (
            f"0 22 * * * /bin/flow remind  {cron.MARKER}\n"
            "0 8 * * 1 something\n"
        )
    }

    def fake_runner(args, **kwargs):
        if args == ["crontab", "-l"]:
            return subprocess.CompletedProcess(args, 0, stdout=state["text"])
        if args == ["crontab", "-"]:
            state["text"] = kwargs["input"]
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        raise AssertionError

    assert cron.remove(runner=fake_runner) is True
    assert cron.MARKER not in state["text"]
    assert "something" in state["text"]


# ---- install-cron CLI --------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "os.db"
    monkeypatch.setenv("FLOW_DB_PATH", str(path))
    return path


def test_install_cron_dry_run(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--dry-run prints the would-be entry but never invokes crontab."""

    def _shouldnt_run(*a, **kw):
        raise AssertionError("crontab must not be invoked on --dry-run")

    monkeypatch.setattr(cron, "read_crontab", _shouldnt_run)
    monkeypatch.setattr(cron, "write_crontab", _shouldnt_run)

    runner = CliRunner()
    r = runner.invoke(main, ["install-cron", "22:00", "--dry-run"])
    assert r.exit_code == 0, r.output
    assert "0 22 * * *" in r.output
    assert cron.MARKER in r.output


def test_install_cron_rejects_bad_time(db_path: Path) -> None:
    runner = CliRunner()
    r = runner.invoke(main, ["install-cron", "noon"])
    assert r.exit_code != 0
    assert "invalid time" in r.output.lower() or "invalid" in r.output.lower()


def test_install_cron_writes_entry(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = {"text": ""}

    def fake_runner(args, **kwargs):
        if args == ["crontab", "-l"]:
            return subprocess.CompletedProcess(args, 0, stdout=state["text"])
        if args == ["crontab", "-"]:
            state["text"] = kwargs["input"]
            return subprocess.CompletedProcess(args, 0)
        raise AssertionError

    monkeypatch.setattr(cron.shutil, "which", lambda _: "/usr/bin/crontab")
    monkeypatch.setattr(cron.subprocess, "run", fake_runner)

    runner = CliRunner()
    r = runner.invoke(main, ["install-cron", "22:00"])
    assert r.exit_code == 0, r.output
    assert cron.MARKER in state["text"]
    assert "0 22 * * *" in state["text"]


def test_install_cron_remove_idempotent(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = {"text": ""}

    def fake_runner(args, **kwargs):
        if args == ["crontab", "-l"]:
            return subprocess.CompletedProcess(args, 0, stdout=state["text"])
        if args == ["crontab", "-"]:
            state["text"] = kwargs["input"]
            return subprocess.CompletedProcess(args, 0)
        raise AssertionError

    monkeypatch.setattr(cron.shutil, "which", lambda _: "/usr/bin/crontab")
    monkeypatch.setattr(cron.subprocess, "run", fake_runner)

    runner = CliRunner()
    runner.invoke(main, ["install-cron", "22:00"])
    r = runner.invoke(main, ["install-cron", "--remove"])
    assert r.exit_code == 0, r.output
    assert cron.MARKER not in state["text"]

    # second remove: nothing to do, but exits cleanly.
    r2 = runner.invoke(main, ["install-cron", "--remove"])
    assert r2.exit_code == 0


def test_install_cron_missing_arg(db_path: Path) -> None:
    runner = CliRunner()
    r = runner.invoke(main, ["install-cron"])
    assert r.exit_code != 0
    assert "missing" in r.output.lower() or "time" in r.output.lower()


# ---- remind ------------------------------------------------------------------


def test_remind_invokes_notify(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "flow.cli._notify.send", lambda title, msg: sent.append((title, msg)) or True
    )

    runner = CliRunner()
    runner.invoke(main, ["add", "Stretch"])
    r = runner.invoke(main, ["remind"])
    assert r.exit_code == 0, r.output
    assert sent, "remind must call notify.send"
    title, msg = sent[0]
    assert title == "flow"
    # nothing done yet → the message lists it as remaining
    assert "Stretch" in msg or "0/1" in msg or "1" in msg


def test_remind_quiet_suppresses_stdout(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("flow.cli._notify.send", lambda *a, **kw: True)
    runner = CliRunner()
    r = runner.invoke(main, ["remind", "--quiet"])
    assert r.exit_code == 0
    assert r.output.strip() == ""


def test_remind_handles_empty_db(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[str] = []
    monkeypatch.setattr(
        "flow.cli._notify.send", lambda title, msg: sent.append(msg) or True
    )
    runner = CliRunner()
    r = runner.invoke(main, ["remind"])
    assert r.exit_code == 0, r.output
    assert "nothing scheduled" in sent[0]


# ---- config: notifications key ----------------------------------------------


def test_config_notifications_default_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FLOW_CONFIG_PATH", str(tmp_path / "c.json"))
    assert _config.get("notifications") == "true"


def test_config_notifications_validates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FLOW_CONFIG_PATH", str(tmp_path / "c.json"))
    _config.set_value("notifications", "false")
    assert _config.get("notifications") == "false"
    with pytest.raises(ValueError):
        _config.set_value("notifications", "yes")


# ---- stats --watch -----------------------------------------------------------


def test_stats_watch_rejects_zero(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = CliRunner()
    r = runner.invoke(main, ["stats", "--watch", "0"])
    assert r.exit_code != 0
    assert ">= 1" in r.output or "watch" in r.output.lower()


def test_stats_watch_passes_through_to_app(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}

    class _FakeApp:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def run(self) -> None:
            captured["ran"] = True

    monkeypatch.setattr("flow.tui.app.FlowApp", _FakeApp, raising=True)
    runner = CliRunner()
    r = runner.invoke(main, ["stats", "--watch", "5"])
    assert r.exit_code == 0, r.output
    assert captured["watch_interval"] == 5
    assert captured["initial"] == "stats"


async def test_stats_watch_timer_refreshes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new completion appears in the table after the watch tick fires."""
    from flow.tui.app import FlowApp
    from flow.tui.screens.stats import StatsScreen

    path = tmp_path / "watch.db"
    monkeypatch.setenv("FLOW_DB_PATH", str(path))
    today = date(2026, 4, 13)
    with db.session(path) as conn:
        h = db.insert_habit(
            conn, Habit(name="Stretch", frequency="daily", created_at=today)
        )

    app = FlowApp(db_path=path, initial="stats", today=today, watch_interval=1)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, StatsScreen)
        before = dict(screen._momentums)
        # add a completion outside the TUI, then trigger reload
        with db.session(path) as conn:
            db.upsert_completion(conn, Completion(habit_id=h.id, date=today))
        screen._load()
        await pilot.pause()
        after = dict(screen._momentums)
        assert before != after  # momentum recomputed after reload
