-- Add separate five-minute Cloudroom placement counts and coverage.
-- Why: cloud execution bypasses BB host placement. Preserve BB-only history unchanged.
-- Apply: back up the local metrics.sqlite3, then run this file with sqlite3.
-- The reporter applies the same additive schema automatically on its next connection.
-- Verify: scan-placement --json reports both sources; each table has at most one row
-- per slot. Reports combine paired scans only and label earlier history BB-only.
-- Only the reporter's local aggregate database changes. BB, Cloudroom, existing
-- metrics, delivery state, and any production service database remain untouched.
CREATE TABLE IF NOT EXISTS cloudroom_placement_samples (
    slot INTEGER PRIMARY KEY,
    day TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    local_count INTEGER CHECK (local_count >= 0),
    cloud_count INTEGER CHECK (cloud_count >= 0),
    unknown_count INTEGER CHECK (unknown_count >= 0),
    status TEXT NOT NULL
);
INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema', '6');
