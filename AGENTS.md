# Agentic Productivity agent instructions

## How to answer

- Always make your responses clear & very concise.
- Write in short sentences.
- Use clear, plain English.
- Ask in plain text. Do not use a multiple-choice questions UI.

## Other people

- Other agents and humans work in this repo.
- Never undo, revert, or overwrite their changes.
- Stay on your task.

## ADRs

- Do not create a new ADR unless the User asks for one.
- Existing decisions live in `docs/adr/`.

## Git

- Do not push to GitHub unless the User asks you to.

## Subagents

- Do not launch subagents unless the User asks you to.

## Source of truth

- Metric contracts: `docs/metrics.md`
- Collector locations and coverage: `docs/collectors.md`
- Runtime and launchd behavior: `docs/launchd.md`

## Hard constraints

- Never store raw prompt text, model responses, tool output, identities, or session IDs in the aggregate database.
- Never commit the Discord webhook, SQLite state, logs, session data, or other secrets.
- Never send paths, repository names, identities, session IDs, or prompt text outside the Mac.
- Never turn a failed, unreadable, or unsupported collector into a silent zero. Report its coverage state.
- Keep days on the user's local Mac calendar (supersedes Europe/Warsaw; see ADR 0003).
- Keep delivery idempotent: one report per report day unless explicitly forced.
- Keep the webhook in macOS Keychain under `com.corral.agentic-productivity.discord-webhook`.

## Working rules

- Read the relevant docs and source files in full before changing behavior.
- Prefer native, read-only agent registries over process inspection or guessed timestamps.
- Add a behavior-focused test for every collector or metric change.
- Preserve the existing aggregate database during installs and uninstalls.
- Keep the LaunchAgent template free of secrets.
- If GitHub auth fails inside a restricted agent shell, verify it through the normal Keychain-backed environment before declaring authentication blocked.
- Never run Amp or other login-gated CLIs with a fake `HOME`. That can open a browser sign-in page. Do not trigger their auth flows.

## Verification

```sh
./scripts/test.sh
./bin/agentic-productivity mock
```
