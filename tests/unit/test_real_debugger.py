from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from verilog_agent.agents import AgentOutputError, ArtifactRepository, RealDebugger
from verilog_agent.domain import Candidate, PortDirection, PortSpec, RTLTaskContext, SpecIR
from verilog_agent.testing import ScriptedProvider


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
    assert "test.sv" not in provider.requests[0]["input_text"]
    assert "ref.sv" not in provider.requests[0]["input_text"]


def test_real_debugger_requires_explicit_router_authorization(tmp_path: Path) -> None:
    context, candidate, runs = _context(tmp_path)
    context.metadata.pop("reflection_decision")
    debugger = RealDebugger(ScriptedProvider(), artifacts=ArtifactRepository(runs))

    with pytest.raises(AgentOutputError, match="explicit repair route"):
        debugger.repair(context, candidate)
