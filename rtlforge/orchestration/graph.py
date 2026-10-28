"""LangGraph owns execution, branching, durable checkpoints and human interrupts."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from threading import Lock
from typing import Any, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from rtlforge.domain.enums import EventType, PortDirection, TaskStage
from rtlforge.domain.models import RTLTaskContext, SpecIR
from rtlforge.orchestration.graph_storage import GraphRuntimeStore
from rtlforge.orchestration.operations import OperationResult, Outcome, WorkflowOperations
from rtlforge.orchestration.policy import DebugAction, WorkflowPolicy, validate_debug_action
from rtlforge.providers import ModelProviderError
from rtlforge.providers.usage import RecordingProvider, model_call_task
from rtlforge.services.candidate_evaluation import MultiFileEDARunner
from rtlforge.services.debug_tools import DebugTools


class TestbenchAgent(Protocol):
    def generate_for_contract(self, contract: SpecIR) -> tuple[str, list[str]]: ...


class GraphState(TypedDict):
    # No golden TB/reference source or raw simulator transcripts.
    context: dict[str, Any]
    task_id: str
    step: int
    outcome: str
    details: dict[str, Any]
    entry_node: str
    after_tb: str


_ROUTES = {
    "enter": {"ready": "planner"},
    "planner": {"planned": "testbench", "wait": "await_user"},
    "testbench": {
        "tb_ready": "coder",
        "tb_resume_static": "static_evaluator",
        "tb_resume_verify": "advisory_verifier",
        "tb_resume_router": "router",
        "tb_resume_debug": "debug_decision",
    },
    "coder": {"generated": "static_evaluator"},
    "static_evaluator": {"evaluated": "static_policy"},
    "static_policy": {
        "static_passed": "advisory_verifier",
        "rejected": "router",
        "regenerate": "coder",
    },
    "advisory_verifier": {
        "golden_required": "verifier",
        "rejected": "router",
        "wait": "await_user",
        "functionally_verified": "synthesizer",
    },
    "verifier": {"verified": END, "rejected": "router",
                 "functionally_verified": "synthesizer"},
    "synthesizer": {"verified": END, "rejected": "router", "wait": "await_user"},
    "router": {"repair_allowed": "debug_decision", "regenerate": "coder", "wait": "await_user"},
    "debug_decision": {
        "investigate": "debug_tool",
        "repair_allowed": "debugger",
        "wait": "await_user",
    },
    "debug_tool": {"investigated": "debug_decision", "verified": END,
                   "rejected": "router", "wait": "await_user"},
    "debugger": {"repaired": "static_evaluator", "rejected": "router"},
    "await_user": {
        "human_accepted": END,
        "answered_debug": "debug_decision",
        "answered_spec": "planner",
        "answered_synthesis": "synthesizer",
    },
}
_NODE_STAGE = {
    "enter": TaskStage.SPECIFYING,
    "planner": TaskStage.SPECIFYING,
    "testbench": TaskStage.PLANNED,
    "coder": TaskStage.GENERATING,
    "static_evaluator": TaskStage.EVALUATING,
    "static_policy": TaskStage.EVALUATING,
    "advisory_verifier": TaskStage.VERIFYING,
    "verifier": TaskStage.VERIFYING,
    "synthesizer": TaskStage.SYNTHESIZING,
    "router": TaskStage.CLASSIFYING,
    "debug_decision": TaskStage.CLASSIFYING,
    "debug_tool": TaskStage.CLASSIFYING,
    "debugger": TaskStage.REPAIRING,
    "await_user": TaskStage.WAITING_FOR_USER,
}
_RESULT_STAGE = {
    "planned": TaskStage.PLANNED,
    "tb_ready": TaskStage.GENERATING,
    "generated": TaskStage.EVALUATING,
    "repaired": TaskStage.EVALUATING,
    "rejected": TaskStage.CLASSIFYING,
    "regenerate": TaskStage.GENERATING,
    "static_passed": TaskStage.VERIFYING,
    "wait": TaskStage.WAITING_FOR_USER,
    "verified": TaskStage.COMPLETED,
    "functionally_verified": TaskStage.SYNTHESIZING,
    "answered_synthesis": TaskStage.SYNTHESIZING,
    "human_accepted": TaskStage.COMPLETED,
    "answered_debug": TaskStage.CLASSIFYING,
    "answered_spec": TaskStage.SPECIFYING,
    "failed": TaskStage.FAILED,
    "cancelled": TaskStage.CANCELLED,
}


class LangGraphOrchestrator:
    """Share actions/policies with the legacy scheduler, never its dispatch loop."""

    def __init__(
        self,
        *,
        testbench_agent: TestbenchAgent,
        generated_tb_runner: MultiFileEDARunner,
        runs_root: Path,
        has_golden_testbench: bool = True,
        skip_generated_testbench: bool = False,
        checkpoint_database: Path | None = None,
        max_debug_actions: int = 12,
        **components: Any,
    ) -> None:
        if max_debug_actions < 1:
            raise ValueError("max_debug_actions must be positive")
        self.operations = WorkflowOperations(**components)
        self.policy = WorkflowPolicy(self.operations)
        self.store = self.operations.store
        self.runs_root = Path(runs_root)
        self.testbench_agent = testbench_agent
        provider = getattr(testbench_agent, "provider", None)
        if provider is not None and not isinstance(provider, RecordingProvider):
            testbench_agent.provider = RecordingProvider(provider, role="testbench")
        self.generated_tb_runner = generated_tb_runner
        self.has_golden_testbench = has_golden_testbench
        if skip_generated_testbench and not has_golden_testbench:
            raise ValueError("Skipping generated TB requires an independent golden testbench")
        self.skip_generated_testbench = skip_generated_testbench
        self.max_debug_actions = max_debug_actions
        audit = self.store.database_path.resolve()
        path = (
            Path(checkpoint_database)
            if checkpoint_database is not None
            else (audit.with_name(audit.stem + ".langgraph.sqlite"))
        )
        if path.resolve() == audit or (
            self.operations.memory_store is not None
            and path.resolve() in {
                item.resolve() for item in getattr(
                    self.operations.memory_store, "database_paths",
                    [self.operations.memory_store.path],
                )
            }
        ):
            raise ValueError("Graph checkpoints must not share the audit or Memory database")
        self.runtime = GraphRuntimeStore(path)
        self.debug_tools = DebugTools(self.operations, self.runs_root)
        self._run_lock = Lock()
        builder = StateGraph(GraphState)
        builder.add_node("bootstrap", lambda state: state)
        builder.add_conditional_edges(
            "bootstrap",
            lambda state: state["entry_node"],
            {name: name for name in _ROUTES},
        )
        for name, routes in _ROUTES.items():
            builder.add_node(name, self._node(name))
            builder.add_conditional_edges(
                name,
                lambda state: state["outcome"],
                {**routes, "failed": END, "cancelled": END},
            )
        builder.add_edge(START, "bootstrap")
        self.graph = builder.compile(checkpointer=self.runtime.checkpointer)

    def _signature(self) -> dict:
        return {
            "version": 2,
            "has_golden": self.has_golden_testbench,
            **({"skip_generated_testbench": True} if self.skip_generated_testbench else {}),
            "runs_root": str(self.runs_root.resolve()),
            "max_debug_actions": self.max_debug_actions,
            "memory_mode": self.operations.memory_mode,
        }

    def create_task(self, raw_spec: str, **kwargs: Any) -> RTLTaskContext:
        metadata = dict(kwargs.pop("metadata", None) or {})
        tags = list(metadata.get("experiment_tags", []))
        for tag in ("baseline_m1", "orchestrator=langgraph"):
            if tag not in tags:
                tags.append(tag)
        metadata.update(experiment_tags=tags, graph_runtime=self._signature())
        return self.operations.create_task(raw_spec, metadata=metadata, **kwargs)

    @staticmethod
    def _config(task_id: str) -> dict:
        return {"configurable": {"thread_id": task_id}, "recursion_limit": 256}

    def load_task(self, task_id: str) -> RTLTaskContext:
        snapshot = self.graph.get_state(self._config(task_id))
        if snapshot.values:
            return RTLTaskContext.from_dict(snapshot.values["context"])
        return self.operations._context(task_id)

    def _initial(self, context: RTLTaskContext) -> GraphState:
        # Only used for a fresh task or explicit migration of a legacy audit snapshot.
        entries = {
            TaskStage.CREATED: "enter",
            TaskStage.SPECIFYING: "planner",
            TaskStage.PLANNED: "testbench",
            TaskStage.GENERATING: "coder",
            TaskStage.EVALUATING: "static_evaluator",
            TaskStage.VERIFYING: "advisory_verifier",
            TaskStage.SYNTHESIZING: "synthesizer",
            TaskStage.CLASSIFYING: "router",
            TaskStage.REPAIRING: "router",
            TaskStage.WAITING_FOR_USER: "await_user",
        }
        entry = entries[context.current_stage]
        after_tb = "coder"
        path = context.metadata.get("generated_testbench_path")
        if entry in {
            "coder",
            "static_evaluator",
            "advisory_verifier",
            "router",
            "debug_decision",
        } and (not path or not Path(path).is_file()):
            after_tb, entry = entry, "testbench"
        context.metadata["graph_runtime"] = self._signature()
        tags = context.metadata.setdefault("experiment_tags", [])
        if "orchestrator=langgraph" not in tags:
            tags.append("orchestrator=langgraph")
        return {
            "context": context.to_dict(),
            "task_id": context.task_id,
            "step": 0,
            "outcome": "ready",
            "details": {},
            "entry_node": entry,
            "after_tb": after_tb,
        }

    def run_task(
        self,
        task_id: str,
        *,
        interrupt_after: list[str] | None = None,
    ) -> RTLTaskContext:
        return self._invoke(task_id, interrupt_after=interrupt_after)

    def resume_task(
        self, task_id: str, response: dict, *, interrupt_after: list[str] | None = None,
    ) -> RTLTaskContext:
        snapshot = self.graph.get_state(self._config(task_id))
        if not any(task.interrupts for task in snapshot.tasks):
            raise ValueError("Task is not paused for user input")
        context = self.load_task(task_id)
        self._validate_response(context, response)
        return self._invoke(task_id, response=response, interrupt_after=interrupt_after)

    def _invoke(
        self,
        task_id: str,
        *,
        response: dict | None = None,
        interrupt_after: list[str] | None = None,
    ) -> RTLTaskContext:
        if not self._run_lock.acquire(blocking=False):
            raise RuntimeError("This runtime is already executing a task")
        try:
            context = self.load_task(task_id)
            saved_runtime = dict(context.metadata.get("graph_runtime", self._signature()))
            current_runtime = self._signature()
            # A directory rename/junction may change spelling without changing the run root.
            for runtime in (saved_runtime, current_runtime):
                runtime["runs_root"] = str(Path(runtime["runs_root"]).resolve())
            if saved_runtime != current_runtime:
                raise ValueError("Cannot resume with changed graph runtime configuration")
            if context.current_stage.is_terminal:
                return context
            snapshot = self.graph.get_state(self._config(task_id))
            waiting = any(task.interrupts for task in snapshot.tasks)
            if waiting and response is None:
                if self.runtime.is_cancelled(task_id):
                    response = {"action": "cancel"}
                else:
                    return context
            initial = None if snapshot.values else self._initial(context)
            if response is not None:
                initial = Command(resume=response)
            result = self.graph.invoke(
                initial,
                self._config(task_id),
                interrupt_after=interrupt_after,
            )
            return RTLTaskContext.from_dict(result["context"])
        finally:
            self._run_lock.release()

    def request_cancel(self, task_id: str) -> RTLTaskContext:
        context = self.load_task(task_id)
        if not context.current_stage.is_terminal and self.runtime.request_cancel(task_id):
            self.operations._record(context, EventType.CANCEL_REQUESTED)
        context.cancel_requested = self.runtime.is_cancelled(task_id)
        return context

    def close(self) -> None:
        self.runtime.close()

    def _node(self, name: str):
        def execute(state: GraphState) -> GraphState:
            receipt = self.runtime.load_result(state["task_id"], state["step"], name)
            if receipt is not None:
                return receipt
            context = RTLTaskContext.from_dict(state["context"])
            ops = self.operations
            ops.set_stage(context, _NODE_STAGE[name])
            if self.runtime.is_cancelled(context.task_id):
                context.cancel_requested = True
                result = ops._cancel(context)
            elif name == "await_user":
                # interrupt must be outside the normal exception handler. It re-enters this node.
                result = self._await_user(context)
            else:
                try:
                    with model_call_task(context):
                        result = self._execute(name, context, state)
                except ModelProviderError as error:
                    result = ops._fail(context, "SYSTEM_ERROR", str(error))
                except PermissionError as error:
                    result = ops._fail(context, "POLICY_VIOLATION", str(error))
                except Exception as error:
                    result = ops._fail(context, "ORCHESTRATION_ERROR", str(error))
            target = _RESULT_STAGE.get(
                result.outcome.value if isinstance(result.outcome, Outcome) else result.outcome
            )
            outcome = (
                result.outcome.value if isinstance(result.outcome, Outcome) else result.outcome
            )
            if target is not None:
                ops.set_stage(context, target)
            if outcome == "verified":
                context.metadata.update(golden_tb_pass=True, final_status="golden_pass")
                ops._record(
                    context,
                    EventType.SYSTEM_SUCCESS,
                    {
                        "selected_candidate_id": context.selected_candidate_id,
                    },
                )
            elif outcome == "failed":
                context.metadata["final_status"] = (
                    "max_retry_exhausted"
                    if context.failure_category == "BUDGET_EXHAUSTED"
                    else context.failure_category or "failed"
                )
            elif outcome == "cancelled":
                context.metadata["final_status"] = "cancelled"
            ops._checkpoint(context)
            update = {
                **state,
                "context": context.to_dict(),
                "step": state["step"] + 1,
                "outcome": outcome,
                "details": result.details,
            }
            if outcome == "answered_spec":
                update["after_tb"] = "coder"
            # Persist completed side effects before LangGraph commits its own node checkpoint.
            self.runtime.save_result(state["task_id"], state["step"], name, update)
            return update

        return execute

    def _execute(self, name: str, context: RTLTaskContext, state: GraphState) -> OperationResult:
        ops = self.operations
        actions = {
            "planner": "plan",
            "coder": "generate",
            "static_evaluator": "evaluate",
            "verifier": "verify",
            "synthesizer": "synthesize",
            "debugger": "repair",
        }
        if name in actions:
            return ops.perform(actions[name], context)
        if name == "enter":
            return OperationResult("ready")
        if name == "testbench":
            return self._create_testbench(context, state["after_tb"])
        if name == "static_policy":
            return self.policy.after_static(context, state["details"])
        if name == "router":
            return self.policy.after_failure(context)
        if name == "advisory_verifier":
            if self.skip_generated_testbench:
                return OperationResult("golden_required")
            result = self._run_generated_testbench(context)
            if self.has_golden_testbench:
                return OperationResult("golden_required")
            if result.get("category") in {"SYSTEM_ERROR", "PATH_VIOLATION", "INPUT_NOT_FOUND"}:
                return ops._fail(context, "SYSTEM_ERROR", "Generated TB tool execution failed")
            if result.get("category") in {"SYNTAX_ERROR", "LINT_ERROR", "TOOL_INCOMPATIBILITY"}:
                context.metadata.update(
                    pause_reason="generated_tb_problem",
                    clarification_question="生成 TB 的编译或兼容性检查失败，请复核测试与契约。",
                )
                return OperationResult(Outcome.WAIT)
            if context.metadata.get("generated_tb_pass"):
                context.failure_category = None
                context.metadata["golden_tb_pass"] = False
                context.metadata["functional_verified_candidate"] = context.selected_candidate_id
                if context.synthesis_constraints is not None:
                    return OperationResult(Outcome.FUNCTIONALLY_VERIFIED)
                context.metadata.update(
                    final_status="generated_tb_pass_await_manual",
                    pause_reason="generated_tb_review",
                )
                return OperationResult(Outcome.WAIT)
            # Auxiliary evidence may initiate investigation, never declare independent success.
            context.failure_category = (
                "TIMEOUT" if result.get("category") == "TIMEOUT" else "FUNCTIONAL_MISMATCH"
            )
            advisory = context.metadata["generated_tb_attempts"][-1]["advisory_feedback"]
            context.metadata["verification_attempts"] = [
                {
                    "candidate_id": context.selected_candidate_id,
                    "debugger_feedback": {
                        "category": context.failure_category,
                        "agent_tb_advisory": advisory,
                    },
                }
            ]
            return OperationResult(Outcome.REJECTED)
        if name == "debug_decision":
            return self._debug_decision(context)
        if name == "debug_tool":
            action = validate_debug_action(
                context, context.metadata["pending_debug_action"], self.max_debug_actions + 1
            )
            if action is DebugAction.RUN_SYNTHESIS:
                return ops.perform("synthesize", context)
            observation = self.debug_tools.call(action, context)
            context.metadata.setdefault("debug_observations", []).append(
                {
                    "candidate_id": context.selected_candidate_id,
                    "tool": action.value,
                    "result": observation,
                }
            )
            return OperationResult("investigated")
        raise ValueError(f"Unknown node: {name}")

    def _create_testbench(self, context: RTLTaskContext, after_tb: str) -> OperationResult:
        if context.spec_ir is None:
            return self.operations._fail(context, "CONTRACT_ERROR", "No hardware contract")
        if context.spec_ir.missing_semantics():
            return self.operations._fail(context, "CONTRACT_ERROR", "Contract needs clarification")
        contract = context.spec_ir.to_dict()
        if self.skip_generated_testbench:
            context.metadata["generated_tb_skipped"] = "independent_golden_testbench"
            context.metadata["generated_test_objectives"] = []
            return OperationResult({
                "coder": "tb_ready", "static_evaluator": "tb_resume_static",
                "advisory_verifier": "tb_resume_verify", "router": "tb_resume_router",
                "debug_decision": "tb_resume_debug",
            }[after_tb])
        source, objectives = self.testbench_agent.generate_for_contract(context.spec_ir)
        if context.spec_ir.to_dict() != contract:
            context.spec_ir = SpecIR.from_dict(contract)
            return self.operations._fail(context, "CONTRACT_VIOLATION", "TB changed the contract")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", context.task_id) is None:
            raise ValueError("unsafe task_id")
        revision = len(context.metadata.get("user_responses", []))
        filename = "generated_tb.sv" if not revision else f"generated_tb_v{revision + 1}.sv"
        path = self.runs_root / context.task_id / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8", newline="\n")
        context.metadata.update(
            generated_testbench_path=str(path),
            generated_test_objectives=objectives,
            generated_tb_contract_sha256=hashlib.sha256(
                json.dumps(contract, sort_keys=True).encode()
            ).hexdigest(),
        )
        self.operations._record(context, EventType.GENERATED_TB_CREATED, {"path": str(path)})
        return OperationResult(
            {
                "coder": "tb_ready",
                "static_evaluator": "tb_resume_static",
                "advisory_verifier": "tb_resume_verify",
                "router": "tb_resume_router",
                "debug_decision": "tb_resume_debug",
            }[after_tb]
        )

    def _debug_decision(self, context: RTLTaskContext) -> OperationResult:
        count = context.metadata.get("debug_action_count", 0)
        if count >= self.max_debug_actions:
            return self.operations._budget_exhausted(context, "Debugger action budget exhausted")
        candidate = context.candidate()
        observations = [
            row
            for row in context.metadata.get("debug_observations", [])[-6:]
            if row["candidate_id"] == candidate.candidate_id
        ]
        chooser = getattr(self.operations.debugger, "decide_next_action", None)
        decision = (
            chooser(
                {
                    "contract": context.spec_ir.to_dict() if context.spec_ir else None,
                    "candidate_id": candidate.candidate_id,
                    "failure_category": context.failure_category,
                    "remaining_repairs": context.budget.max_repairs
                    - context.budget.repair_attempts,
                    "remaining_actions": self.max_debug_actions - count,
                    "observations": observations,
                    "user_answer": context.metadata.get("debugger_user_answer"),
                    "allowed_actions": [action.value for action in DebugAction if
                                        action is not DebugAction.READ_SYNTHESIS_REPORT
                                        or candidate.metadata.get("synthesis_result")
                                        if action is not DebugAction.RUN_SYNTHESIS or (
                                            self.operations.synthesis_runner is not None
                                            and context.synthesis_constraints is not None
                                            and context.metadata.get(
                                                "functional_verified_candidate")
                                            == candidate.candidate_id
                                        )],
                    "synthesis_constraints": (
                        context.synthesis_constraints.to_dict()
                        if context.synthesis_constraints else None
                    ),
                }
            )
            if chooser is not None
            else {"action": "repair", "reason": "Legacy Debugger adapter"}
        )
        try:
            action = validate_debug_action(context, decision, self.max_debug_actions)
        except (ValueError, LookupError) as error:
            return self.operations._fail(context, "POLICY_VIOLATION", str(error))
        context.metadata["debug_action_count"] = count + 1
        context.metadata["pending_debug_action"] = decision
        self.operations._record(
            context,
            EventType.DEBUG_ACTION_SELECTED,
            {
                "candidate_id": candidate.candidate_id,
                "action": action.value,
                "reason": decision["reason"],
            },
        )
        if action is DebugAction.REPAIR:
            return OperationResult(Outcome.REPAIR_ALLOWED)
        if action is DebugAction.ASK_USER:
            context.metadata.update(
                pause_reason="debugger_question", clarification_question=decision["reason"]
            )
            return OperationResult(Outcome.WAIT)
        if action is DebugAction.STOP:
            return self.operations._fail(context, "AGENT_STOPPED", decision["reason"])
        return OperationResult("investigate")

    @staticmethod
    def _validate_response(context: RTLTaskContext, response: dict) -> None:
        if not isinstance(response, dict):
            raise ValueError("Resume response must be an object")
        action = response.get("action")
        if action in {"cancel", "reject"}:
            return
        if action == "accept" and context.metadata.get("pause_reason") == "generated_tb_review":
            if context.metadata.get("generated_tb_pass") is True:
                return
        if action == "answer" and isinstance(response.get("message"), str):
            if response["message"].strip() and len(response["message"]) <= 20000:
                return
        raise ValueError("Expected cancel, reject, a valid answer, or generated-TB acceptance")

    def _await_user(self, context: RTLTaskContext) -> OperationResult:
        response = interrupt(
            {
                "task_id": context.task_id,
                "reason": context.metadata.get("pause_reason", "requirement_clarification"),
                "question": context.metadata.get("clarification_question")
                or (
                    "Generated tests passed; review the limited verification evidence."
                    if context.metadata.get("generated_tb_pass")
                    else "Clarify the requirement or constraints before continuing."
                ),
                "generated_tb_pass": context.metadata.get("generated_tb_pass"),
                "contract_version": context.spec_ir.version if context.spec_ir else None,
                "questions": context.metadata.get("pending_clarifications", []),
            }
        )
        self._validate_response(context, response)
        context.metadata.setdefault("user_responses", []).append(response)
        action = response["action"]
        if action == "cancel":
            return self.operations._cancel(context)
        if action == "reject":
            return self.operations._fail(context, "USER_REJECTED", "User rejected the result")
        if action == "accept":
            context.failure_category = None
            context.metadata.update(final_status="manual_accepted", golden_tb_pass=False)
            return OperationResult("human_accepted")
        if context.metadata.get("pause_reason") == "synthesis_constraints":
            from rtlforge.domain.synthesis import SynthesisConstraints

            # The console collects a form; API clients send the same JSON object as message.
            values = json.loads(response["message"])
            previous = context.synthesis_constraints.to_dict()
            context.synthesis_constraints = SynthesisConstraints.from_dict({**previous, **values})
            context.metadata.pop("pause_reason", None)
            return OperationResult("answered_synthesis")
        if context.metadata.get("pause_reason") == "debugger_question":
            context.metadata["debugger_user_answer"] = response["message"]
            return OperationResult("answered_debug")
        self.operations.apply_spec_answer(context, response["message"])
        return OperationResult("answered_spec")

    def _run_generated_testbench(self, context: RTLTaskContext) -> dict:
        from rtlforge.integration import dependency_files

        path = context.metadata.get("generated_testbench_path")
        if not path or context.selected_candidate_id is None:
            context.metadata["generated_tb_pass"] = False
            return {"status": "ERROR", "category": "SYSTEM_ERROR"}
        try:
            result = self.generated_tb_runner.run_files(
                "simulate", [Path(path), Path(context.candidate().artifact_path),
                             *dependency_files(context)]
            )
            passed = result.get("status") == "PASS" and (
                "GENERATED_TB_PASS" in result.get("stdout", "")
            )
        except Exception as error:
            result = {"status": "ERROR", "category": "SYSTEM_ERROR", "reason": str(error)}
            passed = False
        evidence = (
            self.runs_root
            / context.task_id
            / f"generated_tb_result_{context.selected_candidate_id}.json"
        )
        evidence.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        context.metadata["generated_tb_pass"] = passed
        stdout = str(result.get("stdout", "")) if not passed else ""
        output_names = (
            [port.name for port in context.spec_ir.ports if port.direction is PortDirection.OUTPUT]
            if context.spec_ir is not None
            else []
        )
        value_mismatch = re.search(
            r"\b([A-Za-z_][A-Za-z0-9_]*)\s+mismatch\s*:\s*"
            r"got\s+([0-9A-Fa-f_xXzZ]+)\s+expected\s+([0-9A-Fa-f_xXzZ]+)",
            stdout,
            re.IGNORECASE,
        )
        first_value_mismatch = None
        output_lookup = {name.lower(): name for name in output_names}
        if value_mismatch and value_mismatch.group(1).lower() in output_lookup:
            observed, expected = value_mismatch.group(2), value_mismatch.group(3)
            if len(observed) <= 64 and len(expected) <= 64:
                first_value_mismatch = {
                    "output": output_lookup[value_mismatch.group(1).lower()],
                    "observed": observed,
                    "agent_tb_expected": expected,
                }
        advisory_attempt = {
            "candidate_id": context.selected_candidate_id,
            "advisory_feedback": {
                "category": result.get("category"),
                "mismatched_outputs": [
                    name
                    for name in output_names
                    if re.search(rf"\b{re.escape(name)}\s+mismatch\b", stdout, re.IGNORECASE)
                ],
                "first_value_mismatch": first_value_mismatch,
                "unasserted_outputs": [
                    name
                    for name in output_names
                    if re.search(rf"\b{re.escape(name)}\s+not asserted\b", stdout, re.IGNORECASE)
                ],
                "completion_timeout": bool(
                    re.search(
                        r"\bcompletion timeout\b|"
                        r"\btimeout\b[^\n]*(?:\bdone\b|\bcomplete\b|\b\d+\s+(?:clock\s+)?cycles\b)|"
                        r"\bexceeded\s+\d+\s+(?:clock\s+)?cycles\b|"
                        r"\bcycle limit exceeded\b",
                        stdout,
                        re.IGNORECASE,
                    )
                ),
            },
        }
        context.metadata.setdefault("generated_tb_attempts", []).append(advisory_attempt)
        self.operations._record(
            context,
            EventType.GENERATED_TB_CHECKED,
            {
                "passed": passed,
                "evidence_path": str(evidence),
                "advisory_feedback": advisory_attempt["advisory_feedback"],
            },
        )

        return result
