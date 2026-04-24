-- v1: initial schema
CREATE TABLE habits (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    description TEXT,
    frequency   TEXT NOT NULL,
    unit        TEXT,
    target      REAL,
    created_at  DATE NOT NULL,
    archived_at DATE
);
CREATE TABLE completions (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    habit_id  INTEGER NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
    date      DATE NOT NULL,
    value     REAL,
    note      TEXT,
    UNIQUE(habit_id, date)
);
CREATE INDEX idx_completions_habit_date ON completions(habit_id, date);
