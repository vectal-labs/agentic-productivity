from __future__ import annotations

import base64
import json
import hashlib
import os
import re
import shutil
import sqlite3
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any
from urllib.parse import quote
from .database import Database
from .model import Collection, CommitResult, Coverage, HarnessResult


INSTRUCTION_ROLES = {"user", "system", "developer"}


@dataclass(frozen=True)
class CollectorContext:
    home: Path
    code_roots: tuple[Path, ...]
    start: date
    end: date
    database: Database
    now: datetime
    timezone: tzinfo

    @property
    def start_timestamp(self) -> float:
        return datetime.combine(self.start, time.min, self.timezone).timestamp()

    def includes(self, day: date | None) -> bool:
        return day is not None and self.start <= day <= self.end


def _instant(value: Any, zone: tzinfo) -> datetime | None:
    if value is None or isinstance(value, bool):
        return None
    parsed: datetime
    try:
        if isinstance(value, (int, float)):
            number = float(value)
            if number > 10_000_000_000:
                number /= 1000
            parsed = datetime.fromtimestamp(number, timezone.utc)
        elif isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            if re.fullmatch(r"\d+(?:\.\d+)?", text):
                return _instant(float(text), zone)
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            return None
    except (OverflowError, OSError, ValueError):
        return None
    return parsed.astimezone(zone)


def _day(value: Any, zone: tzinfo) -> date | None:
    parsed = _instant(value, zone)
    return parsed.date() if parsed is not None else None


def _command_installed(name: str) -> bool:
    return shutil.which(name) is not None


def _recent_files(roots: Iterable[Path], pattern: str, start_timestamp: float) -> list[Path]:
    paths: dict[str, Path] = {}
    threshold = start_timestamp - 2 * 86400
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob(pattern):
            try:
                if path.is_file() and path.stat().st_mtime >= threshold:
                    paths[str(path.resolve())] = path
            except OSError:
                continue
    return sorted(paths.values())


def _json_lines(path: Path) -> Iterable[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    yield value
    except OSError:
        return


def _content_has_instruction(content: Any) -> bool:
    if isinstance(content, str):
        return bool(content.strip())
    if not isinstance(content, list):
        return False
    for item in content:
        if isinstance(item, str) and item.strip():
            return True
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "tool_result":
            continue
        if item_type in {"text", "input_text"}:
            if str(item.get("text", "")).strip():
                return True
        elif item_type is not None:
            return True
    return False


class _PromptCopies:
    """Drop reprinted prompts. Same native id and timestamp counts once."""

    def __init__(self) -> None:
        self._seen: set[tuple[str, str]] = set()

    def take(self, entry_id: Any, timestamp: Any, fallback: Any = None) -> bool:
        ident = "" if entry_id is None else str(entry_id).strip()
        stamp = "" if timestamp is None else str(timestamp)
        if not ident and fallback is not None:
            ident = hashlib.sha256(
                json.dumps(
                    fallback, sort_keys=True, separators=(",", ":"), default=str
                ).encode()
            ).hexdigest()
        if not ident and not stamp:
            return True
        key = (ident, stamp)
        if key in self._seen:
            return False
        self._seen.add(key)
        return True


def collect_codex(context: CollectorContext) -> HarnessResult:
    harness = "Codex"
    roots = [context.home / ".codex/sessions", context.home / ".codex/archived_sessions"]
    installed = _command_installed("codex") or any(root.exists() for root in roots)
    result = HarnessResult(harness)
    if not installed:
        result.coverage = Coverage(harness, False, "absent")
        return result
    files = _recent_files(roots, "*.jsonl", context.start_timestamp)
    copies = _PromptCopies()
    for path in files:
        session_id = path.stem
        activity: set[date] = set()
        prompts: list[date] = []
        real_turn = False
        for row in _json_lines(path):
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            if row.get("type") == "session_meta":
                session_id = str(payload.get("id") or payload.get("session_id") or session_id)
                continue
            day = _day(row.get("timestamp") or payload.get("timestamp"), context.timezone)
            is_instruction = (
                row.get("type") == "response_item"
                and payload.get("type") == "message"
                and payload.get("role") in INSTRUCTION_ROLES
            )
            if is_instruction:
                real_turn = True
            if context.includes(day):
                activity.add(day)
                if is_instruction and copies.take(
                    payload.get("id"),
                    row.get("timestamp") or payload.get("timestamp"),
                    row,
                ):
                    prompts.append(day)
        if not real_turn:
            continue
        for day in activity:
            result.add_session(day, session_id)
        for day in prompts:
            result.add_prompt(day)
    result.coverage = Coverage(harness, True, "full", f"{len(files)} recent session files")
    return result


def collect_claude(context: CollectorContext) -> HarnessResult:
    harness = "Claude Code"
    root = context.home / ".claude/projects"
    installed = _command_installed("claude") or root.exists()
    result = HarnessResult(harness)
    if not installed:
        result.coverage = Coverage(harness, False, "absent")
        return result
    files = _recent_files([root], "*.jsonl", context.start_timestamp)
    copies = _PromptCopies()
    for path in files:
        session_id = path.stem
        agent_id = ""
        activity: set[date] = set()
        prompts: list[date] = []
        real_turn = False
        for row in _json_lines(path):
            session_id = str(row.get("sessionId") or session_id)
            agent_id = str(row.get("agentId") or agent_id)
            day = _day(row.get("timestamp"), context.timezone)
            message = row.get("message") if isinstance(row.get("message"), dict) else {}
            is_instruction = (
                row.get("type") == "user"
                and message.get("role") == "user"
                and _content_has_instruction(message.get("content"))
            )
            if is_instruction:
                real_turn = True
            if context.includes(day):
                activity.add(day)
                if is_instruction and copies.take(
                    row.get("uuid") or row.get("id"), row.get("timestamp"), row
                ):
                    prompts.append(day)
        if not real_turn:
            continue
        identity = f"{session_id}:{agent_id}" if "/subagents/" in str(path) else session_id
        for day in activity:
            result.add_session(day, identity)
        for day in prompts:
            result.add_prompt(day)
    result.coverage = Coverage(harness, True, "full", f"{len(files)} recent session files")
    return result


def _collect_pi_family(
    context: CollectorContext, *, harness: str, root: Path, command: str
) -> HarnessResult:
    installed = _command_installed(command) or root.exists()
    result = HarnessResult(harness)
    if not installed:
        result.coverage = Coverage(harness, False, "absent")
        return result
    files = _recent_files([root], "*.jsonl", context.start_timestamp)
    copies = _PromptCopies()
    for path in files:
        session_id = path.stem
        activity: set[date] = set()
        prompts: list[date] = []
        real_turn = False
        for row in _json_lines(path):
            row_type = row.get("type")
            if row_type == "session":
                session_id = str(row.get("id") or session_id)
                continue
            if row_type == "title":
                continue
            day = _day(row.get("timestamp"), context.timezone)
            message = row.get("message") if isinstance(row.get("message"), dict) else {}
            is_init = row_type == "session_init" and _content_has_instruction(
                row.get("task")
            )
            is_instruction = (
                row_type == "message"
                and message.get("role") in INSTRUCTION_ROLES
                and _content_has_instruction(message.get("content"))
            )
            if is_init or is_instruction:
                real_turn = True
            if context.includes(day):
                activity.add(day)
                if (is_init or is_instruction) and copies.take(
                    row.get("id"), row.get("timestamp"), row
                ):
                    prompts.append(day)
        if not real_turn:
            continue
        for day in activity:
            result.add_session(day, session_id)
        for day in prompts:
            result.add_prompt(day)
    result.coverage = Coverage(harness, True, "full", f"{len(files)} recent session files")
    return result


def collect_droid(context: CollectorContext) -> HarnessResult:
    harness = "Factory Droid"
    root = context.home / ".factory/sessions"
    installed = _command_installed("droid") or root.exists()
    result = HarnessResult(harness)
    if not installed:
        result.coverage = Coverage(harness, False, "absent")
        return result
    files = _recent_files([root], "*.jsonl", context.start_timestamp)
    copies = _PromptCopies()
    for path in files:
        session_id = path.stem
        activity: set[date] = set()
        prompts: list[date] = []
        real_turn = False
        for row in _json_lines(path):
            if row.get("type") == "session_start":
                session_id = str(row.get("id") or session_id)
                continue
            day = _day(row.get("timestamp"), context.timezone)
            message = row.get("message") if isinstance(row.get("message"), dict) else {}
            is_instruction = (
                row.get("type") == "message"
                and message.get("role") in INSTRUCTION_ROLES
                and not str(row.get("id", "")).startswith("context-")
                and _content_has_instruction(message.get("content"))
            )
            if is_instruction:
                real_turn = True
            if context.includes(day):
                activity.add(day)
                if is_instruction and copies.take(
                    row.get("id"), row.get("timestamp"), row
                ):
                    prompts.append(day)
        if not real_turn:
            continue
        for day in activity:
            result.add_session(day, session_id)
        for day in prompts:
            result.add_prompt(day)
    result.coverage = Coverage(harness, True, "full", f"{len(files)} recent session files")
    return result


def _collect_gemini_family(
    context: CollectorContext, *, harness: str, root: Path, command: str
) -> HarnessResult:
    installed = _command_installed(command) or root.exists()
    result = HarnessResult(harness)
    if not installed:
        result.coverage = Coverage(harness, False, "absent")
        return result
    files = _recent_files([root], "session-*.json", context.start_timestamp)
    copies = _PromptCopies()
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        session_id = str(data.get("sessionId") or path.stem)
        activity: set[date] = set()
        prompts: list[date] = []
        real_turn = False
        for message in data.get("messages", []):
            if not isinstance(message, dict):
                continue
            day = _day(message.get("timestamp"), context.timezone)
            is_instruction = message.get("type") in INSTRUCTION_ROLES and _content_has_instruction(
                message.get("content")
            )
            if is_instruction:
                real_turn = True
            if context.includes(day):
                activity.add(day)
                if is_instruction and copies.take(
                    message.get("id"), message.get("timestamp"), message
                ):
                    prompts.append(day)
        if not real_turn:
            continue
        for day in activity:
            result.add_session(day, session_id)
        for day in prompts:
            result.add_prompt(day)
    result.coverage = Coverage(harness, True, "full", f"{len(files)} recent session files")
    return result


def collect_gemini(context: CollectorContext) -> HarnessResult:
    return _collect_gemini_family(
        context,
        harness="Gemini CLI",
        root=context.home / ".gemini/tmp",
        command="gemini",
    )


def collect_qwen(context: CollectorContext) -> HarnessResult:
    return _collect_gemini_family(
        context,
        harness="Qwen Code",
        root=context.home / ".qwen/tmp",
        command="qwen",
    )


def _sqlite_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _collect_opencode_legacy_json(
    context: CollectorContext, result: HarnessResult, root: Path
) -> HarnessResult:
    files = _recent_files([root], "*.json", context.start_timestamp)
    copies = _PromptCopies()
    for path in files:
        try:
            message = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(message, dict):
            continue
        timing = message.get("time") if isinstance(message.get("time"), dict) else {}
        stamp = timing.get("created") or timing.get("completed")
        day = _day(stamp, context.timezone)
        if not context.includes(day):
            continue
        session_id = str(message.get("sessionID") or path.parent.name)
        result.add_session(day, session_id)
        if message.get("role") in INSTRUCTION_ROLES and copies.take(
            message.get("id") or path.stem, stamp, message
        ):
            result.add_prompt(day)
    result.coverage = Coverage(result.harness, True, "full", f"{len(files)} recent message records")
    return result


def _collect_opencode_sqlite(
    context: CollectorContext, result: HarnessResult, path: Path
) -> HarnessResult:
    try:
        with _readonly_sqlite(path) as connection:
            session_cols = _sqlite_columns(connection, "session")
            message_cols = _sqlite_columns(connection, "message")
            if not {"id", "time_created", "time_updated"} <= session_cols:
                result.coverage = Coverage(
                    result.harness, True, "error", "native session schema is unsupported"
                )
                return result
            if not {"session_id", "time_created", "data"} <= message_cols:
                result.coverage = Coverage(
                    result.harness, True, "error", "native message schema is unsupported"
                )
                return result
            copies = _PromptCopies()
            message_has_id = "id" in message_cols
            query = (
                "SELECT id, session_id, time_created, json_extract(data, '$.role') FROM message"
                if message_has_id
                else "SELECT NULL, session_id, time_created, json_extract(data, '$.role') FROM message"
            )
            count = 0
            live_sessions: set[str] = set()
            for message_id, session_id, created_at, role in connection.execute(query):
                day = _day(created_at, context.timezone)
                if not context.includes(day):
                    continue
                count += 1
                identity = str(session_id)
                if role in INSTRUCTION_ROLES:
                    live_sessions.add(identity)
                    result.add_session(day, identity)
                    if copies.take(message_id, created_at, (identity, created_at, role)):
                        result.add_prompt(day)
                elif identity in live_sessions:
                    result.add_session(day, identity)
            for session_id, created_at, updated_at in connection.execute(
                "SELECT id, time_created, time_updated FROM session"
            ):
                identity = str(session_id)
                if identity not in live_sessions:
                    continue
                for value in (created_at, updated_at):
                    day = _day(value, context.timezone)
                    if context.includes(day):
                        result.add_session(day, identity)
    except sqlite3.Error:
        result.coverage = Coverage(
            result.harness, True, "error", "native session database is unreadable"
        )
        return result
    result.coverage = Coverage(
        result.harness, True, "full", f"{count} recent message records"
    )
    return result


def collect_opencode(context: CollectorContext) -> HarnessResult:
    harness = "OpenCode"
    data_root = context.home / ".local/share/opencode"
    database = data_root / "opencode.db"
    legacy_root = data_root / "storage/message"
    installed = (
        _command_installed("opencode") or database.exists() or legacy_root.exists()
    )
    result = HarnessResult(harness)
    if not installed:
        result.coverage = Coverage(harness, False, "absent")
        return result
    if database.exists():
        return _collect_opencode_sqlite(context, result, database)
    return _collect_opencode_legacy_json(context, result, legacy_root)


def _readonly_sqlite(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path))}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def collect_hermes(context: CollectorContext) -> HarnessResult:
    harness = "Hermes"
    path = context.home / ".hermes/state.db"
    installed = _command_installed("hermes") or path.exists()
    result = HarnessResult(harness)
    if not installed:
        result.coverage = Coverage(harness, False, "absent")
        return result
    if not path.exists():
        result.coverage = Coverage(harness, True, "unavailable", "native state database missing")
        return result
    try:
        with _readonly_sqlite(path) as connection:
            rows = connection.execute(
                "SELECT session_id, role, timestamp FROM messages WHERE timestamp >= ?",
                (context.start_timestamp,),
            )
            copies = _PromptCopies()
            count = 0
            for session_id, role, timestamp in rows:
                day = _day(timestamp, context.timezone)
                if not context.includes(day):
                    continue
                count += 1
                result.add_session(day, str(session_id))
                if role in INSTRUCTION_ROLES and copies.take(
                    f"{session_id}:{role}", timestamp
                ):
                    result.add_prompt(day)
            for session_id, started_at in connection.execute(
                """
                SELECT id, started_at FROM sessions
                WHERE system_prompt IS NOT NULL AND length(system_prompt) > 0
                  AND started_at >= ?
                """,
                (context.start_timestamp,),
            ):
                day = _day(started_at, context.timezone)
                if context.includes(day) and copies.take(
                    f"{session_id}:system_prompt", started_at
                ):
                    result.add_session(day, str(session_id))
                    result.add_prompt(day)
    except sqlite3.Error:
        result.coverage = Coverage(harness, True, "error", "native state database unreadable")
        return result
    result.coverage = Coverage(harness, True, "full", f"{count} recent message records")
    return result


def collect_cursor_gui(context: CollectorContext) -> HarnessResult:
    harness = "Cursor GUI"
    path = context.home / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
    installed = Path("/Applications/Cursor.app").exists() or path.exists()
    result = HarnessResult(harness)
    if not installed:
        result.coverage = Coverage(harness, False, "absent")
        return result
    if not path.exists():
        result.coverage = Coverage(harness, True, "unavailable", "native global database missing")
        return result
    query = """
        SELECT substr(k.key, 14) AS composer_id,
               json_extract(h.value, '$.type') AS message_type,
               json_extract(h.value, '$.createdAt') AS created_at
        FROM cursorDiskKV AS k, json_each(k.value, '$.fullConversationHeadersOnly') AS h
        WHERE k.key LIKE 'composerData:%'
          AND json_extract(h.value, '$.createdAt') IS NOT NULL
    """
    try:
        with _readonly_sqlite(path) as connection:
            copies = _PromptCopies()
            count = 0
            for composer_id, message_type, created_at in connection.execute(query):
                day = _day(created_at, context.timezone)
                if not context.includes(day):
                    continue
                count += 1
                result.add_session(day, str(composer_id))
                if message_type == 1 and copies.take(
                    f"{composer_id}:{created_at}", created_at
                ):
                    result.add_prompt(day)
    except sqlite3.Error:
        result.coverage = Coverage(harness, True, "error", "native global database unreadable")
        return result
    result.coverage = Coverage(harness, True, "full", f"{count} recent message headers")
    return result


def _editor_extension_installed(home: Path, prefix: str) -> bool:
    roots = [home / ".cursor/extensions", home / ".vscode/extensions"]
    return any(root.exists() and any(root.glob(f"{prefix}-*")) for root in roots)


def _roo_initial_task(content: Any) -> bool:
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "\n".join(
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
    else:
        return False
    return text.lstrip().startswith("<task>")


def _collect_roo_family(
    context: CollectorContext,
    *,
    harness: str,
    extension_id: str,
) -> HarnessResult:
    storages = [
        context.home
        / "Library/Application Support/Cursor/User/globalStorage"
        / extension_id,
        context.home
        / "Library/Application Support/Code/User/globalStorage"
        / extension_id,
    ]
    task_roots = [storage / "tasks" for storage in storages if (storage / "tasks").exists()]
    installed = any(storage.exists() for storage in storages) or _editor_extension_installed(
        context.home, extension_id
    )
    result = HarnessResult(harness)
    if not installed:
        result.coverage = Coverage(harness, False, "absent")
        return result
    if not task_roots:
        result.coverage = Coverage(harness, True, "full", "0 native task histories")
        return result

    recent = _recent_files(
        task_roots, "api_conversation_history.json", context.start_timestamp
    ) + _recent_files(task_roots, "ui_messages.json", context.start_timestamp)
    task_directories = {path.parent for path in recent}
    unreadable = 0
    copies = _PromptCopies()
    for task in sorted(task_directories):
        host = "Cursor" if "Cursor/User" in str(task) else "VS Code"
        identity = f"{host}:{task.name}"
        api_path = task / "api_conversation_history.json"
        ui_path = task / "ui_messages.json"
        try:
            api_messages = json.loads(api_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            api_messages = []
            unreadable += 1
        if not isinstance(api_messages, list):
            api_messages = []
            unreadable += 1
        for message in api_messages:
            if not isinstance(message, dict):
                continue
            day = _day(message.get("ts"), context.timezone)
            if not context.includes(day):
                continue
            result.add_session(day, identity)
            role = message.get("role")
            content = message.get("content")
            if (
                (
                    role in {"system", "developer"}
                    and _content_has_instruction(content)
                )
                or (role == "user" and _roo_initial_task(content))
            ) and copies.take(message.get("ts"), message.get("ts"), (identity, role, day)):
                result.add_prompt(day)
        try:
            ui_messages = json.loads(ui_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            ui_messages = []
            unreadable += 1
        if not isinstance(ui_messages, list):
            ui_messages = []
            unreadable += 1
        for message in ui_messages:
            if not isinstance(message, dict):
                continue
            day = _day(message.get("ts"), context.timezone)
            if not context.includes(day):
                continue
            result.add_session(day, identity)
            if (
                message.get("type") == "say"
                and message.get("say") == "user_feedback"
                and str(message.get("text", "")).strip()
                and copies.take(message.get("ts"), message.get("ts"), (identity, "feedback", day))
            ):
                result.add_prompt(day)
    detail = f"{len(task_directories)} recent native task histories"
    if unreadable:
        detail += f"; {unreadable} message files unreadable"
    result.coverage = Coverage(
        harness,
        True,
        "partial" if unreadable else "full",
        detail,
    )
    return result


def collect_kilo(context: CollectorContext) -> HarnessResult:
    return _collect_roo_family(
        context,
        harness="Kilo Code",
        extension_id="kilocode.kilo-code",
    )


def collect_roo(context: CollectorContext) -> HarnessResult:
    return _collect_roo_family(
        context,
        harness="Roo Code",
        extension_id="rooveterinaryinc.roo-cline",
    )


def collect_cline(context: CollectorContext) -> HarnessResult:
    return _collect_roo_family(
        context,
        harness="Cline",
        extension_id="saoudrizwan.claude-dev",
    )


def _nested_protobuf_timestamps(data: bytes, depth: int = 0) -> list[datetime]:
    if depth > 5:
        return []
    try:
        fields = list(_protobuf_fields(data))
    except _CursorStoreError:
        return []
    scalars = {
        number: value
        for number, wire_type, value in fields
        if wire_type == 0 and isinstance(value, int)
    }
    timestamps: list[datetime] = []
    seconds = scalars.get(1)
    nanos = scalars.get(2, 0)
    if (
        isinstance(seconds, int)
        and 1_500_000_000 <= seconds <= 2_500_000_000
        and isinstance(nanos, int)
        and 0 <= nanos < 1_000_000_000
    ):
        timestamps.append(datetime.fromtimestamp(seconds + nanos / 1_000_000_000, timezone.utc))
    for _, wire_type, value in fields:
        if wire_type != 2 or not isinstance(value, bytes) or not value:
            continue
        timestamps.extend(_nested_protobuf_timestamps(value, depth + 1))
        try:
            decoded = base64.b64decode(value, validate=True)
        except (ValueError, TypeError):
            continue
        if decoded:
            timestamps.extend(_nested_protobuf_timestamps(decoded, depth + 1))
    return timestamps


def collect_antigravity(context: CollectorContext) -> HarnessResult:
    harness = "Antigravity"
    application = Path("/Applications/Antigravity.app")
    database = (
        context.home
        / "Library/Application Support/Antigravity/User/globalStorage/state.vscdb"
    )
    result = HarnessResult(harness)
    if not application.exists() and not database.exists():
        result.coverage = Coverage(harness, False, "absent")
        return result
    if not database.exists():
        result.coverage = Coverage(
            harness, True, "unavailable", "native global database is missing"
        )
        return result
    try:
        with _readonly_sqlite(database) as connection:
            row = connection.execute(
                "SELECT value FROM ItemTable WHERE key=?",
                ("antigravityUnifiedStateSync.trajectorySummaries",),
            ).fetchone()
    except sqlite3.Error:
        result.coverage = Coverage(
            harness, True, "error", "native global database is unreadable"
        )
        return result
    if row is None:
        result.coverage = Coverage(
            harness, True, "partial", "native store exposes no trajectory summaries"
        )
        return result
    try:
        encoded = row[0].decode("ascii") if isinstance(row[0], bytes) else str(row[0])
        payload = base64.b64decode(encoded, validate=True)
        entries = [
            value
            for number, wire_type, value in _protobuf_fields(payload)
            if number == 1 and wire_type == 2 and isinstance(value, bytes)
        ]
        for index, entry in enumerate(entries):
            fields = list(_protobuf_fields(entry))
            identity_bytes = next(
                (
                    value
                    for number, wire_type, value in fields
                    if number == 1 and wire_type == 2 and isinstance(value, bytes)
                ),
                b"",
            )
            identity = identity_bytes.decode("utf-8", errors="replace") or str(index)
            for timestamp in _nested_protobuf_timestamps(entry):
                day = timestamp.astimezone(context.timezone).date()
                if context.includes(day):
                    result.add_session(day, identity)
    except (ValueError, _CursorStoreError):
        result.coverage = Coverage(
            harness, True, "error", "trajectory summaries are unreadable"
        )
        return result
    result.coverage = Coverage(
        harness,
        True,
        "partial",
        f"{len(entries)} trajectory summaries; native store omits prompt history",
    )
    return result


def _vscode_request_has_instruction(request: dict[str, Any]) -> bool:
    message = request.get("message")
    if isinstance(message, str):
        return bool(message.strip())
    if not isinstance(message, dict):
        return False
    if str(message.get("text", "")).strip():
        return True
    parts = message.get("parts")
    return isinstance(parts, list) and bool(parts)


def collect_github_copilot(context: CollectorContext) -> HarnessResult:
    harness = "GitHub Copilot"
    extension = _editor_extension_installed(context.home, "github.copilot-chat")
    user_root = context.home / "Library/Application Support/Code/User"
    native_storage = user_root / "globalStorage/github.copilot-chat"
    result = HarnessResult(harness)
    if not extension and not native_storage.exists():
        result.coverage = Coverage(harness, False, "absent")
        return result
    roots = list((user_root / "workspaceStorage").glob("*/chatSessions"))
    roots.extend(
        [
            user_root / "globalStorage/emptyWindowChatSessions",
            user_root / "globalStorage/transferredChatSessions",
        ]
    )
    files = _recent_files(roots, "*.json", context.start_timestamp) + _recent_files(
        roots, "*.jsonl", context.start_timestamp
    )
    copies = _PromptCopies()
    unsupported_events = 0
    unreadable = 0

    def consume(session: dict[str, Any], path: Path) -> None:
        identity = str(session.get("sessionId") or path.stem)
        for value in (session.get("creationDate"), session.get("lastMessageDate")):
            day = _day(value, context.timezone)
            if context.includes(day):
                result.add_session(day, identity)
        requests = session.get("requests")
        if not isinstance(requests, list):
            return
        for index, request in enumerate(requests):
            if not isinstance(request, dict):
                continue
            day = _day(request.get("timestamp"), context.timezone)
            if not context.includes(day):
                continue
            result.add_session(day, identity)
            request_id = request.get("requestId") or request.get("id") or index
            if _vscode_request_has_instruction(request) and copies.take(
                request_id, request.get("timestamp")
            ):
                result.add_prompt(day)

    for path in files:
        if path.suffix == ".json":
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                unreadable += 1
                continue
            if isinstance(value, dict):
                consume(value, path)
            else:
                unreadable += 1
            continue
        for event in _json_lines(path):
            value = event.get("v")
            if event.get("kind") == 0 and isinstance(value, dict):
                consume(value, path)
            else:
                unsupported_events += 1
    detail = f"{len(files)} recent native chat session files"
    if unsupported_events:
        detail += f"; {unsupported_events} incremental events unsupported"
    if unreadable:
        detail += f"; {unreadable} files unreadable"
    result.coverage = Coverage(
        harness,
        True,
        "partial" if unsupported_events or unreadable else "full",
        detail,
    )
    return result


class _CursorStoreError(Exception):
    pass


def _read_varint(data: bytes, index: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while index < len(data) and shift < 70:
        byte = data[index]
        index += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, index
        shift += 7
    raise _CursorStoreError("malformed protobuf varint")


def _protobuf_fields(data: bytes) -> Iterable[tuple[int, int, int | bytes]]:
    index = 0
    while index < len(data):
        key, index = _read_varint(data, index)
        field_number = key >> 3
        wire_type = key & 7
        if field_number == 0:
            raise _CursorStoreError("invalid protobuf field")
        if wire_type == 0:
            value, index = _read_varint(data, index)
        elif wire_type == 1:
            end = index + 8
            if end > len(data):
                raise _CursorStoreError("truncated protobuf field")
            value = data[index:end]
            index = end
        elif wire_type == 2:
            length, index = _read_varint(data, index)
            end = index + length
            if end > len(data):
                raise _CursorStoreError("truncated protobuf field")
            value = data[index:end]
            index = end
        elif wire_type == 5:
            end = index + 4
            if end > len(data):
                raise _CursorStoreError("truncated protobuf field")
            value = data[index:end]
            index = end
        else:
            raise _CursorStoreError("unsupported protobuf wire type")
        yield field_number, wire_type, value


def _cursor_blob(connection: sqlite3.Connection, blob_id: bytes | str) -> bytes:
    key = blob_id.hex() if isinstance(blob_id, bytes) else blob_id
    row = connection.execute("SELECT data FROM blobs WHERE id=?", (key,)).fetchone()
    if row is None:
        raise _CursorStoreError("referenced blob is missing")
    value = row[0]
    return bytes(value) if not isinstance(value, str) else value.encode("utf-8")


def _cursor_user_message_has_instruction(data: bytes) -> bool:
    instruction_fields = {1, 3, 8, 11, 14, 16, 18, 19, 21, 22}
    return any(
        field_number in instruction_fields
        and wire_type == 2
        and isinstance(value, bytes)
        and bool(value)
        for field_number, wire_type, value in _protobuf_fields(data)
    )


def _cursor_cli_prompt_total(path: Path) -> int:
    try:
        with _readonly_sqlite(path) as connection:
            row = connection.execute("SELECT value FROM meta WHERE key='0'").fetchone()
            if row is None:
                count = connection.execute("SELECT COUNT(*) FROM blobs").fetchone()
                if count is not None and int(count[0]) == 0:
                    return 0
                raise _CursorStoreError("root metadata is missing")
            raw_meta = row[0]
            if isinstance(raw_meta, bytes):
                raw_meta = raw_meta.decode("ascii")
            try:
                metadata = json.loads(bytes.fromhex(str(raw_meta)).decode("utf-8"))
            except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
                raise _CursorStoreError("root metadata is unreadable") from error
            root_id = metadata.get("latestRootBlobId")
            if not root_id:
                return 0
            root = _cursor_blob(connection, str(root_id))
            prompts = 0
            for field_number, wire_type, value in _protobuf_fields(root):
                if wire_type != 2 or not isinstance(value, bytes):
                    continue
                if field_number == 1:
                    try:
                        message = json.loads(_cursor_blob(connection, value).decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as error:
                        raise _CursorStoreError("root message is unreadable") from error
                    if (
                        isinstance(message, dict)
                        and message.get("role") in {"system", "developer"}
                        and _content_has_instruction(message.get("content"))
                    ):
                        prompts += 1
                elif field_number == 8:
                    turn = _cursor_blob(connection, value)
                    agent_turn = next(
                        (
                            nested
                            for number, nested_wire, nested in _protobuf_fields(turn)
                            if number == 1
                            and nested_wire == 2
                            and isinstance(nested, bytes)
                        ),
                        None,
                    )
                    if agent_turn is None:
                        continue
                    user_message_id = next(
                        (
                            nested
                            for number, nested_wire, nested in _protobuf_fields(agent_turn)
                            if number == 1
                            and nested_wire == 2
                            and isinstance(nested, bytes)
                        ),
                        None,
                    )
                    if user_message_id is None:
                        raise _CursorStoreError("user message reference is missing")
                    if _cursor_user_message_has_instruction(
                        _cursor_blob(connection, user_message_id)
                    ):
                        prompts += 1
            return prompts
    except sqlite3.Error as error:
        raise _CursorStoreError("native state database is unreadable") from error


def collect_cursor_cli(context: CollectorContext) -> HarnessResult:
    harness = "Cursor CLI"
    roots = [
        context.home / ".cursor/chats",
        context.home / ".cursor/acp-sessions",
    ]
    installed = _command_installed("cursor-agent") or any(root.exists() for root in roots)
    result = HarnessResult(harness)
    if not installed:
        result.coverage = Coverage(harness, False, "absent")
        return result
    baseline = context.database.ensure_collector_baseline(harness, context.now)
    stores = _recent_files(roots, "store.db", context.start_timestamp)
    historical_gaps = 0
    unreadable = 0
    acp_sessions = 0
    for store in stores:
        meta_path = store.parent / "meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
        session_id = store.parent.name
        is_acp = store.parent.parent.name == "acp-sessions"
        if is_acp:
            acp_sessions += 1
        try:
            fallback = store.stat().st_mtime
        except OSError:
            unreadable += 1
            continue
        created_at = _instant(meta.get("createdAtMs"), context.timezone)
        updated_at = _instant(meta.get("updatedAtMs") or fallback, context.timezone)
        created = created_at.date() if created_at is not None else None
        updated = updated_at.date() if updated_at is not None else None
        if created is None:
            created = updated
        try:
            total = _cursor_cli_prompt_total(store)
        except _CursorStoreError:
            unreadable += 1
            continue
        if total == 0:
            continue
        for day in {created, updated}:
            if context.includes(day):
                result.add_session(day, session_id)
        observed = updated_at or datetime.fromtimestamp(fallback, context.timezone)
        attribute_first = (
            created is not None
            and created == updated
            and context.includes(updated)
        )
        source_prefix = "native-v2:acp" if is_acp else "native-v2"
        _, first = context.database.observe_source_total(
            harness,
            f"{source_prefix}:{session_id}",
            total,
            observed,
            attribute_first=attribute_first,
        )
        if first and total and not attribute_first:
            historical_gaps += 1
    for day, count in context.database.observed_prompt_counts(
        harness, context.start, context.end
    ).items():
        result.add_prompt(day, count)
    readable = len(stores) - unreadable
    detail = f"{readable} recent sessions; exact daily counts after local baseline"
    if acp_sessions:
        detail += f"; {acp_sessions} ACP sessions"
    baseline_day = baseline.astimezone(context.timezone).date()
    range_predates_baseline = context.start <= baseline_day
    if range_predates_baseline:
        detail += f"; day attribution through {baseline_day.isoformat()} incomplete"
        if historical_gaps:
            detail += f" ({historical_gaps} multi-day sessions)"
    if unreadable:
        detail += f"; {unreadable} native stores unreadable"
    if unreadable and unreadable == len(stores):
        status = "error"
    elif unreadable or range_predates_baseline:
        status = "partial"
    else:
        status = "full"
    result.coverage = Coverage(harness, True, status, detail)
    return result


def _amp_is_logged_in(home: Path) -> bool:
    root = home / ".local/share/amp"
    return (root / "session.json").is_file() or (root / "secrets.json").is_file()


def _amp_env() -> dict[str, str]:
    env = os.environ.copy()
    env["BROWSER"] = "/usr/bin/true"
    return env


def collect_amp(context: CollectorContext) -> HarnessResult:
    harness = "Amp"
    candidates = [context.home / ".amp/bin/amp"]
    discovered = shutil.which("amp")
    if discovered:
        candidates.append(Path(discovered))
    binary = next((path for path in candidates if path.is_file()), None)
    native_root = context.home / ".local/share/amp"
    installed = binary is not None or native_root.exists()
    result = HarnessResult(harness)
    if not installed:
        result.coverage = Coverage(harness, False, "absent")
        return result
    if binary is None:
        result.coverage = Coverage(harness, True, "unavailable", "Amp CLI is unavailable")
        return result
    if not _amp_is_logged_in(context.home):
        result.coverage = Coverage(
            harness, True, "unavailable", "Amp login is missing; CLI was not started"
        )
        return result

    thread_ids: list[str] = []
    page_size = 100
    capped = False
    amp_env = _amp_env()
    for offset in range(0, 5000, page_size):
        try:
            completed = subprocess.run(
                [
                    str(binary),
                    "--no-color",
                    "threads",
                    "list",
                    "--include-archived",
                    "--limit",
                    str(page_size),
                    "--offset",
                    str(offset),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=30,
                env=amp_env,
            )
        except (OSError, subprocess.TimeoutExpired):
            result.coverage = Coverage(harness, True, "error", "thread listing failed")
            return result
        if completed.returncode != 0:
            result.coverage = Coverage(harness, True, "error", "thread listing failed")
            return result
        page = []
        for line in completed.stdout.splitlines():
            matched = re.search(r"(?:^|\s)(T-[A-Za-z0-9_-]+)\s*$", line)
            if matched:
                page.append(matched.group(1))
        thread_ids.extend(page)
        if len(page) < page_size:
            break
    else:
        capped = True

    failed = 0
    copies = _PromptCopies()
    for thread_id in dict.fromkeys(thread_ids):
        try:
            completed = subprocess.run(
                [str(binary), "--no-color", "threads", "export", thread_id],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=60,
                env=amp_env,
            )
        except (OSError, subprocess.TimeoutExpired):
            failed += 1
            continue
        if completed.returncode != 0:
            failed += 1
            continue
        try:
            thread = json.loads(completed.stdout)
        except json.JSONDecodeError:
            failed += 1
            continue
        if not isinstance(thread, dict):
            failed += 1
            continue
        identity = str(thread.get("id") or thread_id)
        messages = thread.get("messages")
        if not isinstance(messages, list):
            failed += 1
            continue
        real_turn = False
        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                continue
            metadata = message.get("meta") if isinstance(message.get("meta"), dict) else {}
            day = _day(metadata.get("sentAt"), context.timezone)
            is_instruction = message.get("role") in INSTRUCTION_ROLES and _content_has_instruction(
                message.get("content")
            )
            if is_instruction:
                real_turn = True
            if not context.includes(day):
                continue
            result.add_session(day, identity)
            message_id = message.get("messageId") or message.get("protocolMessageID") or index
            if is_instruction and copies.take(message_id, metadata.get("sentAt")):
                result.add_prompt(day)
        if real_turn:
            for value in (thread.get("created"), thread.get("updatedAt")):
                day = _day(value, context.timezone)
                if context.includes(day):
                    result.add_session(day, identity)

    if failed or capped:
        detail = f"{len(thread_ids) - failed} threads exported"
        if failed:
            detail += f"; {failed} exports failed"
        if capped:
            detail += "; 5000-thread safety cap reached"
        result.coverage = Coverage(harness, True, "partial", detail)
        return result
    result.coverage = Coverage(
        harness,
        True,
        "full",
        f"{len(thread_ids)} threads exported through the native CLI",
    )
    return result


def collect_pi(context: CollectorContext) -> HarnessResult:
    return _collect_pi_family(
        context,
        harness="Pi Agent",
        root=context.home / ".pi/agent/sessions",
        command="pi",
    )


def collect_prime(context: CollectorContext) -> HarnessResult:
    return _collect_pi_family(
        context,
        harness="Prime Agent",
        root=context.home / ".prime/agent/sessions",
        command="prime-agent",
    )


KIMI_INJECTION_ORIGINS = {
    "injection",
    "system_trigger",
    "background_task",
    "skill_activation",
}


def _kimi_role_and_origin(row: dict[str, Any]) -> tuple[Any, Any]:
    message = row.get("message") if isinstance(row.get("message"), dict) else {}
    role = row.get("role") or message.get("role")
    origin = row.get("origin") if isinstance(row.get("origin"), dict) else {}
    return role, origin.get("kind")


def collect_kimi(context: CollectorContext) -> HarnessResult:
    harness = "Kimi Code"
    data_home = os.environ.get("KIMI_CODE_HOME")
    roots = [Path(data_home).expanduser()] if data_home else []
    roots += [context.home / ".kimi-code", context.home / ".kimi"]
    installed = _command_installed("kimi") or any(root.exists() for root in roots)
    result = HarnessResult(harness)
    if not installed:
        result.coverage = Coverage(harness, False, "absent")
        return result
    files = _recent_files(roots, "wire.jsonl", context.start_timestamp)
    for path in files:
        under_agents = path.parent.parent.name == "agents"
        if under_agents:
            session_id = path.parent.parent.parent.name
            identity = f"{session_id}:{path.parent.name}"
        else:
            session_id = path.parent.name
            identity = session_id
        activity: set[date] = set()
        prompts: list[tuple[date, str]] = []
        for row in _json_lines(path):
            day = _day(row.get("timestamp"), context.timezone)
            if context.includes(day):
                activity.add(day)
            event_type = row.get("type")
            role, origin_kind = _kimi_role_and_origin(row)
            is_user_turn = (
                event_type == "turn.prompt" and origin_kind == "user"
            ) or (
                event_type == "context.append_message"
                and role == "user"
                and (origin_kind is None or origin_kind == "user")
            )
            if not is_user_turn:
                continue
            payload = row.get("prompt")
            if payload is None:
                payload = row.get("text")
            if payload is None:
                payload = row.get("content")
            if not _content_has_instruction(payload):
                continue
            digest = hashlib.sha256(
                json.dumps(
                    {
                        "t": str(row.get("timestamp")),
                        "p": json.dumps(payload, sort_keys=True, default=str),
                    },
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            prompts.append((day, digest))
        for day in activity:
            result.add_session(day, identity)
        seen: set[str] = set()
        for day, digest in prompts:
            key = f"{identity}:{digest}"
            if key in seen:
                continue
            seen.add(key)
            if context.includes(day):
                result.add_prompt(day)
    detail = f"{len(files)} recent wire logs"
    if not files and any(root.exists() for root in roots):
        detail = "native sessions directory exists but no wire logs are recent"
        result.coverage = Coverage(harness, True, "partial", detail)
        return result
    result.coverage = Coverage(harness, True, "full", detail)
    return result


def _grok_root(context: CollectorContext) -> Path:
    override = os.environ.get("GROK_HOME")
    if override:
        return Path(override).expanduser()
    return context.home / ".grok"


def _grok_user_message(row: dict[str, Any]) -> bool:
    event_type = str(row.get("type") or row.get("updateType") or "")
    if "usage" in event_type or "completed" in event_type:
        return False
    message = row.get("message") if isinstance(row.get("message"), dict) else {}
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    role = row.get("role") or message.get("role") or payload.get("role")
    content = row.get("content") or message.get("content") or payload.get("content")
    if isinstance(content, dict):
        content = content.get("text")
    return role == "user" and _content_has_instruction(content)


def collect_grok(context: CollectorContext) -> HarnessResult:
    harness = "Grok Build"
    root = _grok_root(context)
    sessions_root = root / "sessions"
    community_database = root / "grok.db"
    installed = _command_installed("grok") or root.exists()
    result = HarnessResult(harness)
    if not installed:
        result.coverage = Coverage(harness, False, "absent")
        return result
    if not sessions_root.exists():
        if community_database.exists():
            result.coverage = Coverage(
                harness,
                True,
                "unavailable",
                "community grok-cli database detected; official CLI stores no sessions here",
            )
        else:
            result.coverage = Coverage(
                harness, True, "unavailable", "native sessions directory is missing"
            )
        return result
    files = _recent_files([sessions_root], "updates.jsonl", context.start_timestamp) + _recent_files(
        [sessions_root], "chat_history.jsonl", context.start_timestamp
    )
    for path in files:
        session_id = path.parent.name
        activity: set[date] = set()
        prompts: list[tuple[date, str]] = []
        for row in _json_lines(path):
            day = _day(row.get("timestamp") or row.get("ts"), context.timezone)
            if context.includes(day):
                activity.add(day)
            if not _grok_user_message(row):
                continue
            digest = hashlib.sha256(
                json.dumps(row, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
            prompts.append((day, digest))
        for day in activity:
            result.add_session(day, session_id)
        seen: set[str] = set()
        for day, digest in prompts:
            key = f"{session_id}:{digest}"
            if key in seen:
                continue
            seen.add(key)
            if context.includes(day):
                result.add_prompt(day)
    detail = f"{len(files)} recent native session logs"
    if not files:
        detail = "sessions directory exists but no recent native session logs"
        result.coverage = Coverage(harness, True, "partial", detail)
        return result
    result.coverage = Coverage(harness, True, "full", detail)
    return result


def collect_omp(context: CollectorContext) -> HarnessResult:
    return _collect_pi_family(
        context,
        harness="Oh My Pi",
        root=context.home / ".omp/agent/sessions",
        command="omp",
    )


GIT_SCAN_IGNORED_DIRECTORIES = frozenset(
    {
        ".git",
        "node_modules",
        "vendor",
        ".venv",
        "venv",
        "dist",
        "build",
        ".next",
        ".cache",
    }
)


@dataclass(frozen=True)
class CodeRootDetection:
    roots: tuple[Path, ...]
    repository_count: int


def _pruned_git_directories(
    directories: list[str], *, home_scan: bool
) -> list[str]:
    ignored = GIT_SCAN_IGNORED_DIRECTORIES
    if home_scan:
        ignored = ignored | {"Library", ".Trash"}
    return [
        name
        for name in directories
        if not name.startswith(".") and name not in ignored
    ]


def detect_code_roots(home: Path) -> CodeRootDetection:
    home = home.expanduser().resolve()
    if not home.is_dir():
        return CodeRootDetection((), 0)

    repositories: set[Path] = set()
    for current, directories, files in os.walk(home, topdown=True, followlinks=False):
        if ".git" in directories or ".git" in files:
            repositories.add(Path(current))
        directories[:] = _pruned_git_directories(directories, home_scan=True)

    roots: set[Path] = set()
    for repository in repositories:
        relative = repository.relative_to(home)
        roots.add(home / relative.parts[0] if relative.parts else home)
    return CodeRootDetection(
        tuple(sorted(roots, key=lambda path: str(path).casefold())),
        len(repositories),
    )


def _discover_git_roots(code_root: Path) -> list[Path]:
    if not code_root.exists():
        return []
    roots: list[Path] = []
    for current, directories, files in os.walk(
        code_root, topdown=True, followlinks=False
    ):
        if ".git" in files or ".git" in directories:
            roots.append(Path(current))
        directories[:] = _pruned_git_directories(directories, home_scan=False)
    return roots


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=20,
    )


def collect_commits(context: CollectorContext) -> CommitResult:
    harness = "Git"
    if not _command_installed("git"):
        return CommitResult({}, Coverage(harness, False, "unavailable", "git is not installed"))
    if not context.code_roots:
        return CommitResult({}, Coverage(harness, True, "error", "no Git roots detected"))
    roots: dict[str, Path] = {}
    missing_roots = 0
    for code_root in context.code_roots:
        if not code_root.is_dir():
            missing_roots += 1
            continue
        for root in _discover_git_roots(code_root):
            roots[str(root.resolve())] = root
    if missing_roots == len(context.code_roots):
        return CommitResult({}, Coverage(harness, True, "error", "Git roots are missing"))
    common_roots: dict[str, Path] = {}
    errors = missing_roots
    for root in roots.values():
        resolved = _git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=root)
        if resolved.returncode != 0:
            errors += 1
            continue
        common_roots[resolved.stdout.strip()] = root

    unique: dict[str, tuple[date, str, str]] = {}
    creation = re.compile(
        r"^(commit(?: \([^)]*\))?:|merge |cherry-pick:|rebase \((?:pick|reword|edit|squash|fixup|finish)\):)"
    )
    start_dt = datetime.combine(context.start, time.min, context.timezone).isoformat()
    end_dt = datetime.combine(
        context.end + timedelta(days=1), time.min, context.timezone
    ).isoformat()
    for root in common_roots.values():
        email_result = _git("config", "--get-all", "user.email", cwd=root)
        emails = {line.strip().lower() for line in email_result.stdout.splitlines() if line.strip()}
        if not emails:
            errors += 1
            continue
        reflog = _git(
            "reflog",
            "--all",
            f"--since={start_dt}",
            f"--until={end_dt}",
            "--format=%H%x00%gs",
            cwd=root,
        )
        if reflog.returncode != 0:
            errors += 1
            continue
        candidates = {
            line.split("\x00", 1)[0]
            for line in reflog.stdout.splitlines()
            if "\x00" in line and creation.match(line.split("\x00", 1)[1])
        }
        if not candidates:
            continue
        shown = _git(
            "show",
            "-s",
            "--format=%H%x00%ae%x00%ce%x00%cI",
            *sorted(candidates),
            cwd=root,
        )
        if shown.returncode != 0:
            errors += 1
            continue
        for line in shown.stdout.splitlines():
            fields = line.split("\x00")
            if len(fields) != 4:
                continue
            sha, author_email, committer_email, committed_at = fields
            if author_email.lower() not in emails and committer_email.lower() not in emails:
                continue
            day = _day(committed_at, context.timezone)
            if context.includes(day):
                unique[sha] = (day, author_email, committer_email)
    counts: dict[date, int] = {}
    for day, _, _ in unique.values():
        counts[day] = counts.get(day, 0) + 1
    status = "partial" if errors else "full"
    detail = (
        f"{len(common_roots)} unique Git repositories across "
        f"{len(context.code_roots)} roots"
    )
    if errors:
        detail += f"; {errors} repositories or identities unreadable"
    return CommitResult(counts, Coverage(harness, True, status, detail))


COLLECTORS: tuple[Callable[[CollectorContext], HarnessResult], ...] = (
    collect_codex,
    collect_claude,
    collect_cursor_gui,
    collect_cursor_cli,
    collect_kilo,
    collect_roo,
    collect_cline,
    collect_github_copilot,
    collect_antigravity,
    collect_hermes,
    collect_pi,
    collect_omp,
    collect_prime,
    collect_opencode,
    collect_droid,
    collect_gemini,
    collect_qwen,
    collect_amp,
    collect_kimi,
    collect_grok,
)


def collect_all(context: CollectorContext) -> Collection:
    commits = collect_commits(context)
    results: list[HarnessResult] = []
    for collector in COLLECTORS:
        try:
            results.append(collector(context))
        except Exception:
            name = getattr(collector, "__name__", "unknown").removeprefix("collect_")
            failed = HarnessResult(name)
            failed.coverage = Coverage(name, True, "error", "collector failed")
            results.append(failed)
    return Collection(context.start, context.end, commits, tuple(results))
