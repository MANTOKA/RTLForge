from __future__ import annotations

import pytest

from rtlforge.agents import AgentOutputError, RealPlanner
from rtlforge.agents.generator import RealGenerator
from rtlforge.agents.testbench import RealTestbenchGenerator
from rtlforge.domain import RTLTaskContext, SpecIR
from rtlforge.testing import ScriptedProvider, counter_plan_payload


def parse(payload=None):
    return RealPlanner._parse(payload or counter_plan_payload(), "counter").spec_ir


def test_semantic_contract_round_trip_and_old_contract_compatibility():
    spec = parse()
    assert SpecIR.from_dict(spec.to_dict()) == spec
    assert spec.schema_version == 2
    assert spec.missing_semantics() == []
    old = {key: value for key, value in spec.to_dict().items() if key in {
        "module_name", "raw_spec", "ports", "clock", "reset", "version",
    }}
    restored = SpecIR.from_dict(old)
    assert restored.schema_version == 1
    assert restored.clock_edge is None


@pytest.mark.parametrize("field,value", [
    ("clock_edge", "both"), ("reset_polarity", "lowish"), ("reset_kind", "automatic"),
    ("signal_timing", [{"signal": "unknown", "reference": "clock_edge",
                        "min_cycles": 0, "max_cycles": 0}]),
    ("signal_timing", [{"signal": "q", "reference": "clock_edge",
                        "min_cycles": 3, "max_cycles": 1}]),
    ("signal_timing", [{"signal": "q", "reference": "clock_edge",
                        "min_cycles": True, "max_cycles": 1}]),
    ("signal_timing", [{"signal": "q", "reference": "handshake",
                        "min_cycles": 0, "max_cycles": 1}]),
    ("parameters", [{"name": "WIDTH", "default": 0, "minimum": 1}]),
    ("parameters", [{"name": "WIDTH", "default": 3, "allowed_values": [2, 4]}]),
    ("parameters", [{"name": "WIDTH", "default": True}]),
    ("parameters", [{"name": "WIDTH", "default": 4, "minimum": 8, "maximum": 2}]),
])
def test_planner_rejects_invalid_or_conflicting_semantics(field, value):
    payload = counter_plan_payload()
    payload[field] = value
    with pytest.raises(AgentOutputError):
        parse(payload)


def test_unknown_reset_is_not_inferred_from_signal_name_or_assumptions():
    payload = counter_plan_payload()
    payload["reset_polarity"] = payload["reset_kind"] = None
    spec = parse(payload)
    fields = {row["field"] for row in spec.missing_semantics()}
    assert fields == {"reset_polarity", "reset_kind"}
    assert any("同步复位还是异步复位" in row["question"] for row in spec.missing_semantics())


def test_handshake_and_parameter_unknowns_produce_targeted_questions():
    payload = counter_plan_payload()
    payload["ports"].extend([
        {"name": "valid", "direction": "input", "width": 1},
        {"name": "ready", "direction": "output", "width": 1},
    ])
    payload["handshakes"] = [{
        "name": "stream", "kind": "ready_valid", "request": "valid", "acknowledge": "ready",
        "data_signals": ["q"], "transfer_rule": None, "hold_until_ack": None,
    }]
    payload["parameters"] = [{"name": "WIDTH", "default": None, "minimum": 1}]
    fields = {row["field"] for row in parse(payload).missing_semantics()}
    assert {"handshakes.stream.transfer_rule", "handshakes.stream.hold_until_ack",
            "parameters.WIDTH.default", "signal_timing.valid", "signal_timing.ready"} <= fields


def test_handshake_rule_and_control_width_are_validated():
    payload = counter_plan_payload()
    payload["handshakes"] = [{
        "name": "stream", "kind": "ready_valid", "request": "q", "acknowledge": "rst",
        "data_signals": [], "transfer_rule": "same_edge", "hold_until_ack": True,
    }]
    with pytest.raises(AgentOutputError, match="scalar"):
        parse(payload)
    payload["handshakes"][0]["transfer_rule"] = "request_then_ack"
    with pytest.raises(AgentOutputError, match="transfer_rule"):
        parse(payload)


def test_valid_handshake_and_parameter_constraints_round_trip():
    payload = counter_plan_payload()
    payload["ports"].extend([
        {"name": "valid", "direction": "output", "width": 1},
        {"name": "ready", "direction": "input", "width": 1},
    ])
    payload["signal_timing"] += [
        {"signal": signal, "reference": "clock_edge", "min_cycles": 0, "max_cycles": 0}
        for signal in ("valid", "ready")
    ]
    payload["signal_timing"][0]["reference"] = "handshake"
    payload["handshakes"] = [{
        "name": "output_stream", "kind": "ready_valid", "request": "valid",
        "acknowledge": "ready", "data_signals": ["q"], "transfer_rule": "same_edge",
        "hold_until_ack": True,
    }]
    payload["parameters"] = [{
        "name": "WIDTH", "default": 4, "minimum": 1, "maximum": 16, "allowed_values": [4, 8],
    }]
    spec = parse(payload)
    assert not spec.missing_semantics()
    assert SpecIR.from_dict(spec.to_dict()) == spec


def start_done_plan():
    payload = counter_plan_payload()
    payload["ports"].extend([
        {"name": "start", "direction": "input", "width": 1},
        {"name": "done", "direction": "output", "width": 1},
        {"name": "busy", "direction": "output", "width": 1},
    ])
    payload["signal_timing"].extend([
        {"signal": "start", "reference": "handshake", "min_cycles": 0, "max_cycles": 0},
        {"signal": "done", "reference": "handshake", "min_cycles": 1, "max_cycles": 32},
        {"signal": "busy", "reference": "clock_edge", "min_cycles": 0, "max_cycles": 1},
    ])
    payload["handshakes"] = [{
        "name": "operation", "kind": "start_done", "request": "start", "acknowledge": "done",
        "data_signals": ["q"], "transfer_rule": "request_then_ack", "hold_until_ack": False,
    }]
    return payload


def test_handshake_timing_can_reference_declared_request_and_completion_controls():
    spec = parse(start_done_plan())
    assert spec.missing_semantics() == []
    assert SpecIR.from_dict(spec.to_dict()) == spec
    assert spec.handshakes[0].data_signals == ["q"]


def test_handshake_timing_still_rejects_an_unassociated_status_signal():
    payload = start_done_plan()
    payload["signal_timing"][-1]["reference"] = "handshake"
    with pytest.raises(AgentOutputError, match="busy"):
        parse(payload)


def test_planner_corrects_invalid_contract_once_and_keeps_rejected_output():
    import json

    invalid = start_done_plan()
    invalid["signal_timing"][-1]["reference"] = "handshake"
    provider = ScriptedProvider(invalid, start_done_plan())
    context = RTLTaskContext("contract_retry", "Start captures inputs; done in 1..32 cycles.")
    plan = RealPlanner(provider).plan(context)
    assert plan.spec_ir.missing_semantics() == []
    assert len(provider.requests) == 2
    rejected = context.metadata["planner_validation_failures"]
    assert len(rejected) == 1
    assert "busy" in rejected[0]["reason"]
    assert rejected[0]["plan"] == invalid
    correction = json.loads(provider.requests[1]["input_text"])
    assert correction["requirement"] == context.raw_spec
    assert correction["validation_error"] == rejected[0]["reason"]


def test_repeated_invalid_planner_contract_has_a_finite_retry_limit():
    invalid = start_done_plan()
    invalid["signal_timing"][-1]["reference"] = "handshake"
    provider = ScriptedProvider(invalid, invalid)
    context = RTLTaskContext("invalid_contract", "counter")
    with pytest.raises(AgentOutputError, match="busy"):
        RealPlanner(provider).plan(context)
    assert len(provider.requests) == 2
    assert len(context.metadata["planner_validation_failures"]) == 2


def test_planner_correction_does_not_invent_unknown_reset_semantics():
    payload = start_done_plan()
    payload["reset_kind"] = None
    provider = ScriptedProvider(payload)
    plan = RealPlanner(provider).plan(RTLTaskContext("unknown_reset", "counter"))
    assert plan.spec_ir.reset_kind is None
    assert "reset_kind" in {row["field"] for row in plan.spec_ir.missing_semantics()}
    assert len(provider.requests) == 1


@pytest.mark.parametrize("corrected", [True, False])
def test_invalid_planner_contract_never_bypasses_the_graph_gate(tmp_path, corrected):
    from rtlforge.domain import TaskStage
    from rtlforge.testing import FakeVerifier
    from tests.integration.test_langgraph_workflow import _fake_graph

    invalid = start_done_plan()
    invalid["signal_timing"][-1]["reference"] = "handshake"
    provider = ScriptedProvider(invalid, start_done_plan() if corrected else invalid)
    engine = _fake_graph(tmp_path, FakeVerifier())
    engine.operations.planner = RealPlanner(provider)
    task = engine.create_task("counter with declared start/done handshake")
    try:
        result = engine.run_task(task.task_id)
        assert len(provider.requests) == 2
        assert result.budget.repair_attempts == 0
        if corrected:
            assert result.current_stage is TaskStage.COMPLETED
            assert result.spec_ir.missing_semantics() == []
        else:
            assert result.current_stage is TaskStage.FAILED
            assert result.failure_category == "PLANNER_OUTPUT_ERROR"
            assert result.budget.generated_candidates == 0
            assert not result.candidates
            assert not any(e.event_type.value == "EVENT_GENERATED_TB_CREATED"
                           for e in engine.store.list_events(task.task_id))
    finally:
        engine.close()


def test_combinational_contract_needs_no_clock_or_reset_defaults():
    payload = counter_plan_payload()
    payload["ports"] = [
        {"name": "a", "direction": "input", "width": 4},
        {"name": "q", "direction": "output", "width": 4},
    ]
    for field in ("clock", "reset", "clock_edge", "reset_kind", "reset_polarity"):
        payload[field] = None
    payload["signal_timing"] = [
        {"signal": signal, "reference": "input_change", "min_cycles": 0, "max_cycles": 0}
        for signal in ("a", "q")
    ]
    assert not parse(payload).missing_semantics()


def test_tb_and_coder_refuse_to_consume_an_unresolved_contract():
    payload = counter_plan_payload()
    payload["reset_kind"] = None
    plan = RealPlanner._parse(payload, "counter")
    context = RTLTaskContext(task_id="unresolved", raw_spec="counter",
                             spec_ir=plan.spec_ir, task_tree_root=plan.task_tree_root)
    provider = ScriptedProvider()
    with pytest.raises(AgentOutputError, match="clarified"):
        RealTestbenchGenerator(provider).generate_for_contract(plan.spec_ir)
    with pytest.raises(AgentOutputError, match="clarified"):
        RealGenerator(provider).generate(context, 1)
    assert not provider.requests
