from __future__ import annotations

from typing import Any

from verilog_agent.agents.validation import (
    AgentOutputError,
    require_nonempty_string,
    require_string_list,
    require_verilog_identifier,
)
from verilog_agent.domain.enums import PortDirection
from verilog_agent.domain.models import (
    PlanResult,
    PortSpec,
    RTLTaskContext,
    SpecIR,
    TaskTreeNode,
)
from verilog_agent.providers.base import StructuredModelProvider

_LEAF_TASK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "node_id": {"type": "string", "minLength": 1},
        "title": {"type": "string", "minLength": 1},
        "description": {"type": "string", "minLength": 1},
        "acceptance_criteria": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
    },
    "required": ["node_id", "title", "description", "acceptance_criteria"],
}

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "module_name": {"type": "string", "minLength": 1},
        "ports": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "direction": {"type": "string", "enum": ["input", "output", "inout"]},
                    "width": {"type": "integer", "minimum": 1},
                },
                "required": ["name", "direction", "width"],
            },
        },
        "clock": {"type": ["string", "null"]},
        "reset": {"type": ["string", "null"]},
        "assumptions": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "acceptance_criteria": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "minLength": 1},
        },
        "task_tree_root": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "node_id": {"type": "string", "minLength": 1},
                "title": {"type": "string", "minLength": 1},
                "description": {"type": "string", "minLength": 1},
                "acceptance_criteria": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                },
                "children": {"type": "array", "minItems": 1, "items": _LEAF_TASK_SCHEMA},
            },
            "required": [
                "node_id",
                "title",
                "description",
                "acceptance_criteria",
                "children",
            ],
        },
    },
    "required": [
        "module_name",
        "ports",
        "clock",
        "reset",
        "assumptions",
        "acceptance_criteria",
        "task_tree_root",
    ],
}

PLANNER_INSTRUCTIONS = """You are the Requirement Planner in a Verilog RTL system.
Convert the user's requirement into a precise interface, explicit engineering assumptions,
testable acceptance criteria, and a small task tree. Do not generate Verilog or SystemVerilog.
Do not silently turn an ambiguity into a fact: record any chosen interpretation in assumptions.
Use only simple Verilog identifiers for the module and ports. A width is a positive bit count.
clock and reset must be null when absent, otherwise they must name declared input ports.
Write titles, descriptions, assumptions, and acceptance criteria in the user's language.
The root's children are concrete implementation or verification tasks."""


class RealPlanner:
    def __init__(self, provider: StructuredModelProvider) -> None:
        self.provider = provider

    def plan(self, context: RTLTaskContext) -> PlanResult:
        payload = self.provider.generate_structured(
            instructions=PLANNER_INSTRUCTIONS,
            input_text=context.raw_spec,
            schema_name="verilog_requirement_plan",
            schema=PLAN_SCHEMA,
        )
        result = self._parse(payload, context.raw_spec)
        required_module_name = context.metadata.get("required_module_name")
        if required_module_name is not None:
            result.spec_ir.module_name = require_verilog_identifier(
                required_module_name, "required_module_name"
            )
        return result

    @staticmethod
    def _parse(payload: dict[str, Any], raw_spec: str) -> PlanResult:
        try:
            module_name = require_verilog_identifier(payload["module_name"], "module_name")
            raw_ports = payload["ports"]
            if not isinstance(raw_ports, list) or not raw_ports:
                raise AgentOutputError("ports must be a non-empty list")
            ports = []
            for index, raw_port in enumerate(raw_ports):
                if not isinstance(raw_port, dict):
                    raise AgentOutputError(f"ports[{index}] must be an object")
                width = raw_port["width"]
                if isinstance(width, bool) or not isinstance(width, int) or width < 1:
                    raise AgentOutputError(f"ports[{index}].width must be a positive integer")
                ports.append(
                    PortSpec(
                        name=require_verilog_identifier(
                            raw_port["name"], f"ports[{index}].name"
                        ),
                        direction=PortDirection(raw_port["direction"]),
                        width=width,
                    )
                )

            clock = RealPlanner._optional_port_name(payload["clock"], "clock")
            reset = RealPlanner._optional_port_name(payload["reset"], "reset")
            port_by_name = {port.name: port for port in ports}
            for role, name in (("clock", clock), ("reset", reset)):
                if name is not None and (
                    name not in port_by_name
                    or port_by_name[name].direction is not PortDirection.INPUT
                ):
                    raise AgentOutputError(f"{role} must name a declared input port")

            assumptions = require_string_list(payload["assumptions"], "assumptions")
            acceptance = require_string_list(
                payload["acceptance_criteria"], "acceptance_criteria"
            )
            if not acceptance:
                raise AgentOutputError("acceptance_criteria must not be empty")
            tree = RealPlanner._parse_tree(payload["task_tree_root"])
            node_ids = [tree.node_id, *(child.node_id for child in tree.children)]
            if len(node_ids) != len(set(node_ids)):
                raise AgentOutputError("task tree node_id values must be unique")

            spec_ir = SpecIR(
                module_name=module_name,
                raw_spec=raw_spec,
                ports=ports,
                clock=clock,
                reset=reset,
                assumptions=assumptions,
                acceptance_criteria=acceptance,
            )
            return PlanResult(spec_ir=spec_ir, task_tree_root=tree)
        except AgentOutputError:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise AgentOutputError(f"invalid Planner output: {error}") from error

    @staticmethod
    def _optional_port_name(value: object, field_name: str) -> str | None:
        if value is None:
            return None
        return require_verilog_identifier(value, field_name)

    @staticmethod
    def _parse_tree(raw_tree: object) -> TaskTreeNode:
        if not isinstance(raw_tree, dict):
            raise AgentOutputError("task_tree_root must be an object")
        raw_children = raw_tree["children"]
        if not isinstance(raw_children, list) or not raw_children:
            raise AgentOutputError("task_tree_root.children must be a non-empty list")
        children = []
        for index, raw_child in enumerate(raw_children):
            if not isinstance(raw_child, dict):
                raise AgentOutputError(f"task_tree_root.children[{index}] must be an object")
            children.append(
                TaskTreeNode(
                    node_id=require_nonempty_string(
                        raw_child["node_id"], f"task_tree_root.children[{index}].node_id"
                    ),
                    title=require_nonempty_string(
                        raw_child["title"], f"task_tree_root.children[{index}].title"
                    ),
                    description=require_nonempty_string(
                        raw_child["description"],
                        f"task_tree_root.children[{index}].description",
                    ),
                    acceptance_criteria=require_string_list(
                        raw_child["acceptance_criteria"],
                        f"task_tree_root.children[{index}].acceptance_criteria",
                    ),
                )
            )
        tree = TaskTreeNode(
            node_id=require_nonempty_string(raw_tree["node_id"], "task_tree_root.node_id"),
            title=require_nonempty_string(raw_tree["title"], "task_tree_root.title"),
            description=require_nonempty_string(
                raw_tree["description"], "task_tree_root.description"
            ),
            acceptance_criteria=require_string_list(
                raw_tree["acceptance_criteria"], "task_tree_root.acceptance_criteria"
            ),
            children=children,
        )
        return tree
