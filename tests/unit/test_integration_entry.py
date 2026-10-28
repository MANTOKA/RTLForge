from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from rtlforge import console, providers, services
from rtlforge.agents import RealPlanner
from rtlforge.continuation import read_source
from rtlforge.domain import Candidate, RTLTaskContext
from rtlforge.domain.synthesis import SynthesisConstraints
from rtlforge.integration import (
    attach_modules,
    check_top_only,
    copy_modules,
    dependency_files,
    load_module,
    module_contracts,
)
from rtlforge.services.vivado_mcp import write_synthesis_script
from rtlforge.testing import ScriptedProvider, counter_candidate_payload, counter_plan_payload
from tests.integration.test_langgraph_workflow import PassingRunner


def module_fixture(root: Path, name="existing_counter"):
    plan = counter_plan_payload()
    plan["module_name"] = name
    context = RTLTaskContext("source_task", "counter")
    planned = RealPlanner(ScriptedProvider(plan)).plan(context)
    context.spec_ir, context.task_tree_root = planned.spec_ir, planned.task_tree_root
    rtl = root / "candidates" / "cand_01" / f"{name}.v"
    rtl.parent.mkdir(parents=True)
    rtl.write_text(counter_candidate_payload()["candidates"][0]["rtl"].replace("counter(",
                                                                                 name + "("))
    candidate = Candidate("cand_01", str(rtl), sha256(rtl.read_bytes()).hexdigest())
    candidate.metadata["static_evaluation"] = {
        "eligible_for_verification": True, "quality_score": 100,
    }
    context.candidates = [candidate]
    context.selected_candidate_id = candidate.candidate_id
    source = root / "context.json"
    source.write_text(json.dumps(context.to_dict()))
    (root / "verification.json").write_text(json.dumps({
        "passed": True, "selected_candidate_id": "cand_01",
        "testbench": {"source": "user_provided"},
    }))
    return source, load_module(source)


def test_load_relocated_candidate_and_reject_unverified_or_modified(tmp_path):
    source, module = module_fixture(tmp_path / "source")
    state = json.loads(source.read_text())
    state["candidates"][0]["artifact_path"] = str(tmp_path / "old" / "existing_counter.v")
    source.write_text(json.dumps(state))
    assert load_module(source)["rtl_sha256"] == module["rtl_sha256"]
    evidence = source.parent / "verification.json"
    saved = json.loads(evidence.read_text())
    saved["testbench"]["source"] = "agent_generated"
    evidence.write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="独立 TB"):
        load_module(source)
    Path(module["source_rtl"]).write_text("changed")
    with pytest.raises((ValueError, FileNotFoundError)):
        load_module(source)


def test_frozen_snapshot_hash_and_copy(tmp_path):
    _, module = module_fixture(tmp_path / "source")
    context = RTLTaskContext("integration", "top")
    attach_modules(context, tmp_path / "new", [module])
    paths = dependency_files(context)
    Path(module["source_rtl"]).write_text("historical file changed later")
    assert paths[0].read_bytes() != Path(module["source_rtl"]).read_bytes()
    safe = module_contracts(context)
    assert set(safe[0]) == {
        "module_name", "candidate_id", "rtl_sha256", "spec_ir", "task_tree", "required_instance",
    }
    branch = RTLTaskContext("branch", "top")
    copy_modules(context, branch, tmp_path / "branch")
    assert dependency_files(branch)[0].read_bytes() == paths[0].read_bytes()
    paths[0].write_text("tampered")
    with pytest.raises(PermissionError, match="冻结模块"):
        dependency_files(context)


@pytest.mark.parametrize("source", [
    "module top; endmodule",
    "module top; existing_counter dut(); endmodule module existing_counter; endmodule",
])
def test_top_cannot_omit_or_redefine_dependencies(tmp_path, source):
    rtl = tmp_path / "top.sv"
    rtl.write_text(source)
    with pytest.raises(ValueError):
        check_top_only(rtl, "top", [{"module_name": "existing_counter"}])


def test_nested_module_reuse_keeps_transitive_dependencies(tmp_path):
    source, module = module_fixture(tmp_path / "source", "composite")
    _, child = module_fixture(tmp_path / "child", "leaf")
    _, context = read_source(source)
    attach_modules(context, source.parent, [child])
    source.write_text(json.dumps(context.to_dict()))
    nested = load_module(source)
    target = RTLTaskContext("new_top", "top")
    attach_modules(target, tmp_path / "target", [nested])
    assert len(dependency_files(target)) == 2
    contracts = module_contracts(target)
    assert [row["required_instance"] for row in contracts] == [True, False]
    rtl = tmp_path / "top.sv"
    rtl.write_text("module top; composite unit0(); endmodule")
    check_top_only(rtl, "top", contracts)
    branch = RTLTaskContext("branch", "top")
    copy_modules(target, branch, tmp_path / "branch")
    assert module_contracts(branch) == contracts
    assert module["rtl_sha256"] == nested["rtl_sha256"]


def test_mcp_script_reads_all_dependency_sources(tmp_path):
    rtl, dep = tmp_path / "top.sv", tmp_path / "dep.sv"
    script, _ = write_synthesis_script(tmp_path, rtl, SynthesisConstraints(
        part="xc7a12ticsg325-1L", top="top", clock_port="clk", clock_period_ns=10,
    ), [dep])
    text = script.read_text()
    assert text.count("read_verilog -sv") == 2
    assert str(dep).replace("\\", "/") in text
    assert "synth_design -top \"top\"" in text


def test_planner_cannot_redefine_frozen_module_name(tmp_path):
    _, module = module_fixture(tmp_path / "source", "counter")
    context = RTLTaskContext("integration", "new top")
    attach_modules(context, tmp_path / "target", [module])
    corrected = counter_plan_payload()
    corrected["module_name"] = "new_counter_top"
    provider = ScriptedProvider(counter_plan_payload(), corrected)
    result = RealPlanner(provider).plan(context)
    assert result.spec_ir.module_name == "new_counter_top"
    assert len(provider.requests) == 2
    assert "conflicts" in context.metadata["planner_validation_failures"][0]["reason"]


def test_console_integrates_and_continues_without_exposing_tb(tmp_path, monkeypatch):
    _, module = module_fixture(tmp_path / "source")
    monkeypatch.setattr(console, "ROOT", tmp_path)
    env = tmp_path / ".env.test"
    env.write_text("OPENAI_API_KEY=test\nRTLFORGE_MODEL=scripted\n")
    tb = tmp_path / "tb.sv"
    tb.write_text("SECRET_INDEPENDENT_ORACLE")
    candidate = counter_candidate_payload()["candidates"][0]
    candidate["rtl"] = (
        "module counter(input clk,rst,output [3:0] q); "
        "existing_counter instance0(.clk(clk),.rst(rst),.q(q)); endmodule"
    )
    provider = ScriptedProvider(counter_plan_payload(), {"candidates": [candidate]})
    monkeypatch.setattr(providers, "OpenAIResponsesProvider", lambda _: provider)

    class RecordingRunner(PassingRunner):
        def __init__(self):
            self.calls = []

        def run_files(self, action, paths):
            self.calls.append((action, paths))
            return super().run_files(action, paths)

    runner = RecordingRunner()
    monkeypatch.setattr(services, "DockerEDARunner", lambda *a, **kw: runner)
    settings = console.Settings(env_file=str(env), candidates=1, max_repairs=0, memory_mode="off")
    console.run_requirement("Integrate a counter", settings, tb, modules=[module])
    source = next((tmp_path / "runs").rglob("context.json"))
    _, context = read_source(source)
    assert context.current_stage.value == "COMPLETED"
    assert (source.parent / "integration_manifest.json").is_file()
    for request in provider.requests:
        assert json.loads(request["input_text"])["frozen_modules"][0]["module_name"] == (
            "existing_counter"
        )
    assert "SECRET_INDEPENDENT_ORACLE" not in str(provider.requests)
    assert all(len(paths) == (3 if action == "simulate" else 2)
               for action, paths in runner.calls)
    provider.requests.clear()
    console.run_requirement(context.raw_spec, settings, tb, (source, "cand_01"))
    branches = [p for p in (tmp_path / "runs").rglob("context.json") if p != source]
    assert len(branches) == 1
    _, branch = read_source(branches[0])
    assert branch.current_stage.value == "COMPLETED"
    assert dependency_files(branch)[0] != dependency_files(context)[0]
    assert provider.requests == []


def test_menu5_uses_arbitrary_module_selection(tmp_path, monkeypatch):
    _, module = module_fixture(tmp_path / "source", "arbitrary_component")
    tb = tmp_path / "tb.sv"
    tb.write_text("test")
    monkeypatch.setattr(console, "read_requirement", lambda: "My unrelated top")
    answers = iter([str(tb), "", "n"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    monkeypatch.setattr(console, "select_module", lambda: module)
    monkeypatch.setattr(console, "confirm", lambda *a: True)
    monkeypatch.setattr(console, "SETTINGS_PATH", tmp_path / "settings.json")
    calls = []
    monkeypatch.setattr(console, "run_requirement", lambda *a, **kw: calls.append((a, kw)))
    console.integration_prompt(console.Settings())
    assert calls[0][0][0] == "My unrelated top"
    assert calls[0][1]["modules"][0]["module_name"] == "arbitrary_component"
