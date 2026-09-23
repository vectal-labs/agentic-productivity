from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from pathlib import Path

from .fingerprints import FingerprintSigner
from .model import Coverage


MESSAGE_SOURCES = {"bb": "BB user messages", "cloudroom": "Cloudroom user messages"}


@dataclass(frozen=True)
class UserMessage:
    fingerprint: str
    day: date
    location: str
    source: str


@dataclass(frozen=True)
class MessageScan:
    messages: tuple[UserMessage, ...]
    coverage: Coverage


# Evaluate content presence inside SQLite; never load prompt text or attachments.
_VISIBLE_INPUT = """
    COALESCE(json_extract(p.value, '$.visibility'), '') != 'agent-only'
    AND (
        (json_extract(p.value, '$.type') = 'text'
         AND length(trim(json_extract(p.value, '$.text'))) > 0)
        OR json_extract(p.value, '$.type') IN ('image', 'localImage', 'localFile')
    )
"""


def scan_messages(
    home: Path, source: str, start: date, end: date, zone: tzinfo,
    signer: FingerprintSigner,
) -> MessageScan:
    root = (Path(os.environ.get("BB_DATA_DIR", home / ".bb")).expanduser()
            if source == "bb" else home / ".gui-cloudroom")

    def missing(status: str, detail: str) -> MessageScan:
        return MessageScan((), Coverage(MESSAGE_SOURCES[source], status != "absent", status, detail))

    try:
        root.stat()
    except FileNotFoundError:
        return missing("absent", "Profile is absent")
    except OSError:
        return missing("unavailable", "Profile is unreadable")
    try:
        local_host = (root / "host-id").read_text(encoding="utf-8").strip()
        if not local_host:
            return missing("unavailable", "Local machine identity is missing")
        first = int(datetime.combine(start, time.min, zone).timestamp() * 1000)
        last = int(datetime.combine(end + timedelta(days=1), time.min, zone).timestamp() * 1000)
        target = "t.execution_target" if source == "cloudroom" else "'local'"
        with closing(sqlite3.connect((root / "bb.db").as_uri() + "?mode=ro", uri=True, timeout=2)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(f"""
                SELECT e.created_at, t.created_at AS thread_created_at,
                    t.source_thread_id IS NOT NULL AS forked,
                    {target} AS target, env.host_id, h.id AS registered_host,
                    json_extract(e.data, '$.requestId') AS request_id,
                    json_extract(e.data, '$.initiator') AS initiator,
                    json_extract(e.data, '$.direction') AS direction,
                    json_extract(e.data, '$.senderThreadId') IS NOT NULL AS delegated,
                    (json_extract(e.data, '$.retryOfRequestId') IS NOT NULL
                     OR json_extract(e.data, '$.retryAttempt') IS NOT NULL
                     OR json_extract(e.data, '$.continuationOfRequestId') IS NOT NULL) AS retry,
                    json_extract(e.data, '$.systemMessageKind') IS NOT NULL AS automated,
                    CASE WHEN json_type(e.data, '$.inputGroups') = 'array' THEN
                        (SELECT count(*) FROM json_each(e.data, '$.inputGroups') g
                         WHERE EXISTS (SELECT 1 FROM json_each(g.value) p WHERE {_VISIBLE_INPUT}))
                    ELSE EXISTS (SELECT 1 FROM json_each(e.data, '$.input') p WHERE {_VISIBLE_INPUT})
                    END AS message_count
                FROM events e
                LEFT JOIN threads t ON t.id = e.thread_id
                LEFT JOIN environments env ON env.id = COALESCE(e.environment_id, t.environment_id)
                LEFT JOIN hosts h ON h.id = env.host_id
                WHERE e.type = 'client/turn/requested'
                    AND e.created_at >= ? AND e.created_at < ?
                ORDER BY e.created_at, t.created_at
            """, (first, last))
            messages: dict[str, UserMessage] = {}
            for row in rows:
                if row["initiator"] not in {"user", "agent", "system"} or row["direction"] != "outbound":
                    return missing("error", "Unsupported message origin metadata")
                if row["initiator"] != "user" or row["delegated"] or row["retry"] or row["automated"]:
                    continue
                if not isinstance(row["request_id"], str) or not row["request_id"].strip():
                    return missing("error", "User message identity is missing")
                if row["target"] not in {None, "local", "cloud"}:
                    return missing("error", "Unsupported execution target")
                day = datetime.fromtimestamp(row["created_at"] / 1000, zone).date()
                inherited = row["forked"] and row["thread_created_at"] > row["created_at"]
                # Copies retain the request ID but replace its environment. Only
                # the original event can establish historical placement.
                if inherited:
                    location = "unknown"
                elif row["target"] == "cloud":
                    location = "cloud"
                elif row["host_id"] == local_host:
                    location = "local"
                elif row["registered_host"] is not None:
                    location = "cloud"
                else:
                    location = "unknown"
                for index in range(row["message_count"]):
                    fingerprint = signer.digest("user-message-location", row["request_id"], str(index))
                    previous = messages.get(fingerprint)
                    if previous is None or previous.location == "unknown":
                        messages[fingerprint] = UserMessage(fingerprint, day, location, source)
        unknown = sum(message.location == "unknown" for message in messages.values())
        return MessageScan(tuple(messages.values()), Coverage(
            MESSAGE_SOURCES[source], True, "partial" if unknown else "full",
            f"{len(messages)} human messages; {unknown} with unknown location",
        ))
    except (OSError, UnicodeError):
        return missing("unavailable", "Profile or local machine identity is unreadable")
    except (sqlite3.DatabaseError, ValueError, OverflowError, TypeError):
        return missing("error", "Message registry could not be read")
