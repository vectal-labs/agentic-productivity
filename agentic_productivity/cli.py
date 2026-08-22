from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from . import __version__
from .collectors import CollectorContext, WARSAW, collect_all, collect_cursor_cli
from .database import Database
from .reporting import (
    DEFAULT_REPORT_DAYS,
    build_report,
    load_webhook,
    mock_delivery,
    post_discord,
    render_chart,
    store_webhook,
)


def _home() -> Path:
    return Path(os.environ.get("CORRAL_PRODUCTIVITY_HOME", Path.home())).expanduser()


def _state_dir(home: Path) -> Path:
    override = os.environ.get("CORRAL_PRODUCTIVITY_STATE_DIR")
    if override:
        return Path(override).expanduser()
    return home / "Library/Application Support/Corral/Agentic Productivity"


def _database(home: Path) -> Database:
    return Database(_state_dir(home) / "metrics.sqlite3")


def _parse_day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from error


def _emit(value: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, indent=2, sort_keys=True))
        return
    for key, item in value.items():
        if isinstance(item, (dict, list)):
            print(f"{key}: {json.dumps(item, sort_keys=True)}")
        else:
            print(f"{key}: {item}")


def _collect(
    database: Database, home: Path, *, end: date, days: int, now: datetime
) -> dict[str, Any]:
    start = end - timedelta(days=days - 1)
    context = CollectorContext(
        home=home,
        code_root=Path(os.environ.get("CORRAL_PRODUCTIVITY_CODE_ROOT", home / "code")),
        start=start,
        end=end,
        database=database,
        now=now,
    )
    collection = collect_all(context)
    database.store_collection(collection, now)
    coverage = {
        collection.commits.coverage.harness: collection.commits.coverage.status,
        **{
            result.harness: result.coverage.status if result.coverage else "error"
            for result in collection.harnesses
        },
    }
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "coverage": coverage,
    }


def _observe_cursor_cli(database: Database, home: Path, now: datetime) -> dict[str, Any]:
    context = CollectorContext(
        home=home,
        code_root=Path(os.environ.get("CORRAL_PRODUCTIVITY_CODE_ROOT", home / "code")),
        start=now.date(),
        end=now.date(),
        database=database,
        now=now,
    )
    result = collect_cursor_cli(context)
    database.store_harness_result(result, now.date(), now.date(), now)
    return {
        "coverage": result.coverage.status if result.coverage else "error",
        "sessions": result.session_counts().get(now.date(), 0),
        "prompts": result.prompts.get(now.date(), 0),
    }


def _execute_report(
    database: Database,
    home: Path,
    *,
    report_day: date,
    now: datetime,
    days: int,
    force: bool,
    dry_run: bool,
    mock: bool,
) -> tuple[int, dict[str, Any]]:
    if database.is_sent(report_day) and not force and not mock and not dry_run:
        return 0, {"status": "already-sent", "report_day": report_day.isoformat()}

    collection = _collect(database, home, end=report_day, days=days, now=now)
    report = build_report(database, report_day, days)
    base = {
        "report_day": report_day.isoformat(),
        "totals": report.totals,
        "collection": collection,
    }
    if dry_run:
        return 0, {"status": "dry-run", **base}
    if mock:
        return 0, {"status": "mock-delivered", **base, **mock_delivery(report)}

    webhook = load_webhook()
    if webhook is None:
        return 0, {"status": "webhook-not-configured", **base}
    if not database.begin_delivery(report_day, now, force=force):
        return 0, {"status": "already-sending-or-sent", **base}
    try:
        images = [render_chart(chart) for chart in report.charts]
        post_discord(webhook, report, images)
    except Exception as error:
        message = str(error)[:300] or "delivery failed"
        database.finish_delivery(report_day, datetime.now(WARSAW), sent=False, error=message)
        return 1, {"status": "delivery-failed", "error": message, **base}
    database.finish_delivery(report_day, datetime.now(WARSAW), sent=True)
    return 0, {"status": "sent", **base}


def _doctor(home: Path, database: Database) -> dict[str, Any]:
    commands = (
        "codex",
        "claude",
        "cursor-agent",
        "hermes",
        "pi",
        "prime-agent",
        "omp",
        "opencode",
        "droid",
        "gemini",
        "qwen",
        "amp",
    )
    state = _state_dir(home)
    code_root = Path(os.environ.get("CORRAL_PRODUCTIVITY_CODE_ROOT", home / "code"))
    state.mkdir(parents=True, exist_ok=True)
    probe = state / ".write-probe"
    writable = False
    try:
        probe.touch(exist_ok=True)
        probe.unlink(missing_ok=True)
        writable = True
    except OSError:
        pass
    return {
        "ok": writable and code_root.exists(),
        "version": __version__,
        "timezone": str(WARSAW),
        "code_root": str(code_root),
        "state_dir": str(state),
        "state_writable": writable,
        "webhook_configured": load_webhook() is not None,
        "installed_harnesses": [name for name in commands if shutil.which(name)],
        "database": database.status(),
    }


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Agentic productivity reporter")
    root.add_argument("--version", action="version", version=__version__)
    sub = root.add_subparsers(dest="command", required=True)

    collect = sub.add_parser("collect", help="collect and store aggregate metrics")
    collect.add_argument("--end", type=_parse_day)
    collect.add_argument("--days", type=int, default=DEFAULT_REPORT_DAYS)
    collect.add_argument("--json", action="store_true")

    for name, help_text in (
        ("run", "run the scheduled report"),
        ("mock", "run end-to-end without network or delivery state"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--date", type=_parse_day)
        command.add_argument("--days", type=int, default=DEFAULT_REPORT_DAYS)
        command.add_argument("--force", action="store_true")
        command.add_argument("--dry-run", action="store_true")
        command.add_argument("--json", action="store_true")
        if name == "run":
            command.add_argument("--quiet", action="store_true")

    status = sub.add_parser("status", help="show collection and delivery status")
    status.add_argument("--json", action="store_true")

    doctor = sub.add_parser("doctor", help="check local prerequisites and coverage")
    doctor.add_argument("--json", action="store_true")

    configure = sub.add_parser(
        "configure-webhook", help="read a Discord webhook from stdin and store it in Keychain"
    )
    configure.add_argument("--json", action="store_true")
    return root


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    home = _home()
    database = _database(home)
    now = datetime.now(WARSAW)
    as_json = bool(getattr(arguments, "json", False))

    if arguments.command == "configure-webhook":
        webhook = sys.stdin.readline()
        if not webhook.strip():
            print("configure-webhook: pass the URL on stdin", file=sys.stderr)
            return 2
        try:
            store_webhook(webhook)
        except (ValueError, RuntimeError) as error:
            print(f"configure-webhook: {error}", file=sys.stderr)
            return 1
        _emit({"configured": True, "storage": "macOS Keychain"}, as_json)
        return 0

    if arguments.command == "status":
        _emit(database.status(), as_json)
        return 0
    if arguments.command == "doctor":
        result = _doctor(home, database)
        _emit(result, as_json)
        return 0 if result["ok"] else 1
    if arguments.command == "collect":
        if arguments.days < 1 or arguments.days > 366:
            print("collect: --days must be between 1 and 366", file=sys.stderr)
            return 2
        end = arguments.end or (now.date() - timedelta(days=1))
        _emit(_collect(database, home, end=end, days=arguments.days, now=now), as_json)
        return 0

    report_day = arguments.date or (now.date() - timedelta(days=1))
    if arguments.days < 1 or arguments.days > 366:
        print(f"{arguments.command}: --days must be between 1 and 366", file=sys.stderr)
        return 2
    is_mock = arguments.command == "mock"
    quiet = bool(getattr(arguments, "quiet", False))
    observation = None
    if arguments.command == "run":
        observation = _observe_cursor_cli(database, home, now)
    if arguments.command == "run" and not arguments.force and not arguments.dry_run:
        if now.hour < 8:
            if not quiet:
                _emit(
                    {
                        "status": "waiting-for-08:00",
                        "timezone": str(WARSAW),
                        "cursor_observation": observation,
                    },
                    as_json,
                )
            return 0
        if load_webhook() is None:
            if not quiet:
                _emit(
                    {
                        "status": "webhook-not-configured",
                        "cursor_observation": observation,
                    },
                    as_json,
                )
            return 0
    code, result = _execute_report(
        database,
        home,
        report_day=report_day,
        now=now,
        days=arguments.days,
        force=arguments.force or is_mock,
        dry_run=arguments.dry_run,
        mock=is_mock,
    )
    if observation is not None:
        result["cursor_observation"] = observation
    if not quiet or result.get("status") not in {"already-sent", "already-sending-or-sent"}:
        _emit(result, as_json)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
