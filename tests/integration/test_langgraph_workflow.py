from __future__ import annotations

from pathlib import Path

from verilog_agent.agents.showcase import PlannerGeneratorShowcase
from verilog_agent.domain import Budget, TaskStage
from verilog_agent.orchestration.graph import LangGraphOrchestrator
from verilog_agent.orchestration.storage import SQLiteTaskStore
from verilog_agent.services import GoldenTestbenchVerificationService, StaticCandidateEvaluator
from verilog_agent.testing import (
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


def test_langgraph_respects_retry_and_candidate_budgets(tmp_path: Path) -> None:
    graph = _fake_graph(tmp_path, FakeVerifier(always_fail=True))
    task = graph.create_task(
        "Create a counter", task_id="graph_budget",
        budget=Budget(max_candidates=2, max_repairs=1),
    )

    result = graph.run_task(task.task_id)

    assert result.current_stage is TaskStage.FAILED
    assert result.budget.repair_attempts == 1
    assert result.budget.generated_candidates == 2
    assert result.metadata["final_status"] == "max_retry_exhausted"
