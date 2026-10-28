"""Validated, serializable timing and parameter clauses of a hardware contract."""

from __future__ import annotations

from dataclasses import dataclass, field


def integer(value: object, name: str, minimum: int | None = None) -> None:
    if type(value) is not int or (minimum is not None and value < minimum):
        bound = f" >= {minimum}" if minimum is not None else ""
        raise ValueError(f"{name} must be an integer" + bound)


@dataclass(slots=True)
class SignalTiming:
    signal: str
    reference: str
    min_cycles: int
    max_cycles: int | None

    def __post_init__(self) -> None:
        if self.reference not in {"clock_edge", "input_change", "handshake"}:
            raise ValueError("unknown timing reference")
        integer(self.min_cycles, "min_cycles", 0)
        if self.max_cycles is not None:
            integer(self.max_cycles, "max_cycles", self.min_cycles)
        if self.reference == "input_change" and (self.min_cycles, self.max_cycles) != (0, 0):
            raise ValueError("combinational input_change timing must have zero cycle latency")


@dataclass(slots=True)
class HandshakeSpec:
    name: str
    kind: str
    request: str
    acknowledge: str | None
    data_signals: list[str] = field(default_factory=list)
    transfer_rule: str | None = None
    hold_until_ack: bool | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.isidentifier():
            raise ValueError("handshake name must be an identifier")
        if not isinstance(self.request, str) or not self.request.isidentifier():
            raise ValueError("handshake request must be an identifier")
        if self.acknowledge is not None and (
            not isinstance(self.acknowledge, str) or not self.acknowledge.isidentifier()
        ):
            raise ValueError("handshake acknowledge must be an identifier or null")
        if self.kind not in {"ready_valid", "request_ack", "start_done", "valid_only"}:
            raise ValueError("unknown handshake kind")
        allowed = {"same_edge"} if self.kind in {"ready_valid", "valid_only"} else {
            "request_then_ack"
        }
        if self.transfer_rule is not None and self.transfer_rule not in allowed:
            raise ValueError("transfer_rule conflicts with handshake kind")
        if self.hold_until_ack is not None and type(self.hold_until_ack) is not bool:
            raise ValueError("hold_until_ack must be a boolean or null")
        if not isinstance(self.data_signals, list) or any(
            not isinstance(signal, str) for signal in self.data_signals
        ):
            raise ValueError("handshake data_signals must be a string list")
        if self.kind == "valid_only" and self.acknowledge is not None:
            raise ValueError("valid_only cannot have an acknowledge signal")


@dataclass(slots=True)
class ParameterConstraint:
    name: str
    default: int | None
    minimum: int | None = None
    maximum: int | None = None
    allowed_values: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.isidentifier():
            raise ValueError("parameter name must be an identifier")
        for name in ("default", "minimum", "maximum"):
            value = getattr(self, name)
            if value is not None:
                integer(value, f"parameter {name}")
        if not isinstance(self.allowed_values, list):
            raise ValueError("allowed_values must be an integer list")
        for value in self.allowed_values:
            integer(value, "allowed value")
            self._check_range(value)
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("parameter minimum exceeds maximum")
        if self.default is not None:
            self._check_range(self.default)
            if self.allowed_values and self.default not in self.allowed_values:
                raise ValueError("parameter default is not an allowed value")

    def _check_range(self, value: int) -> None:
        if (self.minimum is not None and value < self.minimum) or (
            self.maximum is not None and value > self.maximum
        ):
            raise ValueError("parameter value is outside its declared range")
