# 0007 — Keep shipping simple and fast

Date: 2026-08-22

## Context

This is a small internal tool. It has no production database, public service,
or high-risk deployment process. A normal Git push should not use heavyweight
production safeguards.

## Decision

After the user explicitly asks to push, stage the requested files, write a
clear commit, sync only if needed, and push directly to `main`. Do not use
shipping locks, deployment monitoring, or unrelated release workflows. Run
only quick, relevant validation that has not already passed.

## Consequences

- Routine pushes stay simple and take seconds.
- Normal Git conflicts are handled only when they actually occur.
- Implementation privacy and correctness rules still apply, but shipping does
  not add extra process without a real risk that requires it.
