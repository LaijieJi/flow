-- v2: seasonal windows on habits
ALTER TABLE habits ADD COLUMN start_date DATE;
ALTER TABLE habits ADD COLUMN end_date   DATE;
