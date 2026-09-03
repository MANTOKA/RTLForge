from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from verilog_agent.providers.base import ModelProviderError, ProviderConfigurationError


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


@dataclass(frozen=True, slots=True)
class ProviderSettings:
    api_key: str = field(repr=False)
    model: str
    base_url: str | None = None
    timeout_seconds: int = 120
    max_output_tokens: int = 8192
    max_retries: int = 2

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise ProviderConfigurationError("OPENAI_API_KEY is required")
        if not self.model.strip():
            raise ProviderConfigurationError("VERILOG_AGENT_MODEL is required")
        if self.timeout_seconds < 1:
            raise ProviderConfigurationError("timeout_seconds must be positive")
        if self.max_output_tokens < 1:
            raise ProviderConfigurationError("max_output_tokens must be positive")
        if self.max_retries < 0:
            raise ProviderConfigurationError("max_retries must not be negative")

    @classmethod
    def from_env(cls, environment: dict[str, str] | None = None) -> ProviderSettings:
        values = os.environ if environment is None else environment
        base_url = values.get("OPENAI_BASE_URL", "").strip() or None
        return cls(
            api_key=values.get("OPENAI_API_KEY", ""),
            model=values.get("VERILOG_AGENT_MODEL", ""),
            base_url=base_url,
            timeout_seconds=_positive_int(
                values, "VERILOG_AGENT_REQUEST_TIMEOUT_SECONDS", 120
            ),
            max_output_tokens=_positive_int(
                values, "VERILOG_AGENT_MAX_OUTPUT_TOKENS", 8192
            ),
            max_retries=_non_negative_int(values, "VERILOG_AGENT_MAX_RETRIES", 2),
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

    def generate_structured(
        self,
        *,
        instructions: str,
        input_text: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            response = self._client.responses.create(
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
        except Exception as error:
            raise ModelProviderError(
                f"provider request failed: {type(error).__name__}: {error}"
            ) from error
        output_text = getattr(response, "output_text", None)
        if not isinstance(output_text, str) or not output_text.strip():
            status = getattr(response, "status", "unknown")
            raise ModelProviderError(f"provider returned no output_text (status={status})")
        try:
            payload = json.loads(output_text)
        except json.JSONDecodeError as error:
            raise ModelProviderError("provider output_text is not valid JSON") from error
        if not isinstance(payload, dict):
            raise ModelProviderError("provider structured output must be a JSON object")
        return payload
