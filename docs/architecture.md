# Architecture

## Data flow

1. Native collectors read local agent registries and Git reflogs.
2. The collector converts events into daily aggregate counts in Europe/Warsaw time.
3. SQLite stores only daily counts, collector health, hashed Cursor source keys, baselines, and delivery state.
4. The reporter builds three 90-day Chart.js configurations.
5. QuickChart renders the aggregate configurations into PNG files.
6. Discord receives the daily totals and three PNG attachments.

## Components

- `agentic_productivity/collectors.py`: native registry and Git collectors
- `agentic_productivity/database.py`: aggregate SQLite schema and idempotent delivery state
- `agentic_productivity/reporting.py`: chart configuration, trendlines, Keychain access, QuickChart, and Discord delivery
- `agentic_productivity/cli.py`: manual and scheduled command orchestration
- `launchd/`: LaunchAgent template
- `scripts/install.sh`: installs a private application copy and loads launchd
- `tests/`: behavior and privacy tests

## Storage

The source repository is not the runtime. Installation copies the Python package into `~/Library/Application Support/Corral/Agentic Productivity/app`. Runtime aggregates live beside it in `metrics.sqlite3` and survive reinstalls and uninstalls.

The webhook is separate from both locations. It lives in macOS Keychain under the service name `com.corral.agentic-productivity.discord-webhook`.

## Failure behavior

- Collector failures become explicit health states.
- Daily counts use monotonic upserts, so a later incomplete collection cannot erase a higher count.
- Delivery claims are stored before network calls to avoid accidental duplicate reports.
- Failed deliveries record a short error and can be retried.
