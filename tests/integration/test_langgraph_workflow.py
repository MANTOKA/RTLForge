from __future__ import annotations

from pathlib import Path

import pytest

from rtlforge.agents.showcase import PlannerGeneratorShowcase
from rtlforge.domain import Budget, TaskStage
from rtlforge.orchestration.graph import LangGraphOrchestrator
from rtlforge.orchestration.storage import SQLiteTaskStore
from rtlforge.services import GoldenTestbenchVerificationService, StaticCandidateEvaluator
from rtlforge.testing import (
    OFFLINE_COUNTER_SPEC,
    FakeDebugger,
    FakeEvaluator,
    FakeGenerator,
    FakePlanner,
    FakeVerifier,
    ScriptedProvider,
    counter_candidate_payload,
    counter_plan_payload,
)


class PassingRunner:
    def run(self, action: str, _candidate_path: Path) -> dict:
        return {
            "action": action,
            "status": "PASS",
            "category": "OK",
            "tool": "iverilog",
            "duration_ms": 1,
            "errors": [],
            "metrics": {"score": 100} if action == "eval_quality" else {},
            "stdout": "",
            "stderr": "",
        }

    def run_files(self, action: str, input_paths: list[Path]) -> dict:
        result = self.run(action, input_paths[-1])
        if input_paths[0].name == "generated_tb.sv":
            result["stdout"] = "GENERATED_TB_PASS"
        return result


def _provider() -> ScriptedProvider:
    return ScriptedProvider(
        counter_plan_payload(),
        {
            "testbench": (
                "module tb; initial begin if (1'b0 !== 1'b0) $fatal(1,\"bad\"); "
                "$display(\"GENERATED_TB_PASS\"); $finish; end endmodule"
            ),
            "test_objectives": ["Check reset", "Check increment"],
        },
        {"candidates": counter_candidate_payload()["candidates"][:1]},
    )


def test_langgraph_runs_tb_before_coder_and_preserves_hidden_golden(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    golden = tmp_path / "secret_golden.sv"
    golden.write_text("SECRET_GOLDEN_CONTENT", encoding="utf-8")
    provider = _provider()
    runner = PassingRunner()
    showcase = PlannerGeneratorShowcase(
        provider=provider,
        provider_label="offline",
        database=tmp_path / "audit.sqlite",
        runs_root=root,
        evaluator=StaticCandidateEvaluator(runner, root),
        verifier=GoldenTestbenchVerificationService(runner, root, lambda _context: golden),
        orchestrator_kind="langgraph",
        emit=lambda _message: None,
    )

    summary = showcase.run(OFFLINE_COUNTER_SPEC)

    assert summary["verified_rtl"] is True
    assert summary["generated_tb_pass"] is True
    assert summary["final_status"] == "golden_pass"
    assert [row["schema_name"] for row in provider.requests] == [
        "verilog_requirement_plan", "contract_testbench", "verilog_rtl_candidates",
    ]
    assert "testbench" not in provider.requests[2]["input_text"]
    assert "Check reset" in provider.requests[2]["input_text"]
    assert "SECRET_GOLDEN_CONTENT" not in str(summary)
    assert [row["role"] for row in summary["model_calls"]] == [
        "planner", "testbench", "generator",
    ]
    assert any(event["event_type"] == "EVENT_GENERATED_TB_CHECKED" for event in summary["events"])


def test_golden_only_skips_tb_model_and_advisory_simulation(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    golden = tmp_path / "golden.sv"
    golden.write_text("SECRET_GOLDEN", encoding="utf-8")
    provider = ScriptedProvider(
        counter_plan_payload(),
        {"candidates": counter_candidate_payload()["candidates"][:1]},
    )
    runner = PassingRunner()
    showcase = PlannerGeneratorShowcase(
        provider=provider, provider_label="offline", database=tmp_path / "audit.sqlite",
        runs_root=root, evaluator=StaticCandidateEvaluator(runner, root),
        verifier=GoldenTestbenchVerificationService(runner, root, lambda _: golden),
        orchestrator_kind="langgraph", skip_generated_testbench=True, emit=lambda _: None,
    )
    summary = showcase.run(OFFLINE_COUNTER_SPEC)
    assert summary["verified_rtl"] is True
    assert summary["generated_tb_pass"] is None
    assert [request["schema_name"] for request in provider.requests] == [
        "verilog_requirement_plan", "verilog_rtl_candidates",
    ]
    assert not list(root.rglob("generated_tb*.sv"))
    assert "SECRET_GOLDEN" not in str(summary)


def test_langgraph_without_golden_stops_for_manual_acceptance(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    runner = PassingRunner()
    showcase = PlannerGeneratorShowcase(
        provider=_provider(),
        provider_label="offline",
        database=tmp_path / "audit.sqlite",
        runs_root=root,
        evaluator=StaticCandidateEvaluator(runner, root),
        orchestrator_kind="langgraph",
        emit=lambda _message: None,
    )

    summary = showcase.run(OFFLINE_COUNTER_SPEC)

    assert summary["current_stage"] == "WAITING_FOR_USER"
    assert summary["final_status"] == "generated_tb_pass_await_manual"
    assert summary["verified_rtl"] is False


class FakeTestbenchAgent:
    def generate_for_contract(self, _contract):
        return (
            "module tb; initial begin $display(\"GENERATED_TB_PASS\"); "
            "$finish; end endmodule\n",
            ["Check counter"],
        )


def _fake_graph(tmp_path: Path, verifier: FakeVerifier) -> LangGraphOrchestrator:
    return LangGraphOrchestrator(
        planner=FakePlanner(),
        testbench_agent=FakeTestbenchAgent(),
        generator=FakeGenerator(),
        evaluator=FakeEvaluator(),
        verifier=verifier,
        debugger=FakeDebugger(),
        generated_tb_runner=PassingRunner(),
        runs_root=tmp_path / "runs",
        store=SQLiteTaskStore(tmp_path / "audit.sqlite"),
    )


def test_langgraph_reuses_legacy_repair_loop(tmp_path: Path) -> None:
    graph = _fake_graph(tmp_path, FakeVerifier(failures_before_pass=1))
    task = graph.create_task("Create a counter", task_id="graph_repair")

    result = graph.run_task(task.task_id)

    assert result.current_stage is TaskStage.COMPLETED
    assert result.budget.repair_attempts == 1
    assert result.selected_candidate_id == "cand_repair_01"
    assert result.metadata["final_status"] == "golden_pass"


def test_generated_tb_failure_records_only_advisory_port_diagnostic(tmp_path: Path) -> None:
    class FailingRunner(PassingRunner):
        def run_files(self, action: str, input_paths: list[Path]) -> dict:
            return {
                **self.run(action, input_paths[-1]),
                "status": "FAIL", "category": "SIMULATION_ERROR",
                "stdout": "ERROR: q mismatch. Got private_value, expected private_oracle\n"
                          "ERROR: q not asserted after start\n"
                          "private testbench transcript",
            }

    graph = _fake_graph(tmp_path, FakeVerifier())
    graph.generated_tb_runner = FailingRunner()
    task = graph.create_task("Create a counter", task_id="graph_advisory")

    result = graph.run_task(task.task_id)

    attempt = result.metadata["generated_tb_attempts"][0]
    assert attempt["candidate_id"] == "cand_01"
    assert attempt["advisory_feedback"] == {
        "category": "SIMULATION_ERROR",
        "mismatched_outputs": ["q"],
        "first_value_mismatch": None,
        "unasserted_outputs": ["q"],
        "completion_timeout": False,
    }
    assert "private" not in str(attempt)


def test_generated_tb_numeric_mismatch_is_bounded_advisory(tmp_path: Path) -> None:
    class NumericRunner(PassingRunner):
        def run_files(self, action: str, input_paths: list[Path]) -> dict:
            return {
                **self.run(action, input_paths[-1]),
                "status": "FAIL", "category": "SIMULATION_ERROR",
                "stdout": "FATAL: Q mismatch: got 0000 expected 0001\nprivate TB source",
            }

    graph = _fake_graph(tmp_path, FakeVerifier())
    graph.generated_tb_runner = NumericRunner()
    task = graph.create_task("Create a counter", task_id="graph_numeric_advisory")

    result = graph.run_task(task.task_id)

    advisory = result.metadata["generated_tb_attempts"][0]["advisory_feedback"]
    assert advisory["first_value_mismatch"] == {
        "output": "q", "observed": "0000", "agent_tb_expected": "0001",
    }
    assert "private" not in str(advisory)


@pytest.mark.parametrize("message", [
    "TIMEOUT: Test exceeded 600 clock cycles",
    "ERROR: Test 2 exceeded 512 cycles",
    "FATAL: Cycle limit exceeded",
])
def test_generated_tb_cycle_limit_is_advisory_completion_timeout(
    tmp_path: Path, message: str,
) -> None:
    class TimeoutRunner(PassingRunner):
        def run_files(self, action: str, input_paths: list[Path]) -> dict:
            return {
                **self.run(action, input_paths[-1]),
                "status": "FAIL", "category": "SIMULATION_ERROR", "stdout": message,
            }

    graph = _fake_graph(tmp_path, FakeVerifier())
    graph.generated_tb_runner = TimeoutRunner()
    task = graph.create_task("Create a counter", task_id="graph_timeout_advisory")

    result = graph.run_task(task.task_id)

    assert result.metadata["generated_tb_attempts"][0]["advisory_feedback"][
        "completion_timeout"
    ] is True


def test_langgraph_respects_retry_and_candidate_budgets(tmp_path: Path) -> None:
    graph = _fake_graph(tmp_path, FakeVerifier(always_fail=True))
    task = graph.create_task(
        "Create a counter", task_id="graph_budget",
        budget=Budget(max_candidates=2, max_repairs=1),
    )

    result = graph.run_task(task.task_id)

    assert result.current_stage is TaskStage.FAILED
    assert result.budget.repair_attempts == 1
    assert result.budget.generated_candidates == 1
    assert result.metadata["final_status"] == "max_retry_exhausted"
