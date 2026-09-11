from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .model import Coverage


@dataclass(frozen=True)
class BbPlacement:
    local: int | None
    cloud: int | None
    unknown: int | None
    coverage: Coverage


def _executable(home: Path) -> str | None:
    if command := shutil.which("bb"):
        return command
    # launchd has a minimal PATH; the desktop app ships a standalone CLI.
    bundled = "bb.app/Contents/Resources/app.asar.unpacked/node_modules/bb-app/host-daemon/dist/bb"
    for path in (
        home / ".local/bin/bb",
        home / ".bb/bin/bb",
        Path("/Applications") / bundled,
        home / "Applications" / bundled,
    ):
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def _read_list(command: str, *arguments: str) -> list[dict]:
    environment = os.environ.copy()
    # The packaged CLI uses /usr/bin/env node; launchd omits Homebrew's PATH.
    environment["PATH"] = os.pathsep.join(filter(None, (
        environment.get("PATH"), str(Path(command).parent),
        "/opt/homebrew/bin", "/usr/local/bin",
    )))
    result = subprocess.run(
        [command, *arguments, "--json"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=True,
        timeout=20,
        env=environment,
    )
    rows = json.loads(result.stdout)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("unsupported BB list")
    return rows


def scan_bb(home: Path) -> BbPlacement:
    """Count placement, never persist BB identities or conversation contents."""
    data_dir = Path(os.environ.get("BB_DATA_DIR", home / ".bb")).expanduser()

    def missing(status: str, detail: str, *, installed: bool = True) -> BbPlacement:
        return BbPlacement(None, None, None, Coverage("BB placement", installed, status, detail))

    if not data_dir.is_dir():
        return missing("absent", "BB data directory is absent", installed=False)
    try:
        local_host = (data_dir / "host-id").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return missing("unavailable", "BB local machine identity is unreadable")
    if not local_host:
        return missing("unavailable", "BB local machine identity is missing")
    command = _executable(home)
    if command is None:
        return missing("unavailable", "BB CLI is unavailable")
    try:
        hosts = _read_list(command, "machine", "list")
        threads = _read_list(command, "thread", "list", "--include-hidden")
        if any(not isinstance(h.get("id"), str) or not isinstance(h.get("status"), str) for h in hosts):
            raise ValueError("unsupported BB hosts")
        known_hosts = {h["id"] for h in hosts if h["status"] != "destroyed"}
        if local_host not in known_hosts:
            return missing("unavailable", "BB does not recognize this Mac's machine identity")
        local = cloud = unknown = 0
        seen: set[str] = set()
        for thread in threads:
            if (
                not isinstance(thread.get("id"), str)
                or not {"archivedAt", "deletedAt", "visibility", "environmentHostId"} <= thread.keys()
                or thread["visibility"] not in {"visible", "hidden"}
            ):
                raise ValueError("unsupported BB threads")
            if (
                thread["archivedAt"] is not None
                or thread["deletedAt"] is not None
                or thread["visibility"] != "visible"
                or thread["id"] in seen
            ):
                continue
            seen.add(thread["id"])
            host = thread["environmentHostId"]
            if host is not None and not isinstance(host, str):
                raise ValueError("unsupported BB placement")
            if host == local_host:
                local += 1
            elif host in known_hosts:
                cloud += 1
            else:
                unknown += 1
        return BbPlacement(
            local, cloud, unknown,
            Coverage("BB placement", True, "partial" if unknown else "full",
                     f"{local + cloud + unknown} open threads; {unknown} with unknown placement"),
        )
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError):
        return missing("unavailable", "BB could not be queried")
    except (ValueError, TypeError):
        return missing("error", "BB returned unsupported data")
