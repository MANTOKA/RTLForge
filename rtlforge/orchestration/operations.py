from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from rtlforge.domain.enums import EventType, TaskStage
from rtlforge.domain.models import (
    Budget,
    BudgetExceeded,
    Candidate,
    Event,
    RTLTaskContext,
    VerificationOutcome,
)
from rtlforge.domain.synthesis import SynthesisConstraints
from rtlforge.memory import RepairAttempt, SQLiteMemoryStore
from rtlforge.orchestration.event_bus import EventBus
from rtlforge.orchestration.interfaces import (
    CandidateEvaluator,
    Debugger,
    Generator,
    Planner,
    ReflectionRouter,
    Verifier,
)
from rtlforge.orchestration.storage import SQLiteTaskStore
from rtlforge.providers import ModelProviderError
from rtlforge.providers.usage import RecordingProvider, model_call_task
from rtlforge.services.reflection_router import AdaptiveReflectionRouter


class Outcome(str, Enum):
    PLANNED = "planned"
    GENERATED = "generated"
    EVALUATED = "evaluated"
    STATIC_PASSED = "static_passed"
    REJECTED = "rejected"
    VERIFIED = "verified"
    FUNCTIONALLY_VERIFIED = "functionally_verified"
    REPAIR_ALLOWED = "repair_allowed"
    REPAIRED = "repaired"
    REGENERATE = "regenerate"
    WAIT = "wait"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class OperationResult:
    outcome: Outcome | str
    details: dict[str, Any] = field(default_factory=dict)


class WorkflowOperations:
    """Shared RTL operations and policies; no scheduler or dispatch loop."""

    def __init__(
        self,
        *,
        planner: Planner,
        generator: Generator,
        debugger: Debugger,
        evaluator: CandidateEvaluator,
        verifier: Verifier,
        store: SQLiteTaskStore,
        event_bus: EventBus | None = None,
        router: ReflectionRouter | None = None,
        memory_store: SQLiteMemoryStore | None = None,
        memory_mode: str = "off",
        synthesis_runner=None,
    ) -> None:
        if memory_mode not in {"off", "read_only", "read_write"}:
            raise ValueError("memory_mode must be off, read_only, or read_write")
        if memory_mode != "off" and memory_store is None:
            raise ValueError("memory_store is required when memory is enabled")
        self.planner = planner
        self.generator = generator
        self.debugger = debugger
        for role, agent in (("planner", planner), ("generator", generator), ("debugger", debugger)):
            provider = getattr(agent, "provider", None)
            if provider is not None and not isinstance(provider, RecordingProvider):
                agent.provider = RecordingProvider(provider, role=role)
        self.evaluator = evaluator
        self.verifier = verifier
        self.store = store
        self.event_bus = event_bus or EventBus(store)
        self.router = router or AdaptiveReflectionRouter()
        self.memory_store = memory_store
        self.memory_mode = memory_mode
        self.synthesis_runner = synthesis_runner
        self._contexts: dict[str, RTLTaskContext] = {}

    def create_task(
        self,
        raw_spec: str,
        *,
        task_id: str | None = None,
        budget: Budget | None = None,
        metadata: dict | None = None,
        synthesis_constraints: SynthesisConstraints | None = None,
    ) -> RTLTaskContext:
        if not raw_spec.strip():
            raise ValueError("raw specification must not be empty")
        resolved_task_id = task_id or f"task_{uuid4().hex[:12]}"
        if self.store.load_latest_checkpoint(resolved_task_id) is not None:
            raise ValueError(f"task already exists: {resolved_task_id}")

        context = RTLTaskContext(
            task_id=resolved_task_id,
            raw_spec=raw_spec.strip(),
            budget=budget or Budget(),
            metadata=dict(metadata or {}),
            synthesis_constraints=synthesis_constraints,
        )
        self._contexts[resolved_task_id] = context
        self._record(context, EventType.TASK_CREATED, {"raw_spec": context.raw_spec})
        self._checkpoint(context)
        return context

    def load_task(self, task_id: str) -> RTLTaskContext:
        context = self.store.load_latest_checkpoint(task_id)
        if context is None:
            raise LookupError(f"task checkpoint not found: {task_id}")
        self._contexts[task_id] = context
        return context

    def request_cancel(self, task_id: str) -> RTLTaskContext:
        context = self._context(task_id)
        if context.current_stage.is_terminal:
            return context
        context.cancel_requested = True
        self._record(context, EventType.CANCEL_REQUESTED)
        self._checkpoint(context)
        return context

    def _context(self, task_id: str) -> RTLTaskContext:
        if task_id in self._contexts:
            return self._contexts[task_id]
        return self.load_task(task_id)

    def perform(self, action: str, context: RTLTaskContext) -> OperationResult:
        """Execute one action and return its domain outcome; never advance a scheduler."""
        from rtlforge.agents.validation import AgentOutputError

        handlers = {
            "plan": self._plan,
            "generate": self._generate,
            "evaluate": self._evaluate,
            "verify": self._verify,
            "synthesize": self._synthesize,
            "repair": self._repair,
        }
        contract = context.spec_ir.to_dict() if context.spec_ir is not None else None
        synthesis = (context.synthesis_constraints.to_dict()
                     if context.synthesis_constraints is not None else None)
        try:
            with model_call_task(context):
                result = handlers[action](context)
            if action not in {"plan", "synthesize"} and synthesis != (
                context.synthesis_constraints.to_dict() if context.synthesis_constraints else None
            ):
                context.synthesis_constraints = (
                    SynthesisConstraints.from_dict(synthesis) if synthesis is not None else None
                )
                return self._fail(context, "CONTRACT_VIOLATION", "Operation changed constraints")
            if (
                action != "plan"
                and contract is not None
                and (context.spec_ir is None or context.spec_ir.to_dict() != contract)
            ):
                from rtlforge.domain.models import SpecIR

                context.spec_ir = SpecIR.from_dict(contract)
                return self._fail(context, "CONTRACT_VIOLATION", "Operation changed the contract")
            return result
        except BudgetExceeded as error:
            return self._budget_exhausted(context, str(error))
        except ModelProviderError as error:
            return self._fail(context, "SYSTEM_ERROR", str(error))
        except AgentOutputError as error:
            category = "PLANNER_OUTPUT_ERROR" if action == "plan" else "ORCHESTRATION_ERROR"
            return self._fail(context, category, str(error))
        except Exception as error:
            return self._fail(context, "ORCHESTRATION_ERROR", str(error))

    def _plan(self, context: RTLTaskContext) -> OperationResult:
        previous_version = context.spec_ir.version if context.spec_ir is not None else 0
        previous_tool_calls = len(context.metadata.get("read_tool_calls", []))
        plan = self.planner.plan(context)
        plan.spec_ir.version = previous_version + 1
        context.spec_ir = plan.spec_ir
        context.task_tree_root = plan.task_tree_root
        questions = plan.spec_ir.missing_semantics()
        context.metadata.setdefault("contract_history", []).append({
            "version": plan.spec_ir.version,
            "status": "draft" if questions else "ready",
            "contract": plan.spec_ir.to_dict(),
        })
        self._record(context, EventType.CONTRACT_UPDATED, {
            "previous_version": previous_version, "version": plan.spec_ir.version,
            "status": "draft" if questions else "ready",
        })
        for call in context.metadata.get("read_tool_calls", [])[previous_tool_calls:]:
            self._record(context, EventType.READ_TOOL_CALLED, call)
        if questions:
            context.metadata.update(
                pending_clarifications=questions,
                pause_reason="contract_clarification",
                clarification_question="\n".join(
                    f"{index}. {row['question']}" for index, row in enumerate(questions, 1)
                ),
            )
            self._record(context, EventType.CLARIFICATION_REQUESTED, {
                "contract_version": plan.spec_ir.version, "questions": questions,
            })
            return OperationResult(Outcome.WAIT, {"questions": questions})
        context.metadata.pop("pending_clarifications", None)
        context.metadata.pop("pause_reason", None)
        context.metadata.pop("clarification_question", None)
        if self.memory_store is not None and self.memory_mode != "off":
            context.metadata["retrieved_design_patterns"] = (
                self.memory_store.retrieve_design_patterns(
                    module_family=self._module_family(context.raw_spec, plan.spec_ir.module_name),
                    exclude_problem_id=context.metadata.get("benchmark_problem_id"),
                    limit=3,
                )
            )
        self._record(
            context,
            EventType.PLAN_DONE,
            {"module_name": plan.spec_ir.module_name, "spec_ir_version": plan.spec_ir.version},
        )
        return OperationResult(Outcome.PLANNED)

    def apply_spec_answer(self, context: RTLTaskContext, message: str) -> None:
        """Keep the draft and its questions for replanning; invalidate dependent evidence."""
        context.raw_spec += "\nUser clarification: " + message
        self._record(context, EventType.CLARIFICATION_ANSWERED, {
            "contract_version": context.spec_ir.version if context.spec_ir else None,
            "message": message,
            "questions": context.metadata.get("pending_clarifications", []),
        })
        for key in (
            "generated_testbench_path", "generated_test_objectives", "generated_tb_contract_sha256",
            "generated_tb_pass", "golden_tb_pass", "final_status", "clarification_question",
            "debug_observations", "pending_debug_action", "retrieved_memories",
            "verification_attempts", "reflection_decision", "last_repair_analysis",
            "verification_report", "static_evaluation_report", "verification_order",
            "initial_verification_order",
        ):
            context.metadata.pop(key, None)
        context.failure_category = None
        context.selected_candidate_id = None
        # Retain pause_reason until Planner sees the pending questions; budgets never reset.

    def _generate(self, context: RTLTaskContext) -> OperationResult:
        if context.spec_ir is not None and context.spec_ir.missing_semantics():
            raise ValueError("Cannot generate RTL from an unresolved hardware contract")
        if context.budget.candidate_slots <= 0:
            raise BudgetExceeded("candidate budget exceeded")
        candidates = self.generator.generate(context, 1)
        if len(candidates) != 1:
            raise ValueError("sequential generation requires exactly one candidate")
        context.budget.record_candidates(len(candidates))
        context.candidates.extend(candidates)
        for candidate in candidates:
            candidate.metadata["contract_version"] = context.spec_ir.version
        context.selected_candidate_id = candidates[0].candidate_id
        context.metadata["candidate_policy"] = "sequential_repair_first_v1"
        context.metadata.pop("last_repair_analysis", None)
        self._record(
            context,
            EventType.CANDIDATES_GENERATED,
            {"candidate_ids": [candidate.candidate_id for candidate in candidates]},
        )
        return OperationResult(Outcome.GENERATED)

    def _evaluate(self, context: RTLTaskContext) -> OperationResult:
        outcome = self.evaluator.evaluate(context)
        return OperationResult(Outcome.EVALUATED, asdict(outcome))

    def _verify(self, context: RTLTaskContext) -> OperationResult:
        candidate = context.candidate()
        outcome = self.verifier.verify(context, candidate)
        selected_candidate_id = outcome.details.get("selected_candidate_id")
        if selected_candidate_id is not None:
            context.selected_candidate_id = selected_candidate_id
            candidate = context.candidate(selected_candidate_id)
        if candidate.parent_id is not None:
            self._record_repair_attempt(context, candidate, outcome)
        if outcome.passed:
            if (
                self.memory_store is not None
                and self.memory_mode == "read_write"
                and candidate.parent_id is None
            ):
                design_item = self.memory_store.record_verified_design_pattern(
                    task_id=context.task_id,
                    candidate_id=candidate.candidate_id,
                    module_family=self._module_family(
                        context.raw_spec, context.spec_ir.module_name if context.spec_ir else None
                    ),
                    source_problem_id=context.metadata.get("benchmark_problem_id"),
                    design_pattern=candidate.design_pattern,
                    evidence_path=outcome.evidence_path,
                )
                if design_item is not None:
                    context.metadata.setdefault("memory_consolidations", []).append(
                        design_item.to_dict()
                    )
            context.failure_category = None
            self._record(
                context,
                EventType.VERIFY_PASSED,
                {
                    "candidate_id": candidate.candidate_id,
                    "evidence_path": outcome.evidence_path,
                },
            )
            if candidate.parent_id is not None:
                self._record(
                    context,
                    EventType.PATCH_ACCEPTED,
                    {"candidate_id": candidate.candidate_id, "parent_id": candidate.parent_id},
                )
            context.metadata["functional_verified_candidate"] = candidate.candidate_id
            if context.synthesis_constraints is not None:
                context.metadata["golden_tb_pass"] = True
                return OperationResult(Outcome.FUNCTIONALLY_VERIFIED)
            return OperationResult(Outcome.VERIFIED)

        context.failure_category = outcome.category
        if self.memory_store is not None and self.memory_mode == "read_write":
            feedback = self._selected_debugger_feedback(context)
            error_item = self.memory_store.record_error_observation(
                task_id=context.task_id,
                candidate_id=candidate.candidate_id,
                error_signature=self._error_signature(context, feedback),
                module_family=self._module_family(
                    context.raw_spec, context.spec_ir.module_name if context.spec_ir else None
                ),
                source_problem_id=context.metadata.get("benchmark_problem_id"),
                summary=self._error_memory_summary(context, feedback),
                evidence_path=outcome.evidence_path,
            )
            context.metadata.setdefault("memory_consolidations", []).append(error_item.to_dict())
        self._record(
            context,
            EventType.VERIFY_FAILED,
            {
                "candidate_id": candidate.candidate_id,
                "category": outcome.category,
                "evidence_path": outcome.evidence_path,
            },
        )
        return OperationResult(Outcome.REJECTED)

    def _synthesize(self, context: RTLTaskContext) -> OperationResult:
        constraints = context.synthesis_constraints
        if constraints is None:
            return OperationResult(Outcome.VERIFIED)
        candidate = context.candidate()
        if context.metadata.get("functional_verified_candidate") != candidate.candidate_id:
            return self._fail(context, "POLICY_VIOLATION",
                              "Synthesis needs fresh functional checks")
        if context.spec_ir:
            if constraints.top and constraints.top != context.spec_ir.module_name:
                return self._fail(context, "SYNTHESIS_CONSTRAINT_ERROR", "Top differs from SpecIR")
            constraints.top = constraints.top or context.spec_ir.module_name
            constraints.clock_port = constraints.clock_port or context.spec_ir.clock
            if constraints.clock_port and not any(
                port.name == constraints.clock_port and port.direction.value == "input"
                and port.width == 1 for port in context.spec_ir.ports
            ):
                return self._fail(context, "SYNTHESIS_CONSTRAINT_ERROR", "Invalid clock port")
        questions = constraints.missing()
        if questions:
            context.metadata.update(
                pause_reason="synthesis_constraints",
                clarification_question="\n".join(questions),
            )
            return OperationResult(Outcome.WAIT)
        if self.synthesis_runner is None:
            return self._fail(context, "SYNTHESIS_TOOL_ERROR", "No Vivado MCP runner configured")
        result = self.synthesis_runner.synthesize(context, candidate)
        candidate.metadata["synthesis_result"] = result.to_dict()
        context.metadata.setdefault("synthesis_history", []).append(result.to_dict())
        self._record(context, EventType.SYNTHESIS_CHECKED, result.feedback())
        if result.status != "PASS":
            context.failure_category = result.category
            context.metadata.pop("final_status", None)
            if result.category == "SYNTHESIS_CONSTRAINT_ERROR":
                context.metadata.update(
                    pause_reason="synthesis_constraints",
                    clarification_question="综合时序约束不完整，请核对时钟定义与 XDC。\n"
                    + "\n".join(result.diagnostics),
                )
                return OperationResult(Outcome.WAIT)
            return OperationResult(Outcome.REJECTED)
        context.failure_category = None
        if context.metadata.get("golden_tb_pass"):
            return OperationResult(Outcome.VERIFIED)
        context.metadata.update(
            final_status="generated_tb_pass_await_manual", pause_reason="generated_tb_review",
        )
        return OperationResult(Outcome.WAIT)

    def _regenerate(self, context: RTLTaskContext, reason: str) -> bool:
        if context.budget.candidate_slots <= 0:
            return False
        context.metadata.setdefault("candidate_regenerations", []).append(
            {"after_candidate_id": context.selected_candidate_id, "reason": reason}
        )
        return True

    @staticmethod
    def _selected_debugger_feedback(context: RTLTaskContext) -> dict:
        synthesis = (context.candidate().metadata.get("synthesis_result", {})
                     if context.selected_candidate_id else {})
        if (context.failure_category or "").startswith("SYNTHESIS_") and synthesis:
            return {"category": context.failure_category, "synthesis": {
                key: value for key, value in synthesis.items()
                if key not in {"evidence", "mcp_server_info", "mcp_server_sha256"}
            }}
        attempts = context.metadata.get("verification_attempts", [])
        if isinstance(attempts, list):
            for attempt in attempts:
                if (
                    isinstance(attempt, dict)
                    and attempt.get("candidate_id") == context.selected_candidate_id
                    and isinstance(attempt.get("debugger_feedback"), dict)
                ):
                    return dict(attempt["debugger_feedback"])
        history = context.metadata.get("verification_history", [])
        if isinstance(history, list):
            for cycle in reversed(history):
                if not isinstance(cycle, dict):
                    continue
                for attempt in cycle.get("attempts", []):
                    if (
                        isinstance(attempt, dict)
                        and attempt.get("candidate_id") == context.selected_candidate_id
                        and isinstance(attempt.get("debugger_feedback"), dict)
                    ):
                        return dict(attempt["debugger_feedback"])
        return {}

    def _select_repair_target(self, context: RTLTaskContext) -> None:
        current = context.candidate()
        if current.parent_id is None:
            return
        analysis = self._repair_progress(context, current)
        context.metadata["last_repair_analysis"] = analysis
        if not analysis.get("no_progress"):
            return
        repaired_parent_ids = {
            candidate.parent_id
            for candidate in context.candidates
            if candidate.parent_id is not None
        }
        roots = {
            candidate.candidate_id: candidate
            for candidate in context.candidates
            if candidate.parent_id is None and candidate.candidate_id not in repaired_parent_ids
        }
        order = context.metadata.get(
            "initial_verification_order",
            context.metadata.get("verification_order", []),
        )
        fallback_id = next((candidate_id for candidate_id in order if candidate_id in roots), None)
        if fallback_id is None:
            return
        context.selected_candidate_id = fallback_id
        fallback_feedback = self._selected_debugger_feedback(context)
        context.failure_category = str(
            fallback_feedback.get("category") or context.failure_category
        )
        context.metadata["repair_fallback"] = {
            "from_candidate_id": current.candidate_id,
            "to_candidate_id": fallback_id,
            "reason": "previous repair made no functional mismatch improvement",
        }

    def _repair_progress(self, context: RTLTaskContext, candidate: Candidate) -> dict:
        before = self._debugger_feedback_for(context, candidate.parent_id)
        after = self._debugger_feedback_for(context, candidate.candidate_id)
        before_count = self._mismatch_count(before)
        after_count = self._mismatch_count(after)
        delta = (
            before_count - after_count
            if before_count is not None and after_count is not None
            else None
        )
        parent = context.candidate(candidate.parent_id)
        before_report = parent.metadata.get("static_evaluation", {})
        after_report = candidate.metadata.get("static_evaluation", {})
        before_score = (
            before_report.get("quality_score") if isinstance(before_report, dict) else None
        )
        after_score = after_report.get("quality_score") if isinstance(after_report, dict) else None
        score_gain = (
            float(after_score) - float(before_score)
            if isinstance(before_score, (int, float)) and isinstance(after_score, (int, float))
            else None
        )
        return {
            "candidate_id": candidate.candidate_id,
            "parent_id": candidate.parent_id,
            "before_mismatches": before_count,
            "after_mismatches": after_count,
            "mismatch_reduction": delta,
            "before_static_score": before_score,
            "after_static_score": after_score,
            "static_score_gain": score_gain,
            "no_progress": (
                (delta is not None and delta <= 0)
                or (
                    context.failure_category == "QUALITY_ERROR"
                    and score_gain is not None
                    and score_gain <= 0
                )
            ),
            "previous_rationale": candidate.metadata.get("rationale"),
            "previous_expected_impact": candidate.metadata.get("expected_impact"),
        }

    @staticmethod
    def _debugger_feedback_for(context: RTLTaskContext, candidate_id: str | None) -> dict:
        if candidate_id is None:
            return {}
        history = context.metadata.get("verification_history", [])
        if isinstance(history, list):
            for cycle in reversed(history):
                if not isinstance(cycle, dict):
                    continue
                for attempt in cycle.get("attempts", []):
                    if (
                        isinstance(attempt, dict)
                        and attempt.get("candidate_id") == candidate_id
                        and isinstance(attempt.get("debugger_feedback"), dict)
                    ):
                        return dict(attempt["debugger_feedback"])
        attempts = context.metadata.get("verification_attempts", [])
        if isinstance(attempts, list):
            for attempt in attempts:
                if (
                    isinstance(attempt, dict)
                    and attempt.get("candidate_id") == candidate_id
                    and isinstance(attempt.get("debugger_feedback"), dict)
                ):
                    return dict(attempt["debugger_feedback"])
        return {}

    @staticmethod
    def _mismatch_count(feedback: dict) -> int | None:
        value = feedback.get("mismatch_summary", {}).get("total_mismatched_samples")
        return int(value) if isinstance(value, int) else None

    def _record_repair_attempt(
        self,
        context: RTLTaskContext,
        candidate: Candidate,
        outcome: VerificationOutcome,
    ) -> None:
        before = self._debugger_feedback_for(context, candidate.parent_id)
        after = self._debugger_feedback_for(context, candidate.candidate_id)
        before_count = self._mismatch_count(before)
        after_count = self._mismatch_count(after)
        attempts = context.metadata.setdefault("repair_attempts", [])
        if not isinstance(attempts, list):
            return
        reduction = (
            before_count - after_count
            if before_count is not None and after_count is not None
            else None
        )
        repair_attempt = RepairAttempt(
            task_id=context.task_id,
            candidate_id=candidate.candidate_id,
            parent_candidate_id=str(candidate.parent_id),
            failure_category=str(context.failure_category or outcome.category),
            error_signature=self._error_signature(context, after or before),
            module_family=self._module_family(
                context.raw_spec, context.spec_ir.module_name if context.spec_ir else None
            ),
            benchmark_problem_id=context.metadata.get("benchmark_problem_id"),
            before_mismatches=before_count,
            after_mismatches=after_count,
            mismatch_reduction=reduction,
            passed=outcome.passed,
            rationale=(
                ""
                if candidate.metadata.get("format_fallback")
                else str(candidate.metadata.get("rationale") or "")
            ),
            expected_impact=(
                ""
                if candidate.metadata.get("format_fallback")
                else str(candidate.metadata.get("expected_impact") or "")
            ),
            diff_path=candidate.metadata.get("diff_path"),
            evidence_path=outcome.evidence_path,
            memory_ids=list(candidate.metadata.get("memory_ids", [])),
        )
        attempts.append(
            {
                "attempt_number": context.budget.repair_attempts,
                **repair_attempt.to_dict(),
                "format_fallback": candidate.metadata.get("format_fallback"),
            }
        )
        if self.memory_store is not None and self.memory_mode == "read_write":
            item = self.memory_store.record_repair_attempt(repair_attempt)
            if item is not None:
                context.metadata.setdefault("memory_consolidations", []).append(item.to_dict())
            if repair_attempt.memory_ids and (outcome.passed or reduction is not None):
                self.memory_store.record_use(
                    repair_attempt.memory_ids,
                    positive=outcome.passed or bool(reduction and reduction > 0),
                )

    def _retrieve_memories(
        self, context: RTLTaskContext, feedback: dict,
        *, memory_types: tuple[str, ...] | None = None,
    ) -> None:
        if self.memory_store is None or self.memory_mode == "off":
            context.metadata.pop("retrieved_memories", None)
            return
        memories = self.memory_store.retrieve(
            error_signature=self._error_signature(context, feedback),
            module_family=self._module_family(
                context.raw_spec, context.spec_ir.module_name if context.spec_ir else None
            ),
            **({"memory_types": memory_types} if memory_types is not None else {}),
            exclude_problem_id=context.metadata.get("benchmark_problem_id"),
            limit=3,
        )
        context.metadata["retrieved_memories"] = memories

    @staticmethod
    def _error_signature(context: RTLTaskContext, feedback: dict) -> str:
        outputs = feedback.get("mismatch_summary", {}).get("outputs", [])
        output_names = sorted(
            str(item.get("output"))
            for item in outputs
            if isinstance(item, dict) and item.get("output")
        )
        parts = [str(context.failure_category or feedback.get("category") or "UNKNOWN")]
        if output_names:
            parts.append("outputs=" + ",".join(output_names))
        temporal_pattern = feedback.get("mismatch_summary", {}).get("temporal_pattern")
        if temporal_pattern:
            parts.append(f"pattern={temporal_pattern}")
        return " ".join(parts)

    @staticmethod
    def _error_memory_summary(context: RTLTaskContext, feedback: dict) -> str:
        mismatch = feedback.get("mismatch_summary", {})
        pattern = mismatch.get("temporal_pattern")
        count = mismatch.get("total_mismatched_samples")
        samples = mismatch.get("total_samples")
        detail = f"{count}/{samples} mismatched samples" if count is not None else "failure"
        if pattern:
            detail += f", temporal pattern {pattern}"
        return f"Observed {context.failure_category or 'UNKNOWN'}: {detail}."

    @staticmethod
    def _module_family(raw_spec: str, module_name: str | None = None) -> str:
        name = (module_name or "").lower()
        if name.startswith("rs"):
            if "syndrome" in name:
                return "rs_syndrome"
            if "kes" in name:
                return "rs_kes_ribm" if "ribm" in name else "rs_kes_bm"
            if "chien" in name or "forney" in name:
                return "rs_chien_forney_ribm" if "ribm" in name else "rs_chien_forney_bm"
            if "decoder" in name:
                return "rs_decoder_top_ribm" if "ribm" in name else "rs_decoder_top_bm"
        lowered = raw_spec.lower()
        for family, terms in (
            ("fsm", ("state machine", "fsm", "状态机")),
            ("counter", ("counter", "计数器")),
            ("fifo", ("fifo", "queue", "队列")),
            ("shift_register", ("shift register", "移位寄存器")),
            ("flip_flop", ("flip-flop", "flip flop", "flipflop", "dff", "触发器")),
            ("arithmetic", ("adder", "subtractor", "multiplier", "加法", "乘法")),
        ):
            if any(term in lowered for term in terms):
                return family
        return "generic_rtl"

    def _repair(self, context: RTLTaskContext) -> OperationResult:
        from rtlforge.agents.validation import RepairNoProgressError

        context.budget.record_repair()
        parent = context.candidate()
        try:
            repaired = self.debugger.repair(context, parent)
        except RepairNoProgressError:
            self._record(
                context,
                EventType.PATCH_ROLLED_BACK,
                {
                    "candidate_id": parent.candidate_id,
                    "reason": "unchanged repair",
                },
            )
            context.metadata["last_repair_analysis"] = {
                "no_progress": True,
                "reason": "unchanged_rtl",
                "candidate_id": parent.candidate_id,
                "attempt_number": context.budget.repair_attempts,
            }
            return OperationResult(Outcome.REJECTED)
        if repaired.parent_id != parent.candidate_id:
            raise ValueError("repair candidate must reference its parent candidate")
        repaired.metadata["memory_ids"] = [
            item["memory_id"]
            for item in context.metadata.get("retrieved_memories", [])
            if isinstance(item, dict) and item.get("memory_id")
        ]
        context.candidates.append(repaired)
        context.selected_candidate_id = repaired.candidate_id
        for key in ("golden_tb_pass", "functional_verified_candidate", "final_status"):
            context.metadata.pop(key, None)
        self._record(
            context,
            EventType.REPAIR_PROPOSED,
            {"candidate_id": repaired.candidate_id, "parent_id": repaired.parent_id},
        )
        return OperationResult(Outcome.REPAIRED)

    def set_stage(self, context: RTLTaskContext, target: TaskStage) -> None:
        """Mirror a scheduler-selected stage into the permanent audit projection."""
        source = context.current_stage
        if source is not target:
            context.current_stage = target
            self._record(
                context, EventType.STAGE_CHANGED, {"from": source.value, "to": target.value}
            )

    def _cancel(self, context: RTLTaskContext) -> OperationResult:
        self._record(context, EventType.TASK_CANCELLED)
        return OperationResult(Outcome.CANCELLED)

    def _budget_exhausted(self, context: RTLTaskContext, reason: str) -> OperationResult:
        self._record(context, EventType.BUDGET_EXHAUSTED, {"reason": reason})
        return self._fail(context, "BUDGET_EXHAUSTED", reason)

    def _fail(self, context: RTLTaskContext, category: str, reason: str) -> OperationResult:
        context.failure_category = category
        self._record(context, EventType.SYSTEM_FAILED, {"category": category, "reason": reason})
        return OperationResult(Outcome.FAILED, {"category": category, "reason": reason})

    def _record(
        self,
        context: RTLTaskContext,
        event_type: EventType,
        payload: dict | None = None,
    ) -> Event:
        return self.event_bus.publish(
            Event(
                task_id=context.task_id,
                event_type=event_type,
                stage=context.current_stage,
                payload=payload or {},
            )
        )

    def _checkpoint(self, context: RTLTaskContext) -> int:
        return self.store.save_checkpoint(context)
