from __future__ import annotations

from datetime import date, datetime, timedelta
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

from agentic_productivity import cli
from agentic_productivity.bb import BbPlacement
from agentic_productivity.database import Database
from agentic_productivity.fingerprints import FingerprintSigner
from agentic_productivity.messages import MESSAGE_SOURCES, MessageScan, UserMessage
from agentic_productivity.model import Coverage
from agentic_productivity.reporting import build_report


class MessageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home"
        self.home.mkdir()
        self.database = Database(Path(self.temp.name) / "state/metrics.sqlite3")
        self.zone = ZoneInfo("America/Los_Angeles")
        self.now = datetime(2026, 9, 22, 12, tzinfo=self.zone)
        self.signer = FingerprintSigner(b"test-key-not-a-secret" * 2)
        self.connections = {}
        for source, directory in (("bb", ".bb"), ("cloudroom", ".gui-cloudroom")):
            root = self.home / directory
            root.mkdir()
            (root / "host-id").write_text("private-local-host")
            connection = sqlite3.connect(root / "bb.db")
            self.addCleanup(connection.close)
            self.connections[source] = connection
            connection.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE hosts(id TEXT PRIMARY KEY, destroyed_at INTEGER);
                CREATE TABLE environments(id TEXT PRIMARY KEY, host_id TEXT);
                CREATE TABLE threads(
                    id TEXT PRIMARY KEY, environment_id TEXT, execution_target TEXT,
                    created_at INTEGER, source_thread_id TEXT, archived_at INTEGER, deleted_at INTEGER
                );
                CREATE TABLE events(
                    id TEXT PRIMARY KEY, thread_id TEXT, environment_id TEXT,
                    type TEXT, created_at INTEGER, data TEXT
                );
                INSERT INTO hosts VALUES ('private-local-host', NULL), ('private-cloud-host', 123);
                INSERT INTO environments VALUES ('local', 'private-local-host'), ('remote', 'private-cloud-host');
                INSERT INTO threads VALUES
                    ('private-local-thread', 'local', 'local', 0, NULL, 1, NULL),
                    ('private-remote-thread', 'remote', 'local', 0, NULL, NULL, NULL),
                    ('private-cloud-thread', NULL, 'cloud', 0, NULL, NULL, 1),
                    ('private-unknown-thread', NULL, 'local', 0, NULL, NULL, NULL),
                    ('private-fork-thread', 'remote', 'local', 9999999999999, 'private-local-thread', NULL, NULL);
            """)
        self.environment = mock.patch.dict(os.environ, {
            "CORRAL_PRODUCTIVITY_HOME": str(self.home),
            "CORRAL_PRODUCTIVITY_STATE_DIR": str(self.database.path.parent),
            "CORRAL_PRODUCTIVITY_CODE_ROOT": str(self.home),
            "CORRAL_PRODUCTIVITY_MACHINE": "mac",
            "BB_DATA_DIR": str(self.home / ".bb"),
            "PATH": os.defpath,
            "TZ": self.zone.key,
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def event(self, source: str, identity: str, *, thread="private-local-thread", environment=None,
              stamp=None, event_type="client/turn/requested", **data) -> None:
        payload = {
            "requestId": f"private-request-{identity}", "direction": "outbound",
            "initiator": "user", "senderThreadId": None,
            "input": [{"type": "text", "text": "private prompt never retained"}],
            **data,
        }
        connection = self.connections[source]
        connection.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)", (
            identity, thread, environment, event_type,
            int((stamp or self.now).timestamp() * 1000), json.dumps(payload),
        ))
        connection.commit()

    def scan(self, *, days=2):
        return cli._observe_messages(self.database, self.home,
                                     self.now.date() - timedelta(days=days - 1), self.now.date(), self.now)

    def store(self, database, day, local, cloud, unknown=0):
        messages = tuple(
            UserMessage(self.signer.digest("fixture", day.isoformat(), location, str(i)), day, location, "bb")
            for location, count in (("local", local), ("cloud", cloud), ("unknown", unknown))
            for i in range(count)
        )
        database.store_user_messages({
            "bb": MessageScan(messages, Coverage(MESSAGE_SOURCES["bb"], True, "partial" if unknown else "full")),
            "cloudroom": MessageScan((), Coverage(MESSAGE_SOURCES["cloudroom"], False, "absent")),
        }, day, day, self.now)

    def test_real_cli_report_weights_human_messages_not_threads_without_source_writes(self):
        for i in range(2):
            self.event("bb", f"local-{i}")
        for i in range(30):
            self.event("cloudroom", f"cloud-{i}", thread="private-cloud-thread")
        self.event("bb", "unknown", thread="private-unknown-thread")
        for identity, changes in (
            ("system", {"initiator": "system"}),
            ("agent", {"initiator": "agent", "senderThreadId": "private-agent"}),
            ("delegated", {"senderThreadId": "private-agent"}),
            ("retry", {"retryOfRequestId": "private-request-local-0", "retryAttempt": 2}),
            ("continuation", {"continuationOfRequestId": "private-request-local-0"}),
            ("automated", {"systemMessageKind": "automation"}),
            ("empty", {"input": []}),
            ("setup", {"input": [{"type": "text", "text": "setup", "visibility": "agent-only"}]}),
        ):
            self.event("bb", identity, **changes)
        self.event("bb", "echo", event_type="item/completed")
        self.event("bb", "fork-copy", thread="private-fork-thread", requestId="private-request-local-0")
        self.event("cloudroom", "profile-copy", requestId="private-request-local-0")
        before = {source: "\n".join(connection.iterdump()) for source, connection in self.connections.items()}
        completed = subprocess.run([
            sys.executable, "-m", "agentic_productivity.cli", "mock", "--date", "2026-09-22", "--days", "2", "--json",
        ], text=True, capture_output=True, check=True, timeout=30)
        output = json.loads(completed.stdout)
        self.assertEqual(output["status"], "mock-delivered")
        self.assertIn("4-user-messages.png", output["attachments"])
        report = build_report(self.database, self.now.date(), days=2)
        chart = report.charts[3].config
        local, cloud = chart["data"]["datasets"]
        self.assertEqual((local["label"], cloud["label"]), ("Local", "Cloud"))
        self.assertEqual(local["data"], [None, 6.25])
        self.assertEqual(cloud["data"], [None, 93.75])
        self.assertEqual((local["borderColor"], cloud["borderColor"]), ("#60A5FA", "#34D399"))
        self.assertNotIn("subtitle", chart["options"]["plugins"])
        self.assertEqual(chart["options"]["scales"]["y"]["max"], 100)
        self.assertIn("Unknown message location: 1", report.content)
        self.assertIn("Local **2**", report.content)
        self.assertIn("Cloud **30**", report.content)
        self.assertNotIn("Open threads", report.content)
        with self.database.connect() as connection:
            dump = "\n".join(connection.iterdump())
        # Existing harness collectors legitimately store repository roots, but
        # message collection never stores native request/thread IDs or text.
        for value in (dump, completed.stdout, json.dumps(chart), report.content):
            self.assertNotIn("private-request", value)
            self.assertNotIn("private-local-thread", value)
            self.assertNotIn("private prompt", value)
        for source, connection in self.connections.items():
            self.assertEqual("\n".join(connection.iterdump()), before[source])

    def test_grouped_messages_midnight_and_historical_environment(self):
        before_midnight = datetime(2026, 9, 22, 6, 59, tzinfo=ZoneInfo("UTC"))
        after_midnight = before_midnight + timedelta(minutes=1)
        self.event("bb", "yesterday", stamp=before_midnight, environment="remote")
        self.event("bb", "groups", stamp=after_midnight, inputGroups=[
            [{"type": "text", "text": "one"}],
            [{"type": "localImage", "path": "private-image"}],
            [{"type": "text", "text": "hidden", "visibility": "agent-only"}],
        ])
        self.event("bb", "history", thread="private-remote-thread", environment="local", stamp=after_midnight)
        self.scan()
        rows = self.database.user_message_series(self.now.date()-timedelta(days=1), self.now.date())
        self.assertEqual([(r["local_count"], r["cloud_count"]) for r in rows], [(0, 1), (3, 0)])
        self.assertEqual(build_report(self.database, self.now.date(), days=2).charts[3].config["data"]["datasets"][1]["data"], [100, 0])
        # Both the 23-hour and 25-hour local days include their entire history.
        for day in (date(2026, 3, 8), date(2026, 11, 1)):
            start = datetime.combine(day, datetime.min.time(), self.zone)
            end = datetime.combine(day + timedelta(days=1), datetime.min.time(), self.zone)
            for name, stamp in (("first", start), ("last", end - timedelta(seconds=1)), ("next", end)):
                self.event("bb", f"{day}-{name}", stamp=stamp)
            cli._observe_messages(self.database, self.home, day, day, self.now)
            row = self.database.user_message_series(day, day)[0]
            self.assertEqual((row["local_count"], row["cloud_count"]), (2, 0))

    def test_schedule_refreshes_messages_even_after_report_was_sent(self):
        self.event("bb", "local")
        yesterday = self.now.date() - timedelta(days=1)
        self.database.begin_delivery(yesterday, self.now)
        self.database.finish_delivery(yesterday, self.now, sent=True)
        with (
            mock.patch.object(cli, "datetime") as clock,
            mock.patch.object(cli, "_observe_bb", return_value={}),
            mock.patch.object(cli, "_observe_cursor_cli", return_value={}),
            mock.patch.object(cli, "_import_cloud", return_value={}),
            mock.patch.object(cli, "load_webhook") as webhook,
        ):
            clock.now.return_value = self.now
            self.assertEqual(cli.main(["run", "--quiet", "--json"]), 0)
        webhook.assert_not_called()
        row = self.database.user_message_series(self.now.date(), self.now.date())[0]
        self.assertEqual((row["local_count"], row["cloud_count"]), (1, 0))
        self.assertTrue(self.database.is_sent(yesterday))

    def test_replays_deleted_history_and_missing_source_never_invent_percentages(self):
        self.event("bb", "local")
        self.event("cloudroom", "cloud", thread="private-cloud-thread")
        self.scan()
        self.scan()
        self.connections["bb"].execute("DELETE FROM events")
        self.connections["bb"].commit()
        self.scan()
        chart = lambda: build_report(self.database, self.now.date(), days=1).charts[3].config["data"]["datasets"][1]["data"]
        self.assertEqual(chart(), [50])
        root = self.home / ".gui-cloudroom"
        root.rename(self.home / "missing-profile")
        result = self.scan()
        self.assertEqual(result["sources"]["cloudroom"]["coverage"], "unavailable")
        self.assertEqual(chart(), [None])
        (self.home / "missing-profile").rename(root)
        self.connections["cloudroom"].execute("UPDATE events SET data='not json'")
        self.connections["cloudroom"].commit()
        result = self.scan()
        self.assertEqual(result["sources"]["cloudroom"]["coverage"], "error")
        self.assertEqual(chart(), [None])
        self.connections["cloudroom"].execute("DELETE FROM events")
        self.connections["cloudroom"].commit()
        self.scan()
        self.assertEqual(chart(), [50])

    def test_absent_source_unknown_location_and_overlapping_profiles(self):
        root = self.home / ".gui-cloudroom"
        root.rename(self.home / "unused-profile")
        self.event("bb", "unknown", thread="private-unknown-thread")
        self.scan()
        self.assertIsNone(build_report(self.database, self.now.date(), days=1).charts[3].config["data"]["datasets"][1]["data"][0])
        self.connections["bb"].execute("UPDATE threads SET environment_id='local' WHERE id='private-unknown-thread'")
        self.connections["bb"].commit()
        self.scan()
        self.assertEqual(self.database.user_message_series(self.now.date(), self.now.date())[0]["local_count"], 1)
        (self.home / "unused-profile").rename(root)
        with mock.patch.dict(os.environ, {"BB_DATA_DIR": str(root)}):
            result = self.scan()
        self.assertTrue(all(item["coverage"] == "error" for item in result["sources"].values()))
        self.assertIsNone(build_report(self.database, self.now.date(), days=1).charts[3].config["data"]["datasets"][1]["data"][0])

    def test_chart_window_uses_message_days_never_legacy_thread_samples(self):
        for measured, window in ((0, 14), (1, 14), (6, 14), (14, 14), (15, 30), (30, 30), (31, 90)):
            with self.subTest(measured=measured):
                database = Database(Path(self.temp.name) / f"window-{measured}/metrics.sqlite3")
                offsets = [0, 1, 3, 5, 9, 45] if measured == 6 else list(range(measured))
                for offset in offsets:
                    self.store(database, self.now.date() - timedelta(days=offset), 2, 0)
                    self.store(database, self.now.date() - timedelta(days=offset), 2, 0)
                self.store(database, self.now.date() - timedelta(days=70), 0, 0)
                self.store(database, self.now.date() - timedelta(days=71), 0, 0, 3)
                self.store(database, self.now.date() - timedelta(days=100), 1, 1)
                database.store_bb_placement(BbPlacement(0, 100, 0, Coverage("BB placement", True, "full")), self.now)
                report = build_report(database, self.now.date())
                chart = report.charts[3].config
                expected_days = [self.now.date()-timedelta(days=i) for i in reversed(range(window))]
                expected = [0 if (self.now.date()-day).days in offsets else None for day in expected_days]
                self.assertEqual(chart["data"]["labels"], [day.strftime("%b %-d") for day in expected_days])
                self.assertEqual(chart["options"]["plugins"]["title"]["text"], f"Your messages: local vs cloud -- last {window} days")
                self.assertEqual(chart["data"]["datasets"][1]["data"], expected)
                self.assertEqual(chart["data"]["datasets"][0]["data"], [100 if value is not None else None for value in expected])
                self.assertFalse(chart["data"]["datasets"][1]["spanGaps"])
                self.assertEqual(chart["data"]["datasets"][1]["cubicInterpolationMode"], "monotone")
                self.assertNotIn("subtitle", chart["options"]["plugins"])
                for other in report.charts[:3]:
                    self.assertEqual(len(other.config["data"]["labels"]), 90)
                    self.assertIn("last 90 days", other.config["options"]["plugins"]["title"]["text"])
                short = build_report(database, self.now.date(), days=7).charts[3].config
                self.assertEqual(len(short["data"]["labels"]), 7)
                self.assertIn("last 7 days", short["options"]["plugins"]["title"]["text"])


if __name__ == "__main__":
    unittest.main()
