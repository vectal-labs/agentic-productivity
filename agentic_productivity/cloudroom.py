from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from .bb import BbPlacement
from .model import Coverage


def scan_cloudroom(home: Path) -> BbPlacement:
    """Read placement metadata only; never query prompts or connection credentials."""
    root = home / ".gui-cloudroom"

    def missing(status: str, detail: str, *, installed: bool = True) -> BbPlacement:
        return BbPlacement(None, None, None, Coverage("Cloudroom placement", installed, status, detail))

    try:
        root.stat()
    except FileNotFoundError:
        return missing("absent", "Cloudroom profile is absent", installed=False)
    except OSError:
        return missing("unavailable", "Cloudroom profile is unreadable")
    try:
        local_host = (root / "host-id").read_text(encoding="utf-8").strip()
        if not local_host:
            return missing("unavailable", "Cloudroom local machine identity is missing")
        # Do not use immutable=1: the GUI can be writing to the WAL while we scan.
        with closing(sqlite3.connect((root / "bb.db").as_uri() + "?mode=ro", uri=True, timeout=2)) as connection:
            rows = connection.execute("""
                SELECT t.id, t.execution_target, t.visibility, e.host_id,
                    h.id, h.destroyed_at
                FROM threads t
                LEFT JOIN environments e ON e.id = t.environment_id
                LEFT JOIN hosts h ON h.id = e.host_id
                WHERE t.archived_at IS NULL AND t.deleted_at IS NULL
            """)
            local = cloud = unknown = 0
            seen: set[str] = set()
            for identity, target, visibility, host, registered_host, destroyed_at in rows:
                if not isinstance(identity, str) or target not in {"local", "cloud"} or visibility not in {"visible", "hidden"}:
                    return missing("error", "Cloudroom returned unsupported placement data")
                if visibility == "hidden" or identity in seen:
                    continue
                seen.add(identity)
                if target == "cloud":
                    cloud += 1
                elif host == local_host:
                    local += 1
                elif registered_host is not None and destroyed_at is None:
                    cloud += 1
                else:
                    unknown += 1
        return BbPlacement(
            local, cloud, unknown,
            Coverage("Cloudroom placement", True, "partial" if unknown else "full",
                     f"{local + cloud + unknown} open threads; {unknown} with unknown placement"),
        )
    except (OSError, UnicodeError):
        return missing("unavailable", "Cloudroom local machine identity is unreadable")
    except sqlite3.DatabaseError:
        return missing("error", "Cloudroom registry could not be read")
