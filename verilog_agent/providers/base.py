from __future__ import annotations

from typing import Any, Protocol


class ProviderConfigurationError(ValueError):
    """Raised before a request when required provider configuration is missing."""


class ModelProviderError(RuntimeError):
    """Raised when a provider response cannot satisfy the structured contract."""


class StructuredModelProvider(Protocol):
    def generate_structured(
        self,
        *,
        instructions: str,
        input_text: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]: ...
