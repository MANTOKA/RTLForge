from __future__ import annotations

import pytest

from rtlforge.domain import Budget, EvaluationOutcome, TaskStage
from rtlforge.testing import FakeDebugger, FakeEvaluator, FakeVerifier
from tests.integration.test_fake_orchestrator import _orchestrator
from tests.integration.test_langgraph_workflow import _fake_graph


class StaticRepairFailure(FakeEvaluator):
    def __init__(self, category, always_fail):
        super().__init__()
        self.category = category
        self.always_fail = always_fail

    def evaluate(self, context):
        candidate = context.candidate()
        if candidate.candidate_id == "cand_repair_01" or (
            self.always_fail and candidate.parent_id is not None
        ):
            stage = "compile" if self.category == "SYNTAX_ERROR" else "lint"
            candidate.metadata["static_evaluation"] = {
                "results": {stage: {"errors": [{"message": "repair introduced syntax error"}]}},
            }
            return EvaluationOutcome(
                False, candidate.candidate_id, scores={candidate.candidate_id: 0},
                category=self.category, reason="repair failed static checks",
            )
        return super().evaluate(context)


class RecordingDebugger(FakeDebugger):
    def __init__(self):
        self.targets = []

    def repair(self, context, candidate):
        self.targets.append((candidate.candidate_id, context.failure_category))
        return super().repair(context, candidate)


@pytest.mark.parametrize("scheduler", ["legacy", "langgraph"])
@pytest.mark.parametrize("category", ["SYNTAX_ERROR", "LINT_ERROR"])
@pytest.mark.parametrize("always_fail", [False, True])
def test_static_repair_failure_keeps_debugger_priority(
    tmp_path, scheduler, category, always_fail,
):
    verifier = FakeVerifier(failures_before_pass=1)
    engine = (_fake_graph(tmp_path, verifier) if scheduler == "langgraph"
              else _orchestrator(tmp_path / "audit.sqlite", verifier))
    ops = engine.operations if scheduler == "langgraph" else engine
    ops.evaluator = StaticRepairFailure(category, always_fail)
    debugger = RecordingDebugger()
    ops.debugger = debugger
    try:
        task = engine.create_task("counter", budget=Budget(max_candidates=2, max_repairs=3))
        result = engine.run_task(task.task_id)
        assert debugger.targets[:2] == [
            ("cand_01", "SIMULATION_FUNCTIONAL_FAIL"), ("cand_repair_01", category),
        ]
        assert result.budget.generated_candidates == 1
        assert not result.metadata.get("candidate_regenerations")
        if always_fail:
            assert result.current_stage is TaskStage.FAILED
            assert result.failure_category == "BUDGET_EXHAUSTED"
            assert result.budget.repair_attempts == 3
        else:
            assert result.current_stage is TaskStage.COMPLETED
            assert result.selected_candidate_id == "cand_repair_02"
            assert result.budget.repair_attempts == 2
    finally:
        if scheduler == "langgraph":
            engine.close()
