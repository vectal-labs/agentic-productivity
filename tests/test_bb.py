from __future__ import annotations

from datetime import date, datetime, timedelta
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

from agentic_productivity import bb, cli
from agentic_productivity.bb import BbPlacement
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
        with mock.patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}):
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
        self.assertEqual(local["data"], [30, None, None, None, None, 80])
        self.assertEqual(cloud["data"], [70, None, None, None, None, 20])
        self.assertFalse(cloud["spanGaps"])
        self.assertEqual(cloud["cubicInterpolationMode"], "monotone")
        self.assertEqual(chart.config["options"]["scales"]["y"]["max"], 100)
        partial = build_report(self.database, self.now.date(), days=1)
        self.assertIn("2/3 available", partial.content)
        self.assertIn("Unknown placement: 9.1%", partial.content)
        self.assertIn("MacBook **30.0%**", partial.content)
        unavailable = build_report(self.database, self.now.date() + timedelta(days=1), days=1)
        self.assertIn("no measured percentage", unavailable.content)
        self.assertIn("0/1 available", unavailable.content)

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
