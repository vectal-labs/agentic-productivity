from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .database import Database
from .fingerprints import is_fingerprint
from .model import Coverage


SNAPSHOT_VERSION = 1
ALLOWED_SNAPSHOT_KEYS = {
    "v",
    "machine",
    "slot",
    "collected_at",
    "timezone",
    "start",
    "end",
    "fingerprints",
    "coverage",
}
ALLOWED_COVERAGE_KEYS = {"day", "harness", "status", "detail", "collected_at"}
IP_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def snapshot_directory(state_dir: Path) -> Path:
    return state_dir / "snapshots"


def snapshot_path(state_dir: Path, machine: str, slot: int) -> Path:
    return snapshot_directory(state_dir) / f"{machine}-{slot}.json"


def collection_slot(now: datetime) -> int:
    return int(now.timestamp()) // 300


def _write_private_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(value, separators=(",", ":")) + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def export_snapshot(
    database: Database,
    *,
    machine: str,
    slot: int,
    now: datetime,
    start: date,
    end: date,
    timezone_name: str,
    coverage: list[Coverage],
) -> dict[str, Any]:
    fingerprints = [
        [row["day"], row["metric"], row["harness"], row["fingerprint"]]
        for row in database.fingerprints_between(start, end)
    ]
    payload = {
        "v": SNAPSHOT_VERSION,
        "machine": machine,
        "slot": slot,
        "collected_at": now.isoformat(),
        "timezone": timezone_name,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "fingerprints": fingerprints,
        "coverage": [
            {
                "day": end.isoformat(),
                "harness": item.harness,
                "status": item.status,
                "detail": item.detail,
                "collected_at": now.isoformat(),
            }
            for item in coverage
        ],
    }
    validate_snapshot(payload)
    return payload


def write_snapshot(state_dir: Path, payload: dict[str, Any]) -> Path:
    path = snapshot_path(state_dir, str(payload["machine"]), int(payload["slot"]))
    _write_private_json(path, payload)
    return path


def validate_snapshot(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("snapshot must be an object")
    extra = set(payload) - ALLOWED_SNAPSHOT_KEYS
    if extra:
        raise ValueError("snapshot contains unsupported fields")
    if payload.get("v") != SNAPSHOT_VERSION:
        raise ValueError("unsupported snapshot version")
    machine = payload.get("machine")
    if machine not in {"mac", "cloud"}:
        raise ValueError("snapshot machine is invalid")
    slot = payload.get("slot")
    if not isinstance(slot, int) or slot < 0:
        raise ValueError("snapshot slot is invalid")
    for key in ("collected_at", "timezone", "start", "end"):
        if not isinstance(payload.get(key), str) or not payload[key]:
            raise ValueError(f"snapshot {key} is invalid")
    rows = payload.get("fingerprints")
    if not isinstance(rows, list):
        raise ValueError("snapshot fingerprints are invalid")
    for row in rows:
        if not isinstance(row, list) or len(row) != 4:
            raise ValueError("snapshot fingerprint row is invalid")
        day, metric, harness, fingerprint = row
        if metric not in {"sessions", "prompts"}:
            raise ValueError("snapshot metric is invalid")
        if not isinstance(day, str) or not isinstance(harness, str):
            raise ValueError("snapshot fingerprint fields are invalid")
        if not is_fingerprint(str(fingerprint)):
            raise ValueError("snapshot fingerprint is invalid")
    coverage = payload.get("coverage")
    if not isinstance(coverage, list):
        raise ValueError("snapshot coverage is invalid")
    for item in coverage:
        if not isinstance(item, dict) or set(item) - ALLOWED_COVERAGE_KEYS:
            raise ValueError("snapshot coverage row is invalid")
    return payload


def load_snapshot(path: Path) -> dict[str, Any]:
    return validate_snapshot(json.loads(path.read_text(encoding="utf-8")))


def payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_remote_config(state_dir: Path) -> dict[str, Any] | None:
    path = state_dir / "remote.json"
    if not path.is_file():
        return None
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("remote config is invalid")
    return config


def pull_snapshots(state_dir: Path, *, timeout: int = 45) -> dict[str, Any]:
    config = load_remote_config(state_dir)
    if config is None:
        return {"status": "missing", "detail": "cloud SSH config is absent", "imported": 0}
    ssh = config.get("ssh")
    remote_dir = config.get("remote_snapshots")
    if not isinstance(ssh, dict) or not isinstance(remote_dir, str):
        return {"status": "error", "detail": "cloud SSH config is incomplete", "imported": 0}
    local_dir = snapshot_directory(state_dir) / "incoming"
    local_dir.mkdir(parents=True, exist_ok=True)
    local_dir.chmod(0o700)
    argv = [
        "ssh",
        "-i",
        str(ssh["identity_file"]),
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={ssh['known_hosts']}",
        f"{ssh['user']}@{ssh['host']}",
        f"python3 -c 'import os,sys; print(\"\\n\".join(sorted(os.listdir(sys.argv[1]))))' {remote_dir}",
    ]
    if ssh.get("port"):
        argv[1:1] = ["-p", str(ssh["port"])]
    try:
        listed = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "error", "detail": "cloud SSH listing failed", "imported": 0}
    if listed.returncode != 0:
        return {"status": "error", "detail": "cloud SSH listing failed", "imported": 0}
    names = [line.strip() for line in listed.stdout.splitlines() if line.strip().endswith(".json")]
    copied = 0
    for name in names:
        destination = local_dir / name
        if destination.is_file():
            continue
        scp = [
            "scp",
            "-i",
            str(ssh["identity_file"]),
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={ssh['known_hosts']}",
            f"{ssh['user']}@{ssh['host']}:{remote_dir}/{name}",
            str(destination),
        ]
        if ssh.get("port"):
            scp[1:1] = ["-P", str(ssh["port"])]
        completed = subprocess.run(scp, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=timeout, check=False)
        if completed.returncode != 0:
            destination.unlink(missing_ok=True)
            return {"status": "error", "detail": "cloud snapshot copy failed", "imported": copied}
        destination.chmod(0o600)
        copied += 1
    return {"status": "ok", "detail": f"copied {copied} new snapshots", "imported": copied}


def redact_transport_text(value: str) -> str:
    return IP_RE.sub("<ip>", value)
