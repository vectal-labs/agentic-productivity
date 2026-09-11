-- Add aggregate five-minute BB placement observations for the fourth daily chart.
-- Why: open-thread counts can decrease and must not use monotonic daily upserts.
-- Applied automatically by agentic_productivity/database.py on its next connection.
-- Manual application: back up the local metrics.sqlite3, then run this file with sqlite3.
-- Verify: scan-bb --json records one observation; the next report has 4-bb-placement.png.
-- No production database or existing metric/delivery rows are changed.
CREATE TABLE IF NOT EXISTS bb_placement_samples (
    slot INTEGER PRIMARY KEY,
    day TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    local_count INTEGER CHECK (local_count >= 0),
    cloud_count INTEGER CHECK (cloud_count >= 0),
    unknown_count INTEGER CHECK (unknown_count >= 0),
    status TEXT NOT NULL
);
INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema', '4');
