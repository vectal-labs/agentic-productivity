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
- Deduplicate with a private HMAC fingerprint of the harness plus native session identity. Genuine child sessions stay separate.
- After both the Mac and cloud collectors initialize, daily totals are the set union of fingerprints from both machines. Earlier days stay Mac-only.

## 3. Instruction-bearing prompts

- Count stored inputs with user, system, or developer roles when they contain an instruction.
- Include human prompts, automation, system setup, parent-agent delegation, and subagent instructions.
- Exclude assistant responses, tool results, empty messages, and duplicate storage copies.
- Fingerprint prompts with the harness, original native message identity, and normalized native timestamp. Copied fork history keeps those original identities and counts once. New child turns still count.
- Records without message IDs use the original session identity, native timestamp, role, and canonical instruction content inside the HMAC. If that provenance is missing, exclude the event and mark coverage partial.
- Counter-only stores keep the existing baseline rules. Deduplicate cumulative prompt ordinals within each native session. Never add replicated counters together.
- Never store prompt text in the aggregate database. Fingerprints never enter Discord or chart-rendering requests.

## 4. Open-thread placement

- Every five minutes, query official BB's machine/thread lists and read Cloudroom's `~/.gui-cloudroom/bb.db` in read-only mode. No AI agent runs the scan.
- Count visible, unarchived, undeleted threads across all projects, including idle and pending threads. This is not completed tasks, execution time, or daily unique sessions. Short-lived threads can fall between scans.
- **Local** matches each app's own `host-id`. **Cloud** includes Cloudroom's explicit `execution_target='cloud'` and other registered BB hosts, even disconnected ones. Another physical computer also counts as cloud in the chart. Never infer placement from names or model providers.
- Cloudroom's explicit target takes precedence over its absent BB environment. Missing or removed hosts on native threads are unknown; exclude them from the percentages and report their share separately.
- Store only aggregate counts and coverage, once per source per five-minute interval. Retries replace the same interval. Never retain thread/host IDs, names, prompts, or paths. Overlapping BB/Cloudroom profiles fail rather than count twice.
- Daily remote percentage is `100 × sum(remote counts) / sum(local + remote counts)` across paired, available observations on the Mac's local day. Local is complementary. Never mix successful readings from different intervals to fill a failed pair.
- An absent, never-measured app is not required. Once observed, a missing profile is unavailable, not zero. An unreadable or unsupported expected source excludes the pair from percentages. Empty fleets and unknown-only days have no percentage.
- Keep historical BB observations unchanged and label them **BB-only**. On the rollout day, use only scans from the new paired collector. No backfill from today's thread state.
- Report readable/absent counts per source and sampled slots out of the full local day, including daylight-saving changes. Sleeping Macs and failed scans leave gaps; there is no extrapolation.
- Keep placement separate from native session totals to avoid counting app wrappers twice.

## Time window

- Daily boundaries use the Mac's automatically detected local timezone.
- The scheduled report covers the 90 days ending yesterday.
- Manual collection supports 1 to 366 days.

## Charts

- Commits use a daily line and area chart.
- Sessions and prompts use stacked daily bars. Bar height is the total; colored segments are harness contributions.
- The original three charts have a gray dashed least-squares trend across the displayed window.
- Session and prompt trendlines use the combined daily total across all harnesses.
- Open-thread placement uses Local and Cloud lines on a fixed 0–100% axis, with no subtitle. Monotone interpolation prevents overshoot; missing days break the lines. Source history and coverage details stay in the local summary.
- Only the placement chart adapts its window: up to 14 unique measured dates → last 14 days; 15–30 → last 30 days; more than 30 → last 90 days. Count dates with a known local/cloud percentage within the requested report history, including 0% and 100%. Failed, empty, and unknown-only days do not qualify. A shorter explicit report window remains the upper limit.
- Charts are 2048 × 1080 PNGs with a dark navy background, a top legend, and sparse date labels.
