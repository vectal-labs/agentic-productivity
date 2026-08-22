from __future__ import annotations

import os
from io import StringIO
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from agentic_productivity import installer
from agentic_productivity.local_timezone import local_timezone


class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.app = root / "app"
        self.state = root / "state"
        self.agents = root / "LaunchAgents"
        self.logs = root / "logs"
        updates = {
            "CORRAL_PRODUCTIVITY_APP_DIR": str(self.app),
            "CORRAL_PRODUCTIVITY_STATE_DIR": str(self.state),
            "CORRAL_PRODUCTIVITY_LAUNCH_AGENTS_DIR": str(self.agents),
            "CORRAL_PRODUCTIVITY_LOG_DIR": str(self.logs),
        }
        for key, value in updates.items():
            previous = os.environ.get(key)
            os.environ[key] = value
            self.addCleanup(self._restore_env, key, previous)

    def _restore_env(self, key: str, previous: str | None) -> None:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous

    def test_non_interactive_install_skips_webhook_and_test_report(self) -> None:
        with (
            mock.patch.object(installer, "configure_webhook") as configure,
            mock.patch.object(installer, "send_test_report") as send,
            mock.patch.object(installer, "detect_code_roots") as detect,
            mock.patch.object(installer, "load_agent") as load_agent,
            mock.patch("sys.stdout", StringIO()) as out,
        ):
            code = installer.run(dry_run=False, load=False, interactive=False)

        self.assertEqual(code, 0)
        configure.assert_not_called()
        send.assert_not_called()
        detect.assert_not_called()
        load_agent.assert_not_called()
        self.assertTrue((self.app / "agentic_productivity/cli.py").is_file())
        self.assertTrue((self.app / "agentic_productivity/installer.py").is_file())
        self.assertTrue((self.agents / "com.corral.agentic-productivity.plist").is_file())
        text = out.getvalue()
        self.assertIn("Installed:", text)
        self.assertNotIn("every 5 minutes", text)
        self.assertNotIn("Paste the Discord webhook", text)

    def test_dry_run_is_non_interactive_and_uses_os_timezone(self) -> None:
        with (
            mock.patch.object(installer, "local_timezone_name", return_value="Pacific/Auckland"),
            mock.patch.object(installer, "install_files") as install_files,
            mock.patch.object(installer, "configure_webhook") as configure,
            mock.patch.object(installer, "send_test_report") as send,
            mock.patch.object(installer, "detect_code_roots") as detect,
            mock.patch("sys.stdout", StringIO()) as out,
        ):
            code = installer.run(dry_run=True, load=True, interactive=True)

        self.assertEqual(code, 0)
        install_files.assert_not_called()
        configure.assert_not_called()
        send.assert_not_called()
        detect.assert_not_called()
        text = out.getvalue()
        self.assertIn("Would install app:", text)
        self.assertNotIn("08:00", text)
        self.assertNotIn("every 5 minutes", text)
        self.assertNotIn("Cursor", text)
        self.assertFalse(self.app.exists())

    def test_interactive_install_scans_and_reports_all_git_roots(self) -> None:
        home = Path(self.temporary.name) / "home"
        detection = installer.CodeRootDetection(
            (home / "Projects", home / "dev"), 23
        )
        database = mock.Mock()
        with (
            mock.patch.object(installer, "_home", return_value=home),
            mock.patch.object(installer, "_database", return_value=database),
            mock.patch.object(installer, "detect_code_roots", return_value=detection) as detect,
            mock.patch.object(installer, "install_files"),
            mock.patch.object(installer, "configure_webhook"),
            mock.patch.object(installer, "load_webhook", return_value=None),
            mock.patch("sys.stdout", StringIO()) as out,
        ):
            code = installer.run(dry_run=False, load=False, interactive=True)

        self.assertEqual(code, 0)
        detect.assert_called_once_with(home)
        database.replace_code_roots.assert_called_once()
        self.assertIn("Found 23 repos in ~/Projects, ~/dev", out.getvalue())

    def test_local_timezone_uses_the_os_zone_name(self) -> None:
        with (
            mock.patch.dict(os.environ, {}, clear=False),
            mock.patch("os.readlink", return_value="/var/db/timezone/zoneinfo/Pacific/Auckland"),
        ):
            os.environ.pop("TZ", None)
            zone = local_timezone()

        self.assertEqual(getattr(zone, "key", None), "Pacific/Auckland")

    def test_webhook_skip_requires_explicit_yes(self) -> None:
        for answer in ("", "n", "no", "skip"):
            with mock.patch("sys.stdout", StringIO()) as out:
                self.assertFalse(installer.confirm_skip_webhook(lambda _prompt, value=answer: value))
            self.assertIn("point of this tool", out.getvalue())
            self.assertIn("summary and chart data", out.getvalue())
        for answer in ("y", "yes", "Y", "YES"):
            with mock.patch("sys.stdout", StringIO()):
                self.assertTrue(installer.confirm_skip_webhook(lambda _prompt, value=answer: value))

    def test_empty_webhook_keeps_asking_until_skip_is_confirmed(self) -> None:
        asks = iter(["", "n", "", "y"])
        with mock.patch("sys.stdout", StringIO()) as out:
            result = installer.prompt_webhook(
                existing=False,
                ask=lambda _prompt: next(asks),
            )
        self.assertIsNone(result)
        self.assertIn(installer.SKIP_WARNING, out.getvalue())
        with self.assertRaises(StopIteration):
            next(asks)

    def test_uninstall_removes_app_cache_and_keeps_metrics(self) -> None:
        package = self.app / "agentic_productivity"
        cache = package / "__pycache__"
        cache.mkdir(parents=True)
        (package / "cli.py").write_text("x = 1\n", encoding="utf-8")
        (package / "local_timezone.py").write_text("x = 1\n", encoding="utf-8")
        (cache / "cli.cpython-314.pyc").write_bytes(b"pyc")
        self.agents.mkdir(parents=True)
        plist = self.agents / "com.corral.agentic-productivity.plist"
        plist.write_text("{}\n", encoding="utf-8")
        self.state.mkdir(parents=True)
        metrics = self.state / "metrics.sqlite3"
        metrics.write_bytes(b"sqlite")

        bin_dir = Path(self.temporary.name) / "fake-bin"
        bin_dir.mkdir()
        launchctl = bin_dir / "launchctl"
        launchctl.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        launchctl.chmod(0o755)

        environment = os.environ.copy()
        environment["PATH"] = f"{bin_dir}{os.pathsep}{environment.get('PATH', '')}"
        completed = subprocess.run(
            [str(Path(__file__).resolve().parents[1] / "scripts/uninstall.sh")],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertFalse(self.app.exists())
        self.assertFalse(plist.exists())
        self.assertTrue(metrics.is_file())
        self.assertEqual(metrics.read_bytes(), b"sqlite")


if __name__ == "__main__":
    unittest.main()
