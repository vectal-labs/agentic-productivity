# launchd operation

## Installed job

- Label: `com.corral.agentic-productivity`
- Plist: `~/Library/LaunchAgents/com.corral.agentic-productivity.plist`
- Command: Python runs `agentic_productivity.cli run --days 90 --json --quiet`
- Calendar trigger: 08:00 local time
- Interval trigger: every 300 seconds
- Wake/login trigger: `RunAtLoad`

The interval trigger exists because Cursor CLI does not retain historical per-turn timestamps. Every invocation observes its current native prompt totals. Before 08:00, the command stops after that local observation.

At or after 08:00, the job re-detects top-level Git roots under the home folder, collects yesterday's metrics, and builds the previous 90 days. With a webhook, it sends the report if yesterday has not already been delivered. Without a webhook, it saves the summary and Chart.js data locally without contacting QuickChart. The five-minute interval and `RunAtLoad` provide wake catch-up when the Mac missed 08:00.

SQLite delivery state makes normal runs idempotent. `--force` is the explicit override.

## Local fallback

Local reports live under `~/Library/Application Support/Corral/Agentic Productivity/reports/YYYY-MM-DD/` as `summary.md` and `charts.json`. Repeated runs safely replace that day's files. Reports are kept until the user deletes them.

The same fallback is saved when chart rendering or Discord delivery fails. Failed Discord deliveries remain retryable. Fallback reports never contain PNG files.

## Installation

`install.sh`:

1. Selects Python 3.11 or newer.
2. Scans the home folder for Git repositories during interactive installation and stores their top-level roots.
3. Copies the package into the private Application Support directory.
4. Renders the plist template with absolute paths.
5. Sets private file permissions and configures the Discord webhook.
6. Replaces and starts the user LaunchAgent.
7. Makes that LaunchAgent run every collector immediately and waits for it to finish, so macOS access prompts happen during installation.
8. Preserves the existing SQLite database.

The install-time access check only collects local aggregates. It does not send the scheduled report or mark it delivered. Normal `RunAtLoad` behavior still stops before 08:00 after the Cursor CLI observation.

The webhook is not placed in the plist. The runtime reads it from macOS Keychain only when a report is ready.

## Operations

```sh
./bin/agentic-productivity status
./bin/agentic-productivity doctor
./bin/agentic-productivity mock
launchctl print "gui/$(id -u)/com.corral.agentic-productivity"
launchctl kickstart -k "gui/$(id -u)/com.corral.agentic-productivity"
```

Logs:

- `~/Library/Logs/Corral/agentic-productivity.out.log`
- `~/Library/Logs/Corral/agentic-productivity.err.log`

Uninstalling removes the copied application and LaunchAgent but preserves the aggregate database and Keychain item.
