# flow — Feature Ideas

A running list of features for future iterations. Not a commitment; a scratchpad.

---

## Design principle

**Everything in the CLI must also be reachable from the TUI.**

The CLI is scripting-friendly and stable. The TUI is zero-friction and discoverable. Any command that only exists in one half is a parity bug — a user shouldn't need to drop back to a shell to do something they can see in their own habit list.

Keep the TUI entry points shallow: one keybinding, one modal, one save. If an operation is complex enough to need multiple screens, rethink it.

Top-level screens (check / stats / log / review) are mutually reachable via `c` / `s` / `l` / `R` from anywhere. Navigating to a screen already in the stack pops back to it instead of pushing a duplicate, so wandering never balloons memory.

---

## CLI/TUI parity audit

Current surface. Items without TUI access are debt.

| Capability         | CLI          | TUI            |
|--------------------|--------------|----------------|
| Add habit          | `flow add`   | ✅ `a` key      |
| Mark done / toggle | `flow done`  | ✅ `space`      |
| Set value          | `flow done --value` | ✅ `v` key |
| Add note           | `flow done --note`  | ✅ `n` key |
| List habits        | `flow list`  | ✅ check screen |
| View momentum      | `flow stats` | ✅ stats screen |
| Drill into habit   | `flow stats <h>` | ✅ `enter` on row |
| Edit habit         | `flow edit`  | ✅ `e` on detail / `e` on check |
| Archive habit      | `flow archive` | ✅ `x` on detail |
| Restore (unarchive)| `flow restore` | ✅ `x` on archived detail |
| Completion history | `flow log`   | ✅ `l` on stats |
| Export data        | `flow export`| ✅ `E` on stats |
| Undo last done     | `flow undo`  | ✅ `u` on check |
| Random pick        | `flow random`| ✅ `r` on check |
| Today summary      | `flow today` | ✅ check header |
| Help reference     | `flow help`  | ✅ `h` any screen |
| Theme              | `flow config set theme` | ✅ `t` any screen |
| Week / month digest| `flow week` / `flow month` | ✅ `R` review screen (`w`/`m`) |
| Correlations       | `flow correlations` | ✅ review screen |
| Markdown summary   | `flow summary --out` | ✅ review screen data mirrors it |
| Per-habit alpha    | `flow add --alpha` / `flow edit --alpha` | ✅ edit modal alpha field |

Parity gaps to close before adding new features:
- [x] Edit habit from stats detail screen (`e` key)
- [x] Archive habit from stats detail screen (`x` key)
- [x] Log view as a TUI screen (`l` on stats)
- [x] Export from TUI (`E` on stats)
- [x] `flow restore <habit>` CLI command + TUI action (`x` on archived detail, `A` on stats to reveal archived)

---

## Feature backlog

### Quick wins (≤ 1 hour each)

- [x] **Help modal** — `h` key on check/stats/detail screens opens a reference card of all bindings + short usage notes. CLI parity: `flow help`.
- [x] **`flow restore <habit>`** — unarchive symmetry. TUI: revealed when browsing `list --all`.
- [x] **`flow today`** — non-interactive one-line summary. Useful for shell prompt integration.
- [x] **`flow undo`** — reverse last completion. TUI: `u` key.
- [x] **`flow random`** — pick one scheduled-but-undone habit when decision-paralyzed.
- [x] **Theme toggle** — Textual has dark/light. `t` key + CLI `flow config set theme dark`.
- [x] **Shell completion** — `flow completion <bash|zsh|fish>` prints the eval snippet.
- [x] **Cross-screen navigation** — any TUI screen reaches any other via `c` (check), `s` (stats), `l` (log). Detail still drills in; `escape` pops naturally. Repeat-nav reuses in-stack screens so the stack stays shallow. Nav keys live in a dedicated tab-strip (`NavBar`) at the top of each top-level screen — hidden from the footer so it stays focused on screen-specific actions.
- [x] **`flow` as default TUI entrypoint** — bare `flow` launches the check-in TUI (matches the `htop` / `lazygit` pattern). `flow --help` still prints the command index; subcommands are unchanged.

### Tracking extensions

- [x] **Time tracking** — `duration_seconds` field on completions. CLI `--duration 25m | 1h30m | 90s | 1:30`. TUI: `d` key on check screen. For habits with unit `minutes`/`hours`, `--duration` also derives `value` when `--value` is omitted, so time-based habits ride the existing `value/target` momentum path.
- [x] **Pomodoro timer** — `flow pomo [habit] [--work 25m] [--break 5m] [--cycles N]`. With a habit: each completed work phase is logged via `pomodoro.merge_session`, accumulating `duration_seconds` across sessions and refreshing the derived value for time-unit habits. Without: free-running timer that only rings the bell. TUI: `p` = habit-bound pomo for the highlighted row, `P` = free pomo. Both open a setup modal pre-filled with the defaults (work/break/cycles) so Enter-through starts a standard 25/5. Skipped / partial sessions are not logged.
- [ ] **Counter habits** — increment-style. `flow done pushups +10` adds to today's value instead of replacing. TUI: `+` key increments by 1, `V` key for arbitrary add.
- [ ] **Habit dependencies** — "stretch only after exercise". Soft ordering hint in TUI (dependent habits greyed until prereq done).

### Scheduling gap fixes

Currently supported: `daily`, `weekdays`, `weekly` (Monday), custom weekday lists, `monthly[:N|:last]`, `every:N`, and seasonal windows (`--start-date` / `--end-date`).

- [x] **Monthly frequency** — first/last/Nth day of month. `monthly`, `monthly:15`, `monthly:last`. Days 29–31 auto-clamp to month length.
- [x] **Every X days** — `every:3`. Calendar-anchored on `created_at` (deliberate deviation from last-completion anchoring — keeps `is_scheduled_on` pure so momentum scoring stays straightforward).
- [ ] **Weekly N times** — "workout 3x/week" with flexible day bucketing within the week. Still pending: needs week-state tracking.
- [x] **Seasonal windows** — `--start-date` / `--end-date` on habits. `is_scheduled_on` returns False outside the window. Metadata edits still allowed outside the window (edit from check screen works regardless of today's schedule).

### Review & insight

- [x] **`flow week` / `flow month`** — condensed digest. CLI prints a table of rate/done/scheduled/time/notes per habit. TUI: dedicated review screen reached via `R` from any top-level screen, `w`/`m` toggle range.
- [x] **Score history sparkline** — 12-week unicode-block sparkline on the detail screen. Uses each habit's configured alpha, labels the first-to-last score delta.
- [x] **Correlation view** — `flow correlations` CLI + review screen. Pairwise "given A done, B done" rate with base rate and lift; sorted by surprise (biggest lift above base). Min-shared-days filter skips noisy pairs.
- [x] **Per-habit momentum `alpha`** — `alpha` column on habits (default 0.3). `flow add --alpha`, `flow edit --alpha` (empty resets to default); TUI edit modal has an alpha field. `compute_momentum` picks up `habit.alpha` when no override is passed.
- [x] **Weekly summary file** — `flow summary --out week.md` (also `--range month`). Writes a markdown digest with totals, per-habit table, and any notes captured in the window.

### Data lifecycle

- [x] **`flow import <file>`** — JSON import, round-trips with export. Conflict strategy: skip / overwrite / merge (`--conflict`, default `skip`). New habits always insert; collisions are matched case-insensitively by name.
- [x] **`flow backup`** — snapshot to `~/.flow/backups/habits-YYYY-MM-DD.db` via the SQLite online-backup API. `--output` overrides the path. Refuses to overwrite an existing file so cron runs are non-destructive.
- [x] **Git-sync recipe** — README "Sync via git" section: keep `~/.flow/` in a repo, commit `habits.db` (or rotate via `flow backup`), exclude `*.db-journal`. Pair with `flow backup` for safe snapshots before pushing.
- [x] **Hard-delete after N days archived** — `flow prune --days N` (default 90). `--dry-run` lists candidates, `--yes` skips the confirm prompt. FK cascade removes the completions.

### OS integration

- [x] **Daily reminder via cron** — `flow install-cron HH:MM` rewrites the user's crontab with a single managed entry (identified by a `# flow-reminder` marker) that runs `flow remind`. `--remove` strips it; `--dry-run` prints what would change. Idempotent reinstalls replace the existing entry rather than duplicating it.
- [x] **macOS / Linux notifications** — `flow.notify.send(title, msg)` is a best-effort wrapper over `osascript` / `notify-send`. Wired into Pomodoro phase-end + completion (suppressed when the next ring is the final "done"), and into `flow remind`. Gated by `flow config set notifications true|false` (default `true`).
- [x] **`--watch` mode on stats** — `flow stats --watch N` mounts the TUI dashboard with a `set_interval(N)` reload. Cursor row is preserved across refreshes, and the title shows `· watching (Ns)` so the reload cadence is visible. Also applies to `flow stats <habit>` (detail screen).

### Pause / skip / explain

- [x] **Backfill `--date` on `flow done`** — `flow done <habit> --date YYYY-MM-DD` records a past day. Future dates rejected. Same flag is wired through `flow skip`.
- [x] **Skip status on completions** — new `status` column on completions (`done` / `skipped`). Skipped days are removed from both numerator and denominator in momentum scoring + review digests, so vacations / sick days neither boost nor decay the score. CLI: `flow skip <habit> [--date] [--note]`. TUI: `S` key on check screen toggles skip; `space` overrides skip → done. Round-trips through export/import.
- [x] **Vacation mode (`flow pause` / `flow resume`)** — pause is bulk-skip on scheduled days only. `flow pause <habit> --until DATE` or `--days N`. Existing `done` rows in the window are preserved (INSERT OR IGNORE). `flow resume <habit>` clears all future skips. `flow list` and the detail screen show `(paused → DATE)` when a habit has any future skip; `flow today` and `flow random` exclude paused-today habits from the to-do count.
- [x] **`flow why <habit>`** — score explanation. Prints score / trend / rate, alpha, last-done + last-miss dates, and a 14-day glyph row (D/d/M/S/·) so you can eyeball what's pulling the score.

### Deliberate v1 non-goals worth revisiting

- [ ] **Tags/categories** — only if you regularly hit >10 habits.
- [ ] **Streak view as opt-in** — kept off by default; toggle with `flow config set show-streaks true`. Still never a default metric.
- [ ] **Cloud sync** — probably still no. Git export covers 80% of the need.

---

## Suggested first batch

Three features that stack well:

1. **Help modal** (`h` key) — easy win, immediate discoverability improvement.
2. **CLI/TUI parity pass** — edit, archive, restore, log, export from inside TUI. Close the debt before adding more.
3. **Time tracking** — `duration_seconds` field + CLI/TUI. Foundation for Pomodoro.
4. **Pomodoro** — once time tracking is in place.

Parity fixes are underlined: they make the product feel whole before new surfaces get added on top.
