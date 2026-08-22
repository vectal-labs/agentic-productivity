# 0001 — Interactive terminal installer is the only onboarding path

Date: 2026-08-22

## Context

The original `install.sh` was built for one machine: it asked nothing, and the
Discord webhook was configured in a separate manual step from the README. For
the public open-source release we considered two onboarding approaches:

1. An interactive terminal installer that walks the user through setup.
2. A prompt users paste into their coding agent, letting the agent run setup.

The agent approach is on-brand but non-deterministic: failed setups are hard to
reproduce, and it encourages pasting a secret webhook into agent chats — a bad
look for a privacy-focused tool. Supporting both paths doubles the surface to
maintain and document.

Timezone needs no question at all: the collector can read the system timezone
from the OS, so the webhook is the only required user input.

## Decision

Ship exactly one onboarding path: `./scripts/install.sh` as a nice interactive
TUI experience in the terminal.

- It walks the user through setup step by step with clear, readable output.
- It prompts for the Discord webhook and stores it directly in macOS
  Keychain. No separate configure step.
  (Amended 2026-08-22: input is visible, not hidden. Users paste on their own
  machine and need to see the URL to confirm the paste worked; hidden input
  was worse UX for no real security gain.)
- Timezone is auto-detected from the OS; report time defaults to 08:00 local.
  The installer asks nothing it can figure out itself.
- No agent-prompt onboarding is offered or documented.
- The interactive flow is implemented in Python (stdlib only, no third-party
  TUI libraries); `install.sh` stays as a thin launcher that finds Python.
- The installer ends by sending a real test report to the user's Discord, so
  setup finishes with visible charts. Skipped when no webhook is configured.
- The webhook step is skippable but actively discouraged: the installer shows
  how to create a webhook, and skipping requires an explicit "are you sure"
  confirmation explaining that the daily Discord report is the point of the
  tool. Without a webhook, reports are only available as locally rendered
  PNG files.

## Consequences

- One deterministic, testable setup flow; failures are reproducible.
- The webhook secret goes straight from the user's clipboard to Keychain and
  never through an agent conversation.
- The README setup section collapses to: clone, run the installer.
- Existing non-interactive uses (tests, reinstalls) need a flag or detection to
  skip prompts when a webhook is already configured or stdin is not a TTY.
