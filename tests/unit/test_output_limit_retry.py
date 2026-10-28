from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from rtlforge.providers import OpenAIResponsesProvider, ProviderSettings
from rtlforge.providers.base import OutputLimitError
from rtlforge.providers.interactive import InteractiveProvider


@pytest.mark.parametrize("api", ["responses", "chat_completions", "stream"])
def test_truncation_retries_only_after_user_increases_limit(tmp_path, monkeypatch, api):
    requests = []

    def create(**request):
        requests.append(request)
        first = len(requests) == 1
        response = SimpleNamespace(
            status="incomplete" if first else "completed",
            incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            output_text='{"rtl":' if first else '{"rtl":"complete"}',
        )
        if api == "stream":
            return iter([SimpleNamespace(
                type="response.incomplete" if first else "response.completed", response=response,
            )])
        if api == "chat_completions":
            return SimpleNamespace(choices=[SimpleNamespace(
                finish_reason="length" if first else "stop",
                message=SimpleNamespace(content=response.output_text),
            )])
        return response

    client = SimpleNamespace(
        responses=SimpleNamespace(create=create),
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )
    if api == "stream":
        monkeypatch.setenv("RTLFORGE_RESPONSES_STREAM", "1")
    else:
        monkeypatch.delenv("RTLFORGE_RESPONSES_STREAM", raising=False)
    base = OpenAIResponsesProvider(ProviderSettings(
        api_key="test", model="model", api_style=(
            "chat_completions" if api == "chat_completions" else "responses"
        ),
    ), client=client)
    provider = InteractiveProvider(base, tmp_path)
    monkeypatch.setattr("builtins.input", lambda _: "65536")
    result = provider.generate_structured(
        instructions="generate RTL", input_text="contract", schema_name="verilog_rtl_candidates",
        schema={"type": "object"},
    )
    assert result == {"rtl": "complete"}
    key = "max_tokens" if api == "chat_completions" else "max_output_tokens"
    assert [request[key] for request in requests] == [32768, 65536]
    assert [call["status"] for call in provider.calls] == ["ERROR", "SUCCESS"]
    evidence = tmp_path / "output_limit/attempt_001"
    assert (evidence / "partial_response.txt").read_text(encoding="utf-8") == '{"rtl":'
    assert json.loads((evidence / "decision.json").read_text())["action"] == "retry"


def test_declining_retry_preserves_partial_and_does_not_repeat_call(tmp_path, monkeypatch):
    class TruncatedProvider:
        settings = ProviderSettings(api_key="test", model="model")
        calls = 0

        def generate_structured(self, **kwargs):
            self.calls += 1
            raise OutputLimitError(32768, "partial")

    base = TruncatedProvider()
    provider = InteractiveProvider(base, tmp_path)
    monkeypatch.setattr("builtins.input", lambda _: "n")
    with pytest.raises(OutputLimitError):
        provider.generate_structured(
            instructions="generate", input_text="contract", schema_name="rtl", schema={},
        )
    assert base.calls == 1
    evidence = tmp_path / "output_limit/attempt_001/decision.json"
    assert json.loads(evidence.read_text())["action"] == "stopped"
