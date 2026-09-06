from __future__ import annotations

from pathlib import Path

from verilog_agent.domain import (
    Budget,
    Candidate,
    EvaluationOutcome,
    EventType,
    TaskStage,
    VerificationOutcome,
)
from verilog_agent.memory import SQLiteMemoryStore
from verilog_agent.orchestration import Orchestrator, SQLiteTaskStore
from verilog_agent.testing import (
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
    assert result.selected_candidate_id == "cand_02"
    assert result.budget.generated_candidates == 2
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
    assert len(result.candidates) == 3
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
    assert result.selected_candidate_id == "cand_02"
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


def test_static_syntax_failure_routes_to_debugger(tmp_path: Path) -> None:
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
    assert result.budget.repair_attempts == 1
    assert result.metadata["reflection_decision"]["policy"] == "SYNTAX_REFLECTION"
    assert EventType.REFLECTION_ROUTED in _event_types(orchestrator.store, result.task_id)


class _NoProgressThenPassVerifier:
    def __init__(self) -> None:
        self.calls = 0

    def verify(
        self, context, candidate: Candidate
    ) -> VerificationOutcome:
        self.calls += 1
        if self.calls == 1:
            attempts = [
                _functional_attempt("cand_02", 76),
                _functional_attempt("cand_01", 76),
            ]
            passed = False
            selected = "cand_02"
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


def test_no_progress_repair_falls_back_to_unrepaired_original_candidate(
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
    assert debugger.parent_ids == ["cand_02", "cand_01"]
    assert result.metadata["repair_fallback"] == {
        "from_candidate_id": "cand_repair_01",
        "to_candidate_id": "cand_01",
        "reason": "previous repair made no functional mismatch improvement",
    }
    assert result.metadata["repair_attempts"][0]["mismatch_reduction"] == 0
    assert result.metadata["repair_attempts"][1]["passed"] is True


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
    assert len(memory.list_memories()) == 1
    assert result.metadata["memory_consolidations"][0]["source_problem_id"] == (
        "Prob001_zero"
    )
