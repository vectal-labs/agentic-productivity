from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from agentic_productivity.collectors import CollectorContext, collect_codex, collect_omp, collect_pi
from agentic_productivity.database import Database
from agentic_productivity.fingerprints import FingerprintSigner
from agentic_productivity.model import Collection, CommitResult, Coverage, HarnessResult
from agentic_productivity.remote import (
    export_snapshot,
    payload_hash,
    validate_snapshot,
    write_snapshot,
)
from agentic_productivity.reporting import build_report


DAY = date(2026, 9, 12)
STAMP = "2026-09-12T12:00:00Z"
ZONE = ZoneInfo("Europe/Prague")
KEY = b"test-fingerprint-key-32-bytes!!"


class CloudUnionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.database = Database(self.root / "state/metrics.sqlite3")
        self.signer = FingerprintSigner(KEY)
        self.now = datetime(2026, 9, 13, 8, 0, tzinfo=ZONE)
        self.context = CollectorContext(
            home=self.home,
            code_roots=(),
            start=DAY,
            end=DAY,
            database=self.database,
            now=self.now,
            timezone=ZONE,
            signer=self.signer,
            machine="mac",
        )

    def write_jsonl(self, path: Path, rows: list[dict[str, object]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        stamp = datetime(2026, 9, 12, 18, 0, tzinfo=ZONE).timestamp()
        os.utime(path, (stamp, stamp))

    def test_copied_sessions_union_once(self) -> None:
        payload = {
            "type": "response_item",
            "timestamp": STAMP,
            "payload": {
                "id": "msg-1",
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "hello"}],
            },
        }
        self.write_jsonl(self.home / ".codex/sessions/a/session.jsonl", [payload])
        self.write_jsonl(self.home / ".codex/sessions/b/session.jsonl", [payload])
        result = collect_codex(self.context)
        self.assertEqual(result.session_counts()[DAY], 1)
        self.assertEqual(result.prompts[DAY], 1)

    def test_forked_history_keeps_original_and_counts_new_child_turn(self) -> None:
        copied = {
            "type": "message",
            "id": "user01ab",
            "timestamp": STAMP,
            "message": {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        }
        parent = self.home / ".pi/agent/sessions/project/01_parent.jsonl"
        child = self.home / ".pi/agent/sessions/project/02_fork.jsonl"
        self.write_jsonl(parent, [{"type": "session", "id": "parent00", "timestamp": STAMP}, copied])
        self.write_jsonl(
            child,
            [
                {"type": "session", "id": "fork0000", "timestamp": STAMP, "parentSession": "parent00"},
                copied,
                {
                    "type": "message",
                    "id": "user02cd",
                    "timestamp": "2026-09-12T13:00:00Z",
                    "message": {"role": "user", "content": [{"type": "text", "text": "again"}]},
                },
            ],
        )
        result = collect_pi(self.context)
        self.assertEqual(result.session_counts()[DAY], 2)
        self.assertEqual(result.prompts[DAY], 2)

    def test_distinct_child_turns_remain_separate_sessions(self) -> None:
        parent = self.home / ".omp/agent/sessions/parent.jsonl"
        child = self.home / ".omp/agent/sessions/child.jsonl"
        self.write_jsonl(
            parent,
            [
                {"type": "session", "id": "parent-id", "timestamp": STAMP},
                {
                    "type": "message",
                    "id": "p1",
                    "timestamp": STAMP,
                    "message": {"role": "user", "content": "parent"},
                },
            ],
        )
        self.write_jsonl(
            child,
            [
                {"type": "session", "id": "child-id", "timestamp": STAMP},
                {
                    "type": "session_init",
                    "id": "c1",
                    "timestamp": STAMP,
                    "task": "child task",
                },
            ],
        )
        result = collect_omp(self.context)
        self.assertEqual(result.session_counts()[DAY], 2)
        self.assertEqual(result.prompts[DAY], 2)

    def test_counter_baselines_do_not_add_replicated_totals(self) -> None:
        self.database.mark_machine("mac", datetime(2026, 9, 11, 12, 0, tzinfo=ZONE))
        mac = HarnessResult("Cursor CLI", signer=self.signer)
        cloud = HarnessResult("Cursor CLI", signer=self.signer)
        for result in (mac, cloud):
            result.coverage = Coverage("Cursor CLI", True, "partial", "counter baseline")
            for ordinal in range(1, 6):
                result.add_prompt(DAY, session_id="native-session", ordinal=ordinal)
        self.database.store_collection(
            Collection(DAY, DAY, CommitResult({}, Coverage("Git", True, "absent")), (mac,)),
            self.now,
        )
        other = Database(self.root / "cloud-counter/metrics.sqlite3")
        other.store_collection(
            Collection(DAY, DAY, CommitResult({}, Coverage("Git", True, "absent")), (cloud,)),
            self.now,
        )
        payload = export_snapshot(
            other,
            machine="cloud",
            slot=21,
            now=self.now,
            start=DAY,
            end=DAY,
            timezone_name="Europe/Prague",
            coverage=[cloud.coverage],
        )
        self.database.import_snapshot(payload, self.now)
        rows = {
            (row["metric"], row["harness"]): row["count"]
            for row in self.database.series(DAY, DAY)
        }
        self.assertEqual(rows[("prompts", "Cursor CLI")], 5)

    def test_snapshot_replay_does_not_change_counts(self) -> None:
        mac = HarnessResult("Codex", signer=self.signer)
        mac.add_session(DAY, "native-a")
        mac.add_prompt(DAY, entry_id="m1", timestamp=STAMP, session_id="native-a", role="user", content="hi")
        mac.coverage = Coverage("Codex", True, "full")
        self.database.mark_machine("mac", self.now)
        self.database.store_collection(
            Collection(DAY, DAY, CommitResult({}, Coverage("Git", True, "absent")), (mac,)),
            self.now,
        )
        payload = export_snapshot(
            self.database,
            machine="cloud",
            slot=100,
            now=self.now,
            start=DAY,
            end=DAY,
            timezone_name="Europe/Prague",
            coverage=[Coverage("Codex", True, "full")],
        )
        first = self.database.import_snapshot(payload, self.now)
        second = self.database.import_snapshot(payload, self.now)
        self.assertEqual(first["status"], "imported")
        self.assertEqual(second["status"], "replayed")
        rows = self.database.series(DAY, DAY)
        counts = {(row["metric"], row["harness"]): row["count"] for row in rows}
        self.assertEqual(counts[("sessions", "Codex")], 1)
        self.assertEqual(counts[("prompts", "Codex")], 1)

    def test_missing_cloud_coverage_is_not_a_confirmed_zero(self) -> None:
        self.database.mark_machine("mac", datetime(2026, 9, 11, 12, 0, tzinfo=ZONE))
        self.database.mark_machine("cloud", datetime(2026, 9, 11, 12, 0, tzinfo=ZONE))
        mac = HarnessResult("Codex", signer=self.signer)
        mac.add_session(DAY, "native-a")
        mac.add_prompt(DAY, entry_id="m1", timestamp=STAMP, session_id="native-a", role="user", content="hi")
        mac.coverage = Coverage("Codex", True, "full")
        self.database.store_collection(
            Collection(DAY, DAY, CommitResult({DAY: 1}, Coverage("Git", True, "full")), (mac,)),
            self.now,
        )
        report = build_report(self.database, DAY, days=1)
        serialized = json.dumps({"content": report.content, "charts": [chart.config for chart in report.charts]})
        self.assertIn("cloud missing", report.content)
        self.assertEqual(report.totals["sessions"], 1)
        self.assertNotIn(KEY.hex(), serialized)
        self.assertNotIn("native-a", serialized)

    def test_timezone_boundaries_use_mac_calendar(self) -> None:
        auckland = CollectorContext(
            home=self.home,
            code_roots=(),
            start=date(2026, 9, 13),
            end=date(2026, 9, 13),
            database=self.database,
            now=self.now,
            timezone=ZoneInfo("Pacific/Auckland"),
            signer=self.signer,
        )
        self.write_jsonl(
            self.home / ".codex/sessions/z/session.jsonl",
            [
                {
                    "type": "response_item",
                    "timestamp": "2026-09-12T12:30:00Z",
                    "payload": {"id": "msg-z", "type": "message", "role": "user", "content": []},
                }
            ],
        )
        prague = collect_codex(self.context)
        later = collect_codex(auckland)
        self.assertEqual(prague.session_counts()[DAY], 1)
        self.assertEqual(later.session_counts()[date(2026, 9, 13)], 1)
        self.assertEqual(
            next(iter(prague.session_ids[DAY])),
            next(iter(later.session_ids[date(2026, 9, 13)])),
        )

    def test_historical_totals_stay_mac_only_before_combined_start(self) -> None:
        old = date(2026, 9, 10)
        harness = HarnessResult("Codex")
        harness.add_session(old, "raw-session")
        harness.add_prompt(old, 4)
        harness.coverage = Coverage("Codex", True, "full")
        self.database.store_collection(
            Collection(old, old, CommitResult({old: 9}, Coverage("Git", True, "full")), (harness,)),
            datetime(2026, 9, 11, 8, 0, tzinfo=ZONE),
        )
        self.database.mark_machine("mac", datetime(2026, 9, 11, 15, 0, tzinfo=ZONE))
        self.database.mark_machine("cloud", datetime(2026, 9, 11, 16, 0, tzinfo=ZONE))
        self.assertEqual(self.database.combined_reporting_start(), date(2026, 9, 12))
        signed = HarnessResult("Codex", signer=self.signer)
        signed.add_session(old, "raw-session")
        signed.add_prompt(old, entry_id="old", timestamp="2026-09-10T12:00:00Z", session_id="raw-session", role="user", content="x")
        signed.coverage = Coverage("Codex", True, "full")
        self.database.store_collection(
            Collection(old, old, CommitResult({old: 1}, Coverage("Git", True, "full")), (signed,)),
            self.now,
        )
        rows = {(row["metric"], row["harness"]): row["count"] for row in self.database.series(old, old)}
        self.assertEqual(rows[("commits", "git")], 9)
        self.assertEqual(rows[("prompts", "Codex")], 4)
        report = build_report(self.database, DAY, days=3)
        self.assertIn("History through 2026-09-11 is Mac-only.", report.content)

    def test_offline_catch_up_imports_missed_slots_once(self) -> None:
        self.database.mark_machine("mac", datetime(2026, 9, 11, 12, 0, tzinfo=ZONE))
        self.database.mark_machine("cloud", datetime(2026, 9, 11, 12, 0, tzinfo=ZONE))
        mac = HarnessResult("Codex", signer=self.signer)
        mac.coverage = Coverage("Codex", True, "full")
        self.database.store_collection(
            Collection(DAY, DAY, CommitResult({}, Coverage("Git", True, "absent")), (mac,)),
            self.now,
        )
        cloud = FingerprintSigner(KEY)
        first = HarnessResult("Codex", signer=cloud)
        first.add_session(DAY, "native-a")
        first.add_prompt(DAY, entry_id="m1", timestamp=STAMP, session_id="native-a", role="user", content="hi")
        first.coverage = Coverage("Codex", True, "full")
        extra = HarnessResult("Codex", signer=cloud)
        extra.add_session(DAY, "native-b")
        extra.add_prompt(DAY, entry_id="m2", timestamp="2026-09-12T13:00:00Z", session_id="native-b", role="user", content="two")
        extra.coverage = Coverage("Codex", True, "full")
        other = Database(self.root / "cloud/metrics.sqlite3")
        other.store_collection(
            Collection(DAY, DAY, CommitResult({}, Coverage("Git", True, "absent")), (first,)),
            self.now,
        )
        slot_one = export_snapshot(
            other, machine="cloud", slot=10, now=self.now, start=DAY, end=DAY,
            timezone_name="Europe/Prague", coverage=[first.coverage],
        )
        other.store_collection(
            Collection(DAY, DAY, CommitResult({}, Coverage("Git", True, "absent")), (extra,)),
            self.now + timedelta(minutes=5),
        )
        slot_two = export_snapshot(
            other, machine="cloud", slot=11, now=self.now + timedelta(minutes=5),
            start=DAY, end=DAY, timezone_name="Europe/Prague", coverage=[extra.coverage],
        )
        self.database.import_snapshot(slot_two, self.now)
        self.database.import_snapshot(slot_one, self.now)
        self.database.import_snapshot(slot_two, self.now)
        rows = {(row["metric"], row["harness"]): row["count"] for row in self.database.series(DAY, DAY)}
        self.assertEqual(rows[("sessions", "Codex")], 2)
        self.assertEqual(rows[("prompts", "Codex")], 2)
        self.assertEqual(self.database.imported_slots("cloud"), {10, 11})
        validate_snapshot(slot_one)
        self.assertTrue(payload_hash(slot_one))
        with self.assertRaises(ValueError):
            validate_snapshot({**slot_one, "session_id": "raw"})
        path = write_snapshot(self.root / "state", slot_one)
        self.assertTrue(path.is_file())

    def test_successful_pull_clears_cloud_transport_error(self) -> None:
        from unittest.mock import patch

        from agentic_productivity.cli import _import_cloud

        report_day = self.now.date()
        self.database.mark_machine("mac", datetime(2026, 9, 11, 12, 0, tzinfo=ZONE))
        self.database.mark_machine("cloud", datetime(2026, 9, 11, 12, 0, tzinfo=ZONE))
        self.database.store_machine_coverage(
            "cloud",
            report_day,
            [Coverage("cloud collector", True, "error", "cloud SSH listing failed")],
            self.now,
        )
        failed = build_report(self.database, report_day, days=1)
        self.assertIn("cloud cloud collector error", failed.content)
        payload = export_snapshot(
            self.database,
            machine="cloud",
            slot=30,
            now=self.now,
            start=report_day,
            end=report_day,
            timezone_name="Europe/Prague",
            coverage=[Coverage("Codex", True, "full")],
        )
        incoming = self.root / "state/snapshots/incoming"
        incoming.mkdir(parents=True)
        (incoming / "cloud-30.json").write_text(json.dumps(payload), encoding="utf-8")
        with patch(
            "agentic_productivity.cli.pull_snapshots",
            return_value={"status": "ok", "detail": "copied 1 new snapshots", "imported": 1},
        ):
            result = _import_cloud(self.database, self.now)
        self.assertEqual(result["status"], "ok")
        recovered = build_report(self.database, report_day, days=1)
        self.assertNotIn("cloud collector", recovered.content)


if __name__ == "__main__":
    unittest.main()
