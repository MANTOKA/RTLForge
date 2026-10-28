from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from rtlforge.agents import AgentOutputError, ArtifactRepository, RealGenerator, RealPlanner
from rtlforge.agents.testbench import RealTestbenchGenerator
from rtlforge.domain import (
    PortDirection,
    PortSpec,
    RTLTaskContext,
    SpecIR,
    TaskTreeNode,
)
from rtlforge.testing import (
    ScriptedProvider,
    counter_candidate_payload,
    counter_plan_payload,
)


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


def test_real_planner_builds_valid_domain_plan() -> None:
    provider = ScriptedProvider(counter_plan_payload())
    context = RTLTaskContext(task_id="task_plan", raw_spec="Create a four-bit counter")

    result = RealPlanner(provider).plan(context)

    assert result.spec_ir.module_name == "counter"
    assert result.spec_ir.raw_spec == context.raw_spec
    assert result.spec_ir.clock == "clk"
    assert result.spec_ir.ports[-1].width == 4
    assert [child.node_id for child in result.task_tree_root.children] == ["rtl", "verify"]
    assert provider.requests[0]["schema_name"] == "verilog_requirement_plan"


def test_real_planner_honors_required_module_name_metadata() -> None:
    provider = ScriptedProvider(counter_plan_payload())
    context = RTLTaskContext(
        task_id="task_benchmark_plan",
        raw_spec="Create a four-bit counter",
        metadata={"required_module_name": "TopModule"},
    )

    result = RealPlanner(provider).plan(context)

    assert result.spec_ir.module_name == "TopModule"


def test_planner_preserves_suggested_clarification_answers() -> None:
    payload = counter_plan_payload()
    payload["clarification_options"] = ["同步高有效复位", "异步高有效复位"]
    context = RTLTaskContext(task_id="task_options", raw_spec="counter")
    RealPlanner(ScriptedProvider(payload)).plan(context)
    assert context.metadata["clarification_options"] == payload["clarification_options"]


def test_real_planner_rejects_clock_that_is_not_an_input() -> None:
    payload = counter_plan_payload()
    payload["clock"] = "q"

    with pytest.raises(AgentOutputError, match="clock must name a declared input"):
        RealPlanner(ScriptedProvider(payload, payload)).plan(
            RTLTaskContext(task_id="task_bad_plan", raw_spec="counter")
        )


def test_contract_testbench_request_has_bounded_advisory_scope() -> None:
    provider = ScriptedProvider({
        "testbench": (
            "module tb; initial begin if (1'b0 !== 1'b0) $fatal(1,\"bad\"); "
            "$display(\"GENERATED_TB_PASS\"); $finish; end endmodule"
        ),
        "test_objectives": ["Check reset"],
    })

    RealTestbenchGenerator(provider).generate_for_contract(planned_context().spec_ir)

    instructions = provider.requests[0]["instructions"]
    assert "at most 200 source lines" in instructions
    assert "3 to 5 directed scenarios" in instructions
    assert "full reference algorithm" in instructions
    assert "derive exact expected outputs from the contract" in instructions
    assert "never reuse reset or no-error expected outputs" in instructions
    assert "advisory" in instructions
    assert "locator=1 is only the zero-error locator" not in instructions


def test_rs_bm_contract_testbench_disallows_nonzero_zero_error_locator() -> None:
    provider = ScriptedProvider({
        "testbench": (
            "module tb; initial begin if (1'b0 !== 1'b0) $fatal(1,\"bad\"); "
            "$display(\"GENERATED_TB_PASS\"); $finish; end endmodule"
        ),
        "test_objectives": ["Check BM handshake"],
    })
    contract = planned_context().spec_ir
    contract.module_name = "rs_kes_bm"

    RealTestbenchGenerator(provider).generate_for_contract(contract)

    instructions = provider.requests[0]["instructions"]
    assert "locator=1 is only the zero-error locator" in instructions
    assert "test protocol properties instead of guessing exact values" in instructions


def test_rs_bm_contract_testbench_retries_obvious_wrong_locator() -> None:
    def payload(locator: str) -> dict:
        return {
            "testbench": (
                "module tb; initial begin\n"
                "run_case(140'h0, 80'h1, 70'h0);\n"
                f"run_case({{14{{10'h001}}}}, {locator}, 70'h1);\n"
                "if (1'b0 !== 1'b0) $fatal(1,\"bad\");\n"
                "$display(\"GENERATED_TB_PASS\"); $finish; end endmodule"
            ),
            "test_objectives": ["Check BM outputs"],
        }

    provider = ScriptedProvider(payload("80'h1"), payload("80'h401"))
    contract = planned_context().spec_ir
    contract.module_name = "rs_kes_bm"

    source, _ = RealTestbenchGenerator(provider).generate_for_contract(contract)

    assert len(provider.requests) == 2
    assert "previous TB used an inconsistent RS locator expectation" in (
        provider.requests[1]["instructions"]
    )
    assert "80'h401" in source


def test_rs_bm_contract_testbench_rejects_repeated_wrong_locator() -> None:
    bad = {
        "testbench": (
            "module tb; initial begin run_case({14{10'h001}}, 80'h1, 70'h1); "
            "if (1'b0 !== 1'b0) $fatal(1,\"bad\"); "
            "$display(\"GENERATED_TB_PASS\"); $finish; end endmodule"
        ),
        "test_objectives": ["Check BM outputs"],
    }
    provider = ScriptedProvider(bad, bad)
    contract = planned_context().spec_ir
    contract.module_name = "rs_kes_bm"

    with pytest.raises(AgentOutputError, match="inconsistent locator"):
        RealTestbenchGenerator(provider).generate_for_contract(contract)

    assert len(provider.requests) == 2


def test_rs_bm_contract_testbench_retries_wrong_locator_constant_term() -> None:
    bad = {
        "testbench": (
            "module tb; initial begin if (locator !== 80'h3) $fatal(1,\"bad\"); "
            "$display(\"GENERATED_TB_PASS\"); $finish; end endmodule"
        ),
        "test_objectives": ["Check BM output"],
    }
    good = {
        "testbench": bad["testbench"].replace("80'h3", "80'h401"),
        "test_objectives": bad["test_objectives"],
    }
    provider = ScriptedProvider(bad, good)
    contract = planned_context().spec_ir
    contract.module_name = "rs_kes_bm"

    source, _ = RealTestbenchGenerator(provider).generate_for_contract(contract)

    assert len(provider.requests) == 2
    assert "80'h401" in source


def test_real_generator_writes_hashed_distinct_candidates(tmp_path: Path) -> None:
    provider = ScriptedProvider(counter_candidate_payload())
    generator = RealGenerator(provider, artifacts=ArtifactRepository(tmp_path / "runs"))
    context = planned_context()
    context.metadata["retrieved_design_patterns"] = [
        {
            "memory_id": "mem_pattern",
            "recommended_action": "Use an explicit terminal-count branch.",
        }
    ]

    candidates = generator.generate(context, 2)

    assert [candidate.candidate_id for candidate in candidates] == ["cand_01", "cand_02"]
    assert candidates[0].code_hash != candidates[1].code_hash
    for candidate in candidates:
        path = Path(candidate.artifact_path)
        assert path.is_file()
        assert path.name == "counter.v"
        assert candidate.code_hash == sha256(path.read_bytes()).hexdigest()
    schema = provider.requests[0]["schema"]
    assert schema["properties"]["candidates"]["minItems"] == 2
    assert schema["properties"]["candidates"]["maxItems"] == 2
    request = json.loads(provider.requests[0]["input_text"])
    assert request["retrieved_memory_guidance"][0]["memory_id"] == (
        "mem_pattern"
    )
    assert "worst-case cycles" in provider.requests[0]["instructions"]
    assert "2^m-2 serial multiplications" in provider.requests[0]["instructions"]
    assert "loop counter reaches its terminal value" in provider.requests[0]["instructions"]


def test_real_generator_rejects_duplicates_before_writing(tmp_path: Path) -> None:
    payload = counter_candidate_payload()
    payload["candidates"][1]["rtl"] = payload["candidates"][0]["rtl"]
    generator = RealGenerator(
        ScriptedProvider(payload), artifacts=ArtifactRepository(tmp_path / "runs")
    )

    with pytest.raises(AgentOutputError, match="duplicate"):
        generator.generate(planned_context(), 2)

    assert not (tmp_path / "runs").exists()


def test_sequential_generator_preserves_first_artifact_and_rejects_repeated_rtl(tmp_path):
    payload = counter_candidate_payload()["candidates"]
    generator = RealGenerator(
        ScriptedProvider(*({"candidates": [item]} for item in [*payload, payload[0]])),
        artifacts=ArtifactRepository(tmp_path / "runs"),
    )
    context = planned_context()
    first = generator.generate(context, 1)[0]
    context.candidates.append(first)
    original = Path(first.artifact_path).read_bytes()
    second = generator.generate(context, 1)[0]
    context.candidates.append(second)
    assert second.candidate_id == "cand_02"
    assert Path(first.artifact_path).read_bytes() == original
    with pytest.raises(AgentOutputError, match="duplicate"):
        generator.generate(context, 1)
    assert not (tmp_path / "runs" / context.task_id / "candidates" / "cand_03").exists()


def test_artifact_repository_rejects_unsafe_task_path(tmp_path: Path) -> None:
    generator = RealGenerator(
        ScriptedProvider(counter_candidate_payload()),
        artifacts=ArtifactRepository(tmp_path / "runs"),
    )

    with pytest.raises(AgentOutputError, match="unsafe task_id"):
        generator.generate(planned_context("../escape"), 2)
