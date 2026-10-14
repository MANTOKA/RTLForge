from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from verilog_agent.domain.enums import EventType, PortDirection, TaskStage

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

    def __post_init__(self) -> None:
        if not self.module_name.isidentifier():
            raise ValueError("module_name must be a valid identifier")
        port_names = [port.name for port in self.ports]
        if len(port_names) != len(set(port_names)):
            raise ValueError("port names must be unique")
        if self.coding_style_constraint not in {"strict_sv", "legacy_verilog"}:
            raise ValueError("unknown coding_style_constraint")

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
