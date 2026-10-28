from __future__ import annotations

from pathlib import Path

import pytest

from rtlforge.domain import (
    Budget,
    Candidate,
    EvaluationOutcome,
    EventType,
    TaskStage,
    VerificationOutcome,
)
from rtlforge.memory import SQLiteMemoryStore
from rtlforge.orchestration import Orchestrator, SQLiteTaskStore
from rtlforge.testing import (
    FakeDebugger,
    FakeEvaluator,
    FakeGenerator,
    FakePlanner,
    FakeVerifier,
)


def _orchestrator(database: Path, verifier: FakeVerifier | None = None) -> Orchestrator:
    return Orchestrator(
        planner=FakePlanner(),
        generator=FakeGenerator(),
        debugger=FakeDebugger(),
        evaluator=FakeEvaluator(),
        verifier=verifier or FakeVerifier(),
        store=SQLiteTaskStore(database),
    )


def _event_types(store: SQLiteTaskStore, task_id: str) -> list[EventType]:
    events = store.list_events(task_id)
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    return [event.event_type for event in events]


def test_fake_agents_complete_happy_path(tmp_path: Path) -> None:
    database = tmp_path / "happy.sqlite"
    orchestrator = _orchestrator(database)
    context = orchestrator.create_task("Create a four-bit counter", task_id="task_happy")

    result = orchestrator.run_task(context.task_id)

    assert result.current_stage is TaskStage.COMPLETED
    assert result.selected_candidate_id == "cand_01"
    assert result.budget.generated_candidates == 1
    event_types = _event_types(orchestrator.store, result.task_id)
    assert EventType.PLAN_DONE in event_types
    assert EventType.CANDIDATES_GENERATED in event_types
    assert EventType.SYSTEM_SUCCESS in event_types


def test_fake_debugger_repairs_one_failure(tmp_path: Path) -> None:
    orchestrator = _orchestrator(
        tmp_path / "repair.sqlite", FakeVerifier(failures_before_pass=1)
    )
    context = orchestrator.create_task("Create a counter", task_id="task_repair")

    result = orchestrator.run_task(context.task_id)

    assert result.current_stage is TaskStage.COMPLETED
    assert result.budget.repair_attempts == 1
    assert result.selected_candidate_id == "cand_repair_01"
    assert len(result.candidates) == 2
    event_types = _event_types(orchestrator.store, result.task_id)
    assert EventType.REPAIR_PROPOSED in event_types
    assert EventType.PATCH_ACCEPTED in event_types
    assert EventType.REFLECTION_ROUTED in event_types


def test_repair_budget_exhaustion_fails_and_rolls_back(tmp_path: Path) -> None:
    orchestrator = _orchestrator(tmp_path / "budget.sqlite", FakeVerifier(always_fail=True))
    context = orchestrator.create_task(
        "Create a counter",
        task_id="task_budget",
        budget=Budget(max_candidates=2, max_repairs=1),
    )

    result = orchestrator.run_task(context.task_id)

    assert result.current_stage is TaskStage.FAILED
    assert result.failure_category == "BUDGET_EXHAUSTED"
    assert result.selected_candidate_id == "cand_01"
    event_types = _event_types(orchestrator.store, result.task_id)
    assert EventType.PATCH_ROLLED_BACK in event_types
    assert EventType.BUDGET_EXHAUSTED in event_types


def test_cancel_request_reaches_terminal_cancelled_state(tmp_path: Path) -> None:
    orchestrator = _orchestrator(tmp_path / "cancel.sqlite")
    context = orchestrator.create_task("Create a counter", task_id="task_cancel")
    orchestrator.request_cancel(context.task_id)

    result = orchestrator.run_task(context.task_id)

    assert result.current_stage is TaskStage.CANCELLED
    event_types = _event_types(orchestrator.store, result.task_id)
    assert EventType.CANCEL_REQUESTED in event_types
    assert EventType.TASK_CANCELLED in event_types


def test_new_process_can_resume_from_checkpoint(tmp_path: Path) -> None:
    database = tmp_path / "recovery.sqlite"
    first_process = _orchestrator(database)
    created = first_process.create_task("Create a counter", task_id="task_recovery")
    first_process.advance_task(created.task_id)
    first_process.advance_task(created.task_id)
    assert created.current_stage is TaskStage.PLANNED

    second_process = _orchestrator(database)
    recovered = second_process.load_task("task_recovery")
    recovered_version = recovered.checkpoint_version
    result = second_process.run_task(recovered.task_id)

    assert recovered_version > 1
    assert recovered.spec_ir is not None
    assert result.current_stage is TaskStage.COMPLETED
    assert result.checkpoint_version > recovered_version


class _RejectSyntaxOnce(FakeEvaluator):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def evaluate(self, context):
        self.calls += 1
        if self.calls == 1:
            return EvaluationOutcome(
                accepted=False,
                selected_candidate_id="cand_01",
                reason="compile failed",
                category="SYNTAX_ERROR",
            )
        return super().evaluate(context)


def test_static_syntax_failure_generates_fresh_candidate(tmp_path: Path) -> None:
    evaluator = _RejectSyntaxOnce()
    orchestrator = Orchestrator(
        planner=FakePlanner(),
        generator=FakeGenerator(),
        debugger=FakeDebugger(),
        evaluator=evaluator,
        verifier=FakeVerifier(),
        store=SQLiteTaskStore(tmp_path / "static-repair.sqlite"),
    )
    context = orchestrator.create_task("Create a counter", task_id="task_static_repair")

    result = orchestrator.run_task(context.task_id)

    assert result.current_stage is TaskStage.COMPLETED
    assert result.budget.repair_attempts == 0
    assert result.selected_candidate_id == "cand_02"
    assert result.budget.generated_candidates == 2


class _NoProgressThenPassVerifier:
    def __init__(self) -> None:
        self.calls = 0

    def verify(
        self, context, candidate: Candidate
    ) -> VerificationOutcome:
        self.calls += 1
        if self.calls == 1:
            attempts = [_functional_attempt(candidate.candidate_id, 76)]
            passed = False
            selected = candidate.candidate_id
        elif self.calls == 2:
            attempts = [_functional_attempt(candidate.candidate_id, 76)]
            passed = False
            selected = candidate.candidate_id
        else:
            attempts = [_functional_attempt(candidate.candidate_id, 0)]
            passed = True
            selected = candidate.candidate_id
        context.metadata["verification_attempts"] = attempts
        context.metadata.setdefault("verification_history", []).append(
            {
                "cycle": self.calls,
                "passed": passed,
                "category": "OK" if passed else "FUNCTIONAL_MISMATCH",
                "selected_candidate_id": selected,
                "attempts": attempts,
            }
        )
        return VerificationOutcome(
            passed=passed,
            category="OK" if passed else "FUNCTIONAL_MISMATCH",
            details={"selected_candidate_id": selected},
        )


class _TrackingDebugger(FakeDebugger):
    def __init__(self) -> None:
        self.parent_ids: list[str] = []

    def repair(self, context, candidate: Candidate) -> Candidate:
        self.parent_ids.append(candidate.candidate_id)
        return super().repair(context, candidate)


class _MemoryDebugger(FakeDebugger):
    def repair(self, context, candidate: Candidate) -> Candidate:
        repaired = super().repair(context, candidate)
        repaired.metadata.update(
            rationale="Correct the counter boundary condition.",
            expected_impact="Remove the functional mismatch.",
        )
        return repaired


def _functional_attempt(candidate_id: str, mismatches: int) -> dict:
    return {
        "candidate_id": candidate_id,
        "status": "PASS" if mismatches == 0 else "FAIL",
        "category": "OK" if mismatches == 0 else "FUNCTIONAL_MISMATCH",
        "debugger_feedback": {
            "category": "FUNCTIONAL_MISMATCH",
            "mismatch_summary": {
                "total_mismatched_samples": mismatches,
                "total_samples": 1003,
            },
        },
    }


def test_no_progress_repair_uses_remaining_repairs_before_regeneration(
    tmp_path: Path,
) -> None:
    debugger = _TrackingDebugger()
    orchestrator = Orchestrator(
        planner=FakePlanner(),
        generator=FakeGenerator(),
        debugger=debugger,
        evaluator=FakeEvaluator(),
        verifier=_NoProgressThenPassVerifier(),
        store=SQLiteTaskStore(tmp_path / "fallback.sqlite"),
    )
    context = orchestrator.create_task(
        "Create a counter",
        task_id="task_fallback",
        budget=Budget(max_candidates=2, max_repairs=2),
    )
    context.metadata["verification_order"] = ["cand_repair_01"]
    context.metadata["initial_verification_order"] = ["cand_02", "cand_01"]

    result = orchestrator.run_task(context.task_id)

    assert result.current_stage is TaskStage.COMPLETED
    assert debugger.parent_ids == ["cand_01", "cand_01"]
    assert result.selected_candidate_id == "cand_repair_02"
    assert result.budget.generated_candidates == 1
    assert not result.metadata.get("candidate_regenerations")
    assert result.metadata["repair_attempts"][0]["mismatch_reduction"] == 0
    assert len(result.metadata["repair_attempts"]) == 2


@pytest.mark.parametrize("before, after, expected_no_progress", [
    (60, 60, True), (60, 55, True), (10, 60, False),
])
def test_static_quality_repair_progress_uses_score_delta(
    tmp_path: Path, before: int, after: int, expected_no_progress: bool,
) -> None:
    orchestrator = _orchestrator(tmp_path / "static_progress.sqlite")
    context = orchestrator.create_task("counter")
    parent = Candidate("cand_01", "unused.v", "parent")
    child = Candidate("cand_repair_01", "unused.v", "child", parent_id="cand_01")
    parent.metadata["static_evaluation"] = {"quality_score": before}
    child.metadata["static_evaluation"] = {"quality_score": after}
    context.candidates = [parent, child]
    context.failure_category = "QUALITY_ERROR"

    analysis = orchestrator._repair_progress(context, child)

    assert analysis["before_static_score"] == before
    assert analysis["after_static_score"] == after
    assert analysis["static_score_gain"] == after - before
    assert analysis["no_progress"] is expected_no_progress


@pytest.mark.parametrize("score, roots", [(74, 2), (75, 1), (100, 1)])
def test_static_threshold_controls_sequential_generation(tmp_path, score, roots):
    class Evaluator(FakeEvaluator):
        def evaluate(self, context):
            candidate = context.candidate()
            value = score if candidate.candidate_id == "cand_01" else 90
            return EvaluationOutcome(True, candidate.candidate_id, scores={
                candidate.candidate_id: value,
            })

    orchestrator = _orchestrator(tmp_path / "threshold.sqlite")
    orchestrator.evaluator = Evaluator()
    context = orchestrator.create_task("counter")
    result = orchestrator.run_task(context.task_id)
    assert result.current_stage is TaskStage.COMPLETED
    assert result.budget.generated_candidates == roots
    assert result.budget.repair_attempts == 0


@pytest.mark.parametrize("cap", [1, 2, 3])
def test_repair_exhaustion_does_not_spend_unused_candidate_slots(tmp_path, cap):
    orchestrator = _orchestrator(tmp_path / "cap.sqlite", FakeVerifier(always_fail=True))
    context = orchestrator.create_task("counter", budget=Budget(max_candidates=cap, max_repairs=1))
    result = orchestrator.run_task(context.task_id)
    assert result.current_stage is TaskStage.FAILED
    assert result.budget.generated_candidates == 1
    assert result.budget.repair_attempts == 1
    assert [c.candidate_id for c in result.candidates if c.parent_id is None] == ["cand_01"]


def test_unchanged_debugger_output_exhausts_repairs_without_regeneration(tmp_path):
    from rtlforge.agents.validation import RepairNoProgressError

    class Debugger(FakeDebugger):
        def repair(self, context, candidate):
            raise RepairNoProgressError("Debugger returned an unchanged candidate")

    orchestrator = _orchestrator(
        tmp_path / "unchanged.sqlite", FakeVerifier(failures_before_pass=1)
    )
    orchestrator.debugger = Debugger()
    context = orchestrator.create_task(
        "counter", budget=Budget(max_candidates=2, max_repairs=1)
    )
    result = orchestrator.run_task(context.task_id)
    assert result.current_stage is TaskStage.FAILED
    assert result.failure_category == "BUDGET_EXHAUSTED"
    assert result.budget.generated_candidates == 1
    assert result.budget.repair_attempts == 1


def test_unchanged_debugger_retries_with_remaining_repair_budget(tmp_path):
    from rtlforge.agents.validation import RepairNoProgressError

    class Debugger(FakeDebugger):
        calls = 0

        def repair(self, context, candidate):
            self.calls += 1
            if self.calls == 1:
                raise RepairNoProgressError("Debugger returned an unchanged candidate")
            assert context.metadata["last_repair_analysis"]["reason"] == "unchanged_rtl"
            return super().repair(context, candidate)

    orchestrator = _orchestrator(
        tmp_path / "unchanged_retry.sqlite", FakeVerifier(failures_before_pass=1)
    )
    debugger = Debugger()
    orchestrator.debugger = debugger
    context = orchestrator.create_task(
        "counter", budget=Budget(max_candidates=1, max_repairs=2)
    )
    result = orchestrator.run_task(context.task_id)

    assert result.current_stage is TaskStage.COMPLETED
    assert result.budget.repair_attempts == 2
    assert debugger.calls == 2
    assert result.selected_candidate_id == "cand_repair_02"


def test_unchanged_functional_repair_preserves_no_progress_for_next_prompt(tmp_path):
    from rtlforge.agents.validation import RepairNoProgressError

    class Evaluator(FakeEvaluator):
        def evaluate(self, context):
            if context.selected_candidate_id == "cand_01":
                return EvaluationOutcome(True, "cand_01", scores={"cand_01": 70})
            return super().evaluate(context)

    class Debugger(FakeDebugger):
        calls = 0

        def repair(self, context, candidate):
            self.calls += 1
            if self.calls == 2:
                raise RepairNoProgressError("Debugger returned an unchanged candidate")
            if self.calls == 3:
                analysis = context.metadata["last_repair_analysis"]
                assert analysis["reason"] == "unchanged_rtl"
                assert analysis["candidate_id"] == candidate.candidate_id
            return super().repair(context, candidate)

    orchestrator = _orchestrator(
        tmp_path / "unchanged_functional.sqlite", FakeVerifier(failures_before_pass=1)
    )
    orchestrator.evaluator = Evaluator()
    debugger = Debugger()
    orchestrator.debugger = debugger
    context = orchestrator.create_task(
        "counter", budget=Budget(max_candidates=1, max_repairs=3)
    )

    result = orchestrator.run_task(context.task_id)

    assert result.current_stage is TaskStage.COMPLETED
    assert debugger.calls == 3


def test_resume_at_regeneration_keeps_candidate_number_and_budget(tmp_path):
    database = tmp_path / "regenerate.sqlite"
    first = _orchestrator(database)
    first.evaluator = _RejectSyntaxOnce()
    context = first.create_task("counter")
    for _ in range(5):
        first.advance_task(context.task_id)
    assert context.current_stage is TaskStage.GENERATING
    assert context.budget.generated_candidates == 1
    second = _orchestrator(database)
    result = second.run_task(context.task_id)
    assert result.current_stage is TaskStage.COMPLETED
    assert result.selected_candidate_id == "cand_02"
    assert result.budget.generated_candidates == 2


def test_orchestrator_records_failed_and_verified_repair_memory(tmp_path: Path) -> None:
    memory = SQLiteMemoryStore(
        tmp_path / "memory.sqlite", toolchain_version="sandbox:test"
    )
    orchestrator = Orchestrator(
        planner=FakePlanner(),
        generator=FakeGenerator(),
        debugger=_MemoryDebugger(),
        evaluator=FakeEvaluator(),
        verifier=FakeVerifier(failures_before_pass=1),
        store=SQLiteTaskStore(tmp_path / "tasks.sqlite"),
        memory_store=memory,
        memory_mode="read_write",
    )
    context = orchestrator.create_task(
        "Create a counter",
        task_id="task_memory",
        metadata={"benchmark_problem_id": "Prob001_zero"},
    )

    result = orchestrator.run_task(context.task_id)

    assert result.current_stage is TaskStage.COMPLETED
    assert len(memory.list_attempts()) == 1
    assert {item["memory_type"] for item in memory.list_memories()} == {
        "ERROR_PATTERN",
        "REPAIR_STRATEGY",
    }
    assert result.metadata["memory_consolidations"][0]["source_problem_id"] == (
        "Prob001_zero"
    )


def test_orchestrator_records_design_memory_only_for_original_verified_candidate(
    tmp_path: Path,
) -> None:
    memory = SQLiteMemoryStore(
        tmp_path / "memory.sqlite", toolchain_version="sandbox:test"
    )
    orchestrator = Orchestrator(
        planner=FakePlanner(),
        generator=FakeGenerator(),
        debugger=_MemoryDebugger(),
        evaluator=FakeEvaluator(),
        verifier=FakeVerifier(failures_before_pass=0),
        store=SQLiteTaskStore(tmp_path / "tasks.sqlite"),
        memory_store=memory,
        memory_mode="read_write",
    )
    context = orchestrator.create_task(
        "Create a counter",
        task_id="task_design_memory",
        metadata={"benchmark_problem_id": "Prob002_counter"},
    )

    orchestrator.run_task(context.task_id)

    assert {item["memory_type"] for item in memory.list_memories()} == {
        "DESIGN_PATTERN"
    }
