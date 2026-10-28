from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from rtlforge.domain import RTLTaskContext, TaskStage
from rtlforge.orchestration import Orchestrator, SQLiteTaskStore
from rtlforge.providers import ModelProviderError, OpenAIResponsesProvider, ProviderSettings
from rtlforge.providers.usage import RecordingProvider, model_call_task, summarize_calls
from rtlforge.testing import (
    FakeDebugger,
    FakeEvaluator,
    FakeGenerator,
    FakePlanner,
    FakeVerifier,
    ScriptedProvider,
)


def request(provider):
    return provider.generate_structured(
        instructions="secret prompt", input_text="private RTL",
        schema_name="verilog_rtl_candidates", schema={"type": "object"},
    )


def provider_for(response, chat=False):
    endpoint = SimpleNamespace(create=lambda **_: response)
    client = (SimpleNamespace(chat=SimpleNamespace(completions=endpoint)) if chat
              else SimpleNamespace(responses=endpoint))
    return RecordingProvider(OpenAIResponsesProvider(
        ProviderSettings(api_key="secret-key", model="qwen3-coder-plus",
                         base_url="https://dashscope.aliyuncs.com/v1" if chat else None),
        client=client,
    ), role="generator")


@pytest.mark.parametrize("chat", [False, True])
def test_usage_mapping_and_durable_task_attribution(tmp_path, chat):
    usage = ({"prompt_tokens": 3200, "completion_tokens": 1800,
              "prompt_tokens_details": {"cached_tokens": 200}} if chat else
             SimpleNamespace(input_tokens=3200, output_tokens=1800,
                             input_tokens_details=SimpleNamespace(cached_tokens=200)))
    response = SimpleNamespace(
        usage=usage, output_text='{"ok": true}', status="completed",
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))],
        _request_id="req-123", id="resp-456", model="qwen3-coder-plus",
        temperature=0.1, seed=20261008,
    )
    provider = provider_for(response, chat)
    path = tmp_path / "task" / "model_calls.jsonl"
    context = RTLTaskContext(task_id="task_usage", raw_spec="counter",
                             metadata={"model_calls_path": str(path)})
    with model_call_task(context):
        assert request(provider) == {"ok": True}
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record == context.metadata["model_calls"][0]
    assert record["task_id"] == "task_usage"
    assert record["role"] == "generator"
    assert record["input_tokens"] == 3200
    assert record["output_tokens"] == 1800
    assert record["cached_input_tokens"] == 200
    assert record["usage_status"] == "REPORTED"
    assert record["request_id"] == "req-123"
    assert record["response_id"] == "resp-456"
    assert record["temperature"] == 0.1
    assert record["seed"] == 20261008
    assert record["response_sampling"]["seed"] == 20261008
    assert record["sampling"]["seed_status"] == ("SENT" if chat else "NOT_SENT_UNSUPPORTED")
    assert record["latency_ms"] >= 0
    assert "secret" not in path.read_text(encoding="utf-8")
    assert "private RTL" not in path.read_text(encoding="utf-8")


def test_unsupported_usage_and_missing_cache_are_not_zero():
    provider = RecordingProvider(ScriptedProvider({"ok": True}), role="planner")
    request(provider)
    assert provider.calls[0]["usage_status"] == "NOT_REPORTED"
    assert provider.calls[0]["input_tokens"] is None
    assert provider.calls[0]["seed"] is None
    real = provider_for(SimpleNamespace(output_text="{}", usage={
        "input_tokens": 10, "output_tokens": 0,
    }))
    request(real)
    totals = summarize_calls(provider.calls + real.calls)
    assert totals["input_tokens"] == 10
    assert totals["output_tokens"] == 0
    assert totals["cached_input_tokens"] is None
    assert totals["input_tokens_reported_calls"] == 1
    assert totals["not_reported_calls"] == 1
    assert totals["by_role"]["planner"]["input_tokens"] is None


def test_invalid_json_still_records_charged_tokens():
    provider = provider_for(SimpleNamespace(output_text="bad json", usage={
        "input_tokens": 50, "output_tokens": 20,
    }))
    with pytest.raises(ModelProviderError):
        request(provider)
    assert provider.calls[0]["status"] == "ERROR"
    assert provider.calls[0]["input_tokens"] == 50


def test_transport_failure_records_missing_usage_and_resets_context():
    class Failing:
        def generate_structured(self, **kwargs):
            raise ConnectionError("secret upstream error")

    provider = RecordingProvider(Failing(), role="debugger")
    context = RTLTaskContext(task_id="failed", raw_spec="counter")
    with model_call_task(context), pytest.raises(ConnectionError):
        request(provider)
    assert provider.calls[0]["usage_status"] == "NOT_REPORTED"
    assert provider.calls[0]["status"] == "ERROR"
    assert "secret" not in json.dumps(provider.calls)
    standalone = RecordingProvider(ScriptedProvider({}), role="planner")
    request(standalone)
    assert standalone.calls[0]["task_id"] is None


def test_three_agent_roles_survive_checkpoint_and_do_not_leak_between_tasks(tmp_path):
    class Planner(FakePlanner):
        def plan(self, context):
            request(self.provider)
            return super().plan(context)

    class Generator(FakeGenerator):
        def generate(self, context, count):
            request(self.provider)
            return super().generate(context, count)

    class Debugger(FakeDebugger):
        def repair(self, context, candidate):
            request(self.provider)
            return super().repair(context, candidate)

    def build():
        agents = Planner(), Generator(), Debugger()
        shared = ScriptedProvider({}, {}, {})
        for agent in agents:
            agent.provider = shared
        return Orchestrator(
            planner=agents[0], generator=agents[1], debugger=agents[2],
            evaluator=FakeEvaluator(), verifier=FakeVerifier(failures_before_pass=1),
            store=SQLiteTaskStore(tmp_path / "tasks.sqlite"),
        )

    first = build()
    context = first.create_task("counter", task_id="first")
    first.advance_task(context.task_id)
    first.advance_task(context.task_id)
    second = build()
    result = second.run_task(context.task_id)
    assert result.current_stage is TaskStage.COMPLETED
    rows = second.store.load_latest_checkpoint("first").metadata["model_calls"]
    assert [r["role"] for r in rows] == ["planner", "generator", "debugger"]
    assert {r["task_id"] for r in rows} == {"first"}
    third = build()
    context2 = third.create_task("counter", task_id="second")
    third.run_task(context2.task_id)
    assert {r["task_id"] for r in context2.metadata["model_calls"]} == {"second"}
    assert len(context2.metadata["model_calls"]) == 3
