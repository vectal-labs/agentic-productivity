from __future__ import annotations

from datetime import date, datetime, timedelta
from io import StringIO
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

from agentic_productivity import bb, cli
from agentic_productivity.bb import BbPlacement
from agentic_productivity.cloudroom import scan_cloudroom
from agentic_productivity.database import Database
from agentic_productivity.model import Coverage
from agentic_productivity.reporting import build_report


class BbTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.data = self.home / ".bb"
        self.data.mkdir(parents=True)
        (self.data / "host-id").write_text("private-local-id\n")
        self.database = Database(self.root / "state/metrics.sqlite3")
        self.now = datetime(2026, 8, 7, 0, 1, tzinfo=ZoneInfo("Pacific/Auckland"))
        self.bin = self.root / "bin"
        self.bin.mkdir()
        command = self.bin / "bb"
        command.write_text(
            f"#!{sys.executable}\n"
            "from pathlib import Path\nimport sys\n"
            "root = Path(__file__).parent\n"
            "if sys.argv[1:] == ['machine', 'list', '--json']:\n"
            "    print((root / 'hosts.json').read_text())\n"
            "elif sys.argv[1:] == ['thread', 'list', '--include-hidden', '--json']:\n"
            "    print((root / 'threads.json').read_text())\n"
            "else:\n    raise SystemExit(2)\n"
        )
        command.chmod(0o755)
        self.environment = mock.patch.dict(os.environ, {
            "BB_DATA_DIR": str(self.data),
            "PATH": str(self.bin) + os.pathsep + os.defpath,
            "CORRAL_PRODUCTIVITY_HOME": str(self.home),
            "CORRAL_PRODUCTIVITY_STATE_DIR": str(self.database.path.parent),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.write_fleet([])

    def write_fleet(self, threads: list[dict]) -> None:
        (self.bin / "hosts.json").write_text(json.dumps([
            {"id": "private-local-id", "name": "cloud-sounding-name", "status": "connected"},
            {"id": "private-cloud-id", "name": "macbook-sounding-name", "status": "connected"},
            {"id": "private-offline-id", "status": "disconnected"},
        ]))
        (self.bin / "threads.json").write_text(json.dumps(threads))

    def thread(self, number: int, host: str | None, **changes) -> dict:
        return {
            "id": f"private-thread-{number}", "title": "private title never stored",
            "titleFallback": "private prompt never stored", "environmentHostId": host,
            "archivedAt": None, "deletedAt": None, "visibility": "visible", "status": "idle",
            "providerId": "codex", **changes,
        }

    def sample(self, local: int | None, cloud: int | None, unknown: int | None = 0) -> BbPlacement:
        status = "unavailable" if local is None else "partial" if unknown else "full"
        return BbPlacement(local, cloud, unknown, Coverage("BB placement", True, status))

    def cloudroom_registry(self) -> sqlite3.Connection:
        root = self.home / ".gui-cloudroom"
        root.mkdir()
        (root / "host-id").write_text("private-gui-local")
        connection = sqlite3.connect(root / "bb.db")
        self.addCleanup(connection.close)
        connection.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE hosts (id TEXT PRIMARY KEY, destroyed_at INTEGER);
            CREATE TABLE environments (id TEXT PRIMARY KEY, host_id TEXT);
            CREATE TABLE threads (
                id TEXT PRIMARY KEY, execution_target TEXT, environment_id TEXT,
                visibility TEXT DEFAULT 'visible', archived_at INTEGER, deleted_at INTEGER,
                title TEXT DEFAULT 'private prompt never stored'
            );
            INSERT INTO hosts VALUES ('private-gui-local', NULL), ('private-gui-remote', NULL), ('private-gone', 123);
            INSERT INTO environments VALUES ('local', 'private-gui-local'), ('remote', 'private-gui-remote'), ('gone', 'private-gone');
            INSERT INTO threads(id, execution_target, environment_id) VALUES ('private-cloud-thread', 'cloud', NULL);
        """)
        return connection

    def test_scan_cli_counts_open_placement_and_persists_only_aggregates(self) -> None:
        local = self.thread(1, "private-local-id", providerId="cloud-codex")
        self.write_fleet([
            local, local,
            self.thread(2, "private-local-id", status="active"),
            self.thread(3, "private-cloud-id"),
            self.thread(4, "private-offline-id"),
            self.thread(5, "private-removed-id"),
            self.thread(6, "private-local-id", archivedAt=123),
            self.thread(7, "private-local-id", deletedAt=123),
            self.thread(8, "private-local-id", visibility="hidden"),
        ])
        # Existing history and delivery state survive adding the new metric.
        with self.database.connect() as connection:
            connection.execute("INSERT INTO daily_metrics VALUES ('2026-08-01', 'commits', 'git', 12, 'old')")
        self.database.begin_delivery(date(2026, 8, 1), self.now)
        self.database.finish_delivery(date(2026, 8, 1), self.now, sent=True)
        completed = subprocess.run(
            [sys.executable, "-m", "agentic_productivity.cli", "scan-bb", "--json"],
            capture_output=True, text=True, check=True, timeout=30,
        )
        result = json.loads(completed.stdout)
        self.assertEqual((result["local"], result["cloud"], result["unknown"]), (2, 2, 1))
        self.assertEqual((result["local_percent"], result["cloud_percent"]), (50, 50))
        self.assertEqual(result["coverage"], "partial")
        self.assertTrue(self.database.is_sent(date(2026, 8, 1)))
        self.assertEqual(self.database.series(date(2026, 8, 1), date(2026, 8, 1))[0]["count"], 12)
        with self.database.connect() as connection:
            dump = "\n".join(connection.iterdump())
        self.assertNotIn("private-", dump)
        self.assertNotIn("private title", dump)
        self.assertNotIn("private prompt", dump)
        self.assertNotIn(str(self.home), dump)

    def test_failed_unknown_and_empty_scans_never_invent_percentages(self) -> None:
        for payload in ('{}', '[{"id":"private-thread"}]', 'not json'):
            with self.subTest(payload=payload):
                (self.bin / "threads.json").write_text(payload)
                result = bb.scan_bb(self.home)
                self.assertEqual(result.coverage.status, "error")
                self.assertIsNone(result.local)
                self.assertIsNone(result.cloud)
        with mock.patch.object(bb, "_read_list", side_effect=subprocess.TimeoutExpired("bb", 20)):
            result = bb.scan_bb(self.home)
        self.assertEqual(result.coverage.status, "unavailable")
        self.assertIsNone(result.cloud)
        self.write_fleet([])
        empty = cli._observe_bb(self.database, self.home, self.now)
        self.assertEqual(empty["coverage"], "full")
        self.assertEqual(empty["local"], 0)
        self.assertIsNone(empty["cloud_percent"])
        self.write_fleet([self.thread(1, None)])
        unknown = cli._observe_bb(self.database, self.home, self.now + timedelta(minutes=5))
        self.assertEqual(unknown["unknown"], 1)
        self.assertIsNone(unknown["cloud_percent"])
        (self.data / "host-id").write_text("unrecognized-host")
        result = bb.scan_bb(self.home)
        self.assertEqual(result.coverage.status, "unavailable")
        (self.data / "host-id").unlink()
        self.assertEqual(bb.scan_bb(self.home).coverage.status, "unavailable")

    def test_scan_works_with_launchd_path_and_a_cli_requiring_node(self) -> None:
        self.write_fleet([self.thread(1, "private-cloud-id")])
        command = self.bin / "bb"
        command.write_text("#!/usr/bin/env node\n" + command.read_text().split("\n", 1)[1])
        node = self.bin / "node"
        node.write_text(f"#!{sys.executable}\nimport os, sys\nos.execv(sys.executable, [sys.executable, *sys.argv[1:]])\n")
        node.chmod(0o755)
        (self.home / ".local").mkdir()
        self.bin.rename(self.home / ".local/bin")
        # Emulate launchd without Node, including on Linux where /usr/bin has it.
        with mock.patch.dict(os.environ, {"PATH": str(self.root / "minimal-path")}):
            sample = bb.scan_bb(self.home)
        self.assertEqual(sample.coverage.status, "full")
        self.assertEqual(sample.cloud, 1)

    def test_daily_chart_weights_snapshots_and_keeps_missing_days_blank(self) -> None:
        save = self.database.store_bb_placement
        save(self.sample(10, 0), self.now)
        save(self.sample(2, 8), self.now + timedelta(seconds=1))  # replaces this interval
        save(self.sample(4, 6, 2), self.now + timedelta(minutes=5))
        save(self.sample(None, None, None), self.now + timedelta(minutes=10))
        save(self.sample(None, None, None), self.now + timedelta(days=1))
        save(self.sample(0, 0), self.now + timedelta(days=2))
        save(self.sample(0, 0, 2), self.now + timedelta(days=3))
        save(self.sample(8, 2), self.now + timedelta(days=5))
        end = (self.now + timedelta(days=5)).date()
        rows = self.database.bb_placement_series(self.now.date(), end)
        self.assertEqual(rows[0]["day"], "2026-08-07")  # local day, not UTC
        self.assertEqual((rows[0]["samples"], rows[0]["attempts"]), (2, 3))
        report = build_report(self.database, end, days=6)
        self.assertEqual(len(report.charts), 4)
        chart = report.charts[3]
        self.assertEqual(chart.filename, "4-bb-placement.png")
        local, cloud = chart.config["data"]["datasets"]
        self.assertEqual((local["label"], cloud["label"]), ("Local", "Cloud"))
        self.assertEqual((local["borderColor"], cloud["borderColor"]), ("#60A5FA", "#34D399"))
        self.assertEqual(local["data"], [30, None, None, None, None, 80])
        self.assertEqual(cloud["data"], [70, None, None, None, None, 20])
        self.assertFalse(cloud["spanGaps"])
        self.assertEqual(cloud["cubicInterpolationMode"], "monotone")
        self.assertEqual(chart.config["options"]["scales"]["y"]["max"], 100)
        partial = build_report(self.database, self.now.date(), days=1)
        self.assertIn("2/3 available", partial.content)
        self.assertIn("Unknown placement: 9.1%", partial.content)
        self.assertIn("Local **30.0%**", partial.content)
        self.assertIn("Cloud **70.0%**", partial.content)
        self.assertIn("2/288 daily five-minute slots sampled", partial.content)
        self.assertIn("BB-only history; Cloudroom was not measured", partial.content)
        unavailable = build_report(self.database, self.now.date() + timedelta(days=1), days=1)
        self.assertIn("no measured percentage", unavailable.content)
        self.assertIn("0/1 available", unavailable.content)

    def test_placement_chart_window_uses_unique_measured_dates(self) -> None:
        for measured, window in ((0, 14), (1, 14), (6, 14), (14, 14), (15, 30), (30, 30), (31, 90)):
            with self.subTest(measured=measured):
                database = Database(self.root / f"window-{measured}/metrics.sqlite3")
                offsets = [0, 1, 3, 5, 9, 45] if measured == 6 else list(range(measured))
                for offset in offsets:
                    stamp = self.now - timedelta(days=offset)
                    # Multiple snapshots on one day still represent one measured date.
                    for minute in (0, 5, 10):
                        database.store_bb_placement(self.sample(10, 0), stamp + timedelta(minutes=minute))
                for offset, sample in (
                    (70, self.sample(None, None, None)),
                    (71, self.sample(0, 0)),
                    (72, self.sample(0, 0, 3)),
                    (100, self.sample(5, 5)),
                ):
                    database.store_bb_placement(sample, self.now - timedelta(days=offset))
                report = build_report(database, self.now.date())
                chart = report.charts[3].config
                expected_days = [self.now.date() - timedelta(days=i) for i in reversed(range(window))]
                self.assertEqual(chart["data"]["labels"], [d.strftime("%b %-d") for d in expected_days])
                self.assertEqual(chart["options"]["plugins"]["title"]["text"],
                                 f"Open threads: local vs cloud -- last {window} days")
                expected = [0 if (self.now.date() - d).days in offsets else None for d in expected_days]
                self.assertEqual(chart["data"]["datasets"][1]["data"], expected)
                self.assertEqual(chart["data"]["datasets"][0]["data"],
                                 [100 if v is not None else None for v in expected])
                self.assertFalse(chart["data"]["datasets"][1]["spanGaps"])
                self.assertNotIn("subtitle", chart["options"]["plugins"])
                for other in report.charts[:3]:
                    self.assertEqual(len(other.config["data"]["labels"]), 90)
                    self.assertIn("last 90 days", other.config["options"]["plugins"]["title"]["text"])
                # Explicit shorter reports must not acquire extra days.
                short = build_report(database, self.now.date(), days=7).charts[3].config
                self.assertEqual(len(short["data"]["labels"]), 7)
                self.assertIn("last 7 days", short["options"]["plugins"]["title"]["text"])

    def test_cloudroom_registry_reaches_cli_chart_without_private_data_or_source_writes(self) -> None:
        connection = self.cloudroom_registry()
        connection.executescript("""
            INSERT INTO threads(id, execution_target, environment_id) VALUES
                ('private-local-thread', 'local', 'local'),
                ('private-remote-thread', 'local', 'remote'),
                ('private-unknown-thread', 'local', NULL),
                ('private-removed-thread', 'local', 'gone');
            INSERT INTO threads(id, execution_target, visibility, archived_at, deleted_at) VALUES
                ('private-hidden-thread', 'cloud', 'hidden', NULL, NULL),
                ('private-archived-thread', 'cloud', 'visible', 123, NULL),
                ('private-deleted-thread', 'cloud', 'visible', NULL, 123);
        """)
        # Keep the writer connected: real GUI updates may still be in the WAL.
        before = "\n".join(connection.iterdump())
        local = self.thread(1, "private-local-id")
        self.write_fleet([local, local])
        completed = subprocess.run(
            [sys.executable, "-m", "agentic_productivity.cli", "scan-placement", "--json"],
            capture_output=True, text=True, check=True, timeout=30,
        )
        result = json.loads(completed.stdout)
        self.assertEqual((result["local"], result["cloud"], result["unknown"]), (2, 2, 2))
        self.assertEqual(result["cloud_percent"], 50)
        self.assertEqual(result["coverage"], "partial")
        self.assertEqual(result["sources"]["cloudroom"]["coverage"], "partial")
        self.assertEqual("\n".join(connection.iterdump()), before)
        with self.database.connect() as stored:
            day = date.fromisoformat(stored.execute("SELECT day FROM bb_placement_samples").fetchone()[0])
            dump = "\n".join(stored.iterdump())
        report = build_report(self.database, day, days=1)
        self.assertEqual(report.charts[3].config["data"]["datasets"][1]["data"], [50])
        self.assertEqual(report.charts[3].config["data"]["datasets"][1]["label"], "Cloud")
        self.assertNotIn("subtitle", report.charts[3].config["options"]["plugins"])
        self.assertIn("BB + Cloudroom", report.content)
        self.assertIn("Cloudroom 1 readable", report.content)
        self.assertIn("Unknown placement: 33.3%", report.content)
        for output in (dump, completed.stdout, report.content, json.dumps(report.charts[3].config)):
            self.assertNotIn("private-", output)
            self.assertNotIn("private prompt", output)
            self.assertNotIn(str(self.home), output)

    def test_cloudroom_failures_and_missing_expected_profiles_never_become_zero(self) -> None:
        self.write_fleet([self.thread(1, "private-local-id")])
        absent = cli._observe_bb(self.database, self.home, self.now)
        self.assertEqual(absent["sources"]["cloudroom"]["coverage"], "absent")
        self.assertEqual(absent["cloud_percent"], 0)
        connection = self.cloudroom_registry()
        valid = cli._observe_bb(self.database, self.home, self.now + timedelta(minutes=5))
        self.assertEqual(valid["cloud_percent"], 50)
        connection.execute("UPDATE threads SET execution_target='unsupported'")
        connection.commit()
        invalid = cli._observe_bb(self.database, self.home, self.now + timedelta(minutes=10))
        self.assertEqual(invalid["sources"]["cloudroom"]["coverage"], "error")
        self.assertIsNone(invalid["cloud_percent"])
        connection.execute("DELETE FROM threads")
        connection.commit()
        empty = scan_cloudroom(self.home)
        self.assertEqual((empty.local, empty.cloud, empty.coverage.status), (0, 0, "full"))
        connection.execute("ALTER TABLE threads RENAME COLUMN execution_target TO unsupported")
        connection.commit()
        self.assertEqual(scan_cloudroom(self.home).coverage.status, "error")
        connection.close()
        root = self.home / ".gui-cloudroom"
        (root / "bb.db").unlink()
        missing_file = cli._observe_bb(self.database, self.home, self.now + timedelta(minutes=15))
        self.assertIsNone(missing_file["cloud_percent"])
        self.assertFalse((root / "bb.db").exists())
        root.rename(self.home / "missing-profile")
        missing_profile = cli._observe_bb(self.database, self.home, self.now + timedelta(minutes=20))
        self.assertEqual(missing_profile["sources"]["cloudroom"]["coverage"], "unavailable")
        self.assertIsNone(missing_profile["cloud_percent"])
        # A missing BB source cannot turn surviving Cloudroom observations into 100% remote either.
        (self.home / "missing-profile").rename(root)
        with mock.patch.object(cli, "scan_cloudroom", return_value=self.sample(0, 1)):
            self.data.rename(self.home / "missing-bb")
            missing_bb = cli._observe_bb(self.database, self.home, self.now + timedelta(minutes=25))
        self.assertEqual(missing_bb["sources"]["bb"]["coverage"], "unavailable")
        self.assertIsNone(missing_bb["cloud_percent"])
        with mock.patch.dict(os.environ, {"BB_DATA_DIR": str(root)}):
            overlapping = cli._observe_bb(self.database, self.home, self.now + timedelta(minutes=30))
        self.assertEqual(overlapping["sources"]["cloudroom"]["coverage"], "error")
        self.assertIn("overlap", overlapping["sources"]["cloudroom"]["detail"])
        self.assertIsNone(overlapping["cloud_percent"])

    def test_paired_history_retries_failures_and_rollout_do_not_distort_percentages(self) -> None:
        save = self.database.store_bb_placement
        save(self.sample(10, 0), self.now - timedelta(days=1))
        save(self.sample(100, 0), self.now)
        first = self.now + timedelta(minutes=5)
        save(self.sample(3, 1), first, cloudroom=self.sample(0, 2))
        save(self.sample(2, 2), first + timedelta(seconds=1), cloudroom=self.sample(0, 4))
        save(self.sample(3, 1), first, cloudroom=self.sample(0, 2))  # stale replay
        save(self.sample(100, 0), first + timedelta(minutes=5), cloudroom=self.sample(None, None, None))
        save(self.sample(100, 0), first + timedelta(days=1))  # a missing paired scan, not legacy data
        save(self.sample(0, 0), first + timedelta(days=2), cloudroom=self.sample(0, 0))
        save(self.sample(0, 0, 1), first + timedelta(days=3), cloudroom=self.sample(0, 0, 2))
        report = build_report(self.database, (first + timedelta(days=3)).date(), days=5)
        self.assertEqual(report.charts[3].config["data"]["datasets"][1]["data"], [0, 75, None, None, None])
        self.assertIn("BB-only history; BB + Cloudroom from 2026-08-07", report.content)
        rollout = build_report(self.database, first.date(), days=1)
        self.assertIn("1/2 available", rollout.content)
        self.assertIn("Cloudroom 1 readable, 0 absent, 1 unavailable", rollout.content)
        missing = build_report(self.database, (first + timedelta(days=1)).date(), days=1)
        self.assertIn("no measured percentage", missing.content)
        self.assertIn("BB + Cloudroom", missing.content)
        with self.database.connect() as connection:
            self.assertEqual(connection.execute("SELECT local_count FROM bb_placement_samples ORDER BY slot LIMIT 1").fetchone()[0], 10)
        with mock.patch("agentic_productivity.reporting.local_timezone", return_value=ZoneInfo("America/New_York")):
            for day, slots in ((date(2026, 3, 8), 276), (date(2026, 11, 1), 300)):
                with self.subTest(day=day):
                    self.assertIn(f"0/{slots} daily five-minute slots", build_report(self.database, day, days=1).content)

    def test_schedule_scans_before_morning_and_after_report_was_sent(self) -> None:
        self.write_fleet([self.thread(1, "private-cloud-id")])
        report_day = self.now.date() - timedelta(days=1)
        self.database.begin_delivery(report_day, self.now)
        self.database.finish_delivery(report_day, self.now, sent=True)
        for hour in (7, 10):
            with (
                mock.patch.object(cli, "datetime") as clock,
                mock.patch.object(cli, "_observe_cursor_cli", return_value={}),
                mock.patch.object(cli, "load_webhook") as webhook,
                mock.patch("sys.stdout", StringIO()),
            ):
                clock.now.return_value = self.now.replace(hour=hour)
                self.assertEqual(cli.main(["run", "--json"]), 0)
                webhook.assert_not_called()
        rows = self.database.bb_placement_series(self.now.date(), self.now.date())
        self.assertEqual(rows[0]["samples"], 2)
        self.assertEqual(rows[0]["cloud_count"], 2)


if __name__ == "__main__":
    unittest.main()
