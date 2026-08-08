# Metric definitions

## 1. Unique local commits

- Scan Git repositories recursively under `~/code`.
- Deduplicate primary checkouts and linked worktrees by their shared Git common directory.
- Read creation events from all reflogs within the requested date range.
- Include commits authored or committed with an email configured in that repository.
- Count each commit hash once, even if several worktrees or refs contain it.
- Do not count pushes, fetched upstream commits, or branch movements by themselves.

## 2. Active agent sessions

- Count a session on every Warsaw calendar day where its native registry records activity.
- Include GUI, CLI, headless, resumed, parent, subagent, delegated, and automated sessions.
- Deduplicate repeated records using the native session identity within each harness.
- Keep harnesses separate in storage and charts.

## 3. Instruction-bearing prompts

- Count stored inputs with user, system, or developer roles when they contain an instruction.
- Include human prompts, automation, system setup, parent-agent delegation, and subagent instructions.
- Exclude assistant responses, tool results, empty messages, and duplicate storage copies.
- Never store prompt text in the aggregate database.

## Time window

- Daily boundaries use `Europe/Warsaw`.
- The scheduled report covers the 90 days ending yesterday.
- Manual collection supports 1 to 366 days.

## Charts

- Commits use a daily line and area chart.
- Sessions and prompts use stacked daily bars. Bar height is the total; colored segments are harness contributions.
- The gray dashed line is an ordinary least-squares straight trend across the full displayed window.
- Session and prompt trendlines use the combined daily total across all harnesses.
- Charts are 2048 × 1080 PNGs with a dark navy background, a top legend, and sparse date labels.
