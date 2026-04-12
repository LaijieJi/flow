# flow

A momentum-based habit tracker for the terminal. No streaks. No guilt. Just consistency over time.

## Why

Most habit trackers punish you for missing a day. One broken streak and motivation evaporates. Flow takes a different approach — it tracks **momentum**, not streaks. A single miss barely matters. What matters is the pattern over time.

## Install

Requires Python 3.11+.

```bash
pip install .
```

## Usage

### Add a habit

```bash
flow add "Exercise" --frequency daily
flow add "Read 20 pages" --frequency weekdays --unit pages --target 20
flow add "Language practice" --frequency mon,wed,fri
```

### Daily check-in

```bash
flow check
```

Opens an interactive TUI to review and mark today's habits.

### Mark done from CLI

```bash
flow done exercise
flow done read --value 15 --note "short session"
```

### View momentum

```bash
flow stats
```

```
habit             momentum  trend  completion
─────────────────────────────────────────────
Exercise             74       ↗      80%
Read 20 pages        91       →      95%
Meditate             65       ↘      60%
```

### Other commands

```bash
flow list                   # show all active habits
flow archive <habit>        # soft-delete (data preserved)
flow edit <habit>           # modify a habit
flow log                    # completion history
flow export --format csv    # export data (csv or json)
```

## How momentum works

Flow uses a weighted recency score — an exponential moving average where recent days count more than older ones. Think of it like physical momentum: hard to stop once moving, but it slows down if you stop pushing.

- A single miss barely dents your score
- Recovery is always visible
- Partial completion counts (did 15 of 30 minutes? That matters)
- The trend arrow (↗ → ↘) tells you what matters most: direction

There are no streaks to break. There is no zero state to fear.

## Data

All data lives locally in `~/.flow/` as SQLite. No cloud. No account. No telemetry. Your habits are your business.

## License

MIT
