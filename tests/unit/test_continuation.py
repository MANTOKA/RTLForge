from __future__ import annotations

import json

import pytest

from rtlforge import console, providers, services
from rtlforge.continuation import read_source
from rtlforge.testing import (
    OFFLINE_COUNTER_SPEC,
    ScriptedProvider,
    counter_candidate_payload,
    counter_plan_payload,
)
from tests.integration.test_langgraph_workflow import PassingRunner, _provider


def test_exhausted_task_continues_selected_repair_with_new_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(console, "ROOT", tmp_path)
    env = tmp_path / ".env.test"
    env.write_text("OPENAI_API_KEY=test\nRTLFORGE_MODEL=scripted\n", encoding="utf-8")
    settings = console.Settings(env_file=str(env), memory_mode="off", max_repairs=0, candidates=1)
    provider = ScriptedProvider(counter_plan_payload(), {
        "candidates": counter_candidate_payload()["candidates"][:1],
    })
    monkeypatch.setattr(providers, "OpenAIResponsesProvider", lambda _: provider)
    monkeypatch.setattr(services, "DockerEDARunner", lambda *a, **kw: PassingRunner())
    tb = tmp_path / "golden.sv"
    tb.write_text("module tb; endmodule", encoding="utf-8")
    console.run_requirement(OFFLINE_COUNTER_SPEC, settings, tb)
    source_path = next(tmp_path.rglob("context.json"))
    state = json.loads(source_path.read_text(encoding="utf-8"))
    state.update(current_stage="FAILED", failure_category="BUDGET_EXHAUSTED")
    state["budget"].update(max_repairs=3, repair_attempts=3)
    repair = {**state["candidates"][0], "candidate_id": "cand_repair_01", "parent_id": "cand_01"}
    state["candidates"].append(repair)
    source_path.write_text(json.dumps(state), encoding="utf-8")
    before = source_path.read_bytes()
    prior_rtl = console.file_path(repair["artifact_path"]).read_bytes()
    patched = prior_rtl.decode().replace("q + 4'b0001", "4'b0001 + q")
    provider = ScriptedProvider(
        {"action": "repair", "reason": "Fix imported candidate"},
        {"patched_rtl": patched, "rationale": "Repair", "expected_impact": "Pass", "risks": []},
    )

    class FailsOnce(PassingRunner):
        calls = 0

        def run_files(self, action, input_paths):
            result = super().run_files(action, input_paths)
            if action == "simulate":
                self.calls += 1
                if self.calls == 1:
                    result.update(status="FAIL", category="TIMEOUT")
            return result

    runner = FailsOnce()
    monkeypatch.setattr(services, "DockerEDARunner", lambda *a, **kw: runner)
    settings.max_repairs = 1
    console.run_requirement(
        OFFLINE_COUNTER_SPEC, settings, tb, (source_path, "cand_repair_01"),
    )
    branch_path = next(p for p in tmp_path.rglob("context.json") if p != source_path)
    branch = json.loads(branch_path.read_text(encoding="utf-8"))
    assert branch["current_stage"] == "COMPLETED"
    assert branch["budget"]["repair_attempts"] == 1
    assert branch["metadata"]["continuation"]["source_budget"]["repair_attempts"] == 3
    assert branch["metadata"]["continuation"]["source_candidate_id"] == "cand_repair_01"
    assert branch["candidates"][1]["parent_id"] == "cand_seed_01"
    assert source_path.read_bytes() == before
    assert console.file_path(repair["artifact_path"]).read_bytes() == prior_rtl
    assert [r["schema_name"] for r in provider.requests] == [
        "rtl_debug_action", "verilog_rtl_repair",
    ]


def test_continuation_reuses_generated_tb_without_planner_or_tb_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(console, "ROOT", tmp_path)
    env = tmp_path / ".env.test"
    env.write_text("OPENAI_API_KEY=test\nRTLFORGE_MODEL=scripted\n", encoding="utf-8")
    settings = console.Settings(env_file=str(env), memory_mode="off", max_repairs=1)
    provider = _provider()
    monkeypatch.setattr(providers, "OpenAIResponsesProvider", lambda _: provider)
    monkeypatch.setattr(services, "DockerEDARunner", lambda *a, **kw: PassingRunner())
    console.run_requirement(OFFLINE_COUNTER_SPEC, settings, None)
    source_path = next(tmp_path.rglob("context.json"))
    provider = ScriptedProvider()
    console.run_requirement(OFFLINE_COUNTER_SPEC, settings, None, (source_path, "cand_01"))
    assert provider.requests == []
    assert len(list(tmp_path.rglob("generated_tb.sv"))) == 2


def test_read_source_finds_context_from_rtl_path(tmp_path):
    from rtlforge.domain import RTLTaskContext

    task = tmp_path / "task"
    rtl = task / "candidates/cand_01/dut.v"
    rtl.parent.mkdir(parents=True)
    rtl.write_text("module dut; endmodule", encoding="utf-8")
    context = task / "context.json"
    context.write_text(json.dumps(RTLTaskContext("task", "dut").to_dict()), encoding="utf-8")
    found, _ = read_source(rtl)
    assert found == context
    with pytest.raises(ValueError, match="context.json"):
        read_source(tmp_path / "missing")
