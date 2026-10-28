from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from rtlforge.agents import AgentOutputError, ArtifactRepository, RealDebugger
from rtlforge.agents.validation import RepairNoProgressError
from rtlforge.domain import Candidate, PortDirection, PortSpec, RTLTaskContext, SpecIR
from rtlforge.testing import ScriptedProvider


def _context(tmp_path: Path) -> tuple[RTLTaskContext, Candidate, Path]:
    runs = tmp_path / "runs"
    source = runs / "task_debug" / "candidates" / "cand_01" / "counter.v"
    source.parent.mkdir(parents=True)
    rtl = (
        "module counter(input clk, input rst, output reg [3:0] q);\n"
        "always @(posedge clk) if (rst) q <= 4'b0; else q <= q - 1'b1;\n"
        "endmodule\n"
    )
    source.write_text(rtl, encoding="utf-8")
    candidate = Candidate("cand_01", str(source), sha256(rtl.encode()).hexdigest())
    context = RTLTaskContext(task_id="task_debug", raw_spec="incrementing counter")
    context.spec_ir = SpecIR(
        module_name="counter",
        raw_spec=context.raw_spec,
        ports=[
            PortSpec("clk", PortDirection.INPUT),
            PortSpec("rst", PortDirection.INPUT),
            PortSpec("q", PortDirection.OUTPUT, 4),
        ],
        clock="clk",
        reset="rst",
        acceptance_criteria=["q increments after each non-reset edge"],
    )
    context.candidates = [candidate]
    context.selected_candidate_id = candidate.candidate_id
    context.failure_category = "FUNCTIONAL_MISMATCH"
    context.budget.repair_attempts = 1
    context.metadata["reflection_decision"] = {
        "policy": "FUNCTIONAL_REFLECTION",
        "action": "DEBUG",
        "invokes_debugger": True,
    }
    context.metadata["verification_attempts"] = [
        {
            "candidate_id": "cand_01",
            "debugger_feedback": {
                "category": "FUNCTIONAL_MISMATCH",
                "tool": "vvp",
                "errors": [{"message": "Simulation reported mismatches"}],
                "timed_out": False,
            },
        }
    ]
    context.metadata["repair_attempts"] = [
        {
            "candidate_id": "cand_repair_00",
            "mismatch_reduction": 0,
            "rationale": "An earlier threshold edit made no progress.",
        }
    ]
    context.metadata["last_repair_analysis"] = {
        "no_progress": True,
        "before_mismatches": 12,
        "after_mismatches": 12,
    }
    context.metadata["retrieved_memories"] = [
        {
            "memory_id": "mem_counter",
            "applicability_conditions": "counter boundary mismatch",
            "recommended_action": "Re-check the wrap threshold.",
        }
    ]
    context.metadata["generated_tb_attempts"] = [{
        "candidate_id": "cand_01",
        "advisory_feedback": {
            "category": "SIMULATION_ERROR",
            "mismatched_outputs": ["q"],
            "unasserted_outputs": ["q"],
            "completion_timeout": False,
            "raw_transcript": "private agent TB values",
        },
    }]
    return context, candidate, runs


def test_real_debugger_writes_child_candidate_and_auditable_diff(tmp_path: Path) -> None:
    context, candidate, runs = _context(tmp_path)
    repaired = (
        "module counter(input clk, input rst, output reg [3:0] q);\n"
        "always @(posedge clk) if (rst) q <= 4'b0; else q <= q + 1'b1;\n"
        "endmodule"
    )
    provider = ScriptedProvider(
        {
            "patched_rtl": repaired,
            "rationale": "Correct the counter direction.",
            "expected_impact": "The state increments after each clock edge.",
            "risks": ["Reset polarity still relies on the confirmed specification."],
        }
    )

    child = RealDebugger(provider, artifacts=ArtifactRepository(runs)).repair(
        context, candidate
    )

    assert child.candidate_id == "cand_repair_01"
    assert child.parent_id == "cand_01"
    assert child.generation == 1
    assert Path(child.artifact_path).read_text(encoding="utf-8").endswith("endmodule\n")
    patch = Path(child.metadata["diff_path"]).read_text(encoding="utf-8")
    assert "q <= q - 1'b1" in patch
    assert "q <= q + 1'b1" in patch
    request = json.loads(provider.requests[0]["input_text"])
    assert request["debugger_feedback"]["tool"] == "vvp"
    assert request["prior_repair_attempts"][0]["mismatch_reduction"] == 0
    assert request["last_repair_analysis"]["no_progress"] is True
    assert request["retrieved_memories"][0]["memory_id"] == "mem_counter"
    assert request["debugger_feedback"]["agent_tb_advisory"] == {
        "source": "agent_generated_tb",
        "authority": "advisory",
        "category": "SIMULATION_ERROR",
        "mismatched_outputs": ["q"],
        "unasserted_outputs": ["q"],
        "completion_timeout": False,
    }
    assert "private agent TB values" not in provider.requests[0]["input_text"]
    assert "completion timeout" in provider.requests[0]["instructions"]
    assert "worst-case cycles" in provider.requests[0]["instructions"]
    assert "2^m-2 serial multiplications" in provider.requests[0]["instructions"]
    assert "iteration variable" in provider.requests[0]["instructions"]
    assert "coefficient-wise loops" in provider.requests[0]["instructions"]
    assert "test.sv" not in provider.requests[0]["input_text"]
    assert "ref.sv" not in provider.requests[0]["input_text"]


def test_quality_repair_receives_sanitized_static_deductions(tmp_path: Path) -> None:
    context, candidate, runs = _context(tmp_path)
    context.failure_category = "QUALITY_ERROR"
    candidate.metadata["static_evaluation"] = {
        "scorecard": {
            "static_quality_score": 25,
            "threshold": 75,
            "deductions": [{
                "rule": "WIDTH", "penalty": 10,
                "evidence": {
                    "file": "/private/path/counter.v", "line": 2,
                    "message": "Array index has the wrong width",
                },
            }],
        },
        "results": {"eval_quality": {"stdout": "private full tool transcript"}},
    }
    repaired = (
        "module counter(input clk, input rst, output reg [3:0] q);\n"
        "always @(posedge clk) if (rst) q <= 4'b0; else q <= q + 1'b1;\n"
        "endmodule"
    )
    provider = ScriptedProvider({
        "patched_rtl": repaired,
        "rationale": "Correct the implementation.",
        "expected_impact": "Improves the static quality.",
        "risks": ["Functional verification is still required."],
    })

    RealDebugger(provider, artifacts=ArtifactRepository(runs)).repair(context, candidate)

    feedback = json.loads(provider.requests[0]["input_text"])["debugger_feedback"]
    assert feedback["static_quality"] == {
        "score": 25,
        "threshold": 75,
        "deductions": [{
            "rule": "WIDTH", "penalty": 10, "file": "counter.v", "line": 2,
            "message": "Array index has the wrong width",
        }],
    }
    assert "private full tool transcript" not in provider.requests[0]["input_text"]


def test_functional_repair_receives_only_bounded_agent_tb_value(tmp_path: Path) -> None:
    context, candidate, runs = _context(tmp_path)
    context.metadata["generated_tb_attempts"][0]["advisory_feedback"][
        "first_value_mismatch"
    ] = {"output": "q", "observed": "0000", "agent_tb_expected": "0001"}
    original = Path(candidate.artifact_path).read_text(encoding="utf-8")
    provider = ScriptedProvider({
        "patched_rtl": original.replace("q - 1'b1", "q + 1'b1"),
        "rationale": "Fix direction.", "expected_impact": "Increment.", "risks": ["Retest."],
    })

    RealDebugger(provider, artifacts=ArtifactRepository(runs)).repair(context, candidate)

    request = json.loads(provider.requests[0]["input_text"])
    assert request["debugger_feedback"]["agent_tb_advisory"]["first_value_mismatch"] == {
        "output": "q", "observed": "0000", "agent_tb_expected": "0001",
    }
    assert "unverified claim" in provider.requests[0]["instructions"]
    assert "/private/path" not in provider.requests[0]["input_text"]


@pytest.mark.parametrize("category, stage", [("LINT_ERROR", "lint"), ("SYNTAX_ERROR", "compile")])
def test_static_repair_receives_hard_error_before_warnings(
    tmp_path: Path, category: str, stage: str,
) -> None:
    context, candidate, runs = _context(tmp_path)
    context.failure_category = category
    candidate.metadata["static_evaluation"] = {
        "results": {stage: {
            "category": category,
            "errors": [
                *[{
                    "severity": "WARNING", "type": "WIDTH", "file": "/private/counter.v",
                    "line": number, "message": "width warning",
                } for number in range(12)],
                {
                    "severity": "ERROR", "type": "BLKANDNBLK",
                    "file": "/private/counter.v", "line": 34,
                    "message": "Blocked and non-blocking assignments to same variable",
                },
            ],
            "stderr": "private full lint transcript",
        }},
    }
    original = Path(candidate.artifact_path).read_text(encoding="utf-8")
    provider = ScriptedProvider({
        "patched_rtl": original.replace("q - 1'b1", "q + 1'b1"),
        "rationale": "Fix RTL.", "expected_impact": "Improve lint.", "risks": ["Retest."],
    })

    RealDebugger(provider, artifacts=ArtifactRepository(runs)).repair(context, candidate)

    request = json.loads(provider.requests[0]["input_text"])
    diagnostics = request["debugger_feedback"]["static_diagnostics"]
    assert diagnostics["stage"] == stage
    assert diagnostics["items"][0] == {
        "severity": "ERROR", "type": "BLKANDNBLK", "file": "counter.v",
        "line": 34, "message": "Blocked and non-blocking assignments to same variable",
    }
    assert len(diagnostics["items"]) <= 8
    assert "/private/" not in provider.requests[0]["input_text"]
    assert "private full lint transcript" not in provider.requests[0]["input_text"]


def test_functional_repair_rejects_comment_only_change(tmp_path: Path) -> None:
    context, candidate, runs = _context(tmp_path)
    original = Path(candidate.artifact_path).read_text(encoding="utf-8")
    patched = original.replace("endmodule", "// This does not change RTL behavior.\nendmodule")
    provider = ScriptedProvider({
        "patched_rtl": patched,
        "rationale": "Added a comment.",
        "expected_impact": "None.",
        "risks": ["The failure remains."],
    })

    with pytest.raises(RepairNoProgressError, match="comments or whitespace"):
        RealDebugger(provider, artifacts=ArtifactRepository(runs)).repair(context, candidate)

    assert not (runs / context.task_id / "candidates" / "cand_repair_01").exists()


def test_quality_repair_may_change_lint_directive_comment(tmp_path: Path) -> None:
    context, candidate, runs = _context(tmp_path)
    context.failure_category = "QUALITY_ERROR"
    original = Path(candidate.artifact_path).read_text(encoding="utf-8")
    patched = original.replace("endmodule", "// verilator lint_off WIDTH\nendmodule")
    provider = ScriptedProvider({
        "patched_rtl": patched,
        "rationale": "Adjust a lint directive.",
        "expected_impact": "Changes static feedback.",
        "risks": ["Must re-evaluate quality."],
    })

    child = RealDebugger(provider, artifacts=ArtifactRepository(runs)).repair(context, candidate)

    assert child.candidate_id == "cand_repair_01"


def test_real_debugger_requires_explicit_router_authorization(tmp_path: Path) -> None:
    context, candidate, runs = _context(tmp_path)
    context.metadata.pop("reflection_decision")
    debugger = RealDebugger(ScriptedProvider(), artifacts=ArtifactRepository(runs))

    with pytest.raises(AgentOutputError, match="explicit repair route"):
        debugger.repair(context, candidate)
