from __future__ import annotations

from typing import Any, Protocol

from verilog_agent.domain.models import (
    Candidate,
    EvaluationOutcome,
    PlanResult,
    RTLTaskContext,
    VerificationOutcome,
)


class Planner(Protocol):
    def plan(self, context: RTLTaskContext) -> PlanResult: ...


class Generator(Protocol):
    def generate(self, context: RTLTaskContext, candidate_count: int) -> list[Candidate]: ...


class Debugger(Protocol):
    def repair(self, context: RTLTaskContext, candidate: Candidate) -> Candidate: ...


class ReflectionRouter(Protocol):
    def route(self, category: str | None, feedback: dict[str, Any] | None = None) -> Any: ...


class CandidateEvaluator(Protocol):
    def evaluate(self, context: RTLTaskContext) -> EvaluationOutcome: ...


class Verifier(Protocol):
    def verify(self, context: RTLTaskContext, candidate: Candidate) -> VerificationOutcome: ...
