# 0005 — Pruned home scan discovers Git roots; rescanned daily

Date: 2026-08-22. Refines [0004](0004-auto-detect-code-root.md) with the
concrete mechanism and four decisions.

## Context

ADR 0004 decided to auto-detect the code root but left the mechanism open. A
new user with repos outside `~/code` saw a zero-commit chart. Spotlight/mdfind
cannot help: macOS does not index hidden directories like `.git`.

## Decision

Walk the home directory once, pruning `Library`, `node_modules`, `.Trash`,
caches, build dirs, and hidden directories, collecting every `.git` found.

1. **Store parent roots, not repo lists.** The top-level folders that contain
   repos (e.g. `~/Projects`, `~/dev`) become the scan roots; new repos inside
   them are picked up automatically by the existing per-root discovery.
2. **Rescan daily** as part of the morning report run. The pruned scan takes
   seconds, so detected locations can never go stale.
3. **Installer shows and proceeds.** It prints what it found ("Found 23 repos
   in ~/Projects, ~/dev") without asking for confirmation.
   `CORRAL_PRODUCTIVITY_CODE_ROOT` remains the explicit override.
4. **Scope: home only, skip hidden dirs and external volumes.** Dotfiles repos
   and second drives are edge cases covered by the override.

## Consequences

- Commit counting works regardless of where a user keeps code; the `~/code`
  convention is no longer a silent requirement.
- Detected roots are stored locally only (paths never leave the Mac, per the
  privacy constraints).
- A repo on an external volume or inside a hidden directory is not detected;
  that limitation is documented rather than scanned around.
