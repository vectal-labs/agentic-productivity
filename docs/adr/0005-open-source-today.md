# 0005 — This repo is going public today

## Context

Agentic Productivity was a private, single-machine tool. It is being open sourced today. Thousands of people will read the code, docs, and history.

## Decision

Treat every file as public.

- No personal paths, identities, emails, repo names, session data, logs, or secrets in the repo.
- No jokes, sloppy comments, or private notes that would look wrong in public.
- Docs and code stay generic. They describe contracts, not David's machine.
- New work is written for strangers who will copy, audit, and judge it.

## Consequences

- Review diffs for anything a stranger should not see before it lands on `main`.
- Existing personal leftovers must be removed or rewritten, not left as "internal".
- Privacy rules in `AGENTS.md` are now also a public-trust rule.
