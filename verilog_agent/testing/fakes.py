from __future__ import annotations

from hashlib import sha256

from verilog_agent.domain.enums import PortDirection
from verilog_agent.domain.models import (
    Candidate,
    EvaluationOutcome,
    PlanResult,
    PortSpec,
    RTLTaskContext,
    SpecIR,
    TaskTreeNode,
    VerificationOutcome,
)


class FakePlanner:
    def plan(self, context: RTLTaskContext) -> PlanResult:
        criteria = ["counter increments on each rising edge", "reset clears the counter"]
        spec_ir = SpecIR(
            module_name="counter",
            raw_spec=context.raw_spec,
            ports=[
                PortSpec("clk", PortDirection.INPUT),
                PortSpec("rst", PortDirection.INPUT),
                PortSpec("q", PortDirection.OUTPUT, width=4),
            ],
            clock="clk",
            reset="rst",
            assumptions=["synchronous active-high reset"],
            acceptance_criteria=criteria,
        )
        root = TaskTreeNode(
            node_id="root",
            title="Implement counter",
            description=context.raw_spec,
            acceptance_criteria=criteria,
            children=[
                TaskTreeNode(
                    node_id="rtl",
                    title="Generate RTL",
                    description="Create synthesizable counter RTL",
                ),
                TaskTreeNode(
                    node_id="verify",
                    title="Verify behavior",
                    description="Check reset and increment behavior",
                ),
            ],
        )
        return PlanResult(spec_ir=spec_ir, task_tree_root=root)


class FakeGenerator:
    def generate(self, context: RTLTaskContext, candidate_count: int) -> list[Candidate]:
        candidates = []
        module_name = context.spec_ir.module_name if context.spec_ir else "rtl"
        first_index = 1 + sum(candidate.parent_id is None for candidate in context.candidates)
        for index in range(first_index, first_index + candidate_count):
            candidate_id = f"cand_{index:02d}"
            digest = sha256(f"{context.task_id}:{candidate_id}".encode()).hexdigest()
            candidates.append(
                Candidate(
                    candidate_id=candidate_id,
                    artifact_path=(
                        f"runs/{context.task_id}/candidates/{candidate_id}/{module_name}.v"
                    ),
                    code_hash=digest,
                    design_pattern="single-process" if index == 1 else "split-control-data",
                )
            )
        return candidates


class FakeEvaluator:
    def __init__(self, *, reject: bool = False) -> None:
        self.reject = reject

    def evaluate(self, context: RTLTaskContext) -> EvaluationOutcome:
        if self.reject or not context.candidates:
            return EvaluationOutcome(False, None, reason="no acceptable fake candidate")
        selected = context.candidates[-1]
        return EvaluationOutcome(
            accepted=True,
            selected_candidate_id=selected.candidate_id,
            scores={
                candidate.candidate_id: 90.0 + index
                for index, candidate in enumerate(context.candidates)
            },
        )


class FakeVerifier:
    def __init__(self, *, failures_before_pass: int = 0, always_fail: bool = False) -> None:
        self.failures_before_pass = failures_before_pass
        self.always_fail = always_fail
        self.calls = 0

    def verify(self, context: RTLTaskContext, candidate: Candidate) -> VerificationOutcome:
        self.calls += 1
        should_fail = self.always_fail or self.calls <= self.failures_before_pass
        if should_fail:
            return VerificationOutcome(
                passed=False,
                category="SIMULATION_FUNCTIONAL_FAIL",
                evidence_path=f"runs/{context.task_id}/tool_runs/verify_{self.calls:03d}.json",
            )
        return VerificationOutcome(
            passed=True,
            category="OK",
            evidence_path=f"runs/{context.task_id}/tool_runs/verify_{self.calls:03d}.json",
        )


class FakeDebugger:
    def repair(self, context: RTLTaskContext, candidate: Candidate) -> Candidate:
        repair_number = context.budget.repair_attempts
        candidate_id = f"cand_repair_{repair_number:02d}"
        digest = sha256(
            f"{context.task_id}:{candidate.candidate_id}:{candidate_id}".encode()
        ).hexdigest()
        return Candidate(
            candidate_id=candidate_id,
            artifact_path=(
                f"runs/{context.task_id}/candidates/{candidate_id}/"
                f"{context.spec_ir.module_name if context.spec_ir else 'rtl'}.v"
            ),
            code_hash=digest,
            parent_id=candidate.candidate_id,
            generation=candidate.generation + 1,
            design_pattern=candidate.design_pattern,
            metadata={"fake_repair": True},
        )
