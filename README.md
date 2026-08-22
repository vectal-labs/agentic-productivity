# Agentic Productivity

Agentic Productivity measures whether AI agents are making David more productive over time.

Every morning it sends three 90-day charts to a private Discord channel:

- unique local Git commits
- active agent sessions, split by harness
- instruction-bearing prompts, split by harness

The session and prompt charts use stacked bars, so each bar shows both the daily total and each harness's contribution. Every chart includes a gray dashed least-squares trendline.

## Privacy

Raw prompts, responses, paths, repository names, identities, and session IDs never leave the Mac. The local SQLite database stores aggregate counts and operational state only. QuickChart receives dates, counts, and harness labels to render PNGs. Discord receives those aggregate charts and daily totals.

The Discord webhook is stored in macOS Keychain. It is never written to Git, SQLite, logs, environment files, or the LaunchAgent plist.

## Requirements

- Apple Silicon Mac
- Python 3.11 or newer
- Git
- internet access to QuickChart and Discord during report delivery

The application has no third-party Python dependencies.

## First-time setup

Copy this folder to the target Mac, then initialize and commit it:

```sh
cd ~/code/agentic-productivity
git init -b main
./scripts/test.sh
git add .
git commit -m "Initial agentic productivity system"
```

Install the application and LaunchAgent:

```sh
./scripts/install.sh
```

Store the Discord webhook securely:

```sh
read -r -s DISCORD_WEBHOOK
printf '%s\n' "$DISCORD_WEBHOOK" | ./bin/agentic-productivity configure-webhook
unset DISCORD_WEBHOOK
```

Verify the installation without sending anything:

```sh
./bin/agentic-productivity doctor
./bin/agentic-productivity mock
./bin/agentic-productivity status
```

The installed LaunchAgent observes Cursor CLI every five minutes and sends yesterday's report at 08:00 Europe/Warsaw time. If the Mac is asleep, it catches up after wake.

## Commands

```sh
./bin/agentic-productivity doctor
./bin/agentic-productivity collect
./bin/agentic-productivity mock
./bin/agentic-productivity status
./scripts/install.sh
./scripts/uninstall.sh
```

Use `collect --days N` to backfill any local window from 1 to 366 days. The scheduled Discord report uses 90 days.

## Documentation

- [Architecture](docs/architecture.md)
- [Metric definitions](docs/metrics.md)
- [Collector registry](docs/collectors.md)
- [launchd operation](docs/launchd.md)
- [Decision records](docs/adr/README.md)

## License

[MIT](LICENSE)
