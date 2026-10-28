from __future__ import annotations

from types import SimpleNamespace

import pytest

from rtlforge.providers import (
    ModelProviderError,
    OpenAIResponsesProvider,
    ProviderConfigurationError,
    ProviderSettings,
)


class RecordingResponses:
    def __init__(self, output_text: str | None = '{"answer": 1}') -> None:
        self.output_text = output_text
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=self.output_text, status="completed")


class FailingResponses:
    def create(self, **_kwargs):
        raise ConnectionError("upstream disconnected")


def test_provider_reports_incomplete_response_before_parsing() -> None:
    class IncompleteResponses:
        def create(self, **_kwargs):
            return SimpleNamespace(
                output_text='{"answer":', status="incomplete",
                incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            )

    provider = OpenAIResponsesProvider(
        ProviderSettings(api_key="test", model="model"),
        client=SimpleNamespace(responses=IncompleteResponses()),
    )
    with pytest.raises(ModelProviderError, match="response incomplete.*max_output_tokens"):
        provider.generate_structured(
            instructions="plan", input_text="test", schema_name="answer",
            schema={"type": "object"},
        )


def test_provider_reports_json_error_location() -> None:
    provider = OpenAIResponsesProvider(
        ProviderSettings(api_key="test", model="model"),
        client=SimpleNamespace(responses=RecordingResponses('{"answer":')),
    )
    with pytest.raises(ModelProviderError, match="line=1, column=11, position=10"):
        provider.generate_structured(
            instructions="plan", input_text="test", schema_name="answer",
            schema={"type": "object"},
        )


def test_stream_requires_completed_event(monkeypatch):
    monkeypatch.setenv("RTLFORGE_RESPONSES_STREAM", "1")

    class StreamResponses:
        def __init__(self, complete):
            self.complete = complete
            self.request = None

        def create(self, **kwargs):
            self.request = kwargs
            yield SimpleNamespace(type="response.output_text.delta", delta='{"answer":1}')
            if self.complete:
                yield SimpleNamespace(
                    type="response.completed",
                    response=SimpleNamespace(status="completed", output_text='{"answer":1}'),
                )

    for complete in (True, False):
        responses = StreamResponses(complete)
        provider = OpenAIResponsesProvider(
            ProviderSettings(api_key="test", model="configured-model"),
            client=SimpleNamespace(responses=responses),
        )
        def call():
            return provider.generate_structured(
                instructions="test", input_text="test", schema_name="answer",
                schema={"type": "object"},
            )
        if complete:
            assert call() == {"answer": 1}
        else:
            with pytest.raises(ModelProviderError, match="without response.completed"):
                call()
        assert responses.request["stream"] is True


class RecordingCompletions:
    def __init__(self, content: str | None = '{"answer": 1}') -> None:
        self.content = content
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_provider_sends_strict_non_stored_responses_request() -> None:
    responses = RecordingResponses()
    client = SimpleNamespace(responses=responses)
    settings = ProviderSettings(api_key="top-secret", model="configured-model")
    provider = OpenAIResponsesProvider(settings, client=client)

    result = provider.generate_structured(
        instructions="plan",
        input_text="counter",
        schema_name="answer",
        schema={"type": "object"},
    )

    assert result == {"answer": 1}
    assert "top-secret" not in repr(settings)
    request = responses.calls[0]
    assert request["model"] == "configured-model"
    assert request["store"] is False
    assert request["temperature"] == 0.1
    assert request["top_p"] == 1.0
    assert "seed" not in request
    assert request["text"]["format"] == {
        "type": "json_schema",
        "name": "answer",
        "schema": {"type": "object"},
        "strict": True,
    }


def test_responses_reasoning_effort_is_opt_in() -> None:
    responses = RecordingResponses()
    provider = OpenAIResponsesProvider(
        ProviderSettings(api_key="test", model="deepseek-v4-pro", reasoning_effort="none"),
        client=SimpleNamespace(responses=responses),
    )
    provider.generate_structured(
        instructions="test", input_text="test", schema_name="answer",
        schema={"type": "object"},
    )
    assert responses.calls[0]["reasoning"] == {"effort": "none"}


def test_dashscope_qwen_uses_chat_completions_json_object() -> None:
    completions = RecordingCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    settings = ProviderSettings(
        api_key="top-secret",
        model="qwen3-coder-plus",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    provider = OpenAIResponsesProvider(settings, client=client)

    result = provider.generate_structured(
        instructions="Create a plan.",
        input_text="counter",
        schema_name="answer",
        schema={"type": "object"},
    )

    assert result == {"answer": 1}
    request = completions.calls[0]
    assert request["temperature"] == 0.1
    assert request["top_p"] == 1.0
    assert request["seed"] == 20261008
    assert request["model"] == "qwen3-coder-plus"
    assert request["response_format"] == {"type": "json_object"}
    assert request["max_completion_tokens"] == 32768
    assert "JSON" in request["messages"][0]["content"]
    assert '"type":"object"' in request["messages"][0]["content"]


def test_explicit_chat_completions_uses_generic_token_limit() -> None:
    completions = RecordingCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    settings = ProviderSettings(
        api_key="top-secret",
        model="Qwen/Qwen3.5-9B",
        base_url="https://api.siliconflow.cn/v1",
        api_style="chat_completions",
    )
    provider = OpenAIResponsesProvider(settings, client=client)

    result = provider.generate_structured(
        instructions="Create a plan.", input_text="counter",
        schema_name="answer", schema={"type": "object"},
    )

    assert result == {"answer": 1}
    request = completions.calls[0]
    assert request["model"] == "Qwen/Qwen3.5-9B"
    assert request["max_tokens"] == 32768
    assert "max_completion_tokens" not in request
    assert "seed" not in request


@pytest.mark.parametrize(
    "output_text",
    [
        '```json\n{"answer": 1}\n```',
        '```\n{"answer": 1}\n```',
        '<think>internal reasoning</think>\n{"answer": 1}',
        '<think>internal reasoning</think>\n```json\n{"answer": 1}\n```',
    ],
)
def test_provider_accepts_known_json_wrappers(output_text: str) -> None:
    client = SimpleNamespace(responses=RecordingResponses(output_text))
    provider = OpenAIResponsesProvider(
        ProviderSettings(api_key="secret", model="model"), client=client
    )

    result = provider.generate_structured(
        instructions="plan",
        input_text="counter",
        schema_name="answer",
        schema={"type": "object"},
    )

    assert result == {"answer": 1}


@pytest.mark.parametrize("output_text", [None, "", "not-json", "[]"])
def test_provider_rejects_missing_or_invalid_structured_output(output_text: str | None) -> None:
    client = SimpleNamespace(responses=RecordingResponses(output_text))
    provider = OpenAIResponsesProvider(
        ProviderSettings(api_key="secret", model="model"), client=client
    )

    with pytest.raises(ModelProviderError):
        provider.generate_structured(
            instructions="plan",
            input_text="counter",
            schema_name="answer",
            schema={"type": "object"},
        )


def test_provider_rejects_explanatory_text_around_json() -> None:
    client = SimpleNamespace(
        responses=RecordingResponses('Here is the result: {"answer": 1}')
    )
    provider = OpenAIResponsesProvider(
        ProviderSettings(api_key="secret", model="model"), client=client
    )

    with pytest.raises(ModelProviderError, match="not valid JSON"):
        provider.generate_structured(
            instructions="plan",
            input_text="counter",
            schema_name="answer",
            schema={"type": "object"},
        )


def test_provider_recovers_only_complete_fenced_debugger_rtl() -> None:
    rtl = "module counter(input clk, output reg q); always @(posedge clk) q <= ~q; endmodule"
    client = SimpleNamespace(responses=RecordingResponses(f"```verilog\n{rtl}\n```"))
    provider = OpenAIResponsesProvider(
        ProviderSettings(api_key="secret", model="model"), client=client
    )

    result = provider.generate_structured(
        instructions="repair", input_text="counter",
        schema_name="verilog_rtl_repair", schema={"type": "object"},
    )

    assert result["patched_rtl"] == rtl
    assert result["_format_fallback"] == "fenced_verilog_repair"
    with pytest.raises(ModelProviderError, match="not valid JSON"):
        provider.generate_structured(
            instructions="plan", input_text="counter",
            schema_name="plan", schema={"type": "object"},
        )


def test_provider_rejects_incomplete_fenced_debugger_rtl() -> None:
    client = SimpleNamespace(responses=RecordingResponses("```verilog\nmodule counter;\n```"))
    provider = OpenAIResponsesProvider(
        ProviderSettings(api_key="secret", model="model"), client=client
    )
    with pytest.raises(ModelProviderError, match="not valid JSON"):
        provider.generate_structured(
            instructions="repair", input_text="counter",
            schema_name="verilog_rtl_repair", schema={"type": "object"},
        )


def test_provider_wraps_transport_failure_as_model_provider_error() -> None:
    client = SimpleNamespace(responses=FailingResponses())
    provider = OpenAIResponsesProvider(
        ProviderSettings(api_key="secret", model="model"), client=client
    )

    with pytest.raises(ModelProviderError, match="ConnectionError"):
        provider.generate_structured(
            instructions="repair",
            input_text="counter",
            schema_name="answer",
            schema={"type": "object"},
        )


def test_settings_require_key_and_model_and_accept_zero_retries() -> None:
    with pytest.raises(ProviderConfigurationError, match="OPENAI_API_KEY"):
        ProviderSettings.from_env({"RTLFORGE_MODEL": "model"})
    with pytest.raises(ProviderConfigurationError, match="RTLFORGE_MODEL"):
        ProviderSettings.from_env({"OPENAI_API_KEY": "secret"})

    settings = ProviderSettings.from_env(
        {
            "OPENAI_API_KEY": "secret",
            "RTLFORGE_MODEL": "model",
            "RTLFORGE_MAX_RETRIES": "0",
        }
    )

    assert settings.max_retries == 0
