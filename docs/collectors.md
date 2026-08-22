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
| Pi Agent | `~/.pi/agent/sessions` | Sessions and instruction-role messages |
| Oh My Pi | `~/.omp/agent/sessions` | Sessions, instruction-role messages, and `session_init` tasks. Child transcripts are separate sessions |
| Prime Agent | `~/.prime/agent/sessions` | Sessions and instruction-role messages |
| Kimi Code | `~/.kimi-code/sessions/**/wire.jsonl` (`KIMI_CODE_HOME` override), legacy `~/.kimi` | Sessions including sub-agents, plus user-turn prompts. Injection and system-origin rows are excluded |
| Grok Build | `~/.grok/sessions/**/updates.jsonl` with `chat_history.jsonl` as fallback (`GROK_HOME` override) | Sessions and user-turn messages. The community `grok-cli` SQLite database is never read |
| OpenCode | `~/.local/share/opencode/opencode.db`, with `storage/message` as a legacy fallback | Sessions and instruction-role messages |
| Factory Droid | `~/.factory/sessions` | Sessions and instructions, excluding duplicated context rows |
| Gemini CLI | `~/.gemini/tmp/**/session-*.json` | Sessions and instruction-role messages |
| Qwen Code | `~/.qwen/tmp/**/session-*.json` | Sessions and instruction-role messages |
| Amp | Authenticated `amp threads list/export` | Sessions and instruction-role messages. The CLI is not started unless `~/.local/share/amp/session.json` or `secrets.json` already exists |

## Known limitations

- Cursor CLI's native store has session-level timestamps but no historical per-turn timestamps. The five-minute observer creates exact daily prompt deltas after its first baseline. Earlier multi-day attribution remains partial. ACP sessions from `cursor-agent acp` use the same `store.db` format under `~/.cursor/acp-sessions` and are counted in this harness.
- Antigravity exposes trajectory/session timestamps but not prompt history, so its prompt coverage is partial by design.
- A collector with unreadable data reports `partial`, `unavailable`, or `error`; it must not silently claim full coverage.
- Amp's CLI can open a browser login page. The collector refuses to start it without a local login file, and sets `BROWSER` to a no-op.
- Empty session files with no real turn (title slot or header only) are not counted.
- Copied prompts, including OMP and Pi forks, count once per native entry id and timestamp.
- Git roots are stored only in the local aggregate database. Reports contain repository and root counts, never paths or repository names.
- Commits are attributed by local creation reflog entries, not by email or identity. Every commit created on this machine counts, regardless of the configured Git identity at the time.
