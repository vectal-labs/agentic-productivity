# Metric definitions

## 1. Unique local commits

- Scan the top-level Git roots detected under the user's home folder.
- Re-detect those roots for each daily report so new locations are included.
- Skip hidden folders, `Library`, `.Trash`, dependency folders, caches, and build output. `CORRAL_PRODUCTIVITY_CODE_ROOT` bypasses detection when explicitly set.
- Deduplicate primary checkouts and linked worktrees by their shared Git common directory.
- Read creation events from all reflogs within the requested date range.
- Include every commit whose creation (commit, merge, cherry-pick, or rebase) is recorded in a local reflog. Reflogs only record actions performed on this machine, so no email or identity matching is needed and identity changes never affect history.
- Do not count fast-forward merges: they move a ref onto a commit created elsewhere. Only merges that create a merge commit locally count.
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

## 4. BB thread placement

- Scan `bb machine list --json` and `bb thread list --include-hidden --json` every five minutes. No AI agent runs the scan.
- Count visible, unarchived, undeleted threads across all projects, including idle threads. Count each thread once.
- Read the Mac's identity from `~/.bb/host-id` (`BB_DATA_DIR` is supported). Match thread environment host IDs; do not infer placement from names or providers.
- **MacBook** is that local host. **Cloud** means another registered BB host, including temporarily disconnected hosts. This is a local/remote placement metric; BB does not distinguish cloud VMs from another physical remote computer.
- Missing or removed hosts are unknown. Exclude unknown placement from the two percentages and report its share separately.
- Store aggregate counts and coverage, with one replaceable observation per five-minute interval. No thread or host IDs, names, prompts, or paths are retained.
- Each daily cloud percentage is `100 × sum(cloud counts) / sum(local + cloud counts)` across available snapshots on that local day. Local is the complementary share. This measures open-thread placement, not execution time or daily unique sessions.
- No BB, an unreadable identity, invalid data, or a failed request produces a coverage state. Failed scans, empty fleets, and days without measurements never become 0% cloud.
- History starts with the first scan. Do not backfill from current placement. Sleeping Macs and unreachable BB leave coverage gaps; there is no overnight extrapolation.
- Keep BB placement separate from native session totals to avoid counting their BB wrappers twice.

## Time window

- Daily boundaries use the Mac's automatically detected local timezone.
- The scheduled report covers the 90 days ending yesterday.
- Manual collection supports 1 to 366 days.

## Charts

- Commits use a daily line and area chart.
- Sessions and prompts use stacked daily bars. Bar height is the total; colored segments are harness contributions.
- The original three charts have a gray dashed least-squares trend across the displayed window.
- Session and prompt trendlines use the combined daily total across all harnesses.
- BB placement uses two smooth lines, MacBook and Cloud, on a fixed 0–100% axis. Monotone interpolation prevents overshoot; missing days break the lines.
- Charts are 2048 × 1080 PNGs with a dark navy background, a top legend, and sparse date labels.
