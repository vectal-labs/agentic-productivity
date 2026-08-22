# 0002 — Render charts with quickchart.io

## Context

The three 90-day charts need server-side rendering into PNGs. Local rendering would add a real dependency and contradict the prototype scope (ADR 0001).

## Decision

Use quickchart.io to render the Chart.js configs. It is simple, fast, and works with zero dependencies.

Only aggregate numbers are sent: dates, daily counts, and harness labels. No prompt text, names, paths, addresses, identities, session IDs, or repository names ever leave the Mac.

## Consequences

- Chart delivery depends on quickchart.io availability at report time.
- Aggregate counts are visible to QuickChart; this is disclosed in the README.
