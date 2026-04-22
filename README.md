# flow

A momentum-based habit tracker for the terminal. No streaks. No guilt. Just consistency over time.

## Why

Most habit trackers punish you for missing a day. One broken streak and motivation evaporates. Flow takes a different approach - it tracks **momentum**, not streaks. A single miss barely matters. What matters is the pattern over time.

## Install

Requires Python 3.11+.

Recommended — install globally with [pipx](https://pipx.pypa.io/) so `flow` is on your `PATH` in every shell:

```bash
pipx install .
```

Alternatives:

```bash
# plain pip (make sure the install target's bin/ is on your PATH)
pip install --user .

# uv — exposes `flow` as a managed tool
uv tool install .

# inside an activated venv
pip install .
```

After any of these, `flow` is available as a command. Verify with:

```bash
flow --help
which flow
```

If `flow: command not found`, add the install location to your `PATH` (e.g. `~/.local/bin` for `pip --user` / `pipx`, or `~/.local/share/uv/tools/bin` for `uv tool`).

## Quick start

```bash
flow add "Exercise" --frequency daily
flow add "Read" --frequency weekdays --unit pages --target 20
flow                # bare command opens the TUI (htop / lazygit pattern)
```

Run `flow help` for an in-terminal reference card, or `flow --help` for the command index.

## Usage

### Add a habit

```bash
flow add "Exercise" --frequency daily
flow add "Read 20 pages" --frequency weekdays --unit pages --target 20
flow add "Language practice" --frequency mon,wed,fri
flow add "Pay rent" --frequency monthly:1
flow add "Deep clean" --frequency every:14
flow add "Ski" --frequency weekly --start-date 2026-12-01 --end-date 2027-04-15
```

Frequency syntax:

| form                  | meaning                                          |
|-----------------------|--------------------------------------------------|
| `daily`               | every day                                        |
| `weekdays`            | Mon–Fri                                          |
| `weekly`              | Mondays                                          |
| `mon,wed,fri`         | comma list of weekday abbreviations              |
| `monthly[:N\|:last]`  | first / Nth / last day of month (29–31 clamps)   |
| `every:N`             | every N days, anchored to `created_at`           |

Add `--start-date` / `--end-date` for seasonal habits (skipped outside the window, but still editable).

### Daily check-in (TUI)

```bash
flow            # default — opens the check-in TUI
flow check      # explicit
```

Top-level screens (`check` / `stats` / `log`) are mutually reachable from a navbar at the top of each screen.

| key       | action                                              |
|-----------|-----------------------------------------------------|
| `j` / `k` | move cursor                                         |
| `space`   | toggle done                                         |
| `v`       | set value                                           |
| `n`       | add note                                            |
| `d`       | log duration                                        |
| `e`       | edit highlighted habit                              |
| `a`       | add habit                                           |
| `u`       | undo last completion                                |
| `r`       | random pick of scheduled-but-undone                 |
| `p` / `P` | pomodoro (habit-bound / free-running)               |
| `c` / `s` / `l` | jump to check / stats / log                   |
| `t`       | toggle theme                                        |
| `h`       | help modal                                          |
| `q`       | quit                                                |

### Mark done from CLI

```bash
flow done exercise
flow done read --value 15 --note "short session"
flow done exercise --duration 25m              # 25m, 1h30m, 90s, or 1:30
flow done exercise --date 2026-04-10           # backfill a missed day
flow undo                                      # reverse the last completion
flow undo --habit exercise                     # scoped undo
```

For habits with a time unit (`minutes`, `hours`), `--duration` also derives `--value` when one isn't given, so time-based habits ride the same value/target momentum path.

Habit names match by exact → prefix → substring, case-insensitive. `flow done exe` resolves to `Exercise` if it's unambiguous.

### View momentum

```bash
flow list                  # quick table with score, trend, rolling rate
flow list --all            # include archived
flow list --window 30      # change the rolling-rate window
flow stats                 # full TUI dashboard with 30-day grid
flow stats read            # drill into one habit (grid + recent notes)
flow today                 # one-line summary for shell prompts
flow today --format count  # just "3/5"
```

```
habit             momentum  trend  completion
─────────────────────────────────────────────
Exercise             74       ↗      80%
Read 20 pages        91       →      95%
Meditate             65       ↘      60%
```

In `flow stats`: `j`/`k` select a habit, `enter` drills down, `escape` backs out, `A` reveals archived, `E` exports, `l` opens the log, `q` quits.

### Pomodoro

```bash
flow pomo                            # free-running 25/5
flow pomo exercise                   # logs each completed work phase
flow pomo read --work 50m --break 10m --cycles 4
flow pomo --break 0                  # skip breaks
```

When bound to a habit, each completed work phase is merged into today's completion and accumulates `duration_seconds`. Skipped or partial phases are not logged. From the TUI, `p` opens a pomo for the highlighted row, `P` starts a free pomo.

### Other commands

```bash
flow edit <habit>                            # inline flags or $EDITOR for description
flow edit read --target 25 --unit pages
flow edit read --start-date "" --end-date "" # empty string clears a field
flow archive <habit>                         # soft-delete (data preserved)
flow restore <habit>                         # unarchive
flow random                                  # pick one scheduled-but-undone habit
flow log                                     # chronological completions (last 30d)
flow log --habit read --days 90
flow export --format csv                     # stdout (or -o file.csv)
flow export --format json --all              # include archived
flow export --habit read -F json             # filter to one habit
```

### Configuration

```bash
flow config list
flow config set theme dark             # or light
flow config get theme
```

### Shell completion

```bash
flow completion zsh    # also bash, fish — prints the snippet to eval
```

## How momentum works

Flow uses a weighted recency score - an exponential moving average where recent days count more than older ones. Think of it like physical momentum: hard to stop once moving, but it slows down if you stop pushing.

- A single miss barely dents your score
- Recovery is always visible
- Partial completion counts (did 15 of 30 minutes? That matters)
- The trend arrow (↗ → ↘) tells you what matters most: direction
- Non-scheduled days don't decay your score

There are no streaks to break. There is no zero state to fear.

## Data

All data lives locally in `~/.flow/habits.db` as SQLite. No cloud. No account. No telemetry. Your habits are your business.

Point at a different DB for scripting or sandboxing:

```bash
FLOW_DB_PATH=/tmp/flow_demo.db flow add "Test"
```

Config lives next to it at `~/.flow/config.json`.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'     # editable install — `flow` points at your working copy
pytest
```

For a global dev command that tracks your working copy, use `pipx install --editable .` instead.

## License

MIT
