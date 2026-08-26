from __future__ import annotations

from collections import deque
from copy import deepcopy
from typing import Any


class ScriptedProvider:
    """Offline structured provider for deterministic Agent contract tests."""

    def __init__(self, *responses: dict[str, Any]) -> None:
        self.responses = deque(deepcopy(responses))
        self.requests: list[dict[str, Any]] = []

    def generate_structured(
        self,
        *,
        instructions: str,
        input_text: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        self.requests.append(
            {
                "instructions": instructions,
                "input_text": input_text,
                "schema_name": schema_name,
                "schema": deepcopy(schema),
            }
        )
        if not self.responses:
            raise RuntimeError("ScriptedProvider has no response remaining")
        return self.responses.popleft()
