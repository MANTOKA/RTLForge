from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from verilog_agent.agents import AgentOutputError, ArtifactRepository, RealGenerator, RealPlanner
from verilog_agent.domain import (
    PortDirection,
    PortSpec,
    RTLTaskContext,
    SpecIR,
    TaskTreeNode,
)
from verilog_agent.testing import ScriptedProvider


def plan_payload() -> dict:
    return {
        "module_name": "counter",
        "ports": [
            {"name": "clk", "direction": "input", "width": 1},
            {"name": "rst", "direction": "input", "width": 1},
            {"name": "q", "direction": "output", "width": 4},
        ],
        "clock": "clk",
        "reset": "rst",
        "assumptions": ["synchronous active-high reset"],
        "acceptance_criteria": ["reset clears q", "q increments on each clock"],
        "task_tree_root": {
            "node_id": "root",
            "title": "Implement counter",
            "description": "Build and verify a four-bit counter",
            "acceptance_criteria": ["all behavior is verified"],
            "children": [
                {
                    "node_id": "rtl",
                    "title": "Generate RTL",
                    "description": "Generate synthesizable counter RTL",
                    "acceptance_criteria": ["module compiles"],
                },
                {
                    "node_id": "verify",
                    "title": "Verify behavior",
                    "description": "Check reset and increment behavior",
                    "acceptance_criteria": ["counter behavior passes"],
                },
            ],
        },
    }


def planned_context(task_id: str = "task_counter") -> RTLTaskContext:
    criteria = ["reset clears q", "q increments on each clock"]
    return RTLTaskContext(
        task_id=task_id,
        raw_spec="Create a four-bit counter",
        spec_ir=SpecIR(
            module_name="counter",
            raw_spec="Create a four-bit counter",
            ports=[
                PortSpec("clk", PortDirection.INPUT),
                PortSpec("rst", PortDirection.INPUT),
                PortSpec("q", PortDirection.OUTPUT, width=4),
            ],
            clock="clk",
            reset="rst",
            assumptions=["synchronous active-high reset"],
            acceptance_criteria=criteria,
        ),
        task_tree_root=TaskTreeNode(
            node_id="root",
            title="Implement counter",
            description="Build the counter",
            acceptance_criteria=criteria,
            children=[
                TaskTreeNode("rtl", "Generate RTL", "Generate synthesizable RTL")
            ],
        ),
    )


def candidate_payload() -> dict:
    return {
        "candidates": [
            {
                "rtl": (
                    "module counter(input clk, input rst, output reg [3:0] q);\n"
                    "always @(posedge clk) begin\n"
                    "  if (rst) q <= 4'b0; else q <= q + 4'b1;\n"
                    "end\nendmodule"
                ),
                "design_pattern": "single sequential process",
                "assumptions": ["synchronous active-high reset"],
            },
            {
                "rtl": (
                    "module counter(input clk, input rst, output reg [3:0] q);\n"
                    "wire [3:0] next_q = q + 4'b1;\n"
                    "always @(posedge clk) begin\n"
                    "  if (rst) q <= 4'b0; else q <= next_q;\n"
                    "end\nendmodule"
                ),
                "design_pattern": "explicit next-value datapath",
                "assumptions": ["synchronous active-high reset"],
            },
        ]
    }


def test_real_planner_builds_valid_domain_plan() -> None:
    provider = ScriptedProvider(plan_payload())
    context = RTLTaskContext(task_id="task_plan", raw_spec="Create a four-bit counter")

    result = RealPlanner(provider).plan(context)

    assert result.spec_ir.module_name == "counter"
    assert result.spec_ir.raw_spec == context.raw_spec
    assert result.spec_ir.clock == "clk"
    assert result.spec_ir.ports[-1].width == 4
    assert [child.node_id for child in result.task_tree_root.children] == ["rtl", "verify"]
    assert provider.requests[0]["schema_name"] == "verilog_requirement_plan"


def test_real_planner_rejects_clock_that_is_not_an_input() -> None:
    payload = plan_payload()
    payload["clock"] = "q"

    with pytest.raises(AgentOutputError, match="clock must name a declared input"):
        RealPlanner(ScriptedProvider(payload)).plan(
            RTLTaskContext(task_id="task_bad_plan", raw_spec="counter")
        )


def test_real_generator_writes_hashed_distinct_candidates(tmp_path: Path) -> None:
    provider = ScriptedProvider(candidate_payload())
    generator = RealGenerator(provider, artifacts=ArtifactRepository(tmp_path / "runs"))

    candidates = generator.generate(planned_context(), 2)

    assert [candidate.candidate_id for candidate in candidates] == ["cand_01", "cand_02"]
    assert candidates[0].code_hash != candidates[1].code_hash
    for candidate in candidates:
        path = Path(candidate.artifact_path)
        assert path.is_file()
        assert candidate.code_hash == sha256(path.read_bytes()).hexdigest()
    schema = provider.requests[0]["schema"]
    assert schema["properties"]["candidates"]["minItems"] == 2
    assert schema["properties"]["candidates"]["maxItems"] == 2


def test_real_generator_rejects_duplicates_before_writing(tmp_path: Path) -> None:
    payload = candidate_payload()
    payload["candidates"][1]["rtl"] = payload["candidates"][0]["rtl"]
    generator = RealGenerator(
        ScriptedProvider(payload), artifacts=ArtifactRepository(tmp_path / "runs")
    )

    with pytest.raises(AgentOutputError, match="duplicate"):
        generator.generate(planned_context(), 2)

    assert not (tmp_path / "runs").exists()


def test_artifact_repository_rejects_unsafe_task_path(tmp_path: Path) -> None:
    generator = RealGenerator(
        ScriptedProvider(candidate_payload()),
        artifacts=ArtifactRepository(tmp_path / "runs"),
    )

    with pytest.raises(AgentOutputError, match="unsafe task_id"):
        generator.generate(planned_context("../escape"), 2)
