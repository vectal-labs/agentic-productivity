# 0003 — Local timezone and interactive installer schedule

## Context

Day boundaries and the report hour were hardcoded to Europe/Warsaw and 08:00. Public users live elsewhere and have different mornings.

## Decision

- Use each user's local Mac timezone for day boundaries. This supersedes the Europe/Warsaw constraint in AGENTS.md.
- Default report time stays 08:00 local, but the interactive installer asks for it as a setup step.

The installer is interactive: it walks the user through setup (including webhook and report time) instead of requiring manual shell steps.

## Consequences

- Existing databases keep their historical day keys; only future collection uses the local zone.
- The plist template takes the report hour from installer input rather than a fixed constant.
