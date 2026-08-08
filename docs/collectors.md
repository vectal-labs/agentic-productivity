# Collector registry

Collectors read native local stores whenever possible. Paths are relative to the current user's home directory.

| Harness | Native source | Current contract |
|---|---|---|
| Git | `~/code/**/.git` and shared reflogs | Full when repositories and local Git identities are readable |
| Codex | `~/.codex/sessions`, `~/.codex/archived_sessions` | Sessions and instruction-role messages |
| Claude Code | `~/.claude/projects` | Parent and subagent sessions, excluding tool results |
| Cursor GUI | `~/Library/Application Support/Cursor/User/globalStorage/state.vscdb` | Composer sessions and user message headers |
| Cursor CLI | `~/.cursor/chats/**/store.db` | Sessions plus exact prompt deltas after the local baseline |
| Kilo Code | Cursor/VS Code `globalStorage/kilocode.kilo-code/tasks` | Initial tasks and user feedback |
| Roo Code | Cursor/VS Code `globalStorage/rooveterinaryinc.roo-cline/tasks` | Initial tasks and user feedback |
| Cline | Cursor/VS Code `globalStorage/saoudrizwan.claude-dev/tasks` | Initial tasks and user feedback |
| GitHub Copilot | VS Code `workspaceStorage/**/chatSessions` and global chat stores | Native chat requests |
| Antigravity | `~/Library/Application Support/Antigravity/User/globalStorage/state.vscdb` | Session timestamps only; prompt history is unavailable |
| Hermes | `~/.hermes/state.db` | Sessions and instruction-role messages |
| Pi Agent | `~/.pi/agent/sessions` | Sessions and instruction-role messages |
| Prime Agent | `~/.prime/agent/sessions` | Sessions and instruction-role messages |
| OpenCode | `~/.local/share/opencode/storage/message` | Sessions and instruction-role messages |
| Factory Droid | `~/.factory/sessions` | Sessions and instructions, excluding duplicated context rows |
| Gemini CLI | `~/.gemini/tmp/**/session-*.json` | Sessions and instruction-role messages |
| Qwen Code | `~/.qwen/tmp/**/session-*.json` | Sessions and instruction-role messages |
| Amp | Authenticated `amp threads list/export` | Sessions and instruction-role messages |

## Known limitations

- Cursor CLI's native store has session-level timestamps but no historical per-turn timestamps. The five-minute observer creates exact daily prompt deltas after its first baseline. Earlier multi-day attribution remains partial.
- Antigravity exposes trajectory/session timestamps but not prompt history, so its prompt coverage is partial by design.
- A collector with unreadable data reports `partial`, `unavailable`, or `error`; it must not silently claim full coverage.
