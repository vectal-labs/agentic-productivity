# 0006 — Add tests only when they truly earn their place

Date: 2026-08-22

## Context

Multiple agents work in this repo, and each change tends to arrive with new
tests. Left unchecked, that grows an ever-larger suite of unit tests and
overlapping test types that slows every future change without making the
system meaningfully safer. The suite has already grown from 23 to 31+ tests in
a single day.

## Decision

Add a new test only when it truly makes sense: a new behavior, a real bug
being fixed, or a contract that would otherwise break silently. Do not blow up
the repo with unit tests for every internal function or duplicate coverage of
behavior an existing test already exercises. Behavior-focused tests remain the
standard (see AGENTS.md); prefer extending an existing test over adding a
near-copy of it.

## Consequences

- The suite stays fast and readable; every test in it means something.
- Reviewers should push back on tests that assert implementation details or
  restate existing coverage.
- Some trivial code stays untested on purpose. That is accepted.
