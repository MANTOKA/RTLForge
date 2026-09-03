from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from verilog_agent.domain import Candidate, PortDirection, PortSpec, RTLTaskContext, SpecIR
from verilog_agent.services import (
    DockerEDARunner,
    DockerToolError,
    StaticCandidateEvaluator,
    candidate_evaluation,
)


def _tool_result(action: str, *, status: str = "PASS", score: int = 100) -> dict:
    return {
        "schema_version": 1,
        "action": action,
        "stage": action.upper(),
        "status": status,
        "category": (
            "OK"
            if status == "PASS"
            else ("SYNTAX_ERROR" if action == "compile" else "LINT_ERROR")
        ),
        "tool": "verilator" if action != "compile" else "iverilog",
        "returncode": 0 if status == "PASS" else 1,
        "duration_ms": 7,
        "errors": [],
        "metrics": (
            {
                "score": score,
                "threshold": 75,
                "config_version": "score-v1-test",
                "deductions": [],
            }
            if action == "eval_quality"
            else {}
        ),
        "stdout": "",
        "stderr": "",
    }


class RecordingRunner:
    def __init__(
        self, scores: dict[str, int], failures: set[tuple[str, str]] | None = None
    ) -> None:
        self.scores = scores
        self.failures = failures or set()
        self.calls: list[tuple[str, str]] = []

    def run(self, action: str, candidate_path: Path) -> dict:
        candidate_id = candidate_path.parent.name
        self.calls.append((candidate_id, action))
        status = "FAIL" if (candidate_id, action) in self.failures else "PASS"
        return _tool_result(action, status=status, score=self.scores[candidate_id])


class ToolIncompatibilityRunner(RecordingRunner):
    def run(self, action: str, candidate_path: Path) -> dict:
        result = super().run(action, candidate_path)
        if action in {"lint", "eval_quality"}:
            result["status"] = "FAIL"
            result["category"] = "TOOL_INCOMPATIBILITY"
            result["returncode"] = 1
            result["errors"] = [
                {
                    "tool": "verilator",
                    "severity": "ERROR",
                    "type": "BLKLOOPINIT",
                    "message": "Unsupported delayed assignment in reset loop",
                }
            ]
        return result


def _context_with_candidates(tmp_path: Path) -> tuple[RTLTaskContext, Path]:
    runs_root = tmp_path / "runs"
    context = RTLTaskContext(task_id="task_static", raw_spec="counter")
    context.spec_ir = SpecIR(
        module_name="counter",
        raw_spec="counter",
        ports=[
            PortSpec("clk", PortDirection.INPUT),
            PortSpec("count", PortDirection.OUTPUT, 4),
        ],
        clock="clk",
    )
    for candidate_id in ("cand_01", "cand_02"):
        path = runs_root / context.task_id / "candidates" / candidate_id / "counter.v"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "module counter(input wire clk, output reg [3:0] count); endmodule\n",
            encoding="utf-8",
        )
        context.candidates.append(
            Candidate(
                candidate_id=candidate_id,
                artifact_path=str(path),
                code_hash=sha256(path.read_bytes()).hexdigest(),
            )
        )
    return context, runs_root


def test_static_evaluator_selects_highest_quality_passing_candidate(tmp_path: Path) -> None:
    context, runs_root = _context_with_candidates(tmp_path)
    runner = RecordingRunner({"cand_01": 85, "cand_02": 100})

    outcome = StaticCandidateEvaluator(runner, runs_root).evaluate(context)

    assert outcome.accepted is True
    assert outcome.selected_candidate_id == "cand_02"
    assert outcome.scores == {"cand_01": 85.0, "cand_02": 100.0}
    assert len(runner.calls) == 6
    report = json.loads((runs_root / context.task_id / "static_evaluation.json").read_text())
    assert report["candidates"]["cand_01"]["accepted"] is True
    assert report["candidates"]["cand_01"]["scorecard"]["config_version"] == "score-v1-test"
    assert report["candidates"]["cand_01"]["scorecard"]["gate_stage"] == "STATIC_SCORED"
    assert (runs_root / context.task_id / "tool_runs" / "cand_02_lint.json").is_file()
    assert (runs_root / context.task_id / "tool_runs" / "cand_02_interface.json").is_file()


def test_static_evaluator_rejects_candidate_that_fails_a_gate(tmp_path: Path) -> None:
    context, runs_root = _context_with_candidates(tmp_path)
    runner = RecordingRunner(
        {"cand_01": 100, "cand_02": 100}, failures={("cand_02", "lint")}
    )

    outcome = StaticCandidateEvaluator(runner, runs_root).evaluate(context)

    assert outcome.accepted is True
    assert outcome.selected_candidate_id == "cand_01"
    assert context.candidates[1].metadata["static_evaluation"]["accepted"] is False
    assert "lint" in context.candidates[1].metadata["static_evaluation"]["reason"]


def test_static_evaluator_rejects_when_all_candidates_fail(tmp_path: Path) -> None:
    context, runs_root = _context_with_candidates(tmp_path)
    runner = RecordingRunner(
        {"cand_01": 100, "cand_02": 100},
        failures={("cand_01", "compile"), ("cand_02", "lint")},
    )

    outcome = StaticCandidateEvaluator(runner, runs_root).evaluate(context)

    assert outcome.accepted is False
    assert outcome.selected_candidate_id == "cand_01"
    assert outcome.category == "SYNTAX_ERROR"
    assert outcome.scores == {"cand_01": 0.0, "cand_02": 0.0}


def test_static_evaluator_rejects_interface_mismatch_before_eda(tmp_path: Path) -> None:
    context, runs_root = _context_with_candidates(tmp_path)
    Path(context.candidates[0].artifact_path).write_text(
        "module wrong_name(input wire clk, output reg [3:0] count); endmodule\n",
        encoding="utf-8",
    )
    runner = RecordingRunner({"cand_01": 100, "cand_02": 80})

    outcome = StaticCandidateEvaluator(runner, runs_root).evaluate(context)

    assert outcome.accepted is True
    assert outcome.selected_candidate_id == "cand_02"
    assert not any(candidate_id == "cand_01" for candidate_id, _ in runner.calls)
    report = context.candidates[0].metadata["static_evaluation"]
    assert report["scorecard"]["gate_stage"] == "PATH_VALIDATED"
    assert report["results"]["interface"]["category"] == "INTERFACE_MISMATCH"


def test_static_evaluator_enforces_configured_threshold(tmp_path: Path) -> None:
    context, runs_root = _context_with_candidates(tmp_path)
    runner = RecordingRunner({"cand_01": 74, "cand_02": 75})

    outcome = StaticCandidateEvaluator(runner, runs_root).evaluate(context)

    assert outcome.selected_candidate_id == "cand_02"
    rejected = context.candidates[0].metadata["static_evaluation"]
    assert rejected["accepted"] is False
    assert "below threshold" in rejected["reason"]


def test_below_threshold_candidates_remain_as_verification_fallbacks(tmp_path: Path) -> None:
    context, runs_root = _context_with_candidates(tmp_path)
    runner = RecordingRunner({"cand_01": 74, "cand_02": 70})

    outcome = StaticCandidateEvaluator(runner, runs_root).evaluate(context)

    assert outcome.accepted is True
    assert outcome.selected_candidate_id == "cand_01"
    assert context.metadata["verification_order"] == ["cand_01", "cand_02"]
    assert all(
        candidate.metadata["static_evaluation"]["eligible_for_verification"]
        for candidate in context.candidates
    )


def test_verilator_incompatibility_does_not_reject_iverilog_compilable_rtl(
    tmp_path: Path,
) -> None:
    context, runs_root = _context_with_candidates(tmp_path)
    runner = ToolIncompatibilityRunner({"cand_01": 100, "cand_02": 90})

    outcome = StaticCandidateEvaluator(runner, runs_root).evaluate(context)

    assert outcome.accepted is True
    assert outcome.selected_candidate_id == "cand_01"
    report = context.candidates[0].metadata["static_evaluation"]
    assert report["eligible_for_verification"] is True
    assert "tool incompatibility" in report["reason"].lower()


def test_docker_runner_rejects_artifacts_outside_runs_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside.v"
    outside.write_text("module outside; endmodule\n", encoding="utf-8")
    runner = DockerEDARunner(tmp_path / "runs", docker_bin="docker")

    with pytest.raises(DockerToolError, match="outside"):
        runner.run("compile", outside)


def test_docker_runner_rejects_unapproved_oracle_path(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    unapproved = tmp_path / "private" / "golden_test.sv"
    unapproved.parent.mkdir()
    unapproved.write_text("module tb; endmodule\n", encoding="utf-8")
    runner = DockerEDARunner(runs_root, docker_bin="docker")

    with pytest.raises(DockerToolError, match="read-only roots"):
        runner.run_files("simulate", [unapproved])


def test_docker_runner_discovers_standard_docker_desktop_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = (
        tmp_path
        / "Programs"
        / "DockerDesktop"
        / "resources"
        / "bin"
        / "docker.exe"
    )
    cli.parent.mkdir(parents=True)
    cli.touch()
    monkeypatch.delenv("DOCKER_BIN", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(candidate_evaluation.shutil, "which", lambda _: None)

    assert DockerEDARunner(tmp_path / "runs")._docker_binary() == str(cli)
