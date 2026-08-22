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
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .database import Database


QUICKCHART_URL = "https://quickchart.io/chart"
KEYCHAIN_SERVICE = "com.corral.agentic-productivity.discord-webhook"
DEFAULT_REPORT_DAYS = 90
CHART_WIDTH = 2048
CHART_HEIGHT = 1080
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


@dataclass(frozen=True)
class Report:
    day: date
    content: str
    charts: tuple[Chart, Chart, Chart]
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
    partial = [
        row["harness"]
        for row in database.health()
        if row["installed"] and row["status"] != "full"
    ]
    coverage = "All installed collectors full"
    if partial:
        coverage = "Partial coverage: " + ", ".join(partial)
    content = (
        f"**Agentic productivity — {report_day.isoformat()}**\n"
        f"Commits: **{totals['commits']}** · Sessions: **{totals['sessions']}** · "
        f"Prompts: **{totals['prompts']}**\n{coverage}"
    )
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
    )
    return Report(report_day, content, charts, totals)


def render_chart(chart: Chart, *, timeout: int = 20) -> bytes:
    body = json.dumps(
        {
            "version": "4",
            "width": CHART_WIDTH,
            "height": CHART_HEIGHT,
            "devicePixelRatio": 1,
            "backgroundColor": "#0F172A",
            "format": "png",
            "chart": chart.config,
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


def _multipart(content: str, files: list[tuple[str, bytes]]) -> tuple[bytes, str]:
    boundary = "corral-" + secrets.token_hex(16)
    payload = {
        "content": content,
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
    body, content_type = _multipart(report.content, files)
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
        report.content,
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


def load_webhook() -> str | None:
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
        timeout=10,
    )
    if completed.returncode != 0:
        return None
    try:
        return validate_webhook(completed.stdout)
    except ValueError:
        return None
