from __future__ import annotations

from typing import Any, Protocol


class ProviderConfigurationError(ValueError):
    """Raised before a request when required provider configuration is missing."""


class ModelProviderError(RuntimeError):
    """Raised when a provider response cannot satisfy the structured contract."""


class OutputLimitError(ModelProviderError):
    """A response explicitly reported output-length truncation, never a usable artifact."""

    def __init__(self, limit: int, partial_text: str = "") -> None:
        super().__init__(f"provider response incomplete (reason=max_output_tokens, limit={limit})")
        self.limit = limit
        self.partial_text = partial_text


class StructuredModelProvider(Protocol):
    def generate_structured(
        self,
        *,
        instructions: str,
        input_text: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]: ...
