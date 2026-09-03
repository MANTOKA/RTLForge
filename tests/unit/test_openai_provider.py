from __future__ import annotations

from types import SimpleNamespace

import pytest

from verilog_agent.providers import (
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
    assert request["text"]["format"] == {
        "type": "json_schema",
        "name": "answer",
        "schema": {"type": "object"},
        "strict": True,
    }


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
        ProviderSettings.from_env({"VERILOG_AGENT_MODEL": "model"})
    with pytest.raises(ProviderConfigurationError, match="VERILOG_AGENT_MODEL"):
        ProviderSettings.from_env({"OPENAI_API_KEY": "secret"})

    settings = ProviderSettings.from_env(
        {
            "OPENAI_API_KEY": "secret",
            "VERILOG_AGENT_MODEL": "model",
            "VERILOG_AGENT_MAX_RETRIES": "0",
        }
    )

    assert settings.max_retries == 0
