from __future__ import annotations

import re

VERILOG_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class AgentOutputError(ValueError):
    """Raised when model output violates the agent/domain contract."""


class RepairNoProgressError(AgentOutputError):
    """The debugger returned the unchanged input RTL."""


def require_verilog_identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or VERILOG_IDENTIFIER.fullmatch(value) is None:
        raise AgentOutputError(f"{field_name} must be a simple Verilog identifier")
    return value


def require_string_list(value: object, field_name: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise AgentOutputError(f"{field_name} must be a list of non-empty strings")
    return [item.strip() for item in value]


def require_nonempty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AgentOutputError(f"{field_name} must be a non-empty string")
    return value.strip()
