# Agentic Productivity

I wanted to see whether spending time on different tools, setups and internal software is actually improving my productivity. So I built this tool that measures the number of agent sessions, user prompts and git commits I make over time. That way I have real data telling me whether I'm getting more productive or just wasting time building bullshit internal tooling that's not driving results.

![Example report: three 90-day charts](docs/example-graph.png)

## Setup

Clone this repo and run the installer:

```sh
./install.sh
```

The installer walks you through everything: app, Discord webhook, and the LaunchAgent's macOS access prompts. It waits for the background access check, then sends a test report so you see the charts right away.

Every morning at 08:00 you get three 90-day charts in Discord: Git commits, agent sessions, and prompts — the last two split by harness. Only daily counts leave your Mac (to quickchart.io for rendering); prompts, paths, and identities never do.

Needs an Apple Silicon Mac, Python 3.11+, and Git. No third-party dependencies.

## Commands

```sh
./bin/agentic-productivity doctor   # check health
./bin/agentic-productivity mock     # preview the report without sending
./scripts/uninstall.sh              # remove everything
```

## Documentation

- [Architecture](docs/architecture.md)
- [Metric definitions](docs/metrics.md)
- [Collector registry](docs/collectors.md)
- [launchd operation](docs/launchd.md)
- [Decision records](docs/adr/README.md)

## License

[MIT](LICENSE)
