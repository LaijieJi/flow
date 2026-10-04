# Product Specification — `flow`

> A momentum-based habit tracker for the terminal. No streaks. No guilt. Just consistency over time.

---

## Problem Statement

Existing habit trackers optimize for streak maintenance, which creates perverse incentives: users fabricate completions, feel disproportionate guilt from single misses, and quit entirely after a broken streak. The tool should reinforce *consistency over time*, not *perfect execution every day*. The target user is a developer comfortable in the terminal who wants to track personal habits without leaving their workflow environment.

---

## Core Design Principles

**1. Never punish a miss. Only reward a pattern.**
The system never shows a "streak broken" state. There is no concept of a streak in the data model or UI. A missed day is just a data point.

**2. Partial completion is real completion.**
If a habit is "exercise 30 minutes" and you did 15, that is recordable and counts toward momentum. Binary done/not-done is an oversimplification.

**3. Local-first, plain-text storage.**
All data lives in `~/.flow/` as SQLite (queryable, inspectable, portable). No cloud sync in v1. No account. No telemetry.

**4. Zero friction daily check-in.**
The primary interaction is: `flow check` → see today's habits → mark them → done. Under 60 seconds.

**5. Stats serve reflection, not judgment.**
Numbers are shown as context for the user's own analysis, not as a score to optimize. No color-coded shame.

---

## Momentum Metric — Three Models

This is the most consequential design decision. Here are the honest tradeoffs:

### Option A — Rolling Completion Rate

**What it is:** Percentage of scheduled occurrences completed in the last N days (configurable: 7, 14, 30).

```
Exercise  [████████░░]  80%  (last 14 days)
Reading   [██████████]  100% (last 14 days)
Meditation[██████░░░░]  60%  (last 14 days)
```

**Pros:** Intuitive, honest, easy to explain. Tolerates misses gracefully.

**Cons:** A bad week can tank a genuinely good month. Watching 85% decay to 84% can create its own anxiety. The window size is an arbitrary choice that changes the narrative.

**Best for:** Users who want objective data and can emotionally handle variance.

---

### Option B — Weighted Recency Score *(recommended)*

**What it is:** Completion score where recent days are weighted more heavily than older ones. Implemented as an exponential moving average. Think of it like "momentum" in the physics sense — hard to stop once moving, but it does slow down if you stop pushing.

**Formula:** `score = α * today_completed + (1 - α) * yesterday_score` where `α ≈ 0.3`

Result: A value between 0–100 that rises when you complete, decays slowly when you miss, never resets to zero.

```
Exercise   ●●●●●●●●○○  score: 74  ↗
Reading    ●●●●●●●●●●  score: 91  →
Meditation ●●●●○○●●●○  score: 65  ↘
```

**Pros:** A single miss barely dents your score. Recovery is visible. The directional arrow (↗ ↘ →) gives more signal than the number itself. Psychologically forgiving.

**Cons:** The score is not as interpretable as a percentage — "74 means what exactly?" requires user education. The `α` parameter is a hidden judgment call.

**Best for:** Users who want a forgiving system that rewards building back up.

---

### Option C — Visual Chain (no numbers)

**What it is:** A 30-day grid where each day is a block. Completed = filled. Missed = smaller (not empty) block. The visual weight of the chain communicates momentum without a number. Inspired by GitHub's contribution graph but without the shame.

```
Exercise  ▓▓▓▓▓▓▓░▓▓▓▓▓░░▓▓▓▓▓▓▓▓▓▓▓▓▓▓
Reading   ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
```

**Pros:** Qualitative at a glance. No number to anxiety-optimize. Misses are visible but not catastrophic.

**Cons:** Can't be aggregated or compared. Hard to answer "am I improving?" You lose quantitative signal entirely.

**Best for:** Users who want reflection over optimization.

> **Recommendation: Option B**, with the arrow direction as the primary signal and the score as secondary. The arrow tells you if you're building or eroding momentum right now — that's the psychologically useful information. The score is just context.

---

## Data Model

```sql
-- habits.db (SQLite, lives in ~/.flow/)

CREATE TABLE habits (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    frequency   TEXT NOT NULL,  -- 'daily', 'weekdays', 'weekly', or 'mon,wed,fri'
    unit        TEXT,           -- optional: 'minutes', 'pages', 'reps'
    target      REAL,           -- optional: target value if unit is set
    created_at  DATE NOT NULL,
    archived_at DATE            -- soft delete
);

CREATE TABLE completions (
    id          INTEGER PRIMARY KEY,
    habit_id    INTEGER NOT NULL REFERENCES habits(id),
    date        DATE NOT NULL,
    value       REAL,           -- null = binary complete, or actual value if unit set
    note        TEXT,           -- optional reflection, max 280 chars
    UNIQUE(habit_id, date)
);
```

**Deliberately excluded from v1:** tags, categories, reminders/notifications, import/export, sync.

---

## CLI Interface

Every command should be usable non-interactively (for scripting) and interactively (for the TUI). This is a key design constraint.

```bash
# Daily workflow
flow check                  # TUI: today's habits to complete
flow done <habit>           # mark complete from CLI directly
flow done <habit> --value 25 --note "short session"

# Management
flow add "Read 20 pages" --frequency weekdays --unit pages --target 20
flow list                   # show all active habits
flow archive <habit>        # soft-delete (data preserved)
flow edit <habit>           # open in $EDITOR or inline TUI

# Review
flow stats                  # TUI: momentum scores + 30-day grid
flow stats <habit>          # drill down on one habit
flow log                    # chronological completion history

# Data
flow export --format csv    # stdout, pipe-friendly
flow export --format json
```

---

## TUI Screens

### Screen 1: Daily Check-in (`flow check`)

```
┌─ flow ──────────────────────── saturday, apr 11 ─┐
│                                                    │
│  Today's habits                                    │
│                                                    │
│  ○  Exercise          (daily)                      │
│  ●  Read 20 pages     (weekdays)  ✓ done           │
│  ○  Meditate          (daily)                      │
│  –  Language practice (mon/wed/fri) not scheduled  │
│                                                    │
│  [j/k] navigate  [space] toggle  [v] set value     │
│  [n] add note    [q] quit                          │
└────────────────────────────────────────────────────┘
```

### Screen 2: Stats Overview (`flow stats`)

```
┌─ flow stats ──────────────────────── last 30 days ─┐
│                                                     │
│  habit             momentum  trend  completion      │
│  ─────────────────────────────────────────────────  │
│  Exercise             74       ↗      80%           │
│  Read 20 pages        91       →      95%           │
│  Meditate             65       ↘      60%           │
│                                                     │
│  ▓▓▓▓▓▓▓▓▓░▓▓▓▓▓░░▓▓▓▓▓▓▓▓▓▓▓▓▓▓   Exercise       │
│  (30-day completion grid, current habit highlighted) │
│                                                     │
│  [j/k] select habit  [enter] drill down  [q] quit   │
└─────────────────────────────────────────────────────┘
```

---

## Tech Stack

**Runtime:** Python 3.11+

**TUI library: Textual** (not urwid, not blessed)
Textual's component model (widgets, reactive state, CSS-like layout) is the right abstraction for this scope. The learning curve is real but the output is significantly better than raw curses. It also renders well in both dark and light terminals and handles mouse events gracefully.

### Dependencies

| Package | Purpose | Why not alternative |
|---|---|---|
| `textual` | TUI framework | urwid: too low-level; curtsies: abandoned |
| `rich` | Styled terminal output for non-TUI commands | Already a Textual dependency |
| `click` | CLI argument parsing | argparse: too verbose; Typer: hides Click, adds magic |
| `sqlite3` | Storage | stdlib, no extra dependency |
| `python-dateutil` | Flexible date parsing for `--date` flags | datetime alone is painful |

**Deliberately not using:** Pandas, SQLAlchemy, any async framework.

---

## File Structure

```
flow/
├── flow/
│   ├── __init__.py
│   ├── cli.py          # Click entry point, routes to TUI or direct commands
│   ├── db.py           # SQLite connection, migrations, query functions
│   ├── models.py       # Dataclasses: Habit, Completion — no ORM
│   ├── momentum.py     # Pure functions: score calculation, rolling averages
│   ├── tui/
│   │   ├── app.py      # Textual App class
│   │   ├── screens/
│   │   │   ├── check.py    # Daily check-in screen
│   │   │   └── stats.py    # Stats overview screen
│   │   └── widgets/
│   │       ├── habit_row.py
│   │       └── completion_grid.py
│   └── export.py       # CSV/JSON export logic
├── tests/
│   ├── test_momentum.py   # Unit tests for score calculation (critical)
│   └── test_db.py
├── pyproject.toml
└── README.md
```

---

## Build Order

1. `db.py` + `models.py` — get storage right before anything else. Migrations matter from day one.
2. `momentum.py` — pure functions, fully unit-tested. This is the core logic.
3. CLI commands without TUI (`flow done`, `flow list`) — validates data model before building UI.
4. `flow check` TUI screen — the primary daily interaction.
5. `flow stats` TUI screen.
6. Export commands.

---

## Explicit Non-Goals (v1)

- **Not a reminder system.** Notifications are an OS-level problem. Use cron + `flow check` if you want a reminder.
- **Not synced.** Local only in v1. If sync is needed later, a simple git-tracked JSON export solves 80% of use cases.
- **Not gamified.** No badges, no points, no leaderboards. That's the problem we're solving against.
- **Not opinionated about which habits to track.** No suggested habits, no templates.
