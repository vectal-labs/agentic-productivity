# 0004 — Auto-detect the code root

## Context

Commit counting scanned only `~/code`. Users keep repositories in different places, so a hardcoded personal path silently reports zero commits for them.

## Decision

- The interactive installer auto-detects where the user's Git repositories actually live (the location with the most repos) and uses that as the code root.
- If detection is inconclusive, the installer asks for the folder as an explicit setup step.
- No hardcoded personal default remains.

## Consequences

- Commit counts work out of the box for most users without a prompt.
- Detection heuristics need a sane depth limit to stay fast.
