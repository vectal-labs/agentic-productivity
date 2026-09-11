from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HEX64 = re.compile(r"^[0-9a-f]{64}$")
PROMPT_DOMAINS = {"prompt", "prompt-content", "prompt-header", "prompt-ordinal"}


def canonical_instruction(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if content is None:
        return ""
    return json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)


def normalize_timestamp(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    try:
        if isinstance(value, (int, float)):
            number = float(value)
            if number > 10_000_000_000:
                number /= 1000
            parsed = datetime.fromtimestamp(number, timezone.utc)
        elif isinstance(value, str):
            text = value.strip()
            if not text:
                return ""
            if re.fullmatch(r"\d+(?:\.\d+)?", text):
                return normalize_timestamp(float(text))
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        elif isinstance(value, datetime):
            parsed = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        else:
            return str(value).strip()
    except (OverflowError, OSError, TypeError, ValueError):
        return str(value).strip()
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class FingerprintSigner:
    def __init__(self, key: bytes):
        if len(key) < 16:
            raise ValueError("fingerprint key is too short")
        self._key = key

    def digest(self, domain: str, *parts: str) -> str:
        message = domain.encode("utf-8") + b"\x00"
        for part in parts:
            data = part.encode("utf-8")
            message += len(data).to_bytes(4, "big") + data
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()

    def session(self, harness: str, native_id: str) -> str:
        return self.digest("session", harness, native_id)

    def prompt(
        self,
        harness: str,
        *,
        entry_id: Any = None,
        timestamp: Any = None,
        session_id: str = "",
        role: str = "",
        content: Any = None,
        ordinal: int | None = None,
    ) -> str | None:
        if ordinal is not None:
            if not session_id:
                return None
            return self.digest("prompt-ordinal", harness, session_id, str(ordinal))
        ident = "" if entry_id is None else str(entry_id).strip()
        stamp = normalize_timestamp(timestamp)
        if ident:
            return self.digest("prompt", harness, ident, stamp)
        if session_id and stamp:
            canonical = canonical_instruction(content)
            if canonical:
                return self.digest(
                    "prompt-content", harness, session_id, stamp, role, canonical
                )
            return self.digest("prompt-header", harness, session_id, stamp, role)
        return None


def fingerprint_key_path(state_dir: Path) -> Path:
    return state_dir / "fingerprint.key"


def load_fingerprint_key(state_dir: Path, *, create: bool) -> bytes:
    path = fingerprint_key_path(state_dir)
    if path.is_file():
        key = path.read_bytes()
        if len(key) < 16:
            raise RuntimeError("fingerprint key is invalid")
        return key
    if not create:
        raise FileNotFoundError(str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    path.write_bytes(key)
    path.chmod(0o600)
    return key


def is_fingerprint(value: str) -> bool:
    return bool(HEX64.fullmatch(value))
