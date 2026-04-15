# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-04-15

First tagged release.

### Added
- SQLite storage at `~/.flow/habits.db` with versioned migrations.
- Habit model with frequencies: `daily`, `weekdays`, `weekly` (Monday), and custom weekday lists like `mon,wed,fri`.
- Completion model with optional value and short note (≤280 chars).
- Momentum engine:
  - Weighted recency score (EMA, α=0.3) over scheduled days only.
  - Trend arrows (↗ → ↘) from 3-day score delta.
  - Rolling completion rate (default 14-day window).
  - Partial completions contribute proportionally to score and rate.
- CLI commands: `add`, `done`, `list`, `edit`, `log`, `archive`, `export`, `check`, `stats`.
- Fuzzy habit name resolver (exact → prefix → substring, case-insensitive).
- Backdated completions via `--date` on `flow done`.
- Textual TUI:
  - `flow check` — daily interactive check-in (`j`/`k` nav, `space` toggle, `v` value, `n` note, `q` quit).
  - `flow stats` — momentum dashboard with 30-day completion grid.
  - `flow stats <habit>` — drill-down with summary, grid, recent notes.
- CSV and JSON export (`flow export`) with `--all`, `--habit`, `--output` flags.
- Environment override `FLOW_DB_PATH` for scripting and sandboxing.
- 145 tests covering storage, momentum, CLI, TUI pilots, export, and integration flows.

### Design decisions
- No streaks in data model or UI. A miss is just a datapoint.
- No color-coded shame in the completion grid — misses are only dimmed.
- No cloud sync, no telemetry, no account. Local-first by design.
