from dataclasses import replace
from types import SimpleNamespace

import pytest

from rtlforge.benchmarks import human_v2_batch, rtllm_batch
from rtlforge.providers import (
    OpenAIResponsesProvider,
    ProviderConfigurationError,
    ProviderSettings,
)
from rtlforge.providers.usage import RecordingProvider


def test_sampling_env_and_actual_request_without_response_echo():
    settings = ProviderSettings.from_env({
        "OPENAI_API_KEY": "test", "RTLFORGE_MODEL": "qwen3-coder-plus",
        "OPENAI_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "RTLFORGE_TEMPERATURE": "0", "RTLFORGE_TOP_P": "0.9",
        "RTLFORGE_SEED": "42",
    })
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    provider = RecordingProvider(OpenAIResponsesProvider(settings, client=client), role="planner")
    provider.generate_structured(instructions="plan", input_text="x", schema_name="plan", schema={})
    record = provider.calls[0]
    for key, expected in {"temperature": 0, "top_p": 0.9, "seed": 42}.items():
        assert requests[0][key] == expected
        assert record[key] == expected
        assert record["response_sampling"][key] is None
    assert record["sampling"]["seed_status"] == "SENT"
    assert len(record["prompt_sha256"]) == 64


@pytest.mark.parametrize("key,value", [
    ("TEMPERATURE", "nan"), ("TEMPERATURE", "2"), ("TEMPERATURE", "-1"),
    ("TOP_P", "0"), ("TOP_P", "1.1"), ("TOP_P", "oops"),
    ("SEED", "-1"), ("SEED", "2147483648"), ("SEED", "1.2"),
])
def test_invalid_sampling_rejected(key, value):
    with pytest.raises(ProviderConfigurationError):
        ProviderSettings.from_env({"OPENAI_API_KEY": "test", "RTLFORGE_MODEL": "model",
                                   "RTLFORGE_" + key: value})


def test_explicit_api_style_from_env_and_invalid_value():
    settings = ProviderSettings.from_env({
        "OPENAI_API_KEY": "test", "RTLFORGE_MODEL": "model",
        "RTLFORGE_API_STYLE": "chat_completions",
    })
    assert settings.api_style == "chat_completions"
    assert settings.uses_chat_completions
    assert settings.sampling_configuration()["api_style"] == "chat_completions"
    with pytest.raises(ProviderConfigurationError, match="API_STYLE"):
        ProviderSettings.from_env({
            "OPENAI_API_KEY": "test", "RTLFORGE_MODEL": "model",
            "RTLFORGE_API_STYLE": "invalid",
        })


def test_responses_seed_is_configured_but_not_sent():
    settings = ProviderSettings(api_key="test", model="model")
    assert settings.sampling_configuration()["seed"] == 20261008
    assert settings.sampling_configuration()["seed_status"] == "NOT_SENT_UNSUPPORTED"
    assert "seed" not in settings.sampling_arguments()
    assert not replace(settings, model="qwen3-coder-plus",
                       base_url="https://aliyuncs.com.example.org/v1").uses_dashscope_chat


def test_server_echo_does_not_overwrite_request():
    response = SimpleNamespace(output_text="{}", temperature=0.7, top_p=0.8, seed=99)
    client = SimpleNamespace(responses=SimpleNamespace(create=lambda **_: response))
    provider = RecordingProvider(OpenAIResponsesProvider(
        ProviderSettings(api_key="test", model="model"), client=client,
    ), role="debugger")
    provider.generate_structured(
        instructions="debug", input_text="x", schema_name="debug", schema={},
    )
    record = provider.calls[0]
    assert record["temperature"] == 0.1
    assert record["seed"] == 20261008
    assert record["response_sampling"] == {"temperature": 0.7, "top_p": 0.8, "seed": 99}


@pytest.mark.parametrize("module", [human_v2_batch, rtllm_batch])
def test_reports_and_resume_preserve_sampling(module, tmp_path):
    args = SimpleNamespace(candidates=2, max_repairs=2, docker_image="eda:test",
                           dataset_root=tmp_path, memory_mode="off",
                           memory_database=tmp_path / "db")
    settings = ProviderSettings(api_key="test", model="qwen3-coder-plus",
                                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")
    report = module._initial_report(args, settings, 1)
    report["metrics"] = module._metrics([])
    markdown = module._markdown_report(report)
    assert "temperature=0.1" in markdown
    assert "top_p=1.0" in markdown
    assert "seed=20261008" in markdown
    assert "seed status=SENT" in markdown
    module._validate_resume(report, args, settings)
    for change in ({"temperature": 0}, {"top_p": 0.9}, {"seed": 42}):
        with pytest.raises(ValueError, match="sampling"):
            module._validate_resume(report, args, replace(settings, **change))
    report["configuration"]["prompt_version"] = "older"
    with pytest.raises(ValueError, match="prompt_version"):
        module._validate_resume(report, args, settings)
