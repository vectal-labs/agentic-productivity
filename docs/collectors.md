# Collector registry

Collectors read native local stores whenever possible. Paths are relative to the current user's home directory.

| Harness | Native source | Current contract |
|---|---|---|
| Git | Auto-detected top-level roots under the home folder, then shared reflogs | Full when repositories and reflogs are readable |
| Codex | `~/.codex/sessions`, `~/.codex/archived_sessions` | Sessions and instruction-role messages |
| Claude Code | `~/.claude/projects` | Parent and subagent sessions, excluding tool results |
| Cursor GUI | `~/Library/Application Support/Cursor/User/globalStorage/state.vscdb` | Composer sessions and user message headers |
| Cursor CLI | `~/.cursor/chats/**/store.db` and `~/.cursor/acp-sessions/**/store.db` | Sessions plus exact prompt deltas after the local baseline |
| Kilo Code | Cursor/VS Code `globalStorage/kilocode.kilo-code/tasks` | Initial tasks and user feedback |
| Roo Code | Cursor/VS Code `globalStorage/rooveterinaryinc.roo-cline/tasks` | Initial tasks and user feedback |
| Cline | Cursor/VS Code `globalStorage/saoudrizwan.claude-dev/tasks` | Initial tasks and user feedback |
| GitHub Copilot | VS Code `workspaceStorage/**/chatSessions` and global chat stores | Native chat requests |
| Antigravity | `~/Library/Application Support/Antigravity/User/globalStorage/state.vscdb` | Session timestamps only; prompt history is unavailable |
| Hermes | `~/.hermes/state.db` | Sessions and instruction-role messages |
| Pi Agent | `~/.pi/agent/sessions` and `~/.bb/pi-bridge-sessions` (`BB_DATA_DIR` overrides `~/.bb`) | Native Pi transcripts from both locations; model providers such as `openai-codex` remain Pi Agent |
| Oh My Pi | `~/.omp/agent/sessions` | Sessions, instruction-role messages, and `session_init` tasks. Child transcripts are separate sessions |
| Prime Agent | `~/.prime/agent/sessions` | Sessions and instruction-role messages |
| Kimi Code | `~/.kimi-code/sessions/**/wire.jsonl` (`KIMI_CODE_HOME` override), legacy `~/.kimi` | Sessions including sub-agents, plus user-turn prompts. Injection and system-origin rows are excluded |
| Grok Build | `~/.grok/sessions/**/updates.jsonl` with `chat_history.jsonl` as fallback (`GROK_HOME` override) | Sessions and user-turn messages. The community `grok-cli` SQLite database is never read |
| OpenCode | `~/.local/share/opencode/opencode.db`, with `storage/message` as a legacy fallback | Sessions and instruction-role messages |
| Factory Droid | `~/.factory/sessions` | Sessions and instructions, excluding duplicated context rows |
| Gemini CLI | `~/.gemini/tmp/**/session-*.json` | Sessions and instruction-role messages |
| Qwen Code | `~/.qwen/tmp/**/session-*.json` | Sessions and instruction-role messages |
| Amp | Authenticated `amp threads list/export` | Sessions and instruction-role messages. The CLI is not started unless `~/.local/share/amp/session.json` or `secrets.json` already exists |

## Cloud native collector

The Mac LaunchAgent still runs every five minutes. The main Linux VM runs the same collectors from a copied runtime under `~/.local/share/corral/agentic-productivity`, on a systemd user timer at the same interval. Cloud collection continues without the Mac or BB.

Both machines use the Mac timezone. Each collector writes opaque HMAC fingerprints into local SQLite. The Mac pulls cloud snapshot files over verified SSH and unions fingerprints. Replaying a snapshot does not change counts. BB wrappers stay out of native session and prompt totals.

Combined reporting starts at the next Mac-local midnight after both collectors initialize. Missing, stale, unsupported, and partial coverage is retained in local state and fallback summaries. Discord sends only chart images. Missing cloud data is never a confirmed zero.

## Human-message cloud adoption

`agentic_productivity/messages.py` reads message-origin metadata from `~/.bb/bb.db` (`BB_DATA_DIR` supported) and `~/.gui-cloudroom/bb.db`, using each profile's `host-id`. Both connections use `mode=ro`, live WAL support, and a two-second lock timeout. SQL checks visible content without returning it to Python.

Only human `client/turn/requested` events count. Request fingerprints prevent retries, replays, fork copies, or repeated scans from increasing counts. Event environments and Cloudroom's explicit cloud target determine placement. Retained archived/deleted-thread history counts; missing sources and unknown locations remain explicit coverage states.

Run `./bin/agentic-productivity scan-messages --json` to backfill the last 90 days through today without rendering or sending. `--date YYYY-MM-DD --days N` selects another window. Scheduled native collection refreshes this metric automatically on the Mac. See [metric definitions](metrics.md#4-human-message-cloud-adoption).

## Legacy open-thread placement

- `agentic_productivity/bb.py` reads official BB's machine/thread lists and `~/.bb/host-id` (`BB_DATA_DIR` supported). The CLI is discovered on PATH or in standard BB app locations; Node lookup includes Homebrew. Commands have a 20-second timeout. Raw output/errors are never persisted.
- `agentic_productivity/cloudroom.py` reads only placement metadata from `~/.gui-cloudroom/bb.db` and its own `host-id`. SQLite uses `mode=ro` with live WAL support and a two-second lock timeout. The GUI can be closed. No credential, prompt, or transcript reads are needed. Other Cloudroom profiles are not discovered automatically.
- Both sources return aggregate local/remote/unknown counts and coverage. A failed expected source excludes that paired snapshot; its surviving source is never presented as a complete percentage. Previously measured profiles remain expected if they disappear.

These legacy samples stay separate from native session totals and no longer feed the fourth chart. See [metric definitions](metrics.md#legacy-open-thread-placement). Run `./bin/agentic-productivity scan-placement --json` for one scan without reporting or delivery. `scan-bb` remains an alias; JSON's existing `cloud` fields now mean remote, and `sources` shows individual coverage.

## Known limitations

- Cursor CLI's native store has session-level timestamps but no historical per-turn timestamps. The five-minute observer creates exact daily prompt deltas after its first baseline. Earlier multi-day attribution remains partial. ACP sessions from `cursor-agent acp` use the same `store.db` format under `~/.cursor/acp-sessions` and are counted in this harness.
- Antigravity exposes trajectory/session timestamps but not prompt history, so its prompt coverage is partial by design.
- A collector with unreadable data reports `partial`, `unavailable`, or `error`; it must not silently claim full coverage.
- Amp's CLI can open a browser login page. The collector refuses to start it without a local login file, and sets `BROWSER` to a no-op.
- Empty session files with no real turn (title slot or header only) are not counted.
- Copied prompts, including OMP and Pi forks, count once per native entry id and timestamp across all scanned roots, and once across machines after fingerprint union.
- Pi-family collectors retain readable records but mark coverage partial for inaccessible directories, unreadable files, malformed JSON, and instruction records without valid timestamps. Coverage describes the configured roots, not arbitrary custom session directories.
- Git roots are stored only in the local aggregate database. Reports contain repository and root counts, never paths or repository names.
- Commits are attributed by local creation reflog entries, not by email or identity. Every commit created on this machine counts, regardless of the configured Git identity at the time. Cloud Git commits are not merged into this series.
