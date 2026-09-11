from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .fingerprints import FingerprintSigner


@dataclass(frozen=True)
class Coverage:
    harness: str
    installed: bool
    status: str
    detail: str = ""

    def __post_init__(self) -> None:
        if self.status not in {"full", "partial", "unavailable", "absent", "error"}:
            raise ValueError(f"invalid coverage status: {self.status}")


@dataclass
class HarnessResult:
    harness: str
    session_ids: dict[date, set[str]] = field(
        default_factory=lambda: defaultdict(set)
    )
    prompts: Counter[date] = field(default_factory=Counter)
    prompt_fingerprints: dict[date, set[str]] = field(
        default_factory=lambda: defaultdict(set)
    )
    coverage: Coverage | None = None
    signer: FingerprintSigner | None = None
    ambiguous_prompts: int = 0

    def add_session(self, day: date, session_id: str) -> None:
        token = session_id
        if self.signer is not None:
            token = self.signer.session(self.harness, session_id)
        self.session_ids[day].add(token)

    def add_prompt(
        self,
        day: date,
        count: int = 1,
        *,
        entry_id: Any = None,
        timestamp: Any = None,
        session_id: str = "",
        role: str = "",
        content: Any = None,
        ordinal: int | None = None,
    ) -> None:
        if self.signer is None:
            if count > 0:
                self.prompts[day] += count
            return
        fingerprint = self.signer.prompt(
            self.harness,
            entry_id=entry_id,
            timestamp=timestamp,
            session_id=session_id,
            role=role,
            content=content,
            ordinal=ordinal,
        )
        if fingerprint is None:
            self.ambiguous_prompts += max(1, count)
            return
        if fingerprint not in self.prompt_fingerprints[day]:
            self.prompt_fingerprints[day].add(fingerprint)
            self.prompts[day] += 1

    def session_counts(self) -> dict[date, int]:
        return {day: len(ids) for day, ids in self.session_ids.items()}


@dataclass(frozen=True)
class CommitResult:
    counts: dict[date, int]
    coverage: Coverage


@dataclass(frozen=True)
class Collection:
    start: date
    end: date
    commits: CommitResult
    harnesses: tuple[HarnessResult, ...]
