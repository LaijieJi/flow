-- v5: skip status — completions can carry status='done' (default) or 'skipped'.
-- Skipped days are deliberately not-done; momentum scoring excludes them so
-- vacations / sick days don't decay the score. Bulk-skip across a window
-- powers `flow pause`.
ALTER TABLE completions ADD COLUMN status TEXT NOT NULL DEFAULT 'done'
    CHECK (status IN ('done', 'skipped'));
CREATE INDEX idx_completions_status ON completions(status);
