-- v4: per-habit momentum smoothing factor (EMA alpha)
ALTER TABLE habits ADD COLUMN alpha REAL NOT NULL DEFAULT 0.3;
