from __future__ import annotations

import sqlite3
import hashlib
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

from .model import Collection, HarnessResult
from .bb import BbPlacement


SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_metrics (
    day TEXT NOT NULL,
    metric TEXT NOT NULL CHECK (metric IN ('commits', 'sessions', 'prompts')),
    harness TEXT NOT NULL,
    count INTEGER NOT NULL CHECK (count >= 0),
    collected_at TEXT NOT NULL,
    PRIMARY KEY (day, metric, harness)
);

CREATE TABLE IF NOT EXISTS collector_health (
    harness TEXT PRIMARY KEY,
    installed INTEGER NOT NULL CHECK (installed IN (0, 1)),
    status TEXT NOT NULL,
    detail TEXT NOT NULL,
    checked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_snapshots (
    harness TEXT NOT NULL,
    source_key TEXT NOT NULL,
    total INTEGER NOT NULL CHECK (total >= 0),
    observed_at TEXT NOT NULL,
    PRIMARY KEY (harness, source_key)
);

CREATE TABLE IF NOT EXISTS source_daily_counts (
    day TEXT NOT NULL,
    metric TEXT NOT NULL CHECK (metric IN ('prompts')),
    harness TEXT NOT NULL,
    source_key TEXT NOT NULL,
    count INTEGER NOT NULL CHECK (count >= 0),
    PRIMARY KEY (day, metric, harness, source_key)
);

CREATE TABLE IF NOT EXISTS collector_baselines (
    harness TEXT PRIMARY KEY,
    started_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS code_roots (
    path TEXT PRIMARY KEY,
    detected_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bb_placement_samples (
    slot INTEGER PRIMARY KEY,
    day TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    local_count INTEGER CHECK (local_count >= 0),
    cloud_count INTEGER CHECK (cloud_count >= 0),
    unknown_count INTEGER CHECK (unknown_count >= 0),
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deliveries (
    report_day TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('sending', 'sent', 'failed')),
    attempted_at TEXT NOT NULL,
    sent_at TEXT,
    error TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    mode TEXT NOT NULL,
    report_day TEXT,
    status TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass
        connection.row_factory = sqlite3.Row
        try:
            connection.executescript(SCHEMA)
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema', '4')"
            )
            connection.commit()
            yield connection
            connection.commit()
        finally:
            connection.close()

    def replace_code_roots(self, roots: tuple[Path, ...], detected_at: datetime) -> None:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM code_roots")
            connection.executemany(
                "INSERT INTO code_roots(path, detected_at) VALUES(?, ?)",
                ((str(root), detected_at.isoformat()) for root in roots),
            )

    def store_bb_placement(self, sample: BbPlacement, now: datetime) -> None:
        with self.connect() as connection:
            # One observation per scheduled interval, including manual retries.
            connection.execute(
                """
                INSERT INTO bb_placement_samples
                    (slot, day, observed_at, local_count, cloud_count, unknown_count, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(slot) DO UPDATE SET
                    day=excluded.day, observed_at=excluded.observed_at,
                    local_count=excluded.local_count, cloud_count=excluded.cloud_count,
                    unknown_count=excluded.unknown_count, status=excluded.status
                WHERE excluded.observed_at >= bb_placement_samples.observed_at
                """,
                (int(now.timestamp()) // 300, now.date().isoformat(), now.isoformat(),
                 sample.local, sample.cloud, sample.unknown, sample.coverage.status),
            )
            item = sample.coverage
            connection.execute(
                """
                INSERT INTO collector_health(harness, installed, status, detail, checked_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(harness) DO UPDATE SET
                    installed=excluded.installed, status=excluded.status,
                    detail=excluded.detail, checked_at=excluded.checked_at
                """,
                (item.harness, int(item.installed), item.status, item.detail, now.isoformat()),
            )

    def bb_placement_series(self, start: date, end: date) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(connection.execute(
                """
                SELECT day, SUM(local_count) AS local_count, SUM(cloud_count) AS cloud_count,
                    SUM(unknown_count) AS unknown_count,
                    COUNT(local_count) AS samples, COUNT(*) AS attempts
                FROM bb_placement_samples WHERE day BETWEEN ? AND ? GROUP BY day ORDER BY day
                """,
                (start.isoformat(), end.isoformat()),
            ))

    def code_roots(self) -> tuple[Path, ...]:
        with self.connect() as connection:
            rows = connection.execute("SELECT path FROM code_roots ORDER BY path")
            return tuple(Path(str(row["path"])) for row in rows)

    def observe_source_total(
        self,
        harness: str,
        source_key: str,
        total: int,
        observed_at: datetime,
        *,
        attribute_first: bool = False,
    ) -> tuple[int, bool]:
        hashed_key = hashlib.sha256(
            f"{harness}\0{source_key}".encode("utf-8")
        ).hexdigest()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT total FROM source_snapshots WHERE harness=? AND source_key=?",
                (harness, hashed_key),
            ).fetchone()
            first = row is None
            previous = 0 if first else int(row["total"])
            delta = max(0, total - previous)
            if first and not attribute_first:
                delta = 0
            connection.execute(
                """
                INSERT INTO source_snapshots(harness, source_key, total, observed_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(harness, source_key) DO UPDATE SET
                    total=MAX(source_snapshots.total, excluded.total),
                    observed_at=excluded.observed_at
                """,
                (harness, hashed_key, total, observed_at.isoformat()),
            )
            if delta:
                connection.execute(
                    """
                    INSERT INTO source_daily_counts(
                        day, metric, harness, source_key, count
                    ) VALUES(?, 'prompts', ?, ?, ?)
                    ON CONFLICT(day, metric, harness, source_key) DO UPDATE SET
                        count=source_daily_counts.count + excluded.count
                    """,
                    (observed_at.date().isoformat(), harness, hashed_key, delta),
                )
        return delta, first

    def observed_prompt_counts(
        self, harness: str, start: date, end: date
    ) -> dict[date, int]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT day, SUM(count) AS count
                FROM source_daily_counts
                WHERE metric='prompts' AND harness=? AND day BETWEEN ? AND ?
                GROUP BY day
                """,
                (harness, start.isoformat(), end.isoformat()),
            )
            return {date.fromisoformat(row["day"]): int(row["count"]) for row in rows}

    def ensure_collector_baseline(self, harness: str, now: datetime) -> datetime:
        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO collector_baselines(harness, started_at) VALUES(?, ?)",
                (harness, now.isoformat()),
            )
            row = connection.execute(
                "SELECT started_at FROM collector_baselines WHERE harness=?",
                (harness,),
            ).fetchone()
        return datetime.fromisoformat(str(row["started_at"]))

    def store_harness_result(
        self,
        result: HarnessResult,
        start: date,
        end: date,
        collected_at: datetime,
    ) -> None:
        stamp = collected_at.isoformat()
        session_counts = result.session_counts()
        with self.connect() as connection:
            day = start
            while day <= end:
                for metric, count in (
                    ("sessions", session_counts.get(day, 0)),
                    ("prompts", result.prompts.get(day, 0)),
                ):
                    connection.execute(
                        """
                        INSERT INTO daily_metrics(day, metric, harness, count, collected_at)
                        VALUES(?, ?, ?, ?, ?)
                        ON CONFLICT(day, metric, harness) DO UPDATE SET
                            count=MAX(daily_metrics.count, excluded.count),
                            collected_at=excluded.collected_at
                        """,
                        (day.isoformat(), metric, result.harness, count, stamp),
                    )
                day = date.fromordinal(day.toordinal() + 1)
            if result.coverage is not None:
                item = result.coverage
                connection.execute(
                    """
                    INSERT INTO collector_health(harness, installed, status, detail, checked_at)
                    VALUES(?, ?, ?, ?, ?)
                    ON CONFLICT(harness) DO UPDATE SET
                        installed=excluded.installed,
                        status=excluded.status,
                        detail=excluded.detail,
                        checked_at=excluded.checked_at
                    """,
                    (item.harness, int(item.installed), item.status, item.detail, stamp),
                )

    def store_collection(self, collection: Collection, collected_at: datetime) -> None:
        stamp = collected_at.isoformat()
        days = []
        current = collection.start
        while current <= collection.end:
            days.append(current)
            current = date.fromordinal(current.toordinal() + 1)

        with self.connect() as connection:
            for day in days:
                connection.execute(
                    """
                    INSERT INTO daily_metrics(day, metric, harness, count, collected_at)
                    VALUES(?, 'commits', 'git', ?, ?)
                    ON CONFLICT(day, metric, harness) DO UPDATE SET
                        count=MAX(daily_metrics.count, excluded.count),
                        collected_at=excluded.collected_at
                    """,
                    (day.isoformat(), collection.commits.counts.get(day, 0), stamp),
                )
            health = [collection.commits.coverage]
            for result in collection.harnesses:
                session_counts = result.session_counts()
                for day in days:
                    for metric, count in (
                        ("sessions", session_counts.get(day, 0)),
                        ("prompts", result.prompts.get(day, 0)),
                    ):
                        connection.execute(
                            """
                            INSERT INTO daily_metrics(day, metric, harness, count, collected_at)
                            VALUES(?, ?, ?, ?, ?)
                            ON CONFLICT(day, metric, harness) DO UPDATE SET
                                count=MAX(daily_metrics.count, excluded.count),
                                collected_at=excluded.collected_at
                            """,
                            (day.isoformat(), metric, result.harness, count, stamp),
                        )
                if result.coverage is not None:
                    health.append(result.coverage)
            for item in health:
                connection.execute(
                    """
                    INSERT INTO collector_health(harness, installed, status, detail, checked_at)
                    VALUES(?, ?, ?, ?, ?)
                    ON CONFLICT(harness) DO UPDATE SET
                        installed=excluded.installed,
                        status=excluded.status,
                        detail=excluded.detail,
                        checked_at=excluded.checked_at
                    """,
                    (item.harness, int(item.installed), item.status, item.detail, stamp),
                )

    def series(self, start: date, end: date) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """
                    SELECT day, metric, harness, count
                    FROM daily_metrics
                    WHERE day BETWEEN ? AND ?
                    ORDER BY day, metric, harness
                    """,
                    (start.isoformat(), end.isoformat()),
                )
            )

    def health(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    "SELECT * FROM collector_health ORDER BY harness"
                )
            )

    def is_sent(self, report_day: date) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT status FROM deliveries WHERE report_day=?",
                (report_day.isoformat(),),
            ).fetchone()
            return row is not None and row["status"] == "sent"

    def begin_delivery(self, report_day: date, now: datetime, *, force: bool = False) -> bool:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM deliveries WHERE report_day=?",
                (report_day.isoformat(),),
            ).fetchone()
            if not force and row is not None and row["status"] in {"sending", "sent"}:
                return False
            connection.execute(
                """
                INSERT INTO deliveries(report_day, status, attempted_at, sent_at, error)
                VALUES(?, 'sending', ?, NULL, '')
                ON CONFLICT(report_day) DO UPDATE SET
                    status='sending', attempted_at=excluded.attempted_at,
                    sent_at=NULL, error=''
                """,
                (report_day.isoformat(), now.isoformat()),
            )
            return True

    def finish_delivery(
        self, report_day: date, now: datetime, *, sent: bool, error: str = ""
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE deliveries
                SET status=?, sent_at=?, error=?
                WHERE report_day=?
                """,
                (
                    "sent" if sent else "failed",
                    now.isoformat() if sent else None,
                    error[:300],
                    report_day.isoformat(),
                ),
            )

    def status(self) -> dict[str, object]:
        with self.connect() as connection:
            last_metric = connection.execute(
                "SELECT MAX(collected_at) AS value FROM daily_metrics"
            ).fetchone()["value"]
            last_delivery = connection.execute(
                """
                SELECT report_day, status, attempted_at, sent_at, error
                FROM deliveries ORDER BY report_day DESC LIMIT 1
                """
            ).fetchone()
            return {
                "database": str(self.path),
                "last_collection": last_metric,
                "last_delivery": dict(last_delivery) if last_delivery else None,
                "collectors": [dict(row) for row in self.health()],
            }
