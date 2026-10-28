from __future__ import annotations

import json

import pytest

from rtlforge import console
from rtlforge.testing import (
    OFFLINE_COUNTER_SPEC,
    ScriptedProvider,
    counter_candidate_payload,
    counter_plan_payload,
)
from tests.integration.test_langgraph_workflow import PassingRunner, _provider


def test_requirement_accepts_quoted_utf8_file(tmp_path, monkeypatch):
    path = tmp_path / "中文需求.txt"
    path.write_text("设计计数器", encoding="utf-8-sig")
    monkeypatch.setattr("builtins.input", lambda _: f'"{path}"')
    assert console.read_requirement() == "设计计数器"


def test_requirement_multiline(monkeypatch):
    answers = iter(["设计计数器", "同步复位", "/run"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    assert console.read_requirement() == "设计计数器\n同步复位"


@pytest.mark.parametrize("provided", [False, True])
def test_console_runs_real_graph_with_exclusive_tb(tmp_path, monkeypatch, capsys, provided):
    from rtlforge import providers, services

    monkeypatch.setattr(console, "ROOT", tmp_path)
    env = tmp_path / ".env.test"
    env.write_text("OPENAI_API_KEY=test-only\nRTLFORGE_MODEL=scripted\n", encoding="utf-8")
    provider = (
        ScriptedProvider(counter_plan_payload(), {
            "candidates": counter_candidate_payload()["candidates"][:1],
        }) if provided else _provider()
    )
    monkeypatch.setattr(providers, "OpenAIResponsesProvider", lambda _: provider)
    monkeypatch.setattr(services, "DockerEDARunner", lambda *a, **kw: PassingRunner())
    tb = tmp_path / "golden.sv" if provided else None
    if tb:
        tb.write_text("SECRET_ORACLE", encoding="utf-8")
    console.run_requirement(OFFLINE_COUNTER_SPEC, console.Settings(
        env_file=str(env), candidates=1, max_repairs=0, memory_mode="off",
    ), tb)
    output = capsys.readouterr().out
    contexts = list(tmp_path.rglob("context.json"))
    assert len(contexts) == 1
    context = json.loads(contexts[0].read_text(encoding="utf-8"))
    assert (contexts[0].parent / "spec_ir.json").exists()
    assert (contexts[0].parent / "task_tree.json").exists()
    assert (contexts[0].parent / "events.json").exists()
    assert "SECRET_ORACLE" not in str(provider.requests)
    if provided:
        assert context["metadata"]["golden_tb_pass"] is True
        assert not list(tmp_path.rglob("generated_tb.sv"))
        assert "contract_testbench" not in str(provider.requests)
        assert "已通过用户提供的 TB" in output
    else:
        assert context["metadata"]["generated_tb_pass"] is True
        assert not context["metadata"].get("golden_tb_pass")
        assert "生成测试通过，未经独立验证" in output


def test_config_cancel_does_not_start_run(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert console.confirm(console.Settings(), None) is False


def test_console_exports_invalid_planner_output_before_successful_correction(
    tmp_path, monkeypatch,
):
    from rtlforge import providers, services

    invalid = counter_plan_payload()
    invalid["signal_timing"][0]["reference"] = "handshake"
    provider = ScriptedProvider(invalid, counter_plan_payload(), {
        "candidates": counter_candidate_payload()["candidates"][:1],
    })
    monkeypatch.setattr(console, "ROOT", tmp_path)
    env = tmp_path / ".env.test"
    env.write_text("OPENAI_API_KEY=test-only\nRTLFORGE_MODEL=scripted\n", encoding="utf-8")
    monkeypatch.setattr(providers, "OpenAIResponsesProvider", lambda _: provider)
    monkeypatch.setattr(services, "DockerEDARunner", lambda *a, **kw: PassingRunner())
    tb = tmp_path / "golden.sv"
    tb.write_text("SECRET_ORACLE", encoding="utf-8")
    console.run_requirement(OFFLINE_COUNTER_SPEC, console.Settings(
        env_file=str(env), candidates=1, max_repairs=0, memory_mode="off",
    ), tb)
    path = next(tmp_path.rglob("planner_validation_failures.json"))
    failures = json.loads(path.read_text(encoding="utf-8"))
    assert failures[0]["plan"] == invalid
    assert "signal_timing.q" in failures[0]["reason"]
    assert "SECRET_ORACLE" not in str(provider.requests)
    context = json.loads(path.with_name("context.json").read_text(encoding="utf-8"))
    assert context["current_stage"] == "COMPLETED"
    assert [r["schema_name"] for r in provider.requests] == [
        "verilog_requirement_plan", "verilog_requirement_plan", "verilog_rtl_candidates",
    ]


def test_console_preserves_functional_failure_evidence(tmp_path, monkeypatch, capsys):
    from rtlforge import providers, services

    class FailingSimulation(PassingRunner):
        def run_files(self, action, input_paths):
            result = super().run_files(action, input_paths)
            if action == "simulate":
                result.update(status="FAIL", category="ASSERTION_FAILURE")
            return result

    monkeypatch.setattr(console, "ROOT", tmp_path)
    env = tmp_path / ".env.test"
    env.write_text("OPENAI_API_KEY=test-only\nRTLFORGE_MODEL=scripted\n", encoding="utf-8")
    provider = ScriptedProvider(counter_plan_payload(), {
        "candidates": counter_candidate_payload()["candidates"][:1],
    })
    monkeypatch.setattr(providers, "OpenAIResponsesProvider", lambda _: provider)
    monkeypatch.setattr(services, "DockerEDARunner", lambda *a, **kw: FailingSimulation())
    tb = tmp_path / "golden.sv"
    tb.write_text("module tb; endmodule", encoding="utf-8")
    console.run_requirement(OFFLINE_COUNTER_SPEC, console.Settings(
        env_file=str(env), candidates=1, max_repairs=0, memory_mode="off",
    ), tb)
    context = json.loads(next(tmp_path.rglob("context.json")).read_text(encoding="utf-8"))
    assert context["current_stage"] == "FAILED"
    assert not context["metadata"].get("golden_tb_pass")
    assert list(tmp_path.rglob("verification.json"))
    assert "运行失败" in capsys.readouterr().out
