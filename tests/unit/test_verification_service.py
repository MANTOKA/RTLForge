from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from verilog_agent.domain import Candidate, RTLTaskContext
from verilog_agent.services import DockerToolError, GoldenTestbenchVerificationService


def _result(*, status: str, category: str, stdout: str = "") -> dict:
    return {
        "schema_version": 1,
        "action": "simulate",
        "stage": "SIMULATION",
        "status": status,
        "category": category,
        "tool": "vvp",
        "returncode": 0 if status == "PASS" else 1,
        "duration_ms": 5,
        "errors": [],
        "metrics": {},
        "stdout": stdout,
        "stderr": "",
    }


class SimulationRunner:
    def __init__(self, results: dict[str, dict]) -> None:
        self.results = results
        self.calls: list[str] = []
        self.input_calls: list[list[str]] = []

    def run(self, action: str, candidate_path: Path) -> dict:
        return self.run_files(action, [candidate_path])

    def run_files(self, action: str, input_paths: list[Path]) -> dict:
        assert action == "simulate"
        candidate_id = input_paths[-1].parent.name
        self.calls.append(candidate_id)
        self.input_calls.append([path.name for path in input_paths])
        return self.results[candidate_id]


class BrokenRunner(SimulationRunner):
    def run_files(self, action: str, input_paths: list[Path]) -> dict:
        raise DockerToolError("Docker engine unavailable")


def _context(tmp_path: Path) -> tuple[RTLTaskContext, Path, Path]:
    runs_root = tmp_path / "runs"
    context = RTLTaskContext(task_id="task_verify", raw_spec="counter")
    for candidate_id in ("cand_01", "cand_02"):
        rtl = runs_root / context.task_id / "candidates" / candidate_id / "counter.v"
        rtl.parent.mkdir(parents=True, exist_ok=True)
        rtl.write_text("module counter; endmodule\n", encoding="utf-8")
        context.candidates.append(
            Candidate(candidate_id, str(rtl), sha256(rtl.read_bytes()).hexdigest())
        )
    context.selected_candidate_id = "cand_01"
    context.metadata["verification_order"] = ["cand_01", "cand_02"]
    testbench = tmp_path / "benchmark" / "counter_test.sv"
    testbench.parent.mkdir()
    testbench.write_text("module tb; endmodule\n", encoding="utf-8")
    return context, runs_root, testbench


def test_verification_falls_back_to_next_ranked_candidate(tmp_path: Path) -> None:
    context, runs_root, testbench = _context(tmp_path)
    runner = SimulationRunner(
        {
            "cand_01": _result(
                status="FAIL", category="ASSERTION_FAILURE", stdout="secret oracle output"
            ),
            "cand_02": _result(status="PASS", category="OK"),
        }
    )
    service = GoldenTestbenchVerificationService(
        runner,
        runs_root,
        lambda _context: testbench,
        reference_resolver=lambda _context: _reference(tmp_path),
    )

    outcome = service.verify(context, context.candidate())

    assert outcome.passed is True
    assert context.selected_candidate_id == "cand_02"
    assert runner.calls == ["cand_01", "cand_02"]
    assert outcome.details["attempted_candidate_ids"] == ["cand_01", "cand_02"]
    assert "secret oracle output" not in json.dumps(outcome.details)
    report = json.loads((runs_root / context.task_id / "verification.json").read_text())
    assert report["testbench"]["content_exposed_to_agents"] is False
    assert report["testbench"]["confidence"] == "HIGH"
    assert report["reference"]["usage"] == "simulator_black_box"
    assert report["reference"]["content_exposed_to_agents"] is False
    assert runner.input_calls[0] == ["counter_test.sv", "counter_ref.sv", "counter.v"]


def test_verification_returns_best_ranked_failure_for_debugger(tmp_path: Path) -> None:
    context, runs_root, testbench = _context(tmp_path)
    runner = SimulationRunner(
        {
            "cand_01": _result(status="FAIL", category="ASSERTION_FAILURE"),
            "cand_02": _result(status="FAIL", category="TIMEOUT"),
        }
    )
    service = GoldenTestbenchVerificationService(
        runner, runs_root, lambda _context: testbench
    )

    outcome = service.verify(context, context.candidate())

    assert outcome.passed is False
    assert outcome.category == "FUNCTIONAL_MISMATCH"
    assert context.selected_candidate_id == "cand_01"
    assert outcome.details["debugger_feedback"]["category"] == "FUNCTIONAL_MISMATCH"


def test_verification_stops_on_infrastructure_failure(tmp_path: Path) -> None:
    context, runs_root, testbench = _context(tmp_path)
    service = GoldenTestbenchVerificationService(
        BrokenRunner({}), runs_root, lambda _context: testbench
    )

    outcome = service.verify(context, context.candidate())

    assert outcome.passed is False
    assert outcome.category == "SYSTEM_ERROR"
    assert outcome.details["attempted_candidate_ids"] == ["cand_01"]


def test_testbench_compile_error_is_infrastructure_not_candidate_syntax(tmp_path: Path) -> None:
    context, runs_root, testbench = _context(tmp_path)
    syntax_result = _result(status="FAIL", category="SYNTAX_ERROR")
    syntax_result["errors"] = [
        {
            "tool": "iverilog",
            "severity": "ERROR",
            "type": "ERROR",
            "file": str(testbench),
            "line": 7,
            "message": "Unknown module type: RefModule",
        }
    ]
    runner = SimulationRunner({"cand_01": syntax_result, "cand_02": syntax_result})
    service = GoldenTestbenchVerificationService(
        runner, runs_root, lambda _context: testbench
    )

    outcome = service.verify(context, context.candidate())

    assert outcome.category == "SYSTEM_ERROR"
    assert runner.calls == ["cand_01"]


def _reference(tmp_path: Path) -> Path:
    reference = tmp_path / "benchmark" / "counter_ref.sv"
    reference.write_text("module RefModule; endmodule\n", encoding="utf-8")
    return reference
