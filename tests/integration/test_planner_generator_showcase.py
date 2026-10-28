from __future__ import annotations

import json
from pathlib import Path

import pytest

from rtlforge.agents import PlannerGeneratorShowcase
from rtlforge.agents.showcase import ShowcaseRunError
from rtlforge.services import (
    GoldenTestbenchVerificationService,
    StaticCandidateEvaluator,
)
from rtlforge.testing import (
    OFFLINE_COUNTER_SPEC,
    ScriptedProvider,
    counter_candidate_payload,
    counter_plan_payload,
)


class PassingRunner:
    def run(self, action: str, _candidate_path: Path) -> dict:
        return {
            "schema_version": 1,
            "action": action,
            "stage": action.upper(),
            "status": "PASS",
            "category": "OK",
            "tool": "iverilog" if action == "compile" else "verilator",
            "returncode": 0,
            "duration_ms": 1,
            "errors": [],
            "metrics": {"score": 100} if action == "eval_quality" else {},
            "stdout": "",
            "stderr": "",
        }

    def run_files(self, action: str, input_paths: list[Path]) -> dict:
        return self.run(action, input_paths[-1])


def test_showcase_exposes_plan_tree_candidates_and_event_trace(tmp_path: Path) -> None:
    output: list[str] = []
    showcase = PlannerGeneratorShowcase(
        provider=ScriptedProvider(
            counter_plan_payload(), {"candidates": counter_candidate_payload()["candidates"][:1]}
        ),
        provider_label="scripted_test (NO LLM API CALL)",
        database=tmp_path / "showcase.sqlite",
        runs_root=tmp_path / "runs",
        emit=output.append,
    )

    summary = showcase.run(OFFLINE_COUNTER_SPEC, candidate_count=2)

    rendered = "\n".join(output)
    assert "阶段 0：用户自然语言需求" in rendered
    assert OFFLINE_COUNTER_SPEC in rendered
    assert "module_name: counter" in rendered
    assert "- [root] 实现并准备验证 counter" in rendered
    assert "- [rtl] 生成 counter RTL" in rendered
    assert "Candidate: cand_01" in rendered
    assert "design_pattern: 单时序进程" in rendered
    assert "Candidate: cand_02" not in rendered
    assert rendered.count("module counter(") == 1
    assert "EVENT_PLAN_DONE" in rendered
    assert "EVENT_CANDIDATES_GENERATED" in rendered
    assert "下一步尚未执行：候选评分、EDA 验证和 Debugger 修复" in rendered

    assert summary["current_stage"] == "EVALUATING"
    assert summary["verified_rtl"] is False
    assert len(summary["candidates"]) == 1
    task_root = tmp_path / "runs" / summary["task_id"]
    assert (task_root / "input.txt").read_text(encoding="utf-8").strip() == OFFLINE_COUNTER_SPEC
    assert json.loads((task_root / "spec_ir.json").read_text(encoding="utf-8"))[
        "module_name"
    ] == "counter"
    assert json.loads((task_root / "task_tree.json").read_text(encoding="utf-8"))[
        "node_id"
    ] == "root"
    saved_summary = json.loads(
        (task_root / "showcase_summary.json").read_text(encoding="utf-8")
    )
    assert saved_summary["verified_rtl"] is False
    calls = [json.loads(line) for line in
             (task_root / "model_calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["role"] for row in calls] == ["planner", "generator"]
    assert calls == saved_summary["model_calls"]
    assert saved_summary["token_usage"]["not_reported_calls"] == 2
    assert saved_summary["token_usage"]["input_tokens"] is None
    assert all(Path(item["artifact_path"]).is_file() for item in summary["candidates"])


def test_failed_planner_preserves_call_record_and_task_id(tmp_path):
    showcase = PlannerGeneratorShowcase(
        provider=ScriptedProvider({}, {}), provider_label="offline invalid plan",
        database=tmp_path / "tasks.sqlite", runs_root=tmp_path / "runs",
        emit=lambda _: None,
    )
    with pytest.raises(ShowcaseRunError) as raised:
        showcase.run(OFFLINE_COUNTER_SPEC)
    error = raised.value
    assert error.task_id is not None
    assert len(error.model_calls) == 2
    assert all(call["role"] == "planner" for call in error.model_calls)
    path = tmp_path / "runs" / error.task_id / "model_calls.jsonl"
    assert [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] == (
        error.model_calls
    )


def test_showcase_can_stop_after_real_static_candidate_selection(tmp_path: Path) -> None:
    output: list[str] = []
    showcase = PlannerGeneratorShowcase(
        provider=ScriptedProvider(
            counter_plan_payload(), {"candidates": counter_candidate_payload()["candidates"][:1]}
        ),
        provider_label="scripted_test (NO LLM API CALL)",
        database=tmp_path / "showcase.sqlite",
        runs_root=tmp_path / "runs",
        evaluator=StaticCandidateEvaluator(PassingRunner(), tmp_path / "runs"),
        emit=output.append,
    )

    summary = showcase.run(OFFLINE_COUNTER_SPEC, candidate_count=2)

    rendered = "\n".join(output)
    assert summary["current_stage"] == "VERIFYING"
    assert summary["static_gate_passed"] is True
    assert summary["verified_rtl"] is False
    assert summary["selected_candidate_id"] == "cand_01"
    assert "阶段 3：Candidate Evaluation 正在执行 Docker 静态门禁" in rendered
    assert "selected_candidate: cand_01" in rendered
    assert "未执行 testbench 仿真" in rendered


def test_showcase_handles_static_regeneration(tmp_path: Path) -> None:
    class LowFirstRunner(PassingRunner):
        def run(self, action, candidate_path):
            result = super().run(action, candidate_path)
            if action == "eval_quality" and candidate_path.parent.name == "cand_01":
                result["metrics"]["score"] = 74
            return result

    runs_root = tmp_path / "runs"
    showcase = PlannerGeneratorShowcase(
        provider=ScriptedProvider(
            counter_plan_payload(),
            *({"candidates": [item]} for item in counter_candidate_payload()["candidates"]),
        ),
        provider_label="offline test",
        database=tmp_path / "showcase.sqlite",
        runs_root=runs_root,
        evaluator=StaticCandidateEvaluator(LowFirstRunner(), runs_root),
        emit=lambda _: None,
    )
    summary = showcase.run(OFFLINE_COUNTER_SPEC)
    assert summary["current_stage"] == "VERIFYING"
    assert summary["selected_candidate_id"] == "cand_02"
    assert summary["budget"]["generated_candidates"] == 2
    assert len(summary["candidate_regenerations"]) == 1


def test_showcase_can_complete_functional_verification(tmp_path: Path) -> None:
    output: list[str] = []
    runs_root = tmp_path / "runs"
    runner = PassingRunner()
    testbench = tmp_path / "benchmark" / "counter_test.sv"
    testbench.parent.mkdir()
    testbench.write_text("module tb; endmodule\n", encoding="utf-8")
    showcase = PlannerGeneratorShowcase(
        provider=ScriptedProvider(
            counter_plan_payload(), {"candidates": counter_candidate_payload()["candidates"][:1]}
        ),
        provider_label="scripted_test (NO LLM API CALL)",
        database=tmp_path / "showcase.sqlite",
        runs_root=runs_root,
        evaluator=StaticCandidateEvaluator(runner, runs_root),
        verifier=GoldenTestbenchVerificationService(
            runner, runs_root, lambda _context: testbench
        ),
        emit=output.append,
    )

    summary = showcase.run(OFFLINE_COUNTER_SPEC, candidate_count=2)

    rendered = "\n".join(output)
    assert summary["current_stage"] == "COMPLETED"
    assert summary["verified_rtl"] is True
    assert summary["verification"]["passed"] is True
    assert "Verification 正在执行 golden testbench 仿真" in rendered
    assert "verified_rtl: true" in rendered
