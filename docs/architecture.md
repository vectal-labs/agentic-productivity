# Architecture

## Data flow

1. Native collectors read local agent registries and Git reflogs. Read-only BB and Cloudroom message readers count human input by execution location; legacy open-thread readers remain separate.
2. Each machine stores daily aggregate counts and opaque fingerprints in local SQLite, using the Mac timezone.
3. The Mac pulls cloud snapshots over SSH and unions fingerprints for days after combined reporting starts.
4. The reporter builds four 90-day Chart.js configurations from those aggregates. Fingerprints are not sent to Discord or QuickChart.
5. With Discord configured, QuickChart renders the aggregate configurations into PNG files. Discord receives only the images, with no message text ([ADR 0009](adr/0009-images-only-discord-reports.md)).
6. Without Discord, or after a delivery failure, the summary and Chart.js data are saved locally without fallback PNGs.

## Components

- `agentic_productivity/collectors.py`: native registry and Git collectors
- `agentic_productivity/fingerprints.py`: HMAC session and prompt fingerprints
- `agentic_productivity/remote.py`: snapshot export and SSH import
- `agentic_productivity/bb.py`: BB placement scan and source combination
- `agentic_productivity/cloudroom.py`: read-only Cloudroom placement scan
- `agentic_productivity/messages.py`: human-message location collector, origin filtering, and opaque identity deduplication
- `agentic_productivity/database.py`: aggregate SQLite schema and idempotent delivery state
- `agentic_productivity/reporting.py`: chart configuration, trendlines, Keychain access, QuickChart, and Discord delivery
- `agentic_productivity/cli.py`: manual and scheduled command orchestration
- `agentic_productivity/local_timezone.py`: operating-system timezone detection
- `agentic_productivity/linux.py`: Linux runtime and systemd timer install
- `launchd/`: LaunchAgent template
- `systemd/`: Linux collector timer template
- `install.sh`: installs a private application copy and loads launchd
- `tests/`: behavior and privacy tests

## Storage

The source repository is not the runtime. Installation copies the Python package into `~/Library/Application Support/Corral/Agentic Productivity/app` on the Mac and `~/.local/share/corral/agentic-productivity/app` on Linux. Runtime aggregates live beside those copies in `metrics.sqlite3` and survive reinstalls and uninstalls.

Local fallback reports live in dated folders under `reports/`. They are private, safely overwritten for the same report day, and kept until the user deletes them.

The webhook is separate from both locations. It lives in macOS Keychain under the service name `com.corral.agentic-productivity.discord-webhook`.

## Failure behavior

- Collector failures become explicit health states.
- Human-message identities are retained once, with known locations preserved after native history deletion. Failed expected sources leave adoption-chart gaps. Legacy placement snapshots remain unchanged and are never reused as message counts.
- Other daily event counts use monotonic upserts. Legacy placement uses paired, replaceable five-minute snapshots; failed sources stay missing.
- Delivery claims are stored before network calls to avoid accidental duplicate reports.
- Failed deliveries record a short error, save a local fallback, and can be retried.
