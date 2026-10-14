from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from verilog_agent.providers.base import ModelProviderError, ProviderConfigurationError
from verilog_agent.providers.usage import capture_response


def _unwrap_known_json_wrappers(output_text: str) -> str:
    """Remove wrappers emitted by otherwise compatible chat-style providers."""
    candidate = output_text.strip()

    if candidate.startswith("<think>"):
        closing_tag = "</think>"
        closing_index = candidate.find(closing_tag)
        if closing_index != -1:
            candidate = candidate[closing_index + len(closing_tag) :].strip()

    if candidate.startswith("```") and candidate.endswith("```"):
        first_line_end = candidate.find("\n")
        if first_line_end != -1:
            fence_language = candidate[3:first_line_end].strip().lower()
            if fence_language in {"", "json"}:
                candidate = candidate[first_line_end + 1 : -3].strip()

    return candidate


def _positive_int(environment: dict[str, str], name: str, default: int) -> int:
    raw_value = environment.get(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ProviderConfigurationError(f"{name} must be an integer") from error
    if value < 1:
        raise ProviderConfigurationError(f"{name} must be positive")
    return value


def _non_negative_int(environment: dict[str, str], name: str, default: int) -> int:
    raw_value = environment.get(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ProviderConfigurationError(f"{name} must be an integer") from error
    if value < 0:
        raise ProviderConfigurationError(f"{name} must not be negative")
    return value


def _float_setting(environment: dict[str, str], name: str, default: float) -> float:
    try:
        return float(environment.get(name, str(default)))
    except ValueError as error:
        raise ProviderConfigurationError(f"{name} must be a number") from error


@dataclass(frozen=True, slots=True)
class ProviderSettings:
    api_key: str = field(repr=False)
    model: str
    base_url: str | None = None
    api_style: str = "auto"
    timeout_seconds: int = 120
    max_output_tokens: int = 8192
    max_retries: int = 2
    temperature: float = 0.1
    top_p: float = 1.0
    seed: int = 20261008
    reasoning_effort: str | None = None

    @property
    def uses_dashscope_chat(self) -> bool:
        host = urlparse(self.base_url or "").hostname or ""
        return host.endswith(".aliyuncs.com") and self.model.lower().startswith("qwen")

    @property
    def resolved_api_style(self) -> str:
        if self.api_style != "auto":
            return self.api_style
        return "chat_completions" if self.uses_dashscope_chat else "responses"

    @property
    def uses_chat_completions(self) -> bool:
        return self.resolved_api_style == "chat_completions"

    def sampling_arguments(self) -> dict[str, Any]:
        arguments = {"temperature": self.temperature, "top_p": self.top_p}
        if self.uses_dashscope_chat:
            arguments["seed"] = self.seed
        return arguments

    def sampling_configuration(self) -> dict[str, Any]:
        return {
            "temperature": self.temperature, "top_p": self.top_p, "seed": self.seed,
            "seed_status": "SENT" if self.uses_dashscope_chat else "NOT_SENT_UNSUPPORTED",
            "api_style": self.resolved_api_style,
            "reasoning_effort": self.reasoning_effort,
            "request_parameters": self.sampling_arguments(),
        }

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise ProviderConfigurationError("OPENAI_API_KEY is required")
        if not self.model.strip():
            raise ProviderConfigurationError("VERILOG_AGENT_MODEL is required")
        if self.api_style not in {"auto", "responses", "chat_completions"}:
            raise ProviderConfigurationError(
                "VERILOG_AGENT_API_STYLE must be auto, responses, or chat_completions"
            )
        if self.timeout_seconds < 1:
            raise ProviderConfigurationError("timeout_seconds must be positive")
        if self.max_output_tokens < 1:
            raise ProviderConfigurationError("max_output_tokens must be positive")
        if self.max_retries < 0:
            raise ProviderConfigurationError("max_retries must not be negative")
        if self.reasoning_effort not in {None, "none", "low", "high", "max"}:
            raise ProviderConfigurationError("reasoning_effort must be none, low, high, or max")
        if not math.isfinite(self.temperature) or not 0 <= self.temperature < 2:
            raise ProviderConfigurationError("temperature must be finite and in [0, 2)")
        if not math.isfinite(self.top_p) or not 0 < self.top_p <= 1:
            raise ProviderConfigurationError("top_p must be finite and in (0, 1]")
        if (isinstance(self.seed, bool) or not isinstance(self.seed, int)
                or not 0 <= self.seed < 2**31):
            raise ProviderConfigurationError("seed must be an integer in [0, 2^31-1]")

    @classmethod
    def from_env(cls, environment: dict[str, str] | None = None) -> ProviderSettings:
        values = os.environ if environment is None else environment
        base_url = values.get("OPENAI_BASE_URL", "").strip() or None
        return cls(
            api_key=values.get("OPENAI_API_KEY", ""),
            model=values.get("VERILOG_AGENT_MODEL", ""),
            base_url=base_url,
            api_style=values.get("VERILOG_AGENT_API_STYLE", "auto").strip().lower(),
            timeout_seconds=_positive_int(
                values, "VERILOG_AGENT_REQUEST_TIMEOUT_SECONDS", 120
            ),
            max_output_tokens=_positive_int(
                values, "VERILOG_AGENT_MAX_OUTPUT_TOKENS", 8192
            ),
            max_retries=_non_negative_int(values, "VERILOG_AGENT_MAX_RETRIES", 2),
            temperature=_float_setting(values, "VERILOG_AGENT_TEMPERATURE", 0.1),
            top_p=_float_setting(values, "VERILOG_AGENT_TOP_P", 1.0),
            seed=_non_negative_int(values, "VERILOG_AGENT_SEED", 20261008),
            reasoning_effort=values.get("VERILOG_AGENT_REASONING_EFFORT") or None,
        )


class OpenAIResponsesProvider:
    """Call an OpenAI-compatible Responses endpoint with a strict JSON schema."""

    def __init__(self, settings: ProviderSettings, *, client: Any | None = None) -> None:
        self.settings = settings
        self._client = client or self._build_client(settings)

    @staticmethod
    def _build_client(settings: ProviderSettings) -> Any:
        try:
            from openai import OpenAI
        except ImportError as error:
            raise ProviderConfigurationError(
                'OpenAI SDK is not installed; run: python -m pip install -e ".[llm]"'
            ) from error

        arguments: dict[str, Any] = {
            "api_key": settings.api_key,
            "timeout": settings.timeout_seconds,
            "max_retries": settings.max_retries,
        }
        if settings.base_url is not None:
            arguments["base_url"] = settings.base_url
        return OpenAI(**arguments)

    def _uses_chat_completions(self) -> bool:
        return self.settings.uses_chat_completions

    def _generate_chat_json_object(
        self,
        *,
        instructions: str,
        input_text: str,
        schema: dict[str, Any],
    ) -> str | None:
        schema_text = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        token_limit = (
            {"max_completion_tokens": self.settings.max_output_tokens}
            if self.settings.uses_dashscope_chat
            else {"max_tokens": self.settings.max_output_tokens}
        )
        completion = self._client.chat.completions.create(
            **self.settings.sampling_arguments(),
            **token_limit,
            model=self.settings.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"{instructions}\n\n"
                        "Return only one valid JSON object. Do not use Markdown or add "
                        f"explanatory text. The JSON object must follow this schema: {schema_text}"
                    ),
                },
                {"role": "user", "content": input_text},
            ],
            response_format={"type": "json_object"},
        )
        capture_response(completion, chat=True)
        choices = getattr(completion, "choices", None)
        if not choices:
            return None
        message = getattr(choices[0], "message", None)
        return getattr(message, "content", None)

    def generate_structured(
        self,
        *,
        instructions: str,
        input_text: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            if self._uses_chat_completions():
                output_text = self._generate_chat_json_object(
                    instructions=instructions,
                    input_text=input_text,
                    schema=schema,
                )
                status = "completed"
            else:
                request = dict(
                    **self.settings.sampling_arguments(),
                    model=self.settings.model,
                    instructions=instructions,
                    input=input_text,
                    max_output_tokens=self.settings.max_output_tokens,
                    store=False,
                    text={
                        "format": {
                            "type": "json_schema",
                            "name": schema_name,
                            "schema": schema,
                            "strict": True,
                        }
                    },
                )
                if self.settings.reasoning_effort is not None:
                    request["reasoning"] = {"effort": self.settings.reasoning_effort}
                if os.environ.get("VERILOG_AGENT_RESPONSES_STREAM") == "1":
                    chunks = []
                    completed = None
                    for event in self._client.responses.create(**request, stream=True):
                        kind = getattr(event, "type", None)
                        if kind == "response.output_text.delta":
                            chunks.append(getattr(event, "delta", ""))
                        elif kind == "response.completed":
                            completed = getattr(event, "response", None)
                        elif kind in {"response.failed", "response.incomplete"}:
                            raise ModelProviderError(f"stream ended with {kind}")
                    if completed is None or getattr(completed, "status", None) != "completed":
                        raise ModelProviderError("stream ended without response.completed")
                    response = completed
                    output_text = "".join(chunks) or getattr(response, "output_text", None)
                else:
                    response = self._client.responses.create(**request)
                    output_text = getattr(response, "output_text", None)
                capture_response(response)
                status = getattr(response, "status", "unknown")
        except Exception as error:
            raise ModelProviderError(
                f"provider request failed: {type(error).__name__}: {error}"
            ) from error
        if not isinstance(output_text, str) or not output_text.strip():
            raise ModelProviderError(f"provider returned no output_text (status={status})")
        try:
            payload = json.loads(_unwrap_known_json_wrappers(output_text))
        except json.JSONDecodeError as error:
            preview = output_text.strip().replace("\r", "\\r").replace("\n", "\\n")
            if len(preview) > 240:
                preview = f"{preview[:240]}..."
            raise ModelProviderError(
                "provider output_text is not valid JSON "
                "(including supported <think> and Markdown JSON wrappers); "
                f"preview={preview!r}"
            ) from error
        if not isinstance(payload, dict):
            raise ModelProviderError("provider structured output must be a JSON object")
        return payload
