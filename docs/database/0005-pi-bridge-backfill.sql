-- Change: include previously omitted BB Pi prompts and sessions in daily aggregates.
-- Why: native Pi JSONL also lives in BB's pi-bridge-sessions directory.
-- No schema change or direct SQL data repair is required.
--
-- Apply: back up metrics.sqlite3 using SQLite's online backup API, update the
-- installed collectors on both hosts, then rescan Pi using collect_pi and
-- Database.store_harness_result with the existing fingerprint key. The cloud's
-- scheduled collect --snapshot and Mac snapshot import use the same identities.
-- Repeated scans are idempotent. Never replace the key or reset delivery rows.
-- A normal `agentic-productivity collect --days 90` also rescans all harnesses;
-- it does not send a report. Do not use run --force to backfill.
--
-- Verify against the pre-backfill backup: Pi counts include the added bridge
-- records; other harness counts and delivery rows are unchanged. Cloud Pi
-- coverage remains partial if source records are malformed. Queries are read-only.
SELECT day, metric, harness, count
FROM daily_metrics
WHERE day >= '2026-09-12' AND harness = 'Pi Agent'
ORDER BY day, metric;

SELECT harness, status, detail FROM collector_health WHERE harness = 'Pi Agent';
SELECT report_day, status, sent_at FROM deliveries ORDER BY report_day DESC LIMIT 5;
PRAGMA quick_check;
