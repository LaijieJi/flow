-- v6: completion timestamp — captures the moment a completion was logged.
-- NULL on skip rows and on backfills via `--date` (when the actual time is
-- unknown). Stored as ISO 8601 local-naive text; the column type is plain
-- TEXT so we sidestep SQLite's deprecated builtin TIMESTAMP converter.
-- Foundation for time-of-day insights (morning/evening heatmaps).
ALTER TABLE completions ADD COLUMN completed_at TEXT;
