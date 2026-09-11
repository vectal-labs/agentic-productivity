# Architecture

## Data flow

1. Native collectors read local agent registries and Git reflogs. A separate BB scan reads current thread placement.
2. The collector converts events into daily aggregate counts in the Mac's local timezone.
3. SQLite stores daily counts, aggregate BB placement snapshots, collector health, hashed Cursor source keys, baselines, and delivery state.
4. The reporter builds four 90-day Chart.js configurations.
5. With Discord configured, QuickChart renders the aggregate configurations into PNG files and Discord receives the daily totals and attachments.
6. Without Discord, or after a delivery failure, the summary and Chart.js data are saved locally without fallback PNGs.

## Components

- `agentic_productivity/collectors.py`: native registry and Git collectors
- `agentic_productivity/bb.py`: read-only BB placement scan
- `agentic_productivity/database.py`: aggregate SQLite schema and idempotent delivery state
- `agentic_productivity/reporting.py`: chart configuration, trendlines, Keychain access, QuickChart, and Discord delivery
- `agentic_productivity/cli.py`: manual and scheduled command orchestration
- `agentic_productivity/local_timezone.py`: operating-system timezone detection
- `launchd/`: LaunchAgent template
- `install.sh`: installs a private application copy and loads launchd
- `tests/`: behavior and privacy tests

## Storage

The source repository is not the runtime. Installation copies the Python package into `~/Library/Application Support/Corral/Agentic Productivity/app`. Runtime aggregates live beside it in `metrics.sqlite3` and survive reinstalls and uninstalls.

Local fallback reports live in dated folders under `reports/`. They are private, safely overwritten for the same report day, and kept until the user deletes them.

The webhook is separate from both locations. It lives in macOS Keychain under the service name `com.corral.agentic-productivity.discord-webhook`.

## Failure behavior

- Collector failures become explicit health states.
- Daily event counts use monotonic upserts. BB placement uses replaceable five-minute snapshots because open-thread counts can rise or fall; failed observations stay missing.
- Delivery claims are stored before network calls to avoid accidental duplicate reports.
- Failed deliveries record a short error, save a local fallback, and can be retried.
