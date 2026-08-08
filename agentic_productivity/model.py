from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date


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
    coverage: Coverage | None = None

    def add_session(self, day: date, session_id: str) -> None:
        self.session_ids[day].add(session_id)

    def add_prompt(self, day: date, count: int = 1) -> None:
        if count > 0:
            self.prompts[day] += count

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
