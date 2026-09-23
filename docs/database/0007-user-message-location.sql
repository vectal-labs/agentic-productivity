-- Store opaque identities and daily locations for human messages, not open threads.
-- Why: measure cloud adoption by messages sent; retries, fork copies, and repeated
-- scans must not inflate counts. Retain counted messages after native history deletion.
-- Only the reporter's local aggregate database changes. BB/Cloudroom databases
-- remain read-only. No prompt text, native IDs, thread IDs, or paths are stored here.
-- Apply: back up local metrics.sqlite3, then run this file with sqlite3; the app
-- applies this same additive schema automatically on its next connection.
-- Verify: scan-messages --json reports both sources. Repeating it must not increase
-- counts without new messages. The fourth chart uses user_message_locations only.
-- Per-source daily coverage uses existing machine_coverage rows on machine 'mac',
-- with harness names 'BB user messages' and 'Cloudroom user messages'.
CREATE TABLE IF NOT EXISTS user_message_locations (
    fingerprint TEXT PRIMARY KEY,
    day TEXT NOT NULL,
    location TEXT NOT NULL CHECK (location IN ('local', 'cloud', 'unknown')),
    source TEXT NOT NULL CHECK (source IN ('bb', 'cloudroom'))
);
CREATE INDEX IF NOT EXISTS user_message_locations_day ON user_message_locations(day);
INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema', '7');
