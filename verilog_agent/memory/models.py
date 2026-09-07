from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class RepairAttempt:
    task_id: str
    candidate_id: str
    parent_candidate_id: str
    failure_category: str
    error_signature: str
    module_family: str
    benchmark_problem_id: str | None
    before_mismatches: int | None
    after_mismatches: int | None
    mismatch_reduction: int | None
    passed: bool
    rationale: str
    expected_impact: str
    diff_path: str | None
    evidence_path: str | None
    memory_ids: list[str] = field(default_factory=list)
    attempt_id: str = field(default_factory=lambda: f"repair_{uuid4().hex[:16]}")
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MemoryItem:
    memory_id: str
    memory_type: str
    summary: str
    error_signature: str
    module_family: str
    applicability_conditions: str
    recommended_action: str
    source_task_id: str
    source_candidate_id: str
    source_problem_id: str | None
    before_score: float | None
    after_score: float | None
    verification_gate_reached: str
    toolchain_version: str
    evidence_hashes: list[str]
    confidence: float = 0.7
    positive_uses: int = 0
    negative_uses: int = 0
    created_at: str = field(default_factory=_now)
    last_used_at: str | None = None
    status: str = "ACTIVE"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
