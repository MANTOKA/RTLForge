from __future__ import annotations

from typing import Protocol

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


class CandidateEvaluator(Protocol):
    def evaluate(self, context: RTLTaskContext) -> EvaluationOutcome: ...


class Verifier(Protocol):
    def verify(self, context: RTLTaskContext, candidate: Candidate) -> VerificationOutcome: ...
