from __future__ import annotations

from pathlib import Path

import pytest

from rtlforge.domain import Budget, EventType, TaskStage, VerificationOutcome
from rtlforge.orchestration import Orchestrator, SQLiteTaskStore
from rtlforge.orchestration.graph import LangGraphOrchestrator
from rtlforge.orchestration.operations import Outcome, WorkflowOperations
from rtlforge.testing import (
    FakeDebugger,
    FakeEvaluator,
    FakeGenerator,
    FakePlanner,
    FakeVerifier,
)


class StubTestbench:
    def __init__(self):
        self.calls = 0

    def generate_for_contract(self, _contract):
        self.calls += 1
        return "module tb; endmodule\n", ["Check reset and count"]


class Runner:
    def __init__(self, failures=0):
        self.calls = 0
        self.failures = failures

    def run_files(self, _action, _paths):
        self.calls += 1
        failed = self.calls <= self.failures
        return {
            "status": "FAIL" if failed else "PASS",
            "category": "ASSERTION_FAILURE" if failed else "OK",
            "stdout": "q mismatch: got 1 expected 0" if failed else "GENERATED_TB_PASS",
        }


class ChoosingDebugger(FakeDebugger):
    def __init__(self, *actions):
        self.actions = iter(actions)
        self.observations = []

    def decide_next_action(self, observation):
        self.observations.append(observation)
        return {"action": next(self.actions), "reason": "Inspect the current failure"}


def make_graph(root: Path, **overrides) -> LangGraphOrchestrator:
    components = {
        "planner": FakePlanner(),
        "generator": FakeGenerator(),
        "debugger": FakeDebugger(),
        "evaluator": FakeEvaluator(),
        "verifier": FakeVerifier(),
        "store": SQLiteTaskStore(root / "audit.sqlite"),
        "testbench_agent": StubTestbench(),
        "generated_tb_runner": Runner(),
        "runs_root": root / "runs",
    }
    components.update(overrides)
    return LangGraphOrchestrator(**components)


def events(graph, task_id, kind):
    return [row for row in graph.store.list_events(task_id) if row.event_type is kind]


@pytest.mark.parametrize("same_directory", [True, False])
def test_runtime_resume_compares_resolved_run_roots(tmp_path, same_directory):
    graph = make_graph(tmp_path)
    try:
        task = graph.create_task("counter")
        root = graph.runs_root
        task.metadata["graph_runtime"]["runs_root"] = str(
            root.parent / "old-directory-alias" / ".." / root.name
            if same_directory else root.parent / "different-runs"
        )
        graph.store.save_checkpoint(task)
        if same_directory:
            assert graph.run_task(task.task_id).current_stage is TaskStage.COMPLETED
        else:
            with pytest.raises(ValueError, match="changed graph runtime"):
                graph.run_task(task.task_id)
    finally:
        graph.close()


def test_planner_clarification_round_trip_versions_contract_before_tb_and_coder(tmp_path):
    import json

    from rtlforge.agents import RealPlanner
    from rtlforge.testing import ScriptedProvider, counter_plan_payload

    initial, partial, ready = (counter_plan_payload() for _ in range(3))
    initial["clock_edge"] = initial["reset_kind"] = None
    partial["clock_edge"] = None
    first_provider = ScriptedProvider(initial)
    tb_contracts, coder_contracts = [], []

    class ContractTestbench(StubTestbench):
        def generate_for_contract(self, contract):
            tb_contracts.append(contract.to_dict())
            return super().generate_for_contract(contract)

    class ContractCoder(FakeGenerator):
        def generate(self, context, count):
            coder_contracts.append(context.spec_ir.to_dict())
            return super().generate(context, count)

    tb = ContractTestbench()
    first = make_graph(tmp_path, planner=RealPlanner(first_provider), testbench_agent=tb)
    task = first.create_task("计数器，时钟 clk，复位 rst，复位类型和边沿尚未确定")
    paused = first.run_task(task.task_id)
    assert paused.current_stage is TaskStage.WAITING_FOR_USER
    assert paused.metadata["pause_reason"] == "contract_clarification"
    assert paused.spec_ir.version == 1
    assert paused.budget.generated_candidates == 0
    assert tb.calls == 0
    snapshot = first.graph.get_state(first._config(task.task_id))
    question = next(t.interrupts[0].value for t in snapshot.tasks if t.interrupts)
    assert {row["field"] for row in question["questions"]} == {"clock_edge", "reset_kind"}
    first.close()

    provider = ScriptedProvider(partial, ready)
    second = make_graph(tmp_path, planner=RealPlanner(provider), testbench_agent=tb,
                        generator=ContractCoder())
    # Restart must retain the question and must not execute Planner without an answer.
    assert second.run_task(task.task_id).current_stage is TaskStage.WAITING_FOR_USER
    assert not provider.requests
    with pytest.raises(ValueError):
        second.resume_task(task.task_id, {"action": "accept"})
    partial_result = second.resume_task(task.task_id, {
        "action": "answer", "message": "rst 是同步复位，采样边沿我还需要确认。",
    })
    assert partial_result.current_stage is TaskStage.WAITING_FOR_USER
    assert partial_result.spec_ir.version == 2
    assert [row["field"] for row in partial_result.metadata["pending_clarifications"]] == [
        "clock_edge",
    ]
    assert tb.calls == 0
    result = second.resume_task(task.task_id, {"action": "answer", "message": "时钟上升沿采样。"})
    assert result.current_stage is TaskStage.COMPLETED
    assert result.spec_ir.version == 3
    assert result.budget.generated_candidates == 1
    assert result.budget.repair_attempts == 0
    assert tb.calls == 1
    assert tb_contracts == coder_contracts == [result.spec_ir.to_dict()]
    assert result.candidate().metadata["contract_version"] == 3
    assert [row["status"] for row in result.metadata["contract_history"]] == [
        "draft", "draft", "ready",
    ]
    first_replan = json.loads(provider.requests[0]["input_text"])
    assert first_replan["previous_contract"]["version"] == 1
    assert first_replan["pending_questions"] == question["questions"]
    assert "rst 是同步复位" in first_replan["requirement"]
    assert len(events(second, task.task_id, EventType.CLARIFICATION_REQUESTED)) == 2
    assert len(events(second, task.task_id, EventType.CLARIFICATION_ANSWERED)) == 2
    assert len(events(second, task.task_id, EventType.PLAN_DONE)) == 1
    assert "pending_clarifications" not in result.metadata
    second.close()


def test_legacy_planner_can_pause_and_resume_with_the_same_contract_gate(tmp_path):
    from rtlforge.agents import RealPlanner
    from rtlforge.testing import ScriptedProvider, counter_plan_payload

    draft, ready = counter_plan_payload(), counter_plan_payload()
    draft["reset_kind"] = None
    store = SQLiteTaskStore(tmp_path / "audit.sqlite")

    def legacy(provider):
        return Orchestrator(planner=RealPlanner(provider), generator=FakeGenerator(),
                            debugger=FakeDebugger(), evaluator=FakeEvaluator(),
                            verifier=FakeVerifier(), store=store)

    first = legacy(ScriptedProvider(draft))
    task = first.create_task("counter")
    assert first.run_task(task.task_id).current_stage is TaskStage.WAITING_FOR_USER
    second = legacy(ScriptedProvider(ready))
    result = second.resume_task(task.task_id, {"action": "answer", "message": "同步复位"})
    assert result.current_stage is TaskStage.COMPLETED
    assert result.spec_ir.version == 2
    assert result.budget.generated_candidates == 1


def test_planner_answer_can_resume_in_a_separate_python_process(tmp_path):
    import json
    import subprocess
    import sys

    from rtlforge.agents import RealPlanner
    from rtlforge.testing import ScriptedProvider, counter_plan_payload

    draft = counter_plan_payload()
    draft["reset_kind"] = None
    graph = make_graph(tmp_path, planner=RealPlanner(ScriptedProvider(draft)))
    task = graph.create_task("counter")
    assert graph.run_task(task.task_id).current_stage is TaskStage.WAITING_FOR_USER
    graph.close()
    repo = Path(__file__).resolve().parents[2]
    script = """
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "tests" / "integration"))
from test_graph_runtime import make_graph
from rtlforge.agents import RealPlanner
from rtlforge.testing import ScriptedProvider, counter_plan_payload
graph = make_graph(Path(sys.argv[1]), planner=RealPlanner(ScriptedProvider(counter_plan_payload())))
try:
    result = graph.resume_task(sys.argv[2], {"action": "answer", "message": "synchronous reset"})
    print(json.dumps({"stage": result.current_stage.value, "version": result.spec_ir.version,
                      "candidates": result.budget.generated_candidates}))
finally:
    graph.close()
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), task.task_id], cwd=repo,
        capture_output=True, text=True, check=True, timeout=30,
    )
    assert json.loads(completed.stdout) == {"stage": "COMPLETED", "version": 2, "candidates": 1}


def test_evaluation_operation_does_not_regenerate_or_change_stage(tmp_path):
    graph = make_graph(tmp_path, evaluator=FakeEvaluator(reject=True))
    task = graph.create_task("counter")
    task.current_stage = TaskStage.EVALUATING
    result = graph.operations.perform("evaluate", task)
    assert result.outcome is Outcome.EVALUATED
    assert result.details["accepted"] is False
    assert task.current_stage is TaskStage.EVALUATING
    assert not task.metadata.get("candidate_regenerations")
    graph.close()


def test_graph_does_not_dispatch_through_legacy_orchestrator(tmp_path, monkeypatch):
    def forbidden(*_args):
        raise AssertionError("Legacy dispatch must not execute")

    monkeypatch.setattr(Orchestrator, "advance_task", forbidden)
    graph = make_graph(tmp_path)
    task = graph.create_task("counter")
    assert graph.run_task(task.task_id).current_stage is TaskStage.COMPLETED
    assert not isinstance(graph, Orchestrator)
    assert graph.runtime.path != graph.store.database_path
    graph.close()


@pytest.mark.parametrize("boundary", ["planner", "static_policy"])
def test_new_runtime_resumes_next_graph_node_without_skipping_tb_or_verification(
    tmp_path,
    boundary,
):
    tb, runner = StubTestbench(), Runner()
    first = make_graph(tmp_path, testbench_agent=tb, generated_tb_runner=runner)
    task = first.create_task("counter")
    first.run_task(task.task_id, interrupt_after=[boundary])
    first.close()
    verifier = FakeVerifier()
    second = make_graph(tmp_path, testbench_agent=tb, generated_tb_runner=runner, verifier=verifier)
    result = second.run_task(task.task_id)
    assert result.current_stage is TaskStage.COMPLETED
    assert tb.calls == 1
    assert runner.calls == 1
    assert verifier.calls == 1
    assert result.budget.generated_candidates == 1
    second.close()


@pytest.mark.parametrize(
    "stage",
    [
        TaskStage.PLANNED,
        TaskStage.VERIFYING,
        TaskStage.CLASSIFYING,
        TaskStage.REPAIRING,
    ],
)
def test_audit_only_migration_cannot_bypass_graph_testbench(tmp_path, stage):
    graph = make_graph(tmp_path)
    task = graph.create_task("counter")
    graph.operations.perform("plan", task)
    if stage is not TaskStage.PLANNED:
        graph.operations.perform("generate", task)
        task.failure_category = "FUNCTIONAL_MISMATCH"
    task.current_stage = stage
    graph.store.save_checkpoint(task)
    graph.close()

    tb, runner = StubTestbench(), Runner()
    second = make_graph(tmp_path, testbench_agent=tb, generated_tb_runner=runner)
    result = second.run_task(task.task_id)
    assert tb.calls == 1
    assert runner.calls == 1
    assert result.metadata["golden_tb_pass"] is True
    second.close()


def test_human_interrupt_survives_restart_and_cannot_claim_golden_pass(tmp_path):
    first = make_graph(tmp_path, has_golden_testbench=False)
    task = first.create_task("counter")
    result = first.run_task(task.task_id)
    assert result.current_stage is TaskStage.WAITING_FOR_USER
    assert any(t.interrupts for t in first.graph.get_state(first._config(task.task_id)).tasks)
    first.close()

    second = make_graph(tmp_path, has_golden_testbench=False)
    assert second.run_task(task.task_id).current_stage is TaskStage.WAITING_FOR_USER
    with pytest.raises(ValueError):
        second.resume_task(task.task_id, {"action": "golden_pass"})
    result = second.resume_task(task.task_id, {"action": "accept"})
    assert result.current_stage is TaskStage.COMPLETED
    assert result.metadata["final_status"] == "manual_accepted"
    assert result.metadata["golden_tb_pass"] is False
    assert len(events(second, task.task_id, EventType.GENERATED_TB_CREATED)) == 1
    second.close()


def test_debugger_investigates_then_asks_and_resumes_into_repair(tmp_path):
    debugger = ChoosingDebugger("read_diagnostics", "ask_user", "repair")
    verifier = FakeVerifier(failures_before_pass=1)
    graph = make_graph(tmp_path, debugger=debugger, verifier=verifier)
    task = graph.create_task("counter")
    assert graph.run_task(task.task_id).current_stage is TaskStage.WAITING_FOR_USER
    assert graph.load_task(task.task_id).budget.repair_attempts == 0
    graph.close()

    resumed = make_graph(tmp_path, debugger=debugger, verifier=verifier)
    result = resumed.resume_task(task.task_id, {"action": "answer", "message": "同步高有效复位"})
    assert result.current_stage is TaskStage.COMPLETED
    assert result.budget.repair_attempts == 1
    assert debugger.observations[1]["observations"][0]["tool"] == "read_diagnostics"
    assert debugger.observations[2]["user_answer"] == "同步高有效复位"
    resumed.close()


def test_no_golden_failure_can_be_investigated_and_repaired_without_auto_acceptance(tmp_path):
    graph = make_graph(
        tmp_path,
        has_golden_testbench=False,
        generated_tb_runner=Runner(failures=1),
        debugger=ChoosingDebugger("read_diagnostics", "repair"),
    )
    task = graph.create_task("counter")
    result = graph.run_task(task.task_id)
    assert result.current_stage is TaskStage.WAITING_FOR_USER
    assert result.budget.repair_attempts == 1
    assert result.metadata["generated_tb_pass"] is True
    assert not result.metadata.get("golden_tb_pass")
    graph.close()


@pytest.mark.parametrize("action", ["run_shell", "golden_pass", "read_golden"])
def test_invalid_agent_actions_are_rejected_before_execution(tmp_path, action):
    graph = make_graph(
        tmp_path,
        verifier=FakeVerifier(always_fail=True),
        debugger=ChoosingDebugger(action),
    )
    task = graph.create_task("counter")
    result = graph.run_task(task.task_id)
    assert result.failure_category == "POLICY_VIOLATION"
    assert result.budget.repair_attempts == 0
    graph.close()


def test_investigation_loop_has_a_hard_action_budget(tmp_path):
    graph = make_graph(
        tmp_path,
        verifier=FakeVerifier(always_fail=True),
        max_debug_actions=2,
        debugger=ChoosingDebugger("read_diagnostics", "read_diagnostics"),
    )
    task = graph.create_task("counter")
    result = graph.run_task(task.task_id)
    assert result.failure_category == "BUDGET_EXHAUSTED"
    assert result.metadata["debug_action_count"] == 2
    assert result.budget.repair_attempts == 0
    graph.close()


@pytest.mark.parametrize("boundary", ["planner", "testbench", "static_policy", "advisory_verifier"])
def test_cancellation_is_not_overwritten_by_graph_state(tmp_path, boundary):
    graph = make_graph(tmp_path)
    task = graph.create_task("counter")
    graph.run_task(task.task_id, interrupt_after=[boundary])
    graph.request_cancel(task.task_id)
    graph.close()
    second = make_graph(tmp_path)
    result = second.run_task(task.task_id)
    assert result.current_stage is TaskStage.CANCELLED
    assert result.cancel_requested is True
    assert len(events(second, task.task_id, EventType.TASK_CANCELLED)) == 1
    second.close()


def test_cancel_a_human_interrupt_without_calling_another_model(tmp_path):
    graph = make_graph(tmp_path, has_golden_testbench=False)
    task = graph.create_task("counter")
    graph.run_task(task.task_id)
    graph.request_cancel(task.task_id)
    assert graph.run_task(task.task_id).current_stage is TaskStage.CANCELLED
    graph.close()


def test_completed_node_receipt_avoids_duplicate_tb_on_checkpoint_failure(tmp_path, monkeypatch):
    tb = StubTestbench()
    first = make_graph(tmp_path, testbench_agent=tb)
    task = first.create_task("counter")
    save = first.runtime.save_result

    def fail_after_receipt(task_id, step, node, result):
        save(task_id, step, node, result)
        if node == "testbench":
            raise RuntimeError("Simulate process loss before graph checkpoint")

    monkeypatch.setattr(first.runtime, "save_result", fail_after_receipt)
    with pytest.raises(RuntimeError, match="process loss"):
        first.run_task(task.task_id)
    first.close()
    second = make_graph(tmp_path, testbench_agent=tb)
    result = second.run_task(task.task_id)
    assert result.current_stage is TaskStage.COMPLETED
    assert tb.calls == 1
    assert len(events(second, task.task_id, EventType.GENERATED_TB_CREATED)) == 1
    second.close()


def test_contract_mutation_is_rejected(tmp_path):
    class ChangingGenerator(FakeGenerator):
        def generate(self, context, count):
            context.spec_ir.module_name = "unauthorized"
            return super().generate(context, count)

    graph = make_graph(tmp_path, generator=ChangingGenerator())
    task = graph.create_task("counter")
    result = graph.run_task(task.task_id)
    assert result.failure_category == "CONTRACT_VIOLATION"
    assert result.spec_ir.module_name == "counter"
    graph.close()


def test_graph_checkpoint_database_must_be_separate(tmp_path):
    with pytest.raises(ValueError, match="must not share"):
        make_graph(tmp_path, checkpoint_database=tmp_path / "audit.sqlite")


def test_infrastructure_failure_never_invokes_agent_decision(tmp_path):
    class BrokenVerifier:
        def verify(self, _context, _candidate):
            return VerificationOutcome(False, "SYSTEM_ERROR")

    debugger = ChoosingDebugger()
    graph = make_graph(tmp_path, verifier=BrokenVerifier(), debugger=debugger)
    task = graph.create_task("counter")
    result = graph.run_task(task.task_id)
    assert result.failure_category == "SYSTEM_ERROR"
    assert not debugger.observations
    graph.close()


def test_operations_remain_shared_with_legacy(tmp_path):
    graph = make_graph(tmp_path)
    assert isinstance(graph.operations, WorkflowOperations)
    assert issubclass(Orchestrator, WorkflowOperations)
    task = graph.create_task("counter", budget=Budget(max_candidates=1, max_repairs=0))
    assert graph.run_task(task.task_id).budget.generated_candidates == 1
    graph.close()


def test_real_debugger_uses_model_decisions_and_scoped_rtl_tool(tmp_path):
    from rtlforge.agents.debugger import RealDebugger
    from rtlforge.agents.generator import ArtifactRepository
    from rtlforge.testing import ScriptedProvider

    artifacts = ArtifactRepository(tmp_path / "runs")

    class FileGenerator(FakeGenerator):
        def generate(self, context, count):
            rows = super().generate(context, count)
            for row in rows:
                row.artifact_path = str(
                    artifacts.write_candidate(
                        context.task_id,
                        row.candidate_id,
                        "counter",
                        "module counter(input clk, rst, output reg [3:0] q); "
                        "always @(posedge clk) q <= q + 1; endmodule\n",
                    )
                )
            return rows

    provider = ScriptedProvider(
        {"action": "read_rtl", "reason": "Inspect reset priority"},
        {"action": "repair", "reason": "Add synchronous reset"},
        {
            "patched_rtl": "module counter(input clk, rst, output reg [3:0] q); "
            "always @(posedge clk) if (rst) q<=0; else q<=q+1; endmodule\n",
            "rationale": "Reset has priority",
            "expected_impact": "Reset clears output",
            "risks": [],
        },
    )
    graph = make_graph(
        tmp_path,
        generator=FileGenerator(),
        verifier=FakeVerifier(failures_before_pass=1),
        debugger=RealDebugger(provider, artifacts=artifacts),
    )
    task = graph.create_task("counter")
    task.metadata["model_calls_path"] = str(tmp_path / "model_calls.jsonl")
    result = graph.run_task(task.task_id)
    assert result.current_stage is TaskStage.COMPLETED
    assert [row["schema_name"] for row in provider.requests] == [
        "rtl_debug_action",
        "rtl_debug_action",
        "verilog_rtl_repair",
    ]
    assert "module counter" in provider.requests[1]["input_text"]
    assert len(result.metadata["model_calls"]) == 3
    assert len((tmp_path / "model_calls.jsonl").read_text().splitlines()) == 3
    assert result.budget.repair_attempts == 1
    graph.close()


def test_debugger_tool_cannot_read_outside_candidate_run_root(tmp_path):
    secret = tmp_path / "golden.sv"
    secret.write_text("SECRET_GOLDEN", encoding="utf-8")

    class UnsafeGenerator(FakeGenerator):
        def generate(self, context, count):
            rows = super().generate(context, count)
            rows[0].artifact_path = str(secret)
            return rows

    debugger = ChoosingDebugger("read_rtl")
    graph = make_graph(
        tmp_path,
        generator=UnsafeGenerator(),
        verifier=FakeVerifier(always_fail=True),
        debugger=debugger,
    )
    task = graph.create_task("counter")
    result = graph.run_task(task.task_id)
    assert result.failure_category == "POLICY_VIOLATION"
    assert "SECRET_GOLDEN" not in str(debugger.observations)
    graph.close()


def test_generated_only_pass_never_writes_verified_memory(tmp_path):
    from rtlforge.memory import SQLiteMemoryStore

    memory = SQLiteMemoryStore(tmp_path / "memory.sqlite", toolchain_version="test")
    graph = make_graph(
        tmp_path,
        has_golden_testbench=False,
        memory_store=memory,
        memory_mode="read_write",
        generated_tb_runner=Runner(failures=1),
        debugger=ChoosingDebugger("repair"),
    )
    task = graph.create_task("counter")
    graph.run_task(task.task_id)
    graph.resume_task(task.task_id, {"action": "accept"})
    assert memory.list_memories() == []
    assert memory.list_attempts() == []
    graph.close()


def test_cancel_requested_during_tb_generation_prevents_coder(tmp_path):
    class CancellingTestbench(StubTestbench):
        def generate_for_contract(self, contract):
            graph.request_cancel("cancel_during_tb")
            return super().generate_for_contract(contract)

    graph = make_graph(tmp_path, testbench_agent=CancellingTestbench())
    task = graph.create_task("counter", task_id="cancel_during_tb")
    result = graph.run_task(task.task_id)
    assert result.current_stage is TaskStage.CANCELLED
    assert result.budget.generated_candidates == 0
    graph.close()


def test_cancel_during_auxiliary_verification_prevents_golden_execution(tmp_path):
    class CancellingRunner(Runner):
        def run_files(self, action, paths):
            graph.request_cancel("cancel_during_sim")
            return super().run_files(action, paths)

    verifier = FakeVerifier()
    graph = make_graph(tmp_path, generated_tb_runner=CancellingRunner(), verifier=verifier)
    task = graph.create_task("counter", task_id="cancel_during_sim")
    assert graph.run_task(task.task_id).current_stage is TaskStage.CANCELLED
    assert verifier.calls == 0
    graph.close()


def test_tb_generation_exception_uses_the_common_failed_boundary(tmp_path):
    class BrokenTestbench:
        def generate_for_contract(self, _contract):
            raise RuntimeError("TB provider failed")

    graph = make_graph(tmp_path, testbench_agent=BrokenTestbench())
    task = graph.create_task("counter")
    result = graph.run_task(task.task_id)
    assert result.current_stage is TaskStage.FAILED
    assert len(events(graph, task.task_id, EventType.SYSTEM_FAILED)) == 1
    assert result.budget.generated_candidates == 0
    graph.close()
