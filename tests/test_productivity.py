from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import plistlib
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import replace
from datetime import date, datetime
import unittest
from unittest import mock
from zoneinfo import ZoneInfo


REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCTIVITY_ROOT = REPO_ROOT
sys.path.insert(0, str(PRODUCTIVITY_ROOT))

from agentic_productivity.collectors import (  # noqa: E402
    CollectorContext,
    _collect_pi_family,
    collect_amp,
    collect_antigravity,
    collect_claude,
    collect_codex,
    collect_commits,
    collect_cursor_cli,
    collect_cursor_gui,
    collect_cline,
    collect_droid,
    collect_gemini,
    collect_github_copilot,
    collect_grok,
    collect_hermes,
    collect_kilo,
    collect_kimi,
    collect_omp,
    collect_opencode,
    collect_pi,
    collect_qwen,
)
from agentic_productivity.database import Database  # noqa: E402
from agentic_productivity.model import (  # noqa: E402
    Collection,
    CommitResult,
    Coverage,
    HarnessResult,
)
from agentic_productivity.reporting import (  # noqa: E402
    CHART_HEIGHT,
    CHART_WIDTH,
    DEFAULT_REPORT_DAYS,
    MOCK_PNG,
    TREND_COLOR,
    _linear_trend,
    build_report,
    mock_delivery,
    render_chart,
)


DAY = date(2026, 8, 7)
STAMP = "2026-08-07T12:00:00Z"
TEST_ZONE = ZoneInfo("Europe/Warsaw")


def protobuf_varint(value: int) -> bytes:
    output = bytearray()
    while value >= 0x80:
        output.append((value & 0x7F) | 0x80)
        value >>= 7
    output.append(value)
    return bytes(output)


def protobuf_bytes(field_number: int, value: bytes) -> bytes:
    return protobuf_varint((field_number << 3) | 2) + protobuf_varint(len(value)) + value


def protobuf_uint(field_number: int, value: int) -> bytes:
    return protobuf_varint(field_number << 3) + protobuf_varint(value)


class ProductivityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.code = self.home / "code"
        self.code.mkdir(parents=True)
        self.database = Database(self.root / "state/metrics.sqlite3")
        self.context = CollectorContext(
            home=self.home,
            code_root=self.code,
            start=DAY,
            end=DAY,
            database=self.database,
            now=datetime(2026, 8, 8, 8, 0, tzinfo=TEST_ZONE),
            timezone=TEST_ZONE,
        )

    def write_jsonl(self, path: Path, rows: list[dict[str, object]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )
        timestamp = datetime(2026, 8, 8, 0, 0, tzinfo=TEST_ZONE).timestamp()
        os.utime(path, (timestamp, timestamp))

    def test_collector_uses_the_selected_local_calendar_day(self) -> None:
        path = self.home / ".codex/sessions/2026/08/07/session.jsonl"
        self.write_jsonl(
            path,
            [
                {
                    "type": "response_item",
                    "timestamp": "2026-08-07T12:30:00Z",
                    "payload": {"type": "message", "role": "user", "content": []},
                }
            ],
        )
        los_angeles = replace(
            self.context,
            start=date(2026, 8, 7),
            end=date(2026, 8, 7),
            timezone=ZoneInfo("America/Los_Angeles"),
        )
        auckland = replace(
            self.context,
            start=date(2026, 8, 8),
            end=date(2026, 8, 8),
            timezone=ZoneInfo("Pacific/Auckland"),
        )

        self.assertEqual(collect_codex(los_angeles).session_counts()[date(2026, 8, 7)], 1)
        self.assertEqual(collect_codex(auckland).session_counts()[date(2026, 8, 8)], 1)

    def test_codex_counts_instruction_roles_once_and_excludes_duplicate_event(self) -> None:
        path = self.home / ".codex/sessions/2026/08/07/session.jsonl"
        self.write_jsonl(
            path,
            [
                {"type": "session_meta", "timestamp": STAMP, "payload": {"id": "s1"}},
                {
                    "type": "response_item",
                    "timestamp": STAMP,
                    "payload": {"type": "message", "role": "developer", "content": []},
                },
                {
                    "type": "response_item",
                    "timestamp": STAMP,
                    "payload": {"type": "message", "role": "user", "content": []},
                },
                {"type": "event_msg", "timestamp": STAMP, "payload": {"type": "user_message"}},
                {
                    "type": "response_item",
                    "timestamp": STAMP,
                    "payload": {"type": "message", "role": "assistant", "content": []},
                },
            ],
        )
        archived = self.home / ".codex/archived_sessions/session.jsonl"
        archived.parent.mkdir(parents=True)
        archived.write_bytes(path.read_bytes())
        timestamp = datetime(2026, 8, 8, 0, 0, tzinfo=TEST_ZONE).timestamp()
        os.utime(archived, (timestamp, timestamp))

        result = collect_codex(self.context)

        self.assertEqual(result.session_counts()[DAY], 1)
        self.assertEqual(result.prompts[DAY], 2)
        self.assertEqual(result.coverage.status, "full")

    def test_claude_counts_parent_and_subagent_and_excludes_tool_results(self) -> None:
        main = self.home / ".claude/projects/project/parent.jsonl"
        subagent = self.home / ".claude/projects/project/parent/subagents/agent-child.jsonl"
        user = {
            "type": "user",
            "sessionId": "parent",
            "timestamp": STAMP,
            "message": {"role": "user", "content": "instruction"},
        }
        self.write_jsonl(main, [user])
        self.write_jsonl(
            subagent,
            [
                {**user, "agentId": "child", "message": {"role": "user", "content": "delegated"}},
                {
                    "type": "user",
                    "sessionId": "parent",
                    "agentId": "child",
                    "timestamp": STAMP,
                    "message": {"role": "user", "content": [{"type": "tool_result"}]},
                },
            ],
        )

        result = collect_claude(self.context)

        self.assertEqual(result.session_counts()[DAY], 2)
        self.assertEqual(result.prompts[DAY], 2)

    def test_pi_family_and_droid_filter_non_instruction_messages(self) -> None:
        pi = self.home / ".pi/agent/sessions/project/session.jsonl"
        self.write_jsonl(
            pi,
            [
                {"type": "session", "id": "pi-1", "timestamp": STAMP},
                {
                    "type": "message",
                    "timestamp": STAMP,
                    "message": {"role": "user", "content": [{"type": "text", "text": "go"}]},
                },
                {
                    "type": "message",
                    "timestamp": STAMP,
                    "message": {"role": "toolResult", "content": [{"type": "text", "text": "done"}]},
                },
            ],
        )
        droid = self.home / ".factory/sessions/project/droid.jsonl"
        self.write_jsonl(
            droid,
            [
                {"type": "session_start", "id": "d1"},
                {
                    "type": "message",
                    "id": "context-u1",
                    "timestamp": STAMP,
                    "message": {"role": "user", "content": [{"type": "text", "text": "duplicate"}]},
                },
                {
                    "type": "message",
                    "id": "u1",
                    "timestamp": STAMP,
                    "message": {"role": "user", "content": [{"type": "text", "text": "prompt"}]},
                },
                {
                    "type": "message",
                    "id": "tool",
                    "timestamp": STAMP,
                    "message": {"role": "user", "content": [{"type": "tool_result"}]},
                },
            ],
        )

        pi_result = _collect_pi_family(
            self.context,
            harness="Pi Agent",
            root=self.home / ".pi/agent/sessions",
            command="definitely-not-installed",
        )
        droid_result = collect_droid(self.context)

        self.assertEqual(pi_result.prompts[DAY], 1)
        self.assertEqual(droid_result.prompts[DAY], 1)
        self.assertEqual(droid_result.session_counts()[DAY], 1)

    def _omp_title_slot(self, title: str = "draft") -> str:
        slot = {
            "type": "title",
            "v": 1,
            "title": title,
            "updatedAt": STAMP,
            "pad": "",
        }
        def encoded(pad: str) -> bytes:
            slot["pad"] = pad
            return (json.dumps(slot, separators=(",", ":")) + "\n").encode()

        pad = " " * (256 - len(encoded("")))
        line = encoded(pad).decode()
        self.assertEqual(len(line.encode()), 256)
        return line

    def test_omp_counts_children_and_skips_empty_drafts(self) -> None:
        root = self.home / ".omp/agent/sessions/project"
        parent = root / "2026-08-07_parent.jsonl"
        child = root / "2026-08-07_parent" / "task1.jsonl"
        draft = root / "2026-08-07_draft.jsonl"
        user = {
            "type": "message",
            "id": "user01ab",
            "timestamp": STAMP,
            "message": {"role": "user", "content": [{"type": "text", "text": "go"}]},
        }
        parent.parent.mkdir(parents=True)
        parent.write_text(
            self._omp_title_slot("parent")
            + json.dumps({"type": "session", "id": "parent-1", "timestamp": STAMP})
            + "\n"
            + json.dumps(user)
            + "\n",
            encoding="utf-8",
        )
        child.parent.mkdir(parents=True)
        child.write_text(
            json.dumps({"type": "session", "id": "child-1", "timestamp": STAMP})
            + "\n"
            + json.dumps(
                {
                    "type": "session_init",
                    "id": "init01ab",
                    "timestamp": STAMP,
                    "task": "review the importer",
                    "systemPrompt": "you are a reviewer",
                    "tools": ["read"],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        draft.write_text(
            self._omp_title_slot()
            + json.dumps({"type": "session", "id": "draft-1", "timestamp": STAMP})
            + "\n",
            encoding="utf-8",
        )
        stamp = datetime(2026, 8, 8, 0, 0, tzinfo=TEST_ZONE).timestamp()
        for path in (parent, child, draft):
            os.utime(path, (stamp, stamp))

        result = collect_omp(self.context)

        self.assertEqual(result.session_counts()[DAY], 2)
        self.assertEqual(result.prompts[DAY], 2)
        self.assertEqual(result.coverage.status, "full")

    def test_copied_prompts_count_once_across_files(self) -> None:
        copied = {
            "type": "message",
            "id": "user01ab",
            "timestamp": STAMP,
            "message": {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        }
        follow = {
            "type": "message",
            "id": "user02cd",
            "timestamp": "2026-08-07T13:00:00Z",
            "message": {"role": "user", "content": [{"type": "text", "text": "again"}]},
        }
        parent = self.home / ".pi/agent/sessions/project/01_parent.jsonl"
        forked = self.home / ".pi/agent/sessions/project/02_fork.jsonl"
        self.write_jsonl(
            parent,
            [
                {"type": "session", "id": "parent00", "timestamp": STAMP},
                copied,
            ],
        )
        self.write_jsonl(
            forked,
            [
                {
                    "type": "session",
                    "id": "fork0000",
                    "timestamp": STAMP,
                    "parentSession": "parent00",
                },
                copied,
                follow,
            ],
        )

        result = collect_pi(self.context)

        self.assertEqual(result.session_counts()[DAY], 2)
        self.assertEqual(result.prompts[DAY], 2)

    def test_kimi_counts_user_turns_dedupes_and_excludes_injections(self) -> None:
        main = (
            self.home
            / ".kimi-code/sessions/wd_project_ab12/session-uuid-1/agents/main/wire.jsonl"
        )
        subagent = (
            self.home
            / ".kimi-code/sessions/wd_project_ab12/session-uuid-1/agents/agent-0/wire.jsonl"
        )
        self.write_jsonl(
            main,
            [
                {"type": "metadata", "protocol_version": 1, "timestamp": STAMP},
                {
                    "type": "turn.prompt",
                    "timestamp": STAMP,
                    "origin": {"kind": "user"},
                    "prompt": "hello world",
                },
                {
                    "type": "context.append_message",
                    "timestamp": STAMP,
                    "role": "user",
                    "origin": {"kind": "user"},
                    "content": "hello world",
                },
                {
                    "type": "context.append_message",
                    "timestamp": STAMP,
                    "role": "user",
                    "origin": {"kind": "injection"},
                    "content": "permission reminder",
                },
                {
                    "type": "context.append_loop_event",
                    "timestamp": STAMP,
                    "event": {"type": "content.part", "part": {"type": "text"}},
                },
                {"type": "usage.record", "timestamp": STAMP, "usage": {"tokens": 5}},
            ],
        )
        self.write_jsonl(
            subagent,
            [
                {
                    "type": "turn.prompt",
                    "timestamp": STAMP,
                    "origin": {"kind": "user"},
                    "prompt": "delegated instruction",
                },
            ],
        )

        result = collect_kimi(self.context)

        self.assertEqual(result.session_counts()[DAY], 2)
        self.assertEqual(result.prompts[DAY], 2)
        self.assertEqual(result.coverage.status, "full")

    def test_grok_reads_native_logs_and_never_reads_community_database(self) -> None:
        database = self.home / ".grok/grok.db"
        database.parent.mkdir(parents=True)
        database.write_bytes(b"SQLite format 3\x00")
        community = collect_grok(self.context)
        self.assertEqual(community.coverage.status, "unavailable")

        session = self.home / ".grok/sessions/workdir/session-one"
        session.mkdir(parents=True)
        (session / "updates.jsonl").write_text(
            "".join(
                json.dumps(row) + "\n"
                for row in [
                    {"type": "session_metadata", "timestamp": STAMP},
                    {"role": "user", "content": "fix the bug", "timestamp": STAMP},
                    {
                        "type": "turn_completed",
                        "usage": {"tokens": 10},
                        "timestamp": STAMP,
                    },
                ]
            ),
            encoding="utf-8",
        )
        fallback = self.home / ".grok/sessions/workdir/session-two/chat_history.jsonl"
        fallback.parent.mkdir(parents=True)
        fallback.write_text(
            json.dumps(
                {
                    "role": "user",
                    "message": {"role": "user", "content": "second"},
                    "ts": STAMP,
                },
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        stamp = datetime(2026, 8, 7, 12, 0, tzinfo=TEST_ZONE).timestamp()
        os.utime(session / "updates.jsonl", (stamp, stamp))
        os.utime(fallback, (stamp, stamp))

        result = collect_grok(self.context)

        self.assertEqual(result.session_counts()[DAY], 2)
        self.assertEqual(result.prompts[DAY], 2)
        self.assertEqual(result.coverage.status, "full")

    def test_cursor_gui_uses_header_timestamps_without_reading_message_text(self) -> None:
        path = self.home / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
        path.parent.mkdir(parents=True)
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)")
            connection.execute(
                "INSERT INTO cursorDiskKV VALUES(?, ?)",
                (
                    "composerData:composer-1",
                    json.dumps(
                        {
                            "fullConversationHeadersOnly": [
                                {"bubbleId": "u1", "type": 1, "createdAt": STAMP},
                                {"bubbleId": "a1", "type": 2, "createdAt": STAMP},
                            ]
                        }
                    ),
                ),
            )

        result = collect_cursor_gui(self.context)

        self.assertEqual(result.session_counts()[DAY], 1)
        self.assertEqual(result.prompts[DAY], 1)
        self.assertEqual(result.coverage.status, "full")

    def test_cursor_extension_harnesses_count_tasks_and_feedback(self) -> None:
        task = (
            self.home
            / "Library/Application Support/Cursor/User/globalStorage"
            / "kilocode.kilo-code/tasks/task-one"
        )
        task.mkdir(parents=True)
        (task / "api_conversation_history.json").write_text(
            json.dumps(
                [
                    {
                        "role": "user",
                        "ts": 1786104000000,
                        "content": [{"type": "text", "text": "<task>private</task>"}],
                    },
                    {
                        "role": "assistant",
                        "ts": 1786104060000,
                        "content": [{"type": "text", "text": "private response"}],
                    },
                    {
                        "role": "user",
                        "ts": 1786104120000,
                        "content": [{"type": "text", "text": "tool output only"}],
                    },
                ]
            ),
            encoding="utf-8",
        )
        (task / "ui_messages.json").write_text(
            json.dumps(
                [
                    {
                        "type": "say",
                        "say": "user_feedback",
                        "text": "private follow-up",
                        "ts": 1786104180000,
                    }
                ]
            ),
            encoding="utf-8",
        )
        for path in task.iterdir():
            timestamp = datetime(2026, 8, 7, 14, 0, tzinfo=TEST_ZONE).timestamp()
            os.utime(path, (timestamp, timestamp))
        extension = self.home / ".cursor/extensions/saoudrizwan.claude-dev-4.1.4"
        extension.mkdir(parents=True)

        kilo = collect_kilo(self.context)
        cline = collect_cline(self.context)

        self.assertEqual(kilo.session_counts()[DAY], 1)
        self.assertEqual(kilo.prompts[DAY], 2)
        self.assertEqual(kilo.coverage.status, "full")
        self.assertTrue(cline.coverage.installed)
        self.assertEqual(cline.coverage.status, "full")
        self.assertEqual(cline.session_counts(), {})

    def test_copilot_and_antigravity_native_registries(self) -> None:
        copilot_extension = self.home / ".vscode/extensions/github.copilot-chat-1.0.0"
        copilot_extension.mkdir(parents=True)
        chat = (
            self.home
            / "Library/Application Support/Code/User/workspaceStorage/work/chatSessions/chat.json"
        )
        chat.parent.mkdir(parents=True)
        chat.write_text(
            json.dumps(
                {
                    "sessionId": "copilot-one",
                    "creationDate": 1786104000000,
                    "lastMessageDate": 1786104060000,
                    "requests": [
                        {
                            "requestId": "request-one",
                            "timestamp": 1786104000000,
                            "message": {"text": "private instruction"},
                            "isSystemInitiated": True,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        timestamp = datetime(2026, 8, 7, 14, 0, tzinfo=TEST_ZONE).timestamp()
        os.utime(chat, (timestamp, timestamp))

        antigravity = (
            self.home
            / "Library/Application Support/Antigravity/User/globalStorage/state.vscdb"
        )
        antigravity.parent.mkdir(parents=True)
        instant = protobuf_uint(1, 1786104000) + protobuf_uint(2, 0)
        summary = protobuf_bytes(3, instant)
        entry = protobuf_bytes(1, b"trajectory-one") + protobuf_bytes(
            2, base64.b64encode(summary)
        )
        payload = protobuf_bytes(1, entry)
        with sqlite3.connect(antigravity) as connection:
            connection.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value BLOB)")
            connection.execute(
                "INSERT INTO ItemTable VALUES(?, ?)",
                (
                    "antigravityUnifiedStateSync.trajectorySummaries",
                    base64.b64encode(payload).decode(),
                ),
            )

        copilot = collect_github_copilot(self.context)
        gravity = collect_antigravity(self.context)

        self.assertEqual(copilot.session_counts()[DAY], 1)
        self.assertEqual(copilot.prompts[DAY], 1)
        self.assertEqual(copilot.coverage.status, "full")
        self.assertEqual(gravity.session_counts()[DAY], 1)
        self.assertEqual(gravity.prompts, {})
        self.assertEqual(gravity.coverage.status, "partial")

    def test_cursor_cli_decodes_native_store_without_storing_text(self) -> None:
        directory = self.home / ".cursor/chats/workspace/session-1"
        directory.mkdir(parents=True)
        store = directory / "store.db"
        user_id = bytes.fromhex("11" * 32)
        turn_id = bytes.fromhex("22" * 32)
        system_id = bytes.fromhex("33" * 32)
        root_id = "44" * 32
        user_message = protobuf_bytes(1, b"private user instruction")
        agent_turn = protobuf_bytes(1, user_id)
        turn = protobuf_bytes(1, agent_turn)
        root = protobuf_bytes(1, system_id) + protobuf_bytes(8, turn_id)
        with sqlite3.connect(store) as connection:
            connection.execute("CREATE TABLE blobs (id TEXT PRIMARY KEY, data BLOB)")
            connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
            connection.executemany(
                "INSERT INTO blobs VALUES(?, ?)",
                [
                    (user_id.hex(), user_message),
                    (turn_id.hex(), turn),
                    (
                        system_id.hex(),
                        json.dumps(
                            {"role": "system", "content": "private system instruction"}
                        ).encode(),
                    ),
                    (root_id, root),
                ],
            )
            connection.execute(
                "INSERT INTO meta VALUES('0', ?)",
                (json.dumps({"latestRootBlobId": root_id}).encode().hex(),),
            )
        (directory / "meta.json").write_text(
            json.dumps(
                {
                    "createdAtMs": 1786096800000,
                    "updatedAtMs": 1786096800000,
                    "title": "private",
                }
            ),
            encoding="utf-8",
        )
        timestamp = datetime(2026, 8, 7, 12, 0, tzinfo=TEST_ZONE).timestamp()
        os.utime(store, (timestamp, timestamp))

        result = collect_cursor_cli(self.context)

        self.assertEqual(result.prompts[DAY], 2)
        self.assertEqual(result.coverage.status, "partial")
        with self.database.connect() as connection:
            snapshot = connection.execute(
                "SELECT total, source_key FROM source_snapshots WHERE harness='Cursor CLI'"
            ).fetchone()
            daily = connection.execute(
                "SELECT count, source_key FROM source_daily_counts WHERE harness='Cursor CLI'"
            ).fetchone()
        self.assertEqual(snapshot["total"], 2)
        self.assertEqual(daily["count"], 2)
        self.assertNotIn("session-1", snapshot["source_key"])
        self.assertNotIn("private", str(snapshot) + str(daily))

    def test_cursor_cli_accumulates_post_baseline_prompt_deltas(self) -> None:
        directory = self.home / ".cursor/chats/workspace/session-old"
        directory.mkdir(parents=True)
        store = directory / "store.db"
        user_ids = [bytes.fromhex(value * 32) for value in ("11", "22", "33")]
        turn_ids = [bytes.fromhex(value * 32) for value in ("44", "55", "66")]
        root_id = "77" * 32
        with sqlite3.connect(store) as connection:
            connection.execute("CREATE TABLE blobs (id TEXT PRIMARY KEY, data BLOB)")
            connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
            for user_id, turn_id in zip(user_ids, turn_ids, strict=True):
                connection.execute(
                    "INSERT INTO blobs VALUES(?, ?)",
                    (user_id.hex(), protobuf_bytes(1, b"private")),
                )
                connection.execute(
                    "INSERT INTO blobs VALUES(?, ?)",
                    (turn_id.hex(), protobuf_bytes(1, protobuf_bytes(1, user_id))),
                )
            connection.execute(
                "INSERT INTO blobs VALUES(?, ?)",
                (root_id, protobuf_bytes(8, turn_ids[0])),
            )
            connection.execute(
                "INSERT INTO meta VALUES('0', ?)",
                (json.dumps({"latestRootBlobId": root_id}).encode().hex(),),
            )
        (directory / "meta.json").write_text(
            json.dumps(
                {
                    "createdAtMs": 1783425600000,
                    "updatedAtMs": 1786104000000,
                }
            ),
            encoding="utf-8",
        )
        timestamp = datetime(2026, 8, 7, 14, 0, tzinfo=TEST_ZONE).timestamp()
        os.utime(store, (timestamp, timestamp))

        baseline = collect_cursor_cli(self.context)
        with sqlite3.connect(store) as connection:
            connection.execute(
                "UPDATE blobs SET data=? WHERE id=?",
                (protobuf_bytes(8, turn_ids[0]) + protobuf_bytes(8, turn_ids[1]), root_id),
            )
        second = collect_cursor_cli(self.context)
        with sqlite3.connect(store) as connection:
            connection.execute(
                "UPDATE blobs SET data=? WHERE id=?",
                (
                    protobuf_bytes(8, turn_ids[0])
                    + protobuf_bytes(8, turn_ids[1])
                    + protobuf_bytes(8, turn_ids[2]),
                    root_id,
                ),
            )
        third = collect_cursor_cli(self.context)

        self.assertEqual(baseline.prompts.get(DAY, 0), 0)
        self.assertEqual(second.prompts[DAY], 1)
        self.assertEqual(third.prompts[DAY], 2)

    def test_cursor_cli_counts_acp_sessions_from_separate_store(self) -> None:
        directory = self.home / ".cursor/acp-sessions/acp-session-1"
        directory.mkdir(parents=True)
        store = directory / "store.db"
        user_id = bytes.fromhex("aa" * 32)
        turn_id = bytes.fromhex("bb" * 32)
        root_id = "cc" * 32
        with sqlite3.connect(store) as connection:
            connection.execute("CREATE TABLE blobs (id TEXT PRIMARY KEY, data BLOB)")
            connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
            connection.executemany(
                "INSERT INTO blobs VALUES(?, ?)",
                [
                    (user_id.hex(), protobuf_bytes(1, b"private acp instruction")),
                    (turn_id.hex(), protobuf_bytes(1, protobuf_bytes(1, user_id))),
                    (root_id, protobuf_bytes(8, turn_id)),
                ],
            )
            connection.execute(
                "INSERT INTO meta VALUES('0', ?)",
                (json.dumps({"latestRootBlobId": root_id}).encode().hex(),),
            )
        (directory / "meta.json").write_text(
            json.dumps({"schemaVersion": 1, "title": "private"}),
            encoding="utf-8",
        )
        timestamp = datetime(2026, 8, 7, 12, 0, tzinfo=TEST_ZONE).timestamp()
        os.utime(store, (timestamp, timestamp))

        result = collect_cursor_cli(self.context)

        self.assertEqual(result.session_counts()[DAY], 1)
        self.assertEqual(result.prompts[DAY], 1)
        self.assertIn("ACP", result.coverage.detail)
        with self.database.connect() as connection:
            snapshot = connection.execute(
                "SELECT total, source_key FROM source_snapshots WHERE harness='Cursor CLI'"
            ).fetchone()
        self.assertEqual(snapshot["total"], 1)
        self.assertNotIn("acp-session-1", snapshot["source_key"])
        self.assertNotIn("private", str(snapshot))

    def test_opencode_reads_sqlite_roles_without_storing_text(self) -> None:
        path = self.home / ".local/share/opencode/opencode.db"
        path.parent.mkdir(parents=True)
        with sqlite3.connect(path) as connection:
            connection.execute(
                """
                CREATE TABLE session (
                    id TEXT PRIMARY KEY,
                    time_created INTEGER,
                    time_updated INTEGER,
                    title TEXT,
                    directory TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE message (
                    id TEXT PRIMARY KEY,
                    session_id TEXT,
                    time_created INTEGER,
                    time_updated INTEGER,
                    data TEXT
                )
                """
            )
            connection.execute(
                "INSERT INTO session VALUES(?, ?, ?, ?, ?)",
                ("ses_test1", 1786104000000, 1786104060000, "private title", "/secret/path"),
            )
            connection.executemany(
                "INSERT INTO message VALUES(?, ?, ?, ?, ?)",
                [
                    (
                        "msg-user",
                        "ses_test1",
                        1786104000000,
                        1786104000000,
                        json.dumps({"role": "user", "time": {"created": 1786104000000}}),
                    ),
                    (
                        "msg-assistant",
                        "ses_test1",
                        1786104060000,
                        1786104060000,
                        json.dumps({"role": "assistant", "time": {"created": 1786104060000}}),
                    ),
                    (
                        "msg-system",
                        "ses_test1",
                        1786104120000,
                        1786104120000,
                        json.dumps({"role": "system", "time": {"created": 1786104120000}}),
                    ),
                ],
            )

        result = collect_opencode(self.context)

        self.assertEqual(result.session_counts()[DAY], 1)
        self.assertEqual(result.prompts[DAY], 2)
        self.assertEqual(result.coverage.status, "full")
        self.assertNotIn("private", result.coverage.detail)
        self.assertNotIn("ses_test1", result.coverage.detail)

    def test_hermes_opencode_gemini_and_amp_native_stores(self) -> None:
        hermes = self.home / ".hermes/state.db"
        hermes.parent.mkdir(parents=True)
        with sqlite3.connect(hermes) as connection:
            connection.execute(
                "CREATE TABLE sessions (id TEXT, started_at REAL, system_prompt TEXT)"
            )
            connection.execute(
                "CREATE TABLE messages (session_id TEXT, role TEXT, timestamp REAL)"
            )
            connection.execute(
                "INSERT INTO sessions VALUES(?, ?, ?)",
                (
                    "h1",
                    datetime.fromisoformat(STAMP.replace("Z", "+00:00")).timestamp(),
                    "private system instruction",
                ),
            )
            connection.executemany(
                "INSERT INTO messages VALUES(?, ?, ?)",
                [
                    ("h1", "user", datetime.fromisoformat(STAMP.replace("Z", "+00:00")).timestamp()),
                    ("h1", "assistant", datetime.fromisoformat(STAMP.replace("Z", "+00:00")).timestamp()),
                ],
            )
        open_message = self.home / ".local/share/opencode/storage/message/s1/m1.json"
        open_message.parent.mkdir(parents=True)
        open_message.write_text(
            json.dumps(
                {
                    "sessionID": "s1",
                    "role": "user",
                    "time": {"created": 1786104000000},
                }
            ),
            encoding="utf-8",
        )
        gemini = self.home / ".gemini/tmp/project/chats/session-one.json"
        gemini.parent.mkdir(parents=True)
        gemini.write_text(
            json.dumps(
                {
                    "sessionId": "g1",
                    "messages": [{"type": "user", "timestamp": STAMP, "content": "go"}],
                }
            ),
            encoding="utf-8",
        )
        qwen = self.home / ".qwen/tmp/project/chats/session-one.json"
        qwen.parent.mkdir(parents=True)
        qwen.write_text(
            json.dumps(
                {
                    "sessionId": "q1",
                    "messages": [{"type": "developer", "timestamp": STAMP, "content": "go"}],
                }
            ),
            encoding="utf-8",
        )
        for path in (open_message, gemini, qwen):
            timestamp = datetime(2026, 8, 7, 13, 0, tzinfo=TEST_ZONE).timestamp()
            os.utime(path, (timestamp, timestamp))
        amp = self.home / ".amp/bin/amp"
        amp.parent.mkdir(parents=True, exist_ok=True)
        amp.write_text(
            """#!/bin/sh
case "$*" in
  *"threads list"*)
    printf '%s\\n' 'Title  Last Updated  Visibility  Messages  Thread ID'
    printf '%s\\n' 'Private  now  private  2  T-test'
    ;;
  *"threads export T-test"*)
    printf '%s\\n' '{"id":"T-test","created":1786104000000,"updatedAt":"2026-08-07T14:00:00Z","messages":[{"messageId":"one","role":"user","content":[{"type":"text","text":"private"}],"meta":{"sentAt":1786104000000}},{"messageId":"two","role":"developer","content":"private","meta":{"sentAt":1786107600000}}]}'
    ;;
  *) exit 2 ;;
esac
""",
            encoding="utf-8",
        )
        amp.chmod(0o700)
        login = self.home / ".local/share/amp/session.json"
        login.parent.mkdir(parents=True, exist_ok=True)
        login.write_text("{}\n", encoding="utf-8")
        amp_result = collect_amp(self.context)

        self.assertEqual(collect_hermes(self.context).prompts[DAY], 2)
        self.assertEqual(collect_opencode(self.context).prompts[DAY], 1)
        self.assertEqual(collect_gemini(self.context).prompts[DAY], 1)
        self.assertEqual(collect_qwen(self.context).prompts[DAY], 1)
        self.assertEqual(amp_result.session_counts()[DAY], 1)
        self.assertEqual(amp_result.prompts[DAY], 2)
        self.assertEqual(amp_result.coverage.status, "full")

    def test_amp_does_not_run_cli_without_local_login(self) -> None:
        marker = self.root / "amp-ran"
        amp = self.home / ".amp/bin/amp"
        amp.parent.mkdir(parents=True)
        amp.write_text(
            f"#!/bin/sh\nprintf ran > '{marker}'\nexit 0\n",
            encoding="utf-8",
        )
        amp.chmod(0o700)

        result = collect_amp(self.context)

        self.assertEqual(result.coverage.status, "unavailable")
        self.assertFalse(marker.exists())
        self.assertEqual(result.session_counts(), {})

    def test_git_counts_unique_local_commit_from_reflog(self) -> None:
        repo = self.code / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Local"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "local@example.test"],
            check=True,
        )
        (repo / "file.txt").write_text("one\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "file.txt"], check=True)
        environment = os.environ.copy()
        environment["GIT_AUTHOR_DATE"] = "2026-08-07T12:00:00+02:00"
        environment["GIT_COMMITTER_DATE"] = "2026-08-07T12:00:00+02:00"
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", "local"],
            check=True,
            env=environment,
        )

        result = collect_commits(self.context)

        self.assertEqual(result.counts[DAY], 1)
        self.assertEqual(result.coverage.status, "full")

    def test_database_is_monotonic_and_delivery_claim_is_idempotent(self) -> None:
        harness = HarnessResult("Codex")
        harness.add_session(DAY, "s1")
        harness.add_prompt(DAY, 2)
        harness.coverage = Coverage("Codex", True, "full")
        collection = Collection(
            DAY,
            DAY,
            CommitResult({DAY: 3}, Coverage("Git", True, "full")),
            (harness,),
        )
        now = datetime(2026, 8, 8, 8, 0, tzinfo=TEST_ZONE)
        self.database.store_collection(collection, now)
        empty = HarnessResult("Codex")
        empty.coverage = Coverage("Codex", True, "full")
        self.database.store_collection(
            Collection(DAY, DAY, CommitResult({}, Coverage("Git", True, "full")), (empty,)),
            now,
        )

        rows = self.database.series(DAY, DAY)
        counts = {(row["metric"], row["harness"]): row["count"] for row in rows}
        self.assertEqual(counts[("commits", "git")], 3)
        self.assertEqual(counts[("prompts", "Codex")], 2)
        self.assertTrue(self.database.begin_delivery(DAY, now))
        self.assertFalse(self.database.begin_delivery(DAY, now))
        self.database.finish_delivery(DAY, now, sent=True)
        self.assertTrue(self.database.is_sent(DAY))

    def test_linear_trend_is_a_straight_least_squares_line(self) -> None:
        self.assertEqual(_linear_trend([1, 3, 5]), [1.0, 3.0, 5.0])
        self.assertEqual(_linear_trend([4, 4, 4]), [4.0, 4.0, 4.0])
        self.assertEqual(_linear_trend([]), [])

    def test_report_contains_only_aggregates_and_mock_sends_three_files(self) -> None:
        sensitive = "never-send-this-prompt"
        harness = HarnessResult("Codex")
        harness.add_session(DAY, "private-session-id")
        harness.add_prompt(DAY, 4)
        harness.coverage = Coverage("Codex", True, "full")
        self.database.store_collection(
            Collection(
                DAY,
                DAY,
                CommitResult({DAY: 2}, Coverage("Git", True, "full")),
                (harness,),
            ),
            datetime(2026, 8, 8, 8, 0, tzinfo=TEST_ZONE),
        )

        report = build_report(self.database, DAY)
        serialized = json.dumps(
            {"content": report.content, "charts": [chart.config for chart in report.charts]}
        )
        delivered = mock_delivery(report)

        self.assertNotIn(sensitive, serialized)
        self.assertNotIn("private-session-id", serialized)
        self.assertEqual(len(report.charts), 3)
        self.assertEqual(len(report.charts[0].config["data"]["labels"]), DEFAULT_REPORT_DAYS)
        for chart in report.charts:
            self.assertIn("last 90 days", chart.config["options"]["plugins"]["title"]["text"])
            self.assertTrue(chart.config["options"]["plugins"]["legend"]["display"])
            self.assertEqual(chart.config["options"]["plugins"]["legend"]["position"], "top")
            trend = chart.config["data"]["datasets"][-1]
            self.assertEqual(trend["label"], "Long-term trend")
            self.assertEqual(trend["type"], "line")
            self.assertEqual(trend["borderColor"], TREND_COLOR)
            self.assertEqual(trend["borderDash"], [10, 8])
            self.assertEqual(trend["pointRadius"], 0)
            self.assertEqual(trend["tension"], 0)
            self.assertEqual(trend["lineTension"], 0)
            self.assertNotIn("order", trend)
            self.assertEqual(len(trend["data"]), DEFAULT_REPORT_DAYS)
        for chart in report.charts[1:]:
            self.assertEqual(chart.config["type"], "bar")
            self.assertTrue(chart.config["options"]["scales"]["x"]["stacked"])
            self.assertTrue(chart.config["options"]["scales"]["y"]["stacked"])
        self.assertEqual(delivered["attachments"], [
            "1-commits.png",
            "2-sessions.png",
            "3-prompts.png",
        ])

    def test_renderer_requests_the_90_day_full_hd_style(self) -> None:
        report = build_report(self.database, DAY)

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self) -> bytes:
                return MOCK_PNG

        with mock.patch(
            "agentic_productivity.reporting.urllib.request.urlopen",
            return_value=Response(),
        ) as urlopen:
            image = render_chart(report.charts[0])

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(image, MOCK_PNG)
        self.assertEqual(payload["version"], "4")
        self.assertEqual(payload["width"], CHART_WIDTH)
        self.assertEqual(payload["height"], CHART_HEIGHT)
        self.assertEqual(payload["devicePixelRatio"], 1)
        self.assertEqual(payload["backgroundColor"], "#0F172A")

    def test_installer_dry_run_and_mock_cli_need_no_secret_or_network(self) -> None:
        environment = os.environ.copy()
        environment["HOME"] = str(self.home)
        environment["CORRAL_PRODUCTIVITY_HOME"] = str(self.home)
        environment["CORRAL_PRODUCTIVITY_STATE_DIR"] = str(self.root / "runtime-state")
        environment["CORRAL_PRODUCTIVITY_CODE_ROOT"] = str(self.code)
        dry_run = subprocess.run(
            [str(PRODUCTIVITY_ROOT / "install.sh"), "--dry-run"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            check=False,
        )
        mocked = subprocess.run(
            [
                str(PRODUCTIVITY_ROOT / "bin/agentic-productivity"),
                "mock",
                "--date",
                DAY.isoformat(),
                "--days",
                "1",
                "--json",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            check=False,
            timeout=30,
        )

        self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
        self.assertNotIn("Schedule:", dry_run.stdout)
        self.assertEqual(mocked.returncode, 0, mocked.stderr)
        body = json.loads(mocked.stdout)
        self.assertEqual(body["status"], "mock-delivered")
        self.assertFalse(body["network"])
        self.assertEqual(len(body["attachments"]), 3)

    def test_installer_is_idempotent_and_plist_contains_no_secret(self) -> None:
        app = self.root / "installed/app"
        state = self.root / "installed/state"
        launch_agents = self.root / "installed/LaunchAgents"
        logs = self.root / "installed/logs"
        environment = os.environ.copy()
        environment.update(
            {
                "HOME": str(self.home),
                "CORRAL_PRODUCTIVITY_APP_DIR": str(app),
                "CORRAL_PRODUCTIVITY_STATE_DIR": str(state),
                "CORRAL_PRODUCTIVITY_LAUNCH_AGENTS_DIR": str(launch_agents),
                "CORRAL_PRODUCTIVITY_LOG_DIR": str(logs),
            }
        )
        for _ in range(2):
            completed = subprocess.run(
                [str(PRODUCTIVITY_ROOT / "install.sh"), "--no-load"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

        plist_path = launch_agents / "com.corral.agentic-productivity.plist"
        with plist_path.open("rb") as handle:
            plist = plistlib.load(handle)
        self.assertEqual(plist["StartCalendarInterval"], {"Hour": 8, "Minute": 0})
        self.assertEqual(plist["StartInterval"], 300)
        self.assertTrue(plist["RunAtLoad"])
        self.assertEqual(plist["Umask"], 63)
        self.assertIn("--days", plist["ProgramArguments"])
        days_index = plist["ProgramArguments"].index("--days")
        self.assertEqual(plist["ProgramArguments"][days_index + 1], "90")
        self.assertNotIn("webhook", plist_path.read_text(encoding="utf-8").lower())
        self.assertTrue((app / "agentic_productivity/cli.py").is_file())
        self.assertEqual(plist_path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
