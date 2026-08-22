from __future__ import annotations

import os
from datetime import datetime, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _zone_name(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.removeprefix(":")
    marker = "/zoneinfo/"
    if marker in candidate:
        return candidate.split(marker, 1)[1]
    return None if candidate.startswith("/") else candidate


def local_timezone() -> tzinfo:
    candidates = [_zone_name(os.environ.get("TZ"))]
    try:
        candidates.append(_zone_name(os.readlink("/etc/localtime")))
    except OSError:
        pass
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            return ZoneInfo(candidate)
        except ZoneInfoNotFoundError:
            continue
    return datetime.now().astimezone().tzinfo or timezone.utc


def local_timezone_name() -> str:
    zone = local_timezone()
    return getattr(zone, "key", None) or datetime.now(zone).tzname() or str(zone)
