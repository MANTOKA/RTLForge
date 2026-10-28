from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from rtlforge.domain import Budget, Candidate, RTLTaskContext, TaskStage
from rtlforge.domain.synthesis import SynthesisConstraints, SynthesisResult
from rtlforge.services.vivado_mcp import (
    VivadoMCPRunner,
    find_session_id,
    parse_reports,
    tcl_word,
    write_synthesis_script,
)
from rtlforge.testing import FakeDebugger, FakeVerifier
from tests.integration.test_fake_orchestrator import _orchestrator
from tests.integration.test_langgraph_workflow import _fake_graph


def constraints(**kwargs):
    return SynthesisConstraints(part="xc7a12ticsg325-1L", clock_port="clk",
                                clock_period_ns=10, **kwargs)


@pytest.mark.parametrize("bad", [0, -1, True, float("nan"), float("inf")])
def test_invalid_period(bad):
    with pytest.raises(ValueError):
        SynthesisConstraints(clock_period_ns=bad)


def test_context_roundtrip_and_legacy_compatibility():
    context = RTLTaskContext("task", "counter", synthesis_constraints=constraints())
    assert RTLTaskContext.from_dict(context.to_dict()).synthesis_constraints == constraints()
    legacy = context.to_dict()
    del legacy["synthesis_constraints"]
    assert RTLTaskContext.from_dict(legacy).synthesis_constraints is None


def reports(root, *, wns="1.25", clock_count=1, failure=None):
    if failure:
        (root / "failure.txt").write_text(failure)
        return
    (root / "metrics.tsv").write_text(
        f"vivado_version\t2026.1\nclock_count\t{clock_count}\nwns_ns\t{wns}\n"
    )
    (root / "utilization.rpt").write_text(
        "| Slice LUTs* | 1,234 |\n| Slice Registers | 751 |\n"
        "| Block RAM Tile | 0.5 |\n| DSPs | 0 |\n"
    )
    (root / "timing.rpt").write_text("post-synthesis timing")
    (root / "check_timing.rpt").write_text(
        "1. checking no_clock\nThere are 0 register/latch pins with no clock\n"
        "5. checking unconstrained_internal_endpoints\n"
        "There are 0 pins that are not constrained for maximum delay.\n"
    )
    (root / "synth.dcp").write_bytes(b"test checkpoint")


@pytest.mark.parametrize("wns,limit,category", [
    ("1.25", {}, "OK"), ("-2.5", {}, "SYNTHESIS_TIMING_FAILED"),
    ("1.0", {"lut": 1000}, "SYNTHESIS_RESOURCE_EXCEEDED"),
    ("", {}, "SYNTHESIS_CONSTRAINT_ERROR"),
])
def test_report_acceptance(tmp_path, wns, limit, category):
    reports(tmp_path, wns=wns)
    result = parse_reports(tmp_path, SynthesisResult(
        "candidate", "hash", constraints(resource_limits=limit).to_dict(),
    ))
    assert result.category == category
    assert result.resources["lut"] == 1234
    assert result.resources["bram"] == 0.5
    assert (result.status == "PASS") == (category == "OK")


@pytest.mark.parametrize("message,category", [
    ("ERROR: multi-driven net", "SYNTHESIS_ERROR"),
    ("ERROR: license checkout failed", "SYNTHESIS_TOOL_ERROR"),
])
def test_report_failure_classification(tmp_path, message, category):
    reports(tmp_path, failure=message)
    assert parse_reports(tmp_path, SynthesisResult("c", "h", constraints().to_dict())).category == (
        category
    )


@pytest.mark.parametrize("check", [
    "unknown report format",
    "1. checking no_clock\nThere are 0 register/latch pins with no clock\n"
    "5. checking unconstrained_internal_endpoints\n"
    "There are 3 pins that are not constrained for maximum delay.\n",
])
def test_wns_alone_does_not_prove_timing_coverage(tmp_path, check):
    reports(tmp_path)
    (tmp_path / "check_timing.rpt").write_text(check)
    result = parse_reports(tmp_path, SynthesisResult("c", "hash", constraints().to_dict()))
    assert result.category == "SYNTHESIS_CONSTRAINT_ERROR"
    assert result.status != "PASS"


@pytest.mark.parametrize("endpoints", [0, 3])
def test_vivado_check_timing_skips_table_of_contents(tmp_path, endpoints):
    reports(tmp_path)
    (tmp_path / "check_timing.rpt").write_text(
        "Table of Contents\n1. checking no_clock (0)\n"
        f"4. checking unconstrained_internal_endpoints ({endpoints})\n"
        "5. checking no_input_delay (12)\n\n"
        "1. checking no_clock (0)\n----------------\n"
        "There are 0 register/latch pins with no clock.\n\n"
        f"4. checking unconstrained_internal_endpoints ({endpoints})\n----------------\n"
        f"There are {endpoints} pins that are not constrained for maximum delay.\n\n"
        "5. checking no_input_delay (12)\nThere are 12 input ports with no input delay.\n"
    )
    result = parse_reports(tmp_path, SynthesisResult("c", "hash", constraints().to_dict()))
    assert result.constraint_coverage["unconstrained_internal_endpoints"] == endpoints
    assert result.status == ("PASS" if endpoints == 0 else "FAIL")


def test_source_and_xdc_are_quoted_and_snapshot_bound(tmp_path):
    rtl = tmp_path / 'a $b[1] {test}.sv'
    rtl.write_text("module a; endmodule")
    script, xdc = write_synthesis_script(tmp_path, rtl, constraints(top="a"))
    text = script.read_text()
    assert "read_verilog -sv" in text
    assert "\\$b\\[1]" in text
    assert "create_clock" in xdc.read_text()
    assert tcl_word("a; exit") == '"a; exit"'
    assert find_session_id({"content": [{"text": '{"session_id":"test-123"}'}]}) == "test-123"


class SynthesisSequence:
    def __init__(self, categories):
        self.categories = iter(categories)
        self.calls = []

    def synthesize(self, context, candidate):
        self.calls.append(candidate.candidate_id)
        category = next(self.categories)
        return SynthesisResult(
            candidate.candidate_id, candidate.code_hash, context.synthesis_constraints.to_dict(),
            status="PASS" if category == "OK" else "FAIL", category=category,
            synthesis_status="PASS", timing_status="PASS" if category == "OK" else "FAIL",
            wns_ns=1.0 if category == "OK" else -1.0,
        )


@pytest.mark.parametrize("scheduler", ["legacy", "langgraph"])
@pytest.mark.parametrize("category", [
    "SYNTHESIS_ERROR", "SYNTHESIS_TIMING_FAILED", "SYNTHESIS_RESOURCE_EXCEEDED",
])
@pytest.mark.parametrize("exhaust", [False, True])
def test_synthesis_repair_requires_functional_regression(tmp_path, scheduler, category, exhaust):
    verifier = FakeVerifier()
    engine = (_fake_graph(tmp_path, verifier) if scheduler == "langgraph"
              else _orchestrator(tmp_path / "audit.sqlite", verifier))
    ops = engine.operations if scheduler == "langgraph" else engine
    sequence = SynthesisSequence([category, category if exhaust else "OK"])
    ops.synthesis_runner = sequence

    class Debugger(FakeDebugger):
        def repair(self, context, candidate):
            feedback = ops._selected_debugger_feedback(context)
            assert feedback["synthesis"]["wns_ns"] == -1
            assert context.synthesis_constraints.clock_period_ns == 10
            return super().repair(context, candidate)

    ops.debugger = Debugger()
    task = engine.create_task("counter", budget=Budget(max_candidates=2, max_repairs=1),
                              synthesis_constraints=constraints())
    try:
        result = engine.run_task(task.task_id)
        assert sequence.calls == ["cand_01", "cand_repair_01"]
        assert verifier.calls == 2
        assert result.budget.generated_candidates == 1
        assert result.budget.repair_attempts == 1
        assert result.current_stage is (TaskStage.FAILED if exhaust else TaskStage.COMPLETED)
        if exhaust:
            assert result.failure_category == "BUDGET_EXHAUSTED"
        else:
            assert result.selected_candidate_id == "cand_repair_01"
        events = engine.store.list_events(task.task_id)
        successes = [e for e in events if e.event_type.value == "EVENT_SYSTEM_SUCCESS"]
        assert len(successes) == (0 if exhaust else 1)
    finally:
        if scheduler == "langgraph":
            engine.close()


def test_missing_implementation_settings_pause_and_resume_without_replanning(tmp_path):
    engine = _fake_graph(tmp_path, FakeVerifier())
    sequence = SynthesisSequence(["OK"])
    engine.operations.synthesis_runner = sequence
    task = engine.create_task("counter", synthesis_constraints=SynthesisConstraints())
    try:
        result = engine.run_task(task.task_id)
        assert result.current_stage is TaskStage.WAITING_FOR_USER
        assert result.metadata["pause_reason"] == "synthesis_constraints"
        result = engine.resume_task(task.task_id, {"action": "answer", "message": json.dumps({
            "part": "xc7a12ticsg325-1L", "clock_period_ns": 10,
        })})
        assert result.current_stage is TaskStage.COMPLETED
        assert sequence.calls == ["cand_01"]
    finally:
        engine.close()


def test_mcp_missing_binary_is_recorded_as_infrastructure_not_rtl_failure(tmp_path):
    rtl = tmp_path / "a.sv"
    rtl.write_text("module a; endmodule")
    candidate = Candidate("cand", str(rtl), sha256(rtl.read_bytes()).hexdigest())
    context = RTLTaskContext("task", "a", candidates=[candidate],
                             synthesis_constraints=constraints(top="a"))
    result = VivadoMCPRunner(tmp_path / "missing.exe", tmp_path / "vivado", tmp_path).synthesize(
        context, candidate,
    )
    assert result.category == "SYNTHESIS_TOOL_ERROR"
    assert Path(result.evidence["result"]).is_file()


def test_planner_extracts_implementation_inputs_without_inventing_defaults():
    from rtlforge.agents.planner import RealPlanner
    from rtlforge.testing import ScriptedProvider, counter_plan_payload

    payload = counter_plan_payload()
    payload["synthesis_constraints"] = {
        "part": None, "clock_period_ns": 10,
        "resource_limits": {"lut": None, "ff": None, "bram": None, "dsp": None},
    }
    provider = ScriptedProvider(payload)
    context = RTLTaskContext("task", "100 MHz counter",
                             synthesis_constraints=SynthesisConstraints())
    RealPlanner(provider).plan(context)
    assert context.synthesis_constraints.part is None
    assert context.synthesis_constraints.clock_period_ns == 10
    assert context.synthesis_constraints.resource_limits == {}
    assert "never invent" in provider.requests[0]["instructions"]


def test_agent_can_read_synthesis_feedback_before_repair(tmp_path):
    engine = _fake_graph(tmp_path, FakeVerifier())
    engine.operations.synthesis_runner = SynthesisSequence(["SYNTHESIS_TIMING_FAILED", "OK"])

    class InvestigatingDebugger(FakeDebugger):
        def decide_next_action(self, observation):
            assert "read_synthesis_report" in observation["allowed_actions"]
            assert "run_synthesis" in observation["allowed_actions"]
            if not observation["observations"]:
                return {"action": "read_synthesis_report", "reason": "Locate timing bottleneck"}
            report = observation["observations"][-1]["result"]
            assert report["wns_ns"] == -1
            assert "evidence" not in report
            return {"action": "repair", "reason": "Repair the critical datapath"}

    engine.operations.debugger = InvestigatingDebugger()
    task = engine.create_task("counter", synthesis_constraints=constraints())
    try:
        result = engine.run_task(task.task_id)
        assert result.current_stage is TaskStage.COMPLETED
        assert result.budget.repair_attempts == 1
    finally:
        engine.close()


@pytest.mark.parametrize("category", ["SYNTHESIS_TOOL_ERROR", "SYNTHESIS_CONSTRAINT_ERROR"])
def test_synthesis_environment_or_constraints_do_not_consume_repairs(tmp_path, category):
    engine = _fake_graph(tmp_path, FakeVerifier())
    engine.operations.synthesis_runner = SynthesisSequence([category])
    task = engine.create_task("counter", synthesis_constraints=constraints())
    try:
        result = engine.run_task(task.task_id)
        assert result.current_stage is (
            TaskStage.FAILED if category == "SYNTHESIS_TOOL_ERROR" else TaskStage.WAITING_FOR_USER
        )
        assert result.failure_category == category
        assert result.budget.repair_attempts == 0
        if category == "SYNTHESIS_TOOL_ERROR":
            assert not result.metadata["reflection_decision"]["invokes_debugger"]
        else:
            assert result.metadata["pause_reason"] == "synthesis_constraints"
    finally:
        engine.close()


def test_generated_tb_and_synthesis_pass_still_require_review(tmp_path):
    engine = _fake_graph(tmp_path, FakeVerifier())
    engine.has_golden_testbench = False
    sequence = SynthesisSequence(["OK"])
    engine.operations.synthesis_runner = sequence
    task = engine.create_task("counter", synthesis_constraints=constraints())
    try:
        result = engine.run_task(task.task_id)
        assert result.current_stage is TaskStage.WAITING_FOR_USER
        assert result.metadata["pause_reason"] == "generated_tb_review"
        assert not result.metadata.get("golden_tb_pass")
        assert sequence.calls == ["cand_01"]
    finally:
        engine.close()
