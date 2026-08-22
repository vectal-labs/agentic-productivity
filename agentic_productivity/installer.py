from __future__ import annotations

import itertools
import os
import shutil
import subprocess
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

from .cli import _code_root_override, _collect, _database, _home
from .collectors import CodeRootDetection, detect_code_roots
from .local_timezone import local_timezone, local_timezone_name
from .reporting import (
    DEFAULT_REPORT_DAYS,
    build_report,
    load_webhook,
    post_discord,
    render_chart,
    store_webhook,
)

LABEL = "com.corral.agentic-productivity"
SKIP_WARNING = (
    "The daily Discord report is the point of this tool. "
    "Without a webhook, the summary and chart data are saved locally."
)
WEBHOOK_HELP = """\
Create a webhook in Discord:
  Server settings → Integrations → Webhooks → New Webhook
  Copy the webhook URL."""


def _paths() -> dict[str, Path]:
    home = Path.home()
    support = home / "Library/Application Support/Corral/Agentic Productivity"
    agents = Path(os.environ.get("CORRAL_PRODUCTIVITY_LAUNCH_AGENTS_DIR", home / "Library/LaunchAgents"))
    return {
        "source": Path(__file__).resolve().parents[1],
        "app": Path(os.environ.get("CORRAL_PRODUCTIVITY_APP_DIR", support / "app")),
        "state": Path(os.environ.get("CORRAL_PRODUCTIVITY_STATE_DIR", support)),
        "agents": agents,
        "logs": Path(os.environ.get("CORRAL_PRODUCTIVITY_LOG_DIR", home / "Library/Logs/Corral")),
        "plist": agents / f"{LABEL}.plist",
    }


def install_files(paths: dict[str, Path], python: str) -> None:
    package = paths["app"] / "agentic_productivity"
    package.mkdir(parents=True, exist_ok=True)
    for key in ("state", "agents", "logs"):
        paths[key].mkdir(parents=True, exist_ok=True)
    for src in (paths["source"] / "agentic_productivity").glob("*.py"):
        dest = package / src.name
        shutil.copy2(src, dest)
        dest.chmod(0o600)
    for directory in (paths["app"], package, paths["state"], paths["logs"]):
        directory.chmod(0o700)
    rendered = (
        (paths["source"] / "launchd" / f"{LABEL}.plist.in").read_text(encoding="utf-8")
        .replace("__PYTHON__", python)
        .replace("__APP_DIR__", str(paths["app"]))
        .replace("__OUT_LOG__", str(paths["logs"] / "agentic-productivity.out.log"))
        .replace("__ERR_LOG__", str(paths["logs"] / "agentic-productivity.err.log"))
    )
    temp = paths["agents"] / f".{LABEL}.plist.tmp"
    try:
        temp.write_text(rendered, encoding="utf-8")
        if shutil.which("plutil"):
            subprocess.run(["plutil", "-lint", str(temp)], check=True, capture_output=True)
        os.replace(temp, paths["plist"])
        paths["plist"].chmod(0o600)
    finally:
        temp.unlink(missing_ok=True)


def load_agent(plist: Path) -> None:
    if not shutil.which("launchctl"):
        return
    uid = os.getuid()
    target = f"gui/{uid}/{LABEL}"
    subprocess.run(["launchctl", "bootout", target], capture_output=True, check=False)
    # RunAtLoad starts the job at bootstrap. A kickstart -k here would kill that
    # fresh instance and block for the plist's full 60s ThrottleInterval.
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist)], check=True)


def confirm_skip_webhook(ask) -> bool:
    print(SKIP_WARNING)
    return ask("Are you sure you want to skip? [y/N] ").strip().lower() in {"y", "yes"}


def prompt_webhook(*, existing: bool, ask) -> str | None:
    print(WEBHOOK_HELP)
    if existing:
        print("A Discord webhook is already stored in Keychain.")
        while True:
            choice = ask("Keep the existing webhook, or replace it? [k/r] ").strip().lower()
            if choice in {"", "k", "keep"}:
                return None
            if choice in {"r", "replace"}:
                break
    while True:
        value = ask("Paste the Discord webhook URL (Enter to skip): ").strip()
        if value:
            return value
        if confirm_skip_webhook(ask):
            return None


def configure_webhook(ask=input) -> None:
    existing = load_webhook() is not None
    while True:
        value = prompt_webhook(existing=existing, ask=ask)
        if value is None:
            return
        try:
            store_webhook(value)
            return
        except (ValueError, RuntimeError) as error:
            print(f"Could not store webhook: {error}")
            existing = False


def send_test_report() -> None:
    home = _home()
    database = _database(home)
    now = datetime.now(local_timezone())
    report_day = now.date() - timedelta(days=1)
    _collect(
        database,
        home,
        end=report_day,
        days=DEFAULT_REPORT_DAYS,
        now=now,
        refresh_code_roots=False,
    )
    report = build_report(database, report_day, DEFAULT_REPORT_DAYS)
    webhook = load_webhook()
    if webhook is None:
        return
    post_discord(webhook, report, [render_chart(chart) for chart in report.charts])


def _paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if sys.stdout.isatty() else text


def _ok(text: str) -> None:
    print(f"  {_paint('32', '✓')} {text}")


def _step(number: int, title: str) -> None:
    print(f"\n{_paint('1', f'{number}. {title}')}")


def _display_root(root: Path, home: Path) -> str:
    try:
        relative = root.resolve().relative_to(home.resolve())
    except ValueError:
        return str(root)
    return "~" if not relative.parts else f"~/{relative}"


def _detection_summary(detection: CodeRootDetection, home: Path) -> str:
    noun = "repo" if detection.repository_count == 1 else "repos"
    if not detection.roots:
        return f"Found {detection.repository_count} {noun} in your home folder"
    locations = ", ".join(_display_root(root, home) for root in detection.roots)
    return f"Found {detection.repository_count} {noun} in {locations}"


@contextmanager
def _working(message: str) -> Iterator[None]:
    """Show what a slow step is doing: animated dots on a TTY, one plain line otherwise."""
    if not sys.stdout.isatty():
        print(f"  {message}...")
        yield
        return
    stop = threading.Event()

    def animate() -> None:
        for dots in itertools.cycle((".  ", ".. ", "...")):
            sys.stdout.write(f"\r  {message}{dots}")
            sys.stdout.flush()
            if stop.wait(0.4):
                return

    thread = threading.Thread(target=animate, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()
        sys.stdout.write("\r" + " " * (len(message) + 6) + "\r")
        sys.stdout.flush()


def is_interactive() -> bool:
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


def run(*, dry_run: bool, load: bool, interactive: bool) -> int:
    paths = _paths()
    python = os.environ.get("CORRAL_PYTHON") or sys.executable
    zone = local_timezone_name()
    if dry_run:
        print(f"Would install app: {paths['app']}")
        print(f"Would preserve state: {paths['state'] / 'metrics.sqlite3'}")
        print(f"Would install LaunchAgent: {paths['plist']}")
        return 0
    if interactive:
        print(f"\n{_paint('1', 'Agentic Productivity installer')}")
        _step(1, "Environment")
        _ok(f"Python {'.'.join(str(part) for part in sys.version_info[:3])}")
        _ok(f"Timezone {zone}")
        _step(2, "Git repositories")
        home = _home()
        override = _code_root_override()
        if override is not None:
            _ok(f"Using Git root override {_display_root(override, home)}")
        else:
            with _working("Scanning your home folder for Git repositories"):
                detection = detect_code_roots(home)
                _database(home).replace_code_roots(
                    detection.roots, datetime.now(local_timezone())
                )
            _ok(_detection_summary(detection, home))
        _step(3, "Application")
        with _working("Copying app files"):
            install_files(paths, python)
        _ok(f"Installed {paths['app']}")
        _ok(f"Preserved {paths['state'] / 'metrics.sqlite3'}")
        _step(4, "LaunchAgent")
        if load:
            with _working("Loading the launchd job"):
                load_agent(paths["plist"])
        _ok(f"{'Loaded' if load else 'Wrote'} {paths['plist']}")
        _step(5, "Discord webhook")
        configure_webhook()
        configured = load_webhook() is not None
        _ok(
            "Webhook stored in macOS Keychain"
            if configured
            else "Webhook skipped; summaries and chart data stay local"
        )
        if configured:
            _step(6, "Test report")
            try:
                with _working("Collecting metrics and sending the report to Discord"):
                    send_test_report()
                _ok("Sent a test report to Discord")
            except RuntimeError as error:
                print(f"  Test report failed: {error}. The daily report will retry at 08:00.")
        print(f"\n{_paint('1', 'Done.')}")
    else:
        install_files(paths, python)
        if load:
            load_agent(paths["plist"])
    print(f"Installed: {paths['app']}")
    print(f"LaunchAgent: {paths['plist']}")
    print(f"State: {paths['state'] / 'metrics.sqlite3'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    dry_run = False
    load = True
    for argument in argv if argv is not None else sys.argv[1:]:
        if argument == "--dry-run":
            dry_run = True
        elif argument == "--no-load":
            load = False
        else:
            print(f"install: unknown argument: {argument}", file=sys.stderr)
            return 2
    return run(dry_run=dry_run, load=load, interactive=is_interactive() and not dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
