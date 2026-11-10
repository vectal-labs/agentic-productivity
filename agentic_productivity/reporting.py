from __future__ import annotations

import base64
import getpass
import json
import os
import secrets
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from .database import Database
from .local_timezone import local_timezone


QUICKCHART_URL = "https://quickchart.io/chart"
KEYCHAIN_SERVICE = "com.corral.agentic-productivity.discord-webhook"
DEFAULT_REPORT_DAYS = 90
CHART_WIDTH = 2048
CHART_HEIGHT = 1080
PLACEMENT_CHART_WIDTH = 1620  # 3:2
# QuickChart runs these as JavaScript; render_chart unquotes them.
PERCENT_LABEL = "function(v){return v==null?'':Math.round(v)+'%'}"
PERCENT_TICK = "function(v){return v+'%'}"
MOCK_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
COLORS = (
    "#7C3AED",
    "#2563EB",
    "#0891B2",
    "#059669",
    "#65A30D",
    "#CA8A04",
    "#EA580C",
    "#DC2626",
    "#DB2777",
    "#7C2D12",
    "#475569",
)
TREND_COLOR = "#94A3B8"


@dataclass(frozen=True)
class Chart:
    filename: str
    config: dict[str, Any]
    width: int = CHART_WIDTH


@dataclass(frozen=True)
class Report:
    day: date
    content: str
    charts: tuple[Chart, ...]
    totals: dict[str, int]


LOCAL_REPORT_FILES = ("summary.md", "charts.json")


def local_report_directory(state_dir: Path, report_day: date) -> Path:
    return state_dir / "reports" / report_day.isoformat()


def local_report_exists(state_dir: Path, report_day: date) -> bool:
    directory = local_report_directory(state_dir, report_day)
    return (
        directory.is_dir()
        and not directory.is_symlink()
        and all((directory / name).is_file() for name in LOCAL_REPORT_FILES)
    )


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise OSError(f"local report path is not a private directory: {path}")
    path.chmod(0o700)


def _atomic_private_text(path: Path, content: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}."
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def save_local_report(report: Report, state_dir: Path) -> dict[str, Any]:
    reports_dir = state_dir / "reports"
    report_dir = local_report_directory(state_dir, report.day)
    for directory in (state_dir, reports_dir, report_dir):
        _private_directory(directory)

    chart_data = {
        "report_day": report.day.isoformat(),
        "totals": report.totals,
        "charts": [
            {"name": Path(chart.filename).stem, "config": chart.config}
            for chart in report.charts
        ],
    }
    _atomic_private_text(report_dir / "summary.md", report.content + "\n")
    _atomic_private_text(
        report_dir / "charts.json",
        json.dumps(chart_data, indent=2, sort_keys=True) + "\n",
    )
    return {
        "directory": str(report_dir),
        "files": list(LOCAL_REPORT_FILES),
    }


def _date_spine(start: date, end: date) -> list[date]:
    return [date.fromordinal(value) for value in range(start.toordinal(), end.toordinal() + 1)]


def _base_options(title: str, *, stacked: bool = False) -> dict[str, Any]:
    return {
        "responsive": True,
        "maintainAspectRatio": False,
        "animation": False,
        "interaction": {"mode": "index", "intersect": False},
        "plugins": {
            "title": {
                "display": True,
                "text": title,
                "color": "#E2E8F0",
                "font": {"size": 38, "weight": "normal"},
                "padding": {"top": 18, "bottom": 8},
            },
            "legend": {
                "display": True,
                "position": "top",
                "align": "center",
                "labels": {
                    "color": "#A3B1C6",
                    "boxWidth": 27,
                    "boxHeight": 20,
                    "padding": 24,
                    "font": {"size": 21},
                },
            },
        },
        "scales": {
            "x": {
                "stacked": stacked,
                "ticks": {
                    "color": "#94A3B8",
                    "font": {"size": 19},
                    "maxRotation": 0,
                    "autoSkip": True,
                    "maxTicksLimit": 16,
                    "padding": 8,
                },
                "grid": {"display": False, "drawBorder": False},
                "border": {"display": False},
            },
            "y": {
                "stacked": stacked,
                "beginAtZero": True,
                "ticks": {
                    "color": "#94A3B8",
                    "font": {"size": 19},
                    "precision": 0,
                    "maxTicksLimit": 9,
                    "padding": 12,
                },
                "grid": {"color": "#1E293B", "lineWidth": 1, "drawBorder": False},
                "border": {"display": False},
            },
        },
        "layout": {
            "padding": {
                "left": 28,
                "right": 28,
                "top": 10 if stacked else 36,
                "bottom": 12,
            }
        },
    }


def _linear_trend(values: list[int]) -> list[float]:
    if not values:
        return []
    if len(values) == 1:
        return [float(values[0])]
    midpoint = (len(values) - 1) / 2
    mean = sum(values) / len(values)
    denominator = sum((index - midpoint) ** 2 for index in range(len(values)))
    slope = sum(
        (index - midpoint) * (value - mean) for index, value in enumerate(values)
    ) / denominator
    intercept = mean - slope * midpoint
    return [round(intercept + slope * index, 2) for index in range(len(values))]


def _trend_dataset(values: list[int]) -> dict[str, Any]:
    return {
        "type": "line",
        "label": "Long-term trend",
        "data": _linear_trend(values),
        "borderColor": TREND_COLOR,
        "backgroundColor": "rgba(0,0,0,0)",
        "borderWidth": 3,
        "borderDash": [10, 8],
        "fill": False,
        "pointRadius": 0,
        "pointHoverRadius": 0,
        "tension": 0,
        "lineTension": 0,
        "stack": "trend",
    }


def _chart_config(
    *, title: str, labels: list[str], series: dict[str, list[int]], single: bool
) -> dict[str, Any]:
    datasets = []
    for index, (name, values) in enumerate(series.items()):
        color = COLORS[index % len(COLORS)]
        datasets.append(
            {
                "label": name,
                "data": values,
                "borderColor": color,
                "backgroundColor": color if not single else "rgba(124,58,237,0.18)",
                "borderWidth": 4 if single else 0,
                "fill": single,
                "tension": 0 if single else 0,
                "pointRadius": 4 if single else 0,
                "pointHoverRadius": 5 if single else 0,
                "stack": "metrics",
                "barPercentage": 0.86,
                "categoryPercentage": 0.9,
            }
        )
    daily_totals = [
        sum(values[index] for values in series.values()) for index in range(len(labels))
    ]
    datasets.append(_trend_dataset(daily_totals))
    return {
        "type": "line" if single else "bar",
        "data": {"labels": labels, "datasets": datasets},
        "options": _base_options(title, stacked=not single),
    }


def _cloud_share(counts: list[tuple[int, int] | None]) -> float | None:
    """Cloud share of the pooled (local, cloud) message counts; unmeasured days are None and skipped."""
    local = sum(item[0] for item in counts if item)
    cloud = sum(item[1] for item in counts if item)
    return round(100 * cloud / (local + cloud), 2) if local + cloud else None


def _placement_config(spine: list[date], counts: list[tuple[int, int] | None]) -> dict[str, Any]:
    """Local and Cloud share of human messages, one point per week, weeks counted back from the report day."""
    span = min(7, len(spine))
    weeks = max(1, len(spine) // 7)
    ends = [len(spine) - span * index for index in reversed(range(weeks))]
    cloud = [_cloud_share(counts[end - span:end]) for end in ends]
    local = [None if value is None else round(100 - value, 2) for value in cloud]
    period = "last week" if span == 7 else "yesterday" if span == 1 else f"in the last {span} days"
    title = "Your messages: Local vs Cloud"
    if cloud[-1] is not None:
        title = f"{round(cloud[-1])}% of your messages went to Cloud {period}"
    window = f"one point per week · last {weeks} weeks" if weeks > 1 else f"last {span} days" if span > 1 else "last day"
    options = _base_options(title)
    plugins = options["plugins"]
    plugins["title"].update({"align": "start", "font": {"size": 46, "weight": "bold"},
                             "padding": {"top": 8, "bottom": 6}})
    plugins["subtitle"] = {"display": True, "text": f"Local vs Cloud · {window}", "color": "#94A3B8",
                           "align": "start", "font": {"size": 26}, "padding": {"bottom": 26}}
    plugins["legend"]["align"] = "end"
    plugins["legend"]["labels"]["font"] = {"size": 23}
    plugins["datalabels"] = {"display": False}
    options["scales"]["x"]["ticks"].update({"font": {"size": 22}, "maxTicksLimit": 12, "padding": 10})
    options["scales"]["y"].update({"min": 0, "max": 100})
    options["scales"]["y"]["ticks"].update({"font": {"size": 22}, "stepSize": 25, "callback": PERCENT_TICK})
    options["layout"]["padding"] = {"left": 40, "right": 70, "top": 34, "bottom": 28}

    def dataset(name: str, values: list, color: str, align: str, shown: list[bool]) -> dict[str, Any]:
        return {
            "label": name, "data": values, "borderColor": color, "backgroundColor": color,
            "borderWidth": 6, "fill": False, "pointRadius": 8, "pointHoverRadius": 8,
            "tension": 0, "spanGaps": False,
            "datalabels": {
                "display": shown, "color": color, "backgroundColor": "#0F172A", "borderRadius": 4,
                "padding": 3, "font": {"size": 21, "weight": "bold"}, "align": align, "anchor": "end",
                "offset": 16, "formatter": PERCENT_LABEL, "clip": False,
            },
        }

    return {
        "type": "line",
        "data": {"labels": [spine[end - 1].strftime("%b %-d") for end in ends], "datasets": [
            dataset("Local", local, "#60A5FA", "top", [value is not None for value in local]),
            # Tiny Cloud labels would sit on the date axis.
            dataset("Cloud", cloud, "#34D399", "bottom", [value is not None and value >= 8 for value in cloud]),
        ]},
        "options": options,
    }


def build_report(
    database: Database, report_day: date, days: int = DEFAULT_REPORT_DAYS
) -> Report:
    start = report_day - timedelta(days=days - 1)
    spine = _date_spine(start, report_day)
    labels = [day.strftime("%b %-d") for day in spine]
    raw: dict[str, dict[str, dict[date, int]]] = {}
    for row in database.series(start, report_day):
        raw.setdefault(row["metric"], {}).setdefault(row["harness"], {})[
            date.fromisoformat(row["day"])
        ] = int(row["count"])

    commits = [raw.get("commits", {}).get("git", {}).get(day, 0) for day in spine]

    def harness_series(metric: str) -> dict[str, list[int]]:
        candidates = {
            harness: [values.get(day, 0) for day in spine]
            for harness, values in raw.get(metric, {}).items()
        }
        return dict(
            sorted(
                ((harness, values) for harness, values in candidates.items() if sum(values)),
                key=lambda item: (-sum(item[1]), item[0]),
            )
        )

    sessions = harness_series("sessions")
    prompts = harness_series("prompts")
    yesterday_sessions = sum(values[-1] for values in sessions.values())
    yesterday_prompts = sum(values[-1] for values in prompts.values())
    totals = {
        "commits": commits[-1],
        "sessions": yesterday_sessions,
        "prompts": yesterday_prompts,
    }
    content = (
        f"**Agentic productivity — {report_day.isoformat()}**\n"
        f"Commits: **{totals['commits']}** · Sessions: **{totals['sessions']}** · "
        f"Prompts: **{totals['prompts']}**"
    )
    combined = database.combined_reporting_start()
    if combined is not None and start < combined:
        prior = date.fromordinal(combined.toordinal() - 1)
        content += f"\nHistory through {prior.isoformat()} is Mac-only."
    notes: list[str] = []
    for row in database.machine_coverage_rows(report_day, report_day):
        status = str(row["status"])
        if status in {"full", "absent"}:
            continue
        label = "unsupported" if status == "unavailable" else status
        machine = str(row["machine"])
        harness = str(row["harness"])
        notes.append(f"{machine} {harness} {label}")
    if combined is not None and report_day >= combined:
        cloud_rows = [
            row
            for row in database.machine_coverage_rows(report_day, report_day)
            if row["machine"] == "cloud" and row["harness"] not in {"Git", "cloud collector"}
        ]
        if not cloud_rows:
            notes.append("cloud missing")
        else:
            day_end = datetime.combine(report_day + timedelta(days=1), time.min, local_timezone())
            if any(datetime.fromisoformat(row["collected_at"]) < day_end for row in cloud_rows):
                notes.insert(0, "cloud incomplete")
            last_seen = next(
                (
                    row["last_seen_at"]
                    for row in database.machines()
                    if row["machine"] == "cloud"
                ),
                None,
            )
            if last_seen:
                seen = datetime.fromisoformat(str(last_seen))
                if seen.date() < report_day:
                    notes.append("cloud stale")
    if notes:
        content += "\nCoverage: " + "; ".join(notes[:8]) + "."
    placement = {row["day"]: row for row in database.user_message_series(start, report_day)}
    counts: list[tuple[int, int] | None] = []
    for day in spine:
        row = placement.get(day.isoformat(), {})
        local, cloud = row.get("local_count") or 0, row.get("cloud_count") or 0
        counts.append((local, cloud) if row.get("available") and local + cloud else None)
    measured_days = sum(item is not None for item in counts)
    placement_days = min(days, 14 if measured_days <= 14 else 30 if measured_days <= 30 else 90)
    placement_chart = Chart(
        "4-user-messages.png",
        _placement_config(spine[-placement_days:], counts[-placement_days:]),
        PLACEMENT_CHART_WIDTH,
    )
    day_cloud = _cloud_share(counts[-1:])
    last = placement.get(report_day.isoformat(), {})
    if day_cloud is not None:
        content += (f"\nYour messages: Local **{last['local_count']}** ({100 - day_cloud:.1f}%) · "
                    f"Cloud **{last['cloud_count']}** ({day_cloud:.1f}%).")
    else:
        content += "\nYour messages: **no measured percentage** for this day."
    if last.get("unknown_count"):
        content += f"\nUnknown message location: {last['unknown_count']} messages, excluded from percentages."
    content += "\nHuman messages only, from BB + Cloudroom; automated messages and retries excluded."
    charts = (
        Chart(
            "1-commits.png",
            _chart_config(
                title=f"Unique local commits — last {days} days",
                labels=labels,
                series={"Commits": commits},
                single=True,
            ),
        ),
        Chart(
            "2-sessions.png",
            _chart_config(
                title=f"Active agent sessions — last {days} days",
                labels=labels,
                series=sessions or {"No measured sessions": [0] * len(spine)},
                single=False,
            ),
        ),
        Chart(
            "3-prompts.png",
            _chart_config(
                title=f"Instruction-bearing prompts — last {days} days",
                labels=labels,
                series=prompts or {"No measured prompts": [0] * len(spine)},
                single=False,
            ),
        ),
        placement_chart,
    )
    return Report(report_day, content, charts, totals)


def render_chart(chart: Chart, *, timeout: int = 20) -> bytes:
    config = json.dumps(chart.config, separators=(",", ":"))
    for function in (PERCENT_LABEL, PERCENT_TICK):
        config = config.replace(json.dumps(function), function)
    body = json.dumps(
        {
            "version": "4",
            "width": chart.width,
            "height": CHART_HEIGHT,
            "devicePixelRatio": 1,
            "backgroundColor": "#0F172A",
            "format": "png",
            "chart": config,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    request = urllib.request.Request(
        QUICKCHART_URL,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "Corral/agentic-productivity"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            image = response.read()
            if response.status != 200 or not image.startswith(b"\x89PNG"):
                raise RuntimeError("chart service returned an invalid image")
            return image
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"chart service returned HTTP {error.code}") from None
    except urllib.error.URLError:
        raise RuntimeError("chart service is unreachable") from None


def _multipart(files: list[tuple[str, bytes]]) -> tuple[bytes, str]:
    boundary = "corral-" + secrets.token_hex(16)
    payload = {
        "allowed_mentions": {"parse": []},
        "attachments": [
            {"id": index, "filename": filename}
            for index, (filename, _) in enumerate(files)
        ],
    }
    parts: list[bytes] = []

    def add(value: bytes) -> None:
        parts.append(b"--" + boundary.encode("ascii") + b"\r\n" + value + b"\r\n")

    add(
        b'Content-Disposition: form-data; name="payload_json"\r\n'
        b"Content-Type: application/json\r\n\r\n"
        + json.dumps(payload, separators=(",", ":")).encode("utf-8")
    )
    for index, (filename, image) in enumerate(files):
        add(
            f'Content-Disposition: form-data; name="files[{index}]"; filename="{filename}"\r\n'.encode(
                "utf-8"
            )
            + b"Content-Type: image/png\r\n\r\n"
            + image
        )
    parts.append(b"--" + boundary.encode("ascii") + b"--\r\n")
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def post_discord(webhook: str, report: Report, images: list[bytes], *, timeout: int = 20) -> None:
    files = [(chart.filename, image) for chart, image in zip(report.charts, images, strict=True)]
    # Summaries stay local; Discord is images-only (ADR 0009).
    body, content_type = _multipart(files)
    request = urllib.request.Request(
        webhook,
        data=body,
        headers={"Content-Type": content_type, "User-Agent": "Corral/agentic-productivity"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status not in {200, 204}:
                raise RuntimeError(f"Discord returned HTTP {response.status}")
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Discord returned HTTP {error.code}") from None
    except urllib.error.URLError:
        raise RuntimeError("Discord is unreachable") from None


def mock_delivery(report: Report) -> dict[str, Any]:
    images = [MOCK_PNG for _ in report.charts]
    body, content_type = _multipart(
        [(chart.filename, image) for chart, image in zip(report.charts, images, strict=True)],
    )
    if not content_type.startswith("multipart/form-data; boundary="):
        raise RuntimeError("mock multipart content type is invalid")
    for chart in report.charts:
        if chart.filename.encode("utf-8") not in body:
            raise RuntimeError(f"mock attachment missing: {chart.filename}")
    return {
        "delivered": True,
        "network": False,
        "attachments": [chart.filename for chart in report.charts],
        "bytes": len(body),
        "totals": report.totals,
    }


def validate_webhook(value: str) -> str:
    webhook = value.strip()
    allowed = (
        "https://discord.com/api/webhooks/",
        "https://discordapp.com/api/webhooks/",
        "https://canary.discord.com/api/webhooks/",
    )
    if not webhook.startswith(allowed) or len(webhook) < 60:
        raise ValueError("expected a Discord webhook URL")
    return webhook


def store_webhook(webhook: str) -> None:
    value = validate_webhook(webhook)
    completed = subprocess.run(
        [
            "/usr/bin/security",
            "add-generic-password",
            "-U",
            "-a",
            getpass.getuser(),
            "-s",
            KEYCHAIN_SERVICE,
            "-w",
            value,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=10,
    )
    if completed.returncode != 0:
        raise RuntimeError("could not store the webhook in macOS Keychain")


def load_webhook(*, timeout: int = 10) -> str | None:
    security = Path("/usr/bin/security")
    if not security.exists():
        return None
    completed = subprocess.run(
        [
            str(security),
            "find-generic-password",
            "-w",
            "-a",
            getpass.getuser(),
            "-s",
            KEYCHAIN_SERVICE,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=timeout,
    )
    if completed.returncode != 0:
        return None
    try:
        return validate_webhook(completed.stdout)
    except ValueError:
        return None
