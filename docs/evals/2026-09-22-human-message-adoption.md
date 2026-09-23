# Human-message adoption verification

- Decision: replace open-thread percentages with daily human-message percentages. Other three charts stay unchanged.
- Native contract: BB/Cloudroom `client/turn/requested` events carry origin, request identity, timestamp, and environment metadata. Provider echoes are not new human messages. Fork copies preserve request IDs but replace environments.
- Read-only comparison: independently grouped native SQL counts matched the collector for every completed day from September 8–21, 2026 (14 cases). Cases include 0% cloud, mixed use, archived history, and unknown locations. Unknown locations are excluded, never assigned to Local.
- Privacy: source registries opened with SQLite `mode=ro`; prompt text and attachment paths never returned by the queries. Only opaque fingerprints and aggregate output are retained.
- Behavioral checks: real CLI mock delivery, two versus thirty messages, automated/delegated input, retries, fork/profile copies, grouped input, attachments, midnight and DST boundaries, missing/corrupt sources, source recovery, repeat scans, retained deleted history, and scheduled refresh after daily delivery.
- Regression checks: all 72 tests passed. Legacy placement history remains intact and cannot supply the new chart. Rendered today's chart with Local/Cloud labels and no subtitle.
- Limits: permanently deleted, never-observed history cannot be recovered. Requests without their original environment fall back to the current thread environment; unresolved location stays unknown. Attribution relies on the apps' recorded sender provenance.

References: [metric contract](../metrics.md#4-human-message-cloud-adoption), [SQLite read-only URIs](https://www.sqlite.org/uri.html), [Chart.js missing data](https://www.chartjs.org/docs/latest/charts/line.html#spangaps).
