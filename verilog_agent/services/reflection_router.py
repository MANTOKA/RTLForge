from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class ReflectionPolicy(str, Enum):
    SYNTAX = "SYNTAX_REFLECTION"
    FUNCTIONAL = "FUNCTIONAL_REFLECTION"
    DEADLOCK_TERMINATION = "DEADLOCK_TERMINATION_REFLECTION"
    REQUIREMENT = "REQUIREMENT_REFLECTION"
    INFRASTRUCTURE = "INFRASTRUCTURE_RECOVERY"


class ReflectionAction(str, Enum):
    DEBUG = "DEBUG"
    WAIT_FOR_USER = "WAIT_FOR_USER"
    RECOVER_INFRASTRUCTURE = "RECOVER_INFRASTRUCTURE"


@dataclass(frozen=True, slots=True)
class ReflectionDecision:
    category: str
    policy: ReflectionPolicy
    action: ReflectionAction
    reason: str

    @property
    def invokes_debugger(self) -> bool:
        return self.action is ReflectionAction.DEBUG

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["policy"] = self.policy.value
        payload["action"] = self.action.value
        payload["invokes_debugger"] = self.invokes_debugger
        return payload


class AdaptiveReflectionRouter:
    """Map normalized deterministic failure evidence to one repair policy."""

    _SYNTAX = {
        "SYNTAX_ERROR",
        "LINT_ERROR",
        "LINT_RISK_ERROR",
        "INTERFACE_MISMATCH",
        "QUALITY_ERROR",
    }
    _FUNCTIONAL = {
        "ASSERTION_FAILURE",
        "FUNCTIONAL_MISMATCH",
        "SIMULATION_ERROR",
        "SIMULATION_FUNCTIONAL_FAIL",
        "LATENCY_MISMATCH",
        "RESET_ERROR",
    }
    _TIMEOUT = {"TIMEOUT", "SIMULATION_TIMEOUT"}
    _REQUIREMENT = {"BENCHMARK_BOUNDS_EXCEEDED", "RESOURCE_CONSTRAINT"}
    _INFRASTRUCTURE = {
        "SYSTEM_ERROR",
        "PATH_VIOLATION",
        "INPUT_NOT_FOUND",
        "UNSUPPORTED_INPUT",
        "TOOL_INCOMPATIBILITY",
    }

    def route(
        self,
        category: str | None,
        feedback: dict[str, Any] | None = None,
    ) -> ReflectionDecision:
        normalized = (category or "UNKNOWN").upper()
        if normalized in self._SYNTAX:
            return ReflectionDecision(
                normalized,
                ReflectionPolicy.SYNTAX,
                ReflectionAction.DEBUG,
                "Repair declarations, types, widths, assignments, or interface conformance.",
            )
        if normalized in self._FUNCTIONAL:
            return ReflectionDecision(
                normalized,
                ReflectionPolicy.FUNCTIONAL,
                ReflectionAction.DEBUG,
                "Repair RTL behavior, state transitions, reset, latency, or boundary handling.",
            )
        if normalized in self._TIMEOUT:
            return ReflectionDecision(
                normalized,
                ReflectionPolicy.DEADLOCK_TERMINATION,
                ReflectionAction.DEBUG,
                "Inspect non-termination, stalled handshakes, and cyclic combinational behavior.",
            )
        if normalized in self._REQUIREMENT:
            return ReflectionDecision(
                normalized,
                ReflectionPolicy.REQUIREMENT,
                ReflectionAction.WAIT_FOR_USER,
                "Meeting the bound may change the microarchitecture or user constraints.",
            )
        if normalized in self._INFRASTRUCTURE:
            return ReflectionDecision(
                normalized,
                ReflectionPolicy.INFRASTRUCTURE,
                ReflectionAction.RECOVER_INFRASTRUCTURE,
                "The evidence identifies a tool or environment failure, not an RTL repair target.",
            )
        return ReflectionDecision(
            normalized,
            ReflectionPolicy.INFRASTRUCTURE,
            ReflectionAction.RECOVER_INFRASTRUCTURE,
            "Unknown failures are fail-closed and must be classified before an LLM edits RTL.",
        )
