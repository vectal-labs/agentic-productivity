# 0001 — Internal prototype, Discord-only, local fallback

## Context

This project started as David's private measurement system and is being opened up publicly. There is pressure to over-engineer: multiple delivery targets, polished setup flows, broad platform support.

## Decision

- Treat the project as a quick-and-dirty internal prototype. Keep it simple and rough around the edges; do not over-engineer.
- Discord is the only external delivery platform. No Slack, email, Telegram, or other targets.
- Save the three charts and a summary locally when no Discord webhook is configured or Discord delivery fails.

## Consequences

- No multi-platform delivery abstractions.
- The tool remains useful offline and for users who skip Discord.
- Polish beyond what a prototype needs is explicitly out of scope until the data proves the tool matters.
