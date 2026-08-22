from __future__ import annotations

import os
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from agentic_productivity import installer


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
            mock.patch.object(installer, "load_agent") as load_agent,
            mock.patch("sys.stdout", StringIO()) as out,
        ):
            code = installer.run(dry_run=False, load=False, interactive=False)

        self.assertEqual(code, 0)
        configure.assert_not_called()
        send.assert_not_called()
        load_agent.assert_not_called()
        self.assertTrue((self.app / "agentic_productivity/cli.py").is_file())
        self.assertTrue((self.app / "agentic_productivity/installer.py").is_file())
        self.assertTrue((self.agents / "com.corral.agentic-productivity.plist").is_file())
        text = out.getvalue()
        self.assertIn("Installed:", text)
        self.assertIn("report at 08:00", text)
        self.assertNotIn("Paste the Discord webhook", text)

    def test_dry_run_is_non_interactive_and_uses_os_timezone(self) -> None:
        with (
            mock.patch.object(installer, "local_timezone_name", return_value="Pacific/Auckland"),
            mock.patch.object(installer, "install_files") as install_files,
            mock.patch.object(installer, "configure_webhook") as configure,
            mock.patch.object(installer, "send_test_report") as send,
            mock.patch("sys.stdout", StringIO()) as out,
        ):
            code = installer.run(dry_run=True, load=True, interactive=True)

        self.assertEqual(code, 0)
        install_files.assert_not_called()
        configure.assert_not_called()
        send.assert_not_called()
        text = out.getvalue()
        self.assertIn("Would install app:", text)
        self.assertIn("report at 08:00 Pacific/Auckland", text)
        self.assertFalse(self.app.exists())

    def test_webhook_skip_requires_explicit_yes(self) -> None:
        for answer in ("", "n", "no", "skip"):
            with mock.patch("sys.stdout", StringIO()) as out:
                self.assertFalse(installer.confirm_skip_webhook(lambda _prompt, value=answer: value))
            self.assertIn("point of this tool", out.getvalue())
            self.assertIn("locally rendered PNG", out.getvalue())
        for answer in ("y", "yes", "Y", "YES"):
            with mock.patch("sys.stdout", StringIO()):
                self.assertTrue(installer.confirm_skip_webhook(lambda _prompt, value=answer: value))

    def test_empty_webhook_keeps_asking_until_skip_is_confirmed(self) -> None:
        asks = iter(["n", "y"])
        secrets = iter(["", ""])
        with mock.patch("sys.stdout", StringIO()) as out:
            result = installer.prompt_webhook(
                existing=False,
                ask=lambda _prompt: next(asks),
                secret=lambda _prompt: next(secrets),
            )
        self.assertIsNone(result)
        self.assertIn(installer.SKIP_WARNING, out.getvalue())
        with self.assertRaises(StopIteration):
            next(asks)


if __name__ == "__main__":
    unittest.main()
