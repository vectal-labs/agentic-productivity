# launchd operation

## Installed job

- Label: `com.corral.agentic-productivity`
- Plist: `~/Library/LaunchAgents/com.corral.agentic-productivity.plist`
- Command: Python runs `agentic_productivity.cli run --days 90 --json --quiet`
- Calendar trigger: 08:00 local time
- Interval trigger: every 300 seconds
- Wake/login trigger: `RunAtLoad`

The interval trigger exists because Cursor CLI does not retain historical per-turn timestamps. Every invocation observes its current native prompt totals. Before 08:00, the command stops after that local observation.

At or after 08:00, the job collects yesterday's metrics, builds the previous 90 days, and sends the report if yesterday has not already been delivered. The five-minute interval and `RunAtLoad` provide wake catch-up when the Mac missed 08:00.

SQLite delivery state makes normal runs idempotent. `--force` is the explicit override.

## Installation

`install.sh`:

1. Selects Python 3.11 or newer.
2. Copies the package into the private Application Support directory.
3. Renders the plist template with absolute paths.
4. Sets private permissions.
5. Replaces and starts the user LaunchAgent.
6. Preserves the existing SQLite database.

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
