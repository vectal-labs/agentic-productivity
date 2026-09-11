from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
import tempfile
import unittest
import subprocess
from dataclasses import replace
from unittest.mock import patch
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
    pull_snapshots,
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
        machine = patch.dict(os.environ, {"CORRAL_PRODUCTIVITY_MACHINE": "mac", "TZ": "Europe/Prague"})
        machine.start()
        self.addCleanup(machine.stop)
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

    def test_timestamp_variants_union_through_two_native_collectors(self) -> None:
        for machine in ("mac", "cloud"):
            self.database.mark_machine(machine, self.now - timedelta(days=2))
        for machine, stamp in (("mac", STAMP), ("cloud", "2026-09-12T12:00:00+00:00")):
            home = self.root / machine
            db = self.database if machine == "mac" else Database(self.root / "remote/metrics.sqlite3")
            for identity in ("copied-session", "separate-session" if machine == "cloud" else "copied-session"):
                self.write_jsonl(home / f".codex/sessions/{identity}.jsonl", [
                    {"type": "session_meta", "payload": {"id": identity}},
                    {"type": "response_item", "timestamp": stamp,
                     "payload": {"type": "message", "role": "user", "content": "same instruction"}},
                ])
            result = collect_codex(replace(self.context, home=home, database=db, machine=machine))
            self.assertEqual(result.prompts[DAY], 2 if machine == "cloud" else 1)
            db.store_harness_result(result, DAY, DAY, self.now)
            if machine == "cloud":
                snapshot = export_snapshot(db, machine=machine, slot=1, now=self.now,
                    start=DAY, end=DAY, timezone_name="Europe/Prague", coverage=[result.coverage])
                self.database.import_snapshot(snapshot, self.now)
                self.database.import_snapshot(snapshot, self.now)
        report = build_report(self.database, DAY, days=1)
        self.assertEqual(report.totals["sessions"], 2)
        self.assertEqual(report.totals["prompts"], 2)

    def test_cloud_early_scan_stays_incomplete_until_day_is_closed(self) -> None:
        for machine in ("mac", "cloud"):
            self.database.mark_machine(machine, self.now - timedelta(days=2))
        early = datetime(2026, 9, 12, 0, 5, tzinfo=ZONE)
        self.database.store_machine_coverage("cloud", DAY, [Coverage("Codex", True, "full")], early)
        self.database.mark_machine("cloud", early)
        self.assertIn("cloud incomplete", build_report(self.database, DAY, days=1).content)
        payload = export_snapshot(self.database, machine="cloud", slot=2, now=self.now,
            start=DAY, end=self.now.date(), timezone_name="Europe/Prague",
            coverage=[Coverage("Codex", True, "full")])
        self.database.import_snapshot(payload, self.now)
        self.assertNotIn("cloud incomplete", build_report(self.database, DAY, days=1).content)
        # An older catch-up snapshot must not undo the newer checkpoint.
        old = export_snapshot(self.database, machine="cloud", slot=1, now=early,
            start=DAY, end=DAY, timezone_name="Europe/Prague",
            coverage=[Coverage("Codex", True, "error")])
        self.database.import_snapshot(old, self.now)
        self.assertNotIn("cloud Codex error", build_report(self.database, DAY, days=1).content)
        cloud = next(row for row in self.database.machines() if row["machine"] == "cloud")
        self.assertEqual(cloud["last_seen_at"], self.now.isoformat())
        # A heartbeat or a scan of another day cannot close this day's coverage.
        unclosed = DAY + timedelta(days=1)
        self.database.store_machine_coverage("cloud", unclosed, [Coverage("Codex", True, "full")], self.now)
        self.database.mark_machine("cloud", self.now + timedelta(days=1))
        self.assertIn("cloud incomplete", build_report(self.database, unclosed, days=1).content)
        # Midnight is evaluated on the Mac calendar, even for UTC snapshots.
        closed = datetime.fromisoformat("2026-09-13T22:00:00+00:00")
        self.database.store_machine_coverage("cloud", unclosed, [Coverage("Codex", True, "full")], closed)
        self.database.store_machine_coverage("cloud", unclosed, [Coverage("Codex", True, "error")], self.now)
        self.database.mark_machine("cloud", early)
        self.assertNotIn("cloud incomplete", build_report(self.database, unclosed, days=1).content)
        self.assertNotIn("cloud Codex error", build_report(self.database, unclosed, days=1).content)

    def test_invalid_snapshot_is_not_a_successful_import(self) -> None:
        from agentic_productivity.cli import _import_cloud

        for machine in ("mac", "cloud"):
            self.database.mark_machine(machine, self.now - timedelta(days=2))

        incoming = self.root / "state/snapshots/incoming"
        incoming.mkdir(parents=True)
        (incoming / "cloud-1.json").write_text("{partial", encoding="utf-8")
        with patch("agentic_productivity.cli.pull_snapshots", return_value={"status": "ok"}):
            result = _import_cloud(self.database, self.now)
        self.assertEqual(result["status"], "error")
        self.assertIn("cloud collector error", build_report(self.database, self.now.date(), days=1).content)
        self.assertEqual(self.database.imported_slots("cloud"), set())
        payload = export_snapshot(self.database, machine="cloud", slot=1, now=self.now,
            start=DAY, end=DAY, timezone_name="Europe/Prague", coverage=[Coverage("Codex", True, "full")])
        malformed = []
        for field, value in (("machine", []), ("slot", True), ("start", "invalid")):
            malformed.append({**payload, field: value})
        bad_coverage = deepcopy(payload)
        bad_coverage["coverage"][0]["status"] = "invalid"
        malformed.append(bad_coverage)
        malformed.append({**payload, "machine": "mac"})
        for bad in malformed:
            with self.subTest(payload=bad):
                (incoming / "cloud-1.json").write_text(json.dumps(bad), encoding="utf-8")
                with patch("agentic_productivity.cli.pull_snapshots", return_value={"status": "ok"}):
                    self.assertEqual(_import_cloud(self.database, self.now)["status"], "error")
                self.assertEqual(self.database.imported_slots("cloud"), set())
        (incoming / "cloud-1.json").write_text(json.dumps(payload), encoding="utf-8")
        with patch("agentic_productivity.cli.pull_snapshots", return_value={"status": "ok"}):
            self.assertEqual(_import_cloud(self.database, self.now)["imported"], 1)
            self.assertEqual(_import_cloud(self.database, self.now)["replayed"], 1)
        self.assertNotIn("cloud collector error", build_report(self.database, self.now.date(), days=1).content)
        # A successful empty transport alone is not native coverage.
        self.assertIn("cloud missing", build_report(self.database, self.now.date(), days=1).content)

    def test_interrupted_snapshot_download_is_retried_atomically(self) -> None:
        state = self.root / "state"
        incoming = state / "snapshots/incoming"
        incoming.mkdir(parents=True)
        destination = incoming / "cloud-1.json"
        destination.write_text("{partial", encoding="utf-8")
        (state / "remote.json").write_text(json.dumps({"remote_snapshots": "/snapshots", "ssh": {
            "host": "example.invalid", "user": "user", "identity_file": "/key", "known_hosts": "/hosts",
        }}), encoding="utf-8")
        payload = export_snapshot(self.database, machine="cloud", slot=1, now=self.now,
            start=DAY, end=DAY, timezone_name="Europe/Prague", coverage=[Coverage("Codex", True, "full")])
        attempts = []

        def transfer(argv, **kwargs):
            if argv[0] == "ssh":
                return subprocess.CompletedProcess(argv, 0, "cloud-1.json\n", "")
            target = Path(argv[-1])
            self.assertNotEqual(target, destination)
            attempts.append(target)
            target.write_text("{partial" if len(attempts) == 1 else json.dumps(
                {**payload, "machine": "mac"} if len(attempts) == 2 else payload))
            if len(attempts) == 1:
                raise subprocess.TimeoutExpired(argv, 45)
            return subprocess.CompletedProcess(argv, 0)

        with patch("agentic_productivity.remote.subprocess.run", side_effect=transfer):
            self.assertEqual(pull_snapshots(state)["status"], "error")
            self.assertEqual(pull_snapshots(state)["status"], "error")
            self.assertEqual(pull_snapshots(state)["status"], "ok")
            self.assertEqual(pull_snapshots(state)["imported"], 0)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(json.loads(destination.read_text()), payload)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        self.assertFalse(any(incoming.glob("*.tmp")))

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
