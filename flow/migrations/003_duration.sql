-- v3: time tracking — duration (seconds) on completions
ALTER TABLE completions ADD COLUMN duration_seconds INTEGER;
