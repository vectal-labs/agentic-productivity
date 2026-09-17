# Pi bridge discovery — 2026-09-17

## Scope

Count native Pi transcripts in both Pi's default store and BB's bridge store under Pi Agent, regardless of model provider. Respect `BB_DATA_DIR`. Keep existing HMAC identities and Mac-local calendar boundaries. Do not change the metric definition.

## Verification

- The CLI regression failed before the fix: a BB-only next-day prompt was absent from the report. All six initial source-error cases incorrectly reported full coverage.
- After the fix, 62 tests pass, including default/custom BB roots, bridge-only storage, copied/forked history, timestamp normalization, local midnight, repeated CLI scans, privacy, and source-error reporting.
- Read-only Mac comparison covered the 90 days ending September 16: 292 recent files, 50 days with Pi prompts, 1,387 previously omitted prompts across 10 days. Unaffected days retained their counts.
- The cloud host has three bridge transcripts with 40 additional prompts. Four malformed JSON records are excluded and reported as partial coverage, not silently accepted.
- Rehearsed the combined backfill on a temporary SQLite backup with the existing fingerprint key. Replaying it did not change counts. Other harness counts, commits, and every delivery row were unchanged. Mock delivery produced four attachments without network access.

## Expected combined report totals

- September 14: 144 → 557 prompts.
- September 15: 304 → 661 prompts.
- September 16: 205 → 571 prompts.

These are verified stored-input totals, not a reconstruction of missing/corrupted records. Codex setup/subagent counts remain different from Pi's stored inputs. Broader metric comparability is outside this fix.

## Deployment

- Updated only `collectors.py` in both installed runtime copies, after checking their original hashes. Preserved the existing uncommitted work.
- Backed up the prior collectors and aggregate databases on both hosts. The live databases were not manually rewritten and no report was resent.
- Verified both installed collectors against their actual transcripts. The normal scheduler will collect the new sources; the full 90-day Mac history refreshes at the next scheduled report.
- For an immediate user-run backfill: `./bin/agentic-productivity collect --days 90`. See `docs/database/0005-pi-bridge-backfill.sql` for verification.

## Sources

- Local native transcripts, aggregate database, and installed collector copies; inspected read-only before deployment.
- `tests/test_productivity.py`: CLI/report and source-error regressions.
- [Pi session format](https://pi.dev/docs/latest/session-format): entry identities, timestamps, model metadata, and session trees.
- [Python filesystem traversal](https://docs.python.org/3/library/os.html#os.walk): explicit traversal errors; `rglob` suppresses access errors.
- [SQLite online backup](https://www.sqlite.org/backup.html): consistent rehearsal/backup copies of live aggregate state.
