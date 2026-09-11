from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from . import __version__
from .bb import scan_bb
from .collectors import (
    CollectorContext,
    collect_all,
    collect_cursor_cli,
    detect_code_roots,
)
from .database import Database
from .local_timezone import local_timezone
from .reporting import (
    DEFAULT_REPORT_DAYS,
    build_report,
    local_report_exists,
    load_webhook,
    mock_delivery,
    post_discord,
    render_chart,
    save_local_report,
    store_webhook,
)


INSTALL_PREFLIGHT_REQUEST = ".install-preflight-request.json"
INSTALL_PREFLIGHT_RESULT = ".install-preflight-result.json"


def _home() -> Path:
    return Path(os.environ.get("CORRAL_PRODUCTIVITY_HOME", Path.home())).expanduser()


def _state_dir(home: Path) -> Path:
    override = os.environ.get("CORRAL_PRODUCTIVITY_STATE_DIR")
    if override:
        return Path(override).expanduser()
    return home / "Library/Application Support/Corral/Agentic Productivity"


def _database(home: Path) -> Database:
    return Database(_state_dir(home) / "metrics.sqlite3")


def install_preflight_paths(state_dir: Path) -> tuple[Path, Path]:
    return (
        state_dir / INSTALL_PREFLIGHT_REQUEST,
        state_dir / INSTALL_PREFLIGHT_RESULT,
    )


def _write_private_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}."
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _code_root_override() -> Path | None:
    value = os.environ.get("CORRAL_PRODUCTIVITY_CODE_ROOT")
    return Path(value).expanduser().resolve() if value else None


def _code_roots(
    database: Database,
    home: Path,
    now: datetime,
    *,
    refresh: bool,
) -> tuple[Path, ...]:
    override = _code_root_override()
    if override is not None:
        return (override,)
    if refresh:
        detection = detect_code_roots(home)
        database.replace_code_roots(detection.roots, now)
        return detection.roots
    return database.code_roots()


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
    database: Database,
    home: Path,
    *,
    end: date,
    days: int,
    now: datetime,
    refresh_code_roots: bool = True,
) -> dict[str, Any]:
    start = end - timedelta(days=days - 1)
    zone = now.tzinfo or local_timezone()
    context = CollectorContext(
        home=home,
        code_roots=_code_roots(
            database, home, now, refresh=refresh_code_roots
        ),
        start=start,
        end=end,
        database=database,
        now=now,
        timezone=zone,
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
    zone = now.tzinfo or local_timezone()
    context = CollectorContext(
        home=home,
        code_roots=_code_roots(database, home, now, refresh=False),
        start=now.date(),
        end=now.date(),
        database=database,
        now=now,
        timezone=zone,
    )
    result = collect_cursor_cli(context)
    database.store_harness_result(result, now.date(), now.date(), now)
    return {
        "coverage": result.coverage.status if result.coverage else "error",
        "sessions": result.session_counts().get(now.date(), 0),
        "prompts": result.prompts.get(now.date(), 0),
    }


def _observe_bb(database: Database, home: Path, now: datetime) -> dict[str, Any]:
    sample = scan_bb(home)
    database.store_bb_placement(sample, now)
    known = (sample.local or 0) + (sample.cloud or 0)
    return {
        "coverage": sample.coverage.status,
        "detail": sample.coverage.detail,
        "local": sample.local,
        "cloud": sample.cloud,
        "unknown": sample.unknown,
        "local_percent": round(100 * sample.local / known, 2) if known else None,
        "cloud_percent": round(100 * sample.cloud / known, 2) if known else None,
    }


def _run_install_preflight(
    database: Database,
    home: Path,
    now: datetime,
    days: int,
) -> dict[str, Any] | None:
    request_path, result_path = install_preflight_paths(database.path.parent)
    if not request_path.is_file():
        return None
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        request = {}
    request_path.unlink(missing_ok=True)
    token = str(request.get("token") or "")
    webhook_expected = request.get("webhook_configured") is True
    try:
        report_day = now.date() - timedelta(days=1)
        collection = _collect(
            database,
            home,
            end=report_day,
            days=days,
            now=now,
        )
        collection["coverage"]["BB placement"] = _observe_bb(database, home, now)["coverage"]
        webhook_accessible = (
            not webhook_expected or load_webhook(timeout=60) is not None
        )
        result: dict[str, Any] = {
            "token": token,
            "status": "ready" if webhook_accessible else "failed",
            "coverage": collection["coverage"],
            "webhook_accessible": webhook_accessible,
        }
        if not webhook_accessible:
            result["error"] = "webhook-unavailable"
    except Exception as error:
        result = {
            "token": token,
            "status": "failed",
            "error": type(error).__name__,
        }
    _write_private_json(result_path, result)
    return result


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

    state_dir = database.path.parent
    webhook = None if dry_run or mock else load_webhook()
    if (
        webhook is None
        and not force
        and not dry_run
        and not mock
        and local_report_exists(state_dir, report_day)
    ):
        return 0, {
            "status": "already-saved-local",
            "report_day": report_day.isoformat(),
        }

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

    if webhook is None:
        try:
            local_report = save_local_report(report, state_dir)
        except OSError as error:
            return 1, {
                "status": "local-save-failed",
                "error": str(error)[:300] or "local report save failed",
                **base,
            }
        return 0, {"status": "saved-local", "local_report": local_report, **base}
    if not database.begin_delivery(report_day, now, force=force):
        return 0, {"status": "already-sending-or-sent", **base}
    try:
        images = [render_chart(chart) for chart in report.charts]
        post_discord(webhook, report, images)
    except Exception as error:
        message = str(error)[:300] or "delivery failed"
        database.finish_delivery(
            report_day, datetime.now(now.tzinfo), sent=False, error=message
        )
        try:
            local_report = save_local_report(report, state_dir)
        except OSError as local_error:
            return 1, {
                "status": "delivery-and-local-save-failed",
                "error": message,
                "local_error": str(local_error)[:300] or "local report save failed",
                **base,
            }
        return 1, {
            "status": "delivery-failed-local-saved",
            "error": message,
            "local_report": local_report,
            **base,
        }
    database.finish_delivery(report_day, datetime.now(now.tzinfo), sent=True)
    return 0, {"status": "sent", **base}


def _doctor(home: Path, database: Database, zone) -> dict[str, Any]:
    commands = (
        "codex",
        "claude",
        "cursor-agent",
        "hermes",
        "pi",
        "omp",
        "prime-agent",
        "opencode",
        "droid",
        "gemini",
        "qwen",
        "amp",
        "kimi",
        "grok",
    )
    state = _state_dir(home)
    override = _code_root_override()
    code_roots = (override,) if override is not None else database.code_roots()
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
        "ok": writable and bool(code_roots) and all(root.is_dir() for root in code_roots),
        "version": __version__,
        "timezone": getattr(zone, "key", None) or str(zone),
        "code_roots": [str(root) for root in code_roots],
        "code_roots_source": "override" if override is not None else "detected",
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

    scan = sub.add_parser("scan-bb", help="store one local/cloud BB placement snapshot")
    scan.add_argument("--json", action="store_true")

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
    zone = local_timezone()
    now = datetime.now(zone)
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
        result = _doctor(home, database, zone)
        _emit(result, as_json)
        return 0 if result["ok"] else 1
    if arguments.command == "scan-bb":
        result = _observe_bb(database, home, now)
        _emit(result, as_json)
        return 0 if result["coverage"] in {"full", "partial", "absent"} else 1
    if arguments.command == "collect":
        if arguments.days < 1 or arguments.days > 366:
            print("collect: --days must be between 1 and 366", file=sys.stderr)
            return 2
        end = arguments.end or (now.date() - timedelta(days=1))
        result = _collect(database, home, end=end, days=arguments.days, now=now)
        result["bb_observation"] = _observe_bb(database, home, now)
        _emit(result, as_json)
        return 0

    report_day = arguments.date or (now.date() - timedelta(days=1))
    if arguments.days < 1 or arguments.days > 366:
        print(f"{arguments.command}: --days must be between 1 and 366", file=sys.stderr)
        return 2
    is_mock = arguments.command == "mock"
    quiet = bool(getattr(arguments, "quiet", False))
    if arguments.command == "run":
        preflight = _run_install_preflight(database, home, now, arguments.days)
        if preflight is not None:
            if not quiet:
                _emit(preflight, as_json)
            return 0 if preflight.get("status") == "ready" else 1
    observation = None
    bb_observation = None
    if arguments.command == "run":
        bb_observation = _observe_bb(database, home, now)
        observation = _observe_cursor_cli(database, home, now)
    if arguments.command == "run" and not arguments.force and not arguments.dry_run:
        if now.hour < 8:
            if not quiet:
                _emit(
                    {
                        "status": "waiting-for-08:00",
                        "timezone": getattr(zone, "key", None) or str(zone),
                        "cursor_observation": observation,
                        "bb_observation": bb_observation,
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
    if bb_observation is not None:
        result["bb_observation"] = bb_observation
    quiet_statuses = {
        "already-saved-local",
        "already-sending-or-sent",
        "already-sent",
    }
    if not quiet or result.get("status") not in quiet_statuses:
        _emit(result, as_json)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
