# Metric definitions

## 1. Unique local commits

- Scan the top-level Git roots detected under the user's home folder.
- Re-detect those roots for each daily report so new locations are included.
- Skip hidden folders, `Library`, `.Trash`, dependency folders, caches, and build output. `CORRAL_PRODUCTIVITY_CODE_ROOT` bypasses detection when explicitly set.
- Deduplicate primary checkouts and linked worktrees by their shared Git common directory.
- Read creation events from all reflogs within the requested date range.
- Include every commit whose creation (commit, merge, cherry-pick, or rebase) is recorded in a local reflog. Reflogs only record actions performed on this machine, so no email or identity matching is needed and identity changes never affect history.
- Count each commit hash once, even if several worktrees or refs contain it.
- Do not count pushes, fetched upstream commits, or branch movements by themselves.

## 2. Active agent sessions

- Count a session on every local Mac calendar day where its native registry records activity.
- Do not count a session file that has no real turn. A real turn is an instruction-bearing message or a `session_init` entry. Empty drafts do not count.
- Include GUI, CLI, headless, resumed, parent, subagent, delegated, and automated sessions.
- Each native child transcript is its own session, including OMP task files and advisor files. Do not fold them into the parent.
- Deduplicate repeated records using the native session identity within each harness.
- Keep harnesses separate in storage and charts.

## 3. Instruction-bearing prompts

- Count stored inputs with user, system, or developer roles when they contain an instruction.
- Include human prompts, automation, system setup, parent-agent delegation, and subagent instructions.
- Exclude assistant responses, tool results, empty messages, and duplicate storage copies.
- Deduplicate copied prompts within each harness by native entry identity plus timestamp, across files. A fork or export that reprints the same entry counts once. New turns in the child file still count.
- Never store prompt text in the aggregate database.

## Time window

- Daily boundaries use the Mac's automatically detected local timezone.
- The scheduled report covers the 90 days ending yesterday.
- Manual collection supports 1 to 366 days.

## Charts

- Commits use a daily line and area chart.
- Sessions and prompts use stacked daily bars. Bar height is the total; colored segments are harness contributions.
- The gray dashed line is an ordinary least-squares straight trend across the full displayed window.
- Session and prompt trendlines use the combined daily total across all harnesses.
- Charts are 2048 × 1080 PNGs with a dark navy background, a top legend, and sparse date labels.
