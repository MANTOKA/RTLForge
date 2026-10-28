from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from rtlforge.domain.enums import EventType, PortDirection, TaskStage
from rtlforge.domain.semantics import HandshakeSpec, ParameterConstraint, SignalTiming
from rtlforge.domain.synthesis import SynthesisConstraints

DOMAIN_SCHEMA_VERSION = 1


@dataclass(slots=True)
class PortSpec:
    name: str
    direction: PortDirection
    width: int = 1

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("port name must not be empty")
        if self.width < 1:
            raise ValueError("port width must be at least one")

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "direction": self.direction.value, "width": self.width}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PortSpec:
        return cls(
            name=data["name"],
            direction=PortDirection(data["direction"]),
            width=data.get("width", 1),
        )


@dataclass(slots=True)
class SpecIR:
    module_name: str
    raw_spec: str
    ports: list[PortSpec]
    clock: str | None = None
    reset: str | None = None
    assumptions: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    version: int = 1
    coding_style_constraint: str = "legacy_verilog"
    schema_version: int = DOMAIN_SCHEMA_VERSION
    clock_edge: str | None = None
    reset_polarity: str | None = None
    reset_kind: str | None = None
    signal_timing: list[SignalTiming] = field(default_factory=list)
    handshakes: list[HandshakeSpec] = field(default_factory=list)
    parameters: list[ParameterConstraint] = field(default_factory=list)
    clarification_questions: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.module_name.isidentifier():
            raise ValueError("module_name must be a valid identifier")
        port_names = [port.name for port in self.ports]
        if len(port_names) != len(set(port_names)):
            raise ValueError("port names must be unique")
        if self.coding_style_constraint not in {"strict_sv", "legacy_verilog"}:
            raise ValueError("unknown coding_style_constraint")
        if type(self.version) is not int or self.version < 1:
            raise ValueError("contract version must be a positive integer")
        if type(self.schema_version) is not int or self.schema_version not in {1, 2}:
            raise ValueError("unsupported SpecIR schema_version")
        if self.clock_edge not in {None, "posedge", "negedge"}:
            raise ValueError("clock_edge must be posedge, negedge, or null")
        if self.reset_polarity not in {None, "active_high", "active_low"}:
            raise ValueError("unknown reset_polarity")
        if self.reset_kind not in {None, "synchronous", "asynchronous"}:
            raise ValueError("unknown reset_kind")
        port_by_name = {port.name: port for port in self.ports}
        for role, name in (("clock", self.clock), ("reset", self.reset)):
            if name is not None and (
                name not in port_by_name
                or port_by_name[name].direction is not PortDirection.INPUT
                or port_by_name[name].width != 1
            ):
                raise ValueError(f"{role} must name a scalar input port")
        if self.clock is None and self.clock_edge is not None:
            raise ValueError("clock_edge requires a clock")
        if self.clock is not None and self.clock == self.reset:
            raise ValueError("clock and reset must be distinct ports")
        if self.reset is None and (self.reset_polarity or self.reset_kind):
            raise ValueError("reset semantics require a reset port")
        if self.reset_kind == "synchronous" and self.clock is None:
            raise ValueError("synchronous reset requires a clock")
        timing_signals = [timing.signal for timing in self.signal_timing]
        if len(timing_signals) != len(set(timing_signals)):
            raise ValueError("signal timing must be unique per port")
        for timing in self.signal_timing:
            if timing.signal not in port_by_name:
                raise ValueError("timing must reference a declared port")
            if timing.reference in {"clock_edge", "handshake"} and self.clock is None:
                raise ValueError("clocked timing requires a clock")
            if timing.reference == "handshake" and not any(
                timing.signal in handshake.data_signals
                or timing.signal in (handshake.request, handshake.acknowledge)
                for handshake in self.handshakes
            ):
                raise ValueError(
                    f"signal_timing.{timing.signal}: handshake timing requires membership in "
                    "a declared handshake's data_signals, request or acknowledge"
                )
        handshake_names = [handshake.name for handshake in self.handshakes]
        if len(handshake_names) != len(set(handshake_names)):
            raise ValueError("handshake names must be unique")
        for handshake in self.handshakes:
            if self.clock is None:
                raise ValueError("supported handshakes require a clock")
            for name in (handshake.request, handshake.acknowledge):
                if name is not None and (
                    name not in port_by_name or port_by_name[name].width != 1
                ):
                    raise ValueError("handshake controls must name scalar ports")
            if handshake.request == handshake.acknowledge:
                raise ValueError("handshake controls must be distinct")
            if handshake.acknowledge is not None and (
                port_by_name[handshake.request].direction
                is port_by_name[handshake.acknowledge].direction
            ):
                raise ValueError("handshake request/acknowledge must have opposite directions")
            if any(name not in port_by_name for name in handshake.data_signals):
                raise ValueError("handshake data must name declared ports")
        parameter_names = [parameter.name for parameter in self.parameters]
        if len(parameter_names) != len(set(parameter_names)):
            raise ValueError("parameter names must be unique")
        if any(not isinstance(q, str) or not q.strip() for q in self.clarification_questions):
            raise ValueError("clarification questions must be nonempty strings")

    def missing_semantics(self) -> list[dict[str, str]]:
        """Unknowns are questions, not implicit engineering defaults (v1 remains readable)."""
        if self.schema_version < 2:
            return []
        questions = []

        def ask(field: str, question: str) -> None:
            questions.append({"field": field, "question": question})

        if self.clock and self.clock_edge is None:
            ask("clock_edge", f"时钟 {self.clock} 在上升沿还是下降沿采样？")
        if self.reset and self.reset_polarity is None:
            ask("reset_polarity", f"复位 {self.reset} 是高有效还是低有效？")
        if self.reset and self.reset_kind is None:
            ask("reset_kind", f"复位 {self.reset} 是同步复位还是异步复位？")
        timed = {timing.signal for timing in self.signal_timing}
        for port in self.ports:
            if port.name not in {self.clock, self.reset} and port.name not in timed:
                ask(
                    f"signal_timing.{port.name}",
                    f"端口 {port.name} 的采样/输出时序是什么？请指定参考事件和最小/最大周期延迟。",
                )
        for handshake in self.handshakes:
            prefix = f"handshakes.{handshake.name}"
            if handshake.kind != "valid_only" and handshake.acknowledge is None:
                ask(prefix + ".acknowledge", f"握手 {handshake.name} 的应答信号是什么？")
            if handshake.transfer_rule is None:
                ask(prefix + ".transfer_rule", f"握手 {handshake.name} 在同沿完成还是请求后应答？")
            if handshake.hold_until_ack is None:
                ask(prefix + ".hold_until_ack",
                    f"握手 {handshake.name} 等待期间须保持请求和数据吗？")
        for parameter in self.parameters:
            if parameter.default is None:
                ask(f"parameters.{parameter.name}.default",
                    f"参数 {parameter.name} 的默认值是多少？")
        for index, question in enumerate(self.clarification_questions):
            ask(f"clarification_questions.{index}", question)
        return questions

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["ports"] = [port.to_dict() for port in self.ports]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SpecIR:
        return cls(
            module_name=data["module_name"],
            raw_spec=data["raw_spec"],
            ports=[PortSpec.from_dict(port) for port in data.get("ports", [])],
            clock=data.get("clock"),
            reset=data.get("reset"),
            assumptions=list(data.get("assumptions", [])),
            acceptance_criteria=list(data.get("acceptance_criteria", [])),
            version=data.get("version", 1),
            coding_style_constraint=data.get("coding_style_constraint", "legacy_verilog"),
            schema_version=data.get("schema_version", DOMAIN_SCHEMA_VERSION),
            clock_edge=data.get("clock_edge"),
            reset_polarity=data.get("reset_polarity"),
            reset_kind=data.get("reset_kind"),
            signal_timing=[SignalTiming(**item) for item in data.get("signal_timing", [])],
            handshakes=[HandshakeSpec(**item) for item in data.get("handshakes", [])],
            parameters=[ParameterConstraint(**item) for item in data.get("parameters", [])],
            clarification_questions=list(data.get("clarification_questions", [])),
        )


@dataclass(slots=True)
class TaskTreeNode:
    node_id: str
    title: str
    description: str
    children: list[TaskTreeNode] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "title": self.title,
            "description": self.description,
            "children": [child.to_dict() for child in self.children],
            "acceptance_criteria": list(self.acceptance_criteria),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskTreeNode:
        return cls(
            node_id=data["node_id"],
            title=data["title"],
            description=data["description"],
            children=[cls.from_dict(child) for child in data.get("children", [])],
            acceptance_criteria=list(data.get("acceptance_criteria", [])),
        )


@dataclass(slots=True)
class Candidate:
    candidate_id: str
    artifact_path: str
    code_hash: str
    parent_id: str | None = None
    generation: int = 0
    design_pattern: str = ""
    assumptions: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Candidate:
        return cls(**data)


@dataclass(slots=True)
class Budget:
    max_candidates: int = 2
    max_repairs: int = 3
    generated_candidates: int = 0
    repair_attempts: int = 0

    def __post_init__(self) -> None:
        if not 1 <= self.max_candidates <= 3:
            raise ValueError("max_candidates must be between one and three")
        if not 0 <= self.max_repairs <= 3:
            raise ValueError("max_repairs must be between zero and three")

    @property
    def candidate_slots(self) -> int:
        return self.max_candidates - self.generated_candidates

    @property
    def can_repair(self) -> bool:
        return self.repair_attempts < self.max_repairs

    def record_candidates(self, count: int) -> None:
        if count < 1 or count > self.candidate_slots:
            raise BudgetExceeded("candidate budget exceeded")
        self.generated_candidates += count

    def record_repair(self) -> None:
        if not self.can_repair:
            raise BudgetExceeded("repair budget exhausted")
        self.repair_attempts += 1

    def to_dict(self) -> dict[str, int]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Budget:
        return cls(**data)


class BudgetExceeded(RuntimeError):
    pass


@dataclass(slots=True)
class PlanResult:
    spec_ir: SpecIR
    task_tree_root: TaskTreeNode


@dataclass(slots=True)
class EvaluationOutcome:
    accepted: bool
    selected_candidate_id: str | None
    scores: dict[str, float] = field(default_factory=dict)
    reason: str = ""
    category: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class VerificationOutcome:
    passed: bool
    category: str
    evidence_path: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RTLTaskContext:
    task_id: str
    raw_spec: str
    current_stage: TaskStage = TaskStage.CREATED
    spec_ir: SpecIR | None = None
    task_tree_root: TaskTreeNode | None = None
    candidates: list[Candidate] = field(default_factory=list)
    selected_candidate_id: str | None = None
    budget: Budget = field(default_factory=Budget)
    cancel_requested: bool = False
    checkpoint_version: int = 0
    failure_category: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = DOMAIN_SCHEMA_VERSION
    synthesis_constraints: SynthesisConstraints | None = None

    def candidate(self, candidate_id: str | None = None) -> Candidate:
        target = candidate_id or self.selected_candidate_id
        for candidate in self.candidates:
            if candidate.candidate_id == target:
                return candidate
        raise LookupError(f"candidate not found: {target}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "raw_spec": self.raw_spec,
            "current_stage": self.current_stage.value,
            "spec_ir": self.spec_ir.to_dict() if self.spec_ir else None,
            "task_tree_root": self.task_tree_root.to_dict() if self.task_tree_root else None,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "selected_candidate_id": self.selected_candidate_id,
            "budget": self.budget.to_dict(),
            "cancel_requested": self.cancel_requested,
            "checkpoint_version": self.checkpoint_version,
            "failure_category": self.failure_category,
            "metadata": dict(self.metadata),
            "synthesis_constraints": (
                self.synthesis_constraints.to_dict() if self.synthesis_constraints else None
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RTLTaskContext:
        return cls(
            task_id=data["task_id"],
            raw_spec=data["raw_spec"],
            current_stage=TaskStage(data["current_stage"]),
            spec_ir=SpecIR.from_dict(data["spec_ir"]) if data.get("spec_ir") else None,
            task_tree_root=(
                TaskTreeNode.from_dict(data["task_tree_root"])
                if data.get("task_tree_root")
                else None
            ),
            candidates=[Candidate.from_dict(item) for item in data.get("candidates", [])],
            selected_candidate_id=data.get("selected_candidate_id"),
            budget=Budget.from_dict(data.get("budget", {})),
            cancel_requested=data.get("cancel_requested", False),
            checkpoint_version=data.get("checkpoint_version", 0),
            failure_category=data.get("failure_category"),
            metadata=dict(data.get("metadata", {})),
            schema_version=data.get("schema_version", DOMAIN_SCHEMA_VERSION),
            synthesis_constraints=(
                SynthesisConstraints.from_dict(data["synthesis_constraints"])
                if data.get("synthesis_constraints") is not None else None
            ),
        )


@dataclass(slots=True)
class Event:
    task_id: str
    event_type: EventType
    stage: TaskStage
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: uuid4().hex)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    sequence: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "task_id": self.task_id,
            "event_type": self.event_type.value,
            "stage": self.stage.value,
            "payload": dict(self.payload),
            "timestamp": self.timestamp,
            "sequence": self.sequence,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Event:
        return cls(
            event_id=data["event_id"],
            task_id=data["task_id"],
            event_type=EventType(data["event_type"]),
            stage=TaskStage(data["stage"]),
            payload=dict(data.get("payload", {})),
            timestamp=data["timestamp"],
            sequence=data.get("sequence", 0),
        )
