# 0008 — Front-load setup friction into installation

Date: 2026-08-23

## Context

The background job can first touch protected files at 08:00 after installation.
macOS may then show permission prompts or reveal missing access after the user
believes setup is finished.

## Decision

Front-load all setup work and friction that can be handled during installation.
The installer starts the real LaunchAgent, makes it exercise every collector and
permission-gated path it currently needs, and waits for completion. macOS prompts
and access errors must appear before the installer says it is done.

## Consequences

- Installation may take longer and require several approvals.
- The first scheduled report should not contain deferred setup work.
- New collectors or protected resources must join the install-time access check.
