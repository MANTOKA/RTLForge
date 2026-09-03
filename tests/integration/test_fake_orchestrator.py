from __future__ import annotations

from pathlib import Path

from verilog_agent.domain import Budget, EvaluationOutcome, EventType, TaskStage
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
