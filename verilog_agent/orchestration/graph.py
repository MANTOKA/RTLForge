"""LangGraph scheduling over the existing audited RTL workflow operations."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Protocol, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from verilog_agent.domain.enums import EventType, TaskStage
from verilog_agent.domain.models import RTLTaskContext, SpecIR
from verilog_agent.orchestration.orchestrator import Orchestrator
from verilog_agent.providers.usage import RecordingProvider, model_call_task
from verilog_agent.services.candidate_evaluation import MultiFileEDARunner


class TestbenchAgent(Protocol):
    def generate_for_contract(self, contract: SpecIR) -> tuple[str, list[str]]: ...


class GraphState(TypedDict):
    """Only serializable workflow data; never contains golden TB or reference RTL."""

    context: dict[str, Any]
    task_id: str
    stage: str
    generated_tb_path: str | None
    generated_tb_pass: bool | None
    golden_tb_pass: bool | None
    final_status: str | None


_STAGE_NODE = {
    TaskStage.CREATED: "enter",
    TaskStage.SPECIFYING: "planner",
    TaskStage.PLANNED: "testbench",
    TaskStage.GENERATING: "coder",
    TaskStage.EVALUATING: "static_evaluator",
    TaskStage.VERIFYING: "verifier",
    TaskStage.CLASSIFYING: "router",
    TaskStage.REPAIRING: "debugger",
}


class LangGraphOrchestrator(Orchestrator):
    """Replace stage scheduling, not Agents, scoring, Memory, EDA, or audit storage."""

    def __init__(
        self,
        *,
        testbench_agent: TestbenchAgent,
        generated_tb_runner: MultiFileEDARunner,
        runs_root: Path,
        has_golden_testbench: bool = True,
        **legacy_components: Any,
    ) -> None:
        super().__init__(**legacy_components)
        self.testbench_agent = testbench_agent
        provider = getattr(testbench_agent, "provider", None)
        if provider is not None and not isinstance(provider, RecordingProvider):
            testbench_agent.provider = RecordingProvider(provider, role="testbench")
        self.generated_tb_runner = generated_tb_runner
        self.runs_root = Path(runs_root)
        self.has_golden_testbench = has_golden_testbench
        builder = StateGraph(GraphState)
        for node in _STAGE_NODE.values():
            builder.add_node(node, self._node(node))
            builder.add_conditional_edges(node, self._next_node, [*_STAGE_NODE.values(), END])
        builder.add_edge(START, "enter")
        # Graph checkpoints are deliberately separate from the permanent SQLite audit DB.
        self.graph = builder.compile(checkpointer=InMemorySaver())

    def create_task(self, raw_spec: str, **kwargs: Any) -> RTLTaskContext:
        metadata = dict(kwargs.pop("metadata", None) or {})
        tags = list(metadata.get("experiment_tags", []))
        for tag in ("baseline_m1", "orchestrator=langgraph"):
            if tag not in tags:
                tags.append(tag)
        metadata["experiment_tags"] = tags
        return super().create_task(raw_spec, metadata=metadata, **kwargs)

    def run_task(self, task_id: str) -> RTLTaskContext:
        context = self._context(task_id)
        if context.current_stage.is_terminal or context.current_stage is TaskStage.WAITING_FOR_USER:
            return context
        config = {"configurable": {"thread_id": task_id}, "recursion_limit": 50}
        snapshot = self.graph.get_state(config)
        initial = None if snapshot.next else self._state(context)
        result = self.graph.invoke(initial, config)
        context = RTLTaskContext.from_dict(result["context"])
        self._contexts[task_id] = context
        return context

    @staticmethod
    def _next_node(state: GraphState) -> str:
        return _STAGE_NODE.get(TaskStage(state["stage"]), END)

    @staticmethod
    def _state(context: RTLTaskContext) -> GraphState:
        metadata = context.metadata
        return {
            "context": context.to_dict(),
            "task_id": context.task_id,
            "stage": context.current_stage.value,
            "generated_tb_path": metadata.get("generated_testbench_path"),
            "generated_tb_pass": metadata.get("generated_tb_pass"),
            "golden_tb_pass": metadata.get("golden_tb_pass"),
            "final_status": metadata.get("final_status"),
        }

    def _node(self, name: str):
        def execute(state: GraphState) -> GraphState:
            context = RTLTaskContext.from_dict(state["context"])
            self._contexts[context.task_id] = context
            if name == "testbench":
                self._create_testbench(context)
            elif name == "verifier":
                self._run_generated_testbench(context)
                if self.has_golden_testbench:
                    Orchestrator.advance_task(self, context.task_id)
                else:
                    context.metadata["final_status"] = (
                        "generated_tb_pass_await_manual"
                        if context.metadata.get("generated_tb_pass")
                        else "generated_tb_failed_await_manual"
                    )
                    self._transition(context, TaskStage.WAITING_FOR_USER)
            else:
                Orchestrator.advance_task(self, context.task_id)
            if context.current_stage is TaskStage.COMPLETED:
                context.metadata["golden_tb_pass"] = True
                context.metadata["final_status"] = "golden_pass"
            elif context.current_stage is TaskStage.FAILED:
                context.metadata["final_status"] = (
                    "max_retry_exhausted"
                    if context.failure_category == "BUDGET_EXHAUSTED"
                    else context.failure_category or "failed"
                )
            self._checkpoint(context)
            return self._state(context)

        return execute

    def _create_testbench(self, context: RTLTaskContext) -> None:
        if context.spec_ir is None:
            self._fail(context, "CONTRACT_ERROR", "Planner did not produce a contract")
            return
        try:
            with model_call_task(context):
                source, objectives = self.testbench_agent.generate_for_contract(context.spec_ir)
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", context.task_id) is None:
                raise ValueError("unsafe task_id")
            path = self.runs_root / context.task_id / "generated_tb.sv"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8", newline="\n")
            context.metadata["generated_testbench_path"] = str(path)
            context.metadata["generated_test_objectives"] = objectives
            self._record(context, EventType.GENERATED_TB_CREATED, {"path": str(path)})
            self._transition(context, TaskStage.GENERATING)
        except Exception as error:
            self._fail(context, "TESTBENCH_GENERATION_ERROR", str(error))

    def _run_generated_testbench(self, context: RTLTaskContext) -> None:
        path = context.metadata.get("generated_testbench_path")
        if not path or context.selected_candidate_id is None:
            context.metadata["generated_tb_pass"] = False
            return
        try:
            result = self.generated_tb_runner.run_files(
                "simulate", [Path(path), Path(context.candidate().artifact_path)]
            )
            passed = result.get("status") == "PASS" and (
                "GENERATED_TB_PASS" in result.get("stdout", "")
            )
        except Exception as error:
            result = {"status": "ERROR", "category": "SYSTEM_ERROR", "reason": str(error)}
            passed = False
        evidence = (
            self.runs_root / context.task_id /
            f"generated_tb_result_{context.selected_candidate_id}.json"
        )
        evidence.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        context.metadata["generated_tb_pass"] = passed
        self._record(
            context, EventType.GENERATED_TB_CHECKED,
            {"passed": passed, "evidence_path": str(evidence)},
        )
