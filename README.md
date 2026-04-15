# flow

A momentum-based habit tracker for the terminal. No streaks. No guilt. Just consistency over time.

## Why

Most habit trackers punish you for missing a day. One broken streak and motivation evaporates. Flow takes a different approach - it tracks **momentum**, not streaks. A single miss barely matters. What matters is the pattern over time.

## Install

Requires Python 3.11+.

```bash
pip install .
```

This installs the `flow` command.

## Usage

### Add a habit

```bash
flow add "Exercise" --frequency daily
flow add "Read 20 pages" --frequency weekdays --unit pages --target 20
flow add "Language practice" --frequency mon,wed,fri
```

Frequency accepts `daily`, `weekdays`, `weekly` (Monday), or a comma list of weekday abbreviations.

### Daily check-in

```bash
flow check
```

Opens an interactive TUI to review and mark today's habits.

| key     | action        |
|---------|---------------|
| `j`/`k` | move cursor   |
| `space` | toggle done   |
| `v`     | set value     |
| `n`     | add note      |
| `q`     | quit          |

### Mark done from CLI

```bash
flow done exercise
flow done read --value 15 --note "short session"
flow done exercise --date 2026-04-10   # backfill a missed day
```

Habit names match by exact → prefix → substring, case-insensitive. `flow done exe` resolves to `Exercise` if it's unambiguous.

### View momentum

```bash
flow list            # quick table with score, trend, rolling rate
flow stats           # full TUI dashboard with 30-day grid
flow stats read      # drill into one habit (grid + recent notes)
```

```
habit             momentum  trend  completion
─────────────────────────────────────────────
Exercise             74       ↗      80%
Read 20 pages        91       →      95%
Meditate             65       ↘      60%
```

In `flow stats`: `j`/`k` select a habit, `enter` drills down, `escape` backs out, `q` quits.

### Other commands

```bash
flow edit <habit>                         # inline flags or $EDITOR for description
flow edit read --target 25 --unit pages
flow archive <habit>                      # soft-delete (data preserved)
flow list --all                           # include archived
flow log                                  # chronological completions (last 30d)
flow log --habit read --days 90
flow export --format csv                  # stdout (or -o file.csv)
flow export --format json --all           # include archived
flow export --habit read -F json          # filter to one habit
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

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest
```

## License

MIT
