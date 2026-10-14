from __future__ import annotations

from uuid import uuid4

from verilog_agent.domain.enums import EventType, TaskStage
from verilog_agent.domain.models import (
    Budget,
    BudgetExceeded,
    Candidate,
    Event,
    RTLTaskContext,
    VerificationOutcome,
)
from verilog_agent.memory import RepairAttempt, SQLiteMemoryStore
from verilog_agent.orchestration.event_bus import EventBus
from verilog_agent.orchestration.interfaces import (
    CandidateEvaluator,
    Debugger,
    Generator,
    Planner,
    ReflectionRouter,
    Verifier,
)
from verilog_agent.orchestration.state_machine import transition
from verilog_agent.orchestration.storage import SQLiteTaskStore
from verilog_agent.providers import ModelProviderError
from verilog_agent.providers.usage import RecordingProvider, model_call_task
from verilog_agent.services.reflection_router import (
    AdaptiveReflectionRouter,
    ReflectionAction,
)


class Orchestrator:
    """Drive one task through explicit, checkpointed domain stages."""

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
        self._contexts: dict[str, RTLTaskContext] = {}

    def create_task(
        self,
        raw_spec: str,
        *,
        task_id: str | None = None,
        budget: Budget | None = None,
        metadata: dict | None = None,
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

    def run_task(self, task_id: str) -> RTLTaskContext:
        context = self._context(task_id)
        while not context.current_stage.is_terminal and (
            context.current_stage is not TaskStage.WAITING_FOR_USER
        ):
            self.advance_task(task_id)
        return context

    def advance_task(self, task_id: str) -> RTLTaskContext:
        """Advance one state-machine step so a worker can checkpoint between stages."""
        context = self._context(task_id)
        if context.current_stage.is_terminal:
            return context
        try:
            if context.cancel_requested:
                self._cancel(context)
            else:
                with model_call_task(context):
                    self._run_current_stage(context)
        except BudgetExceeded as error:
            self._budget_exhausted(context, str(error))
        except ModelProviderError as error:
            self._fail(context, "SYSTEM_ERROR", str(error))
        except Exception as error:
            self._fail(context, "ORCHESTRATION_ERROR", str(error))
        return context

    def _context(self, task_id: str) -> RTLTaskContext:
        if task_id in self._contexts:
            return self._contexts[task_id]
        return self.load_task(task_id)

    def _run_current_stage(self, context: RTLTaskContext) -> None:
        stage = context.current_stage
        if stage is TaskStage.CREATED:
            self._transition(context, TaskStage.SPECIFYING)
        elif stage is TaskStage.SPECIFYING:
            self._plan(context)
        elif stage is TaskStage.PLANNED:
            self._transition(context, TaskStage.GENERATING)
        elif stage is TaskStage.GENERATING:
            self._generate(context)
        elif stage is TaskStage.EVALUATING:
            self._evaluate(context)
        elif stage is TaskStage.VERIFYING:
            self._verify(context)
        elif stage is TaskStage.CLASSIFYING:
            self._classify(context)
        elif stage is TaskStage.REPAIRING:
            self._repair(context)
        elif stage is TaskStage.WAITING_FOR_USER:
            return self._fail(context, "USER_INPUT_REQUIRED", "task requires user input")
        else:
            raise RuntimeError(f"no orchestration handler for stage {stage.value}")

    def _plan(self, context: RTLTaskContext) -> None:
        plan = self.planner.plan(context)
        context.spec_ir = plan.spec_ir
        context.task_tree_root = plan.task_tree_root
        if self.memory_store is not None and self.memory_mode != "off":
            context.metadata["retrieved_design_patterns"] = (
                self.memory_store.retrieve_design_patterns(
                    module_family=self._module_family(context.raw_spec),
                    exclude_problem_id=context.metadata.get("benchmark_problem_id"),
                    limit=3,
                )
            )
        for call in context.metadata.get("read_tool_calls", []):
            self._record(context, EventType.READ_TOOL_CALLED, call)
        self._record(
            context,
            EventType.PLAN_DONE,
            {"module_name": plan.spec_ir.module_name, "spec_ir_version": plan.spec_ir.version},
        )
        self._transition(context, TaskStage.PLANNED)

    def _generate(self, context: RTLTaskContext) -> None:
        if context.budget.candidate_slots <= 0:
            raise BudgetExceeded("candidate budget exceeded")
        candidates = self.generator.generate(context, 1)
        if len(candidates) != 1:
            raise ValueError("sequential generation requires exactly one candidate")
        context.budget.record_candidates(len(candidates))
        context.candidates.extend(candidates)
        context.selected_candidate_id = candidates[0].candidate_id
        context.metadata["candidate_policy"] = "sequential_repair_first_v1"
        context.metadata.pop("last_repair_analysis", None)
        self._record(
            context,
            EventType.CANDIDATES_GENERATED,
            {"candidate_ids": [candidate.candidate_id for candidate in candidates]},
        )
        self._transition(context, TaskStage.EVALUATING)

    def _evaluate(self, context: RTLTaskContext) -> None:
        outcome = self.evaluator.evaluate(context)
        score = outcome.scores.get(outcome.selected_candidate_id)
        static_failure = not outcome.accepted or (score is not None and score < 75)
        infrastructure_failure = outcome.category in {
            "SYSTEM_ERROR", "TOOL_INCOMPATIBILITY", "PATH_VIOLATION", "INPUT_NOT_FOUND",
        }
        if static_failure and not infrastructure_failure and (
            self._regenerate(context, "static gate failure or static score below 75")
        ):
            return
        if outcome.accepted and score is not None and score < 75:
            context.selected_candidate_id = outcome.selected_candidate_id
            context.failure_category = "QUALITY_ERROR"
            self._record(context, EventType.EVAL_REJECTED, {"reason": "static score below 75"})
            self._transition(context, TaskStage.CLASSIFYING)
            return
        if not outcome.accepted or outcome.selected_candidate_id is None:
            self._record(
                context,
                EventType.EVAL_REJECTED,
                {"reason": outcome.reason, "category": outcome.category},
            )
            if outcome.selected_candidate_id is not None and outcome.category is not None:
                context.selected_candidate_id = outcome.selected_candidate_id
                context.failure_category = outcome.category
                self._transition(context, TaskStage.CLASSIFYING)
            else:
                self._fail(context, "EVALUATION_REJECTED", outcome.reason)
            return

        context.selected_candidate_id = outcome.selected_candidate_id
        self._record(
            context,
            EventType.EVAL_PASSED,
            {
                "selected_candidate_id": outcome.selected_candidate_id,
                "scores": outcome.scores,
            },
        )
        self._transition(context, TaskStage.VERIFYING)

    def _verify(self, context: RTLTaskContext) -> None:
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
                    module_family=self._module_family(context.raw_spec),
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
            self._transition(context, TaskStage.COMPLETED)
            self._record(
                context,
                EventType.SYSTEM_SUCCESS,
                {"selected_candidate_id": candidate.candidate_id},
            )
            return

        context.failure_category = outcome.category
        if self.memory_store is not None and self.memory_mode == "read_write":
            feedback = self._selected_debugger_feedback(context)
            error_item = self.memory_store.record_error_observation(
                task_id=context.task_id,
                candidate_id=candidate.candidate_id,
                error_signature=self._error_signature(context, feedback),
                module_family=self._module_family(context.raw_spec),
                source_problem_id=context.metadata.get("benchmark_problem_id"),
                summary=self._error_memory_summary(context, feedback),
                evidence_path=outcome.evidence_path,
            )
            context.metadata.setdefault("memory_consolidations", []).append(
                error_item.to_dict()
            )
        self._record(
            context,
            EventType.VERIFY_FAILED,
            {
                "candidate_id": candidate.candidate_id,
                "category": outcome.category,
                "evidence_path": outcome.evidence_path,
            },
        )
        self._transition(context, TaskStage.CLASSIFYING)

    def _classify(self, context: RTLTaskContext) -> None:
        candidate = context.candidate()
        if candidate.parent_id is not None:
            analysis = self._repair_progress(context, candidate)
            context.metadata["last_repair_analysis"] = analysis
            if analysis.get("no_progress"):
                self._record(
                    context, EventType.PATCH_ROLLED_BACK,
                    {"candidate_id": candidate.candidate_id, "restored_id": candidate.parent_id},
                )
                context.selected_candidate_id = candidate.parent_id
                if not self._regenerate(context, "repair made no progress"):
                    self._budget_exhausted(
                        context, "repair made no progress; no candidates remaining"
                    )
                return
        self._select_repair_target(context)
        feedback = self._selected_debugger_feedback(context)
        decision = self.router.route(context.failure_category, feedback)
        context.metadata["reflection_decision"] = decision.to_dict()
        self._record(context, EventType.REFLECTION_ROUTED, decision.to_dict())
        if decision.action is ReflectionAction.RECOVER_INFRASTRUCTURE:
            self._fail(
                context,
                context.failure_category or "SYSTEM_ERROR",
                f"{decision.reason} Debugger was not invoked.",
            )
            return
        if decision.action is ReflectionAction.WAIT_FOR_USER:
            self._transition(context, TaskStage.WAITING_FOR_USER)
            return
        if decision.invokes_debugger:
            self._retrieve_memories(context, feedback)
        if context.budget.can_repair:
            self._transition(context, TaskStage.REPAIRING)
            return

        candidate = context.candidate()
        if candidate.parent_id is not None:
            context.selected_candidate_id = candidate.parent_id
            self._record(
                context,
                EventType.PATCH_ROLLED_BACK,
                {"candidate_id": candidate.candidate_id, "restored_id": candidate.parent_id},
            )
        if not self._regenerate(context, "repair budget exhausted"):
            self._budget_exhausted(context, "repair budget exhausted")

    def _regenerate(self, context: RTLTaskContext, reason: str) -> bool:
        if context.budget.candidate_slots <= 0:
            return False
        context.metadata.setdefault("candidate_regenerations", []).append(
            {"after_candidate_id": context.selected_candidate_id, "reason": reason}
        )
        self._transition(context, TaskStage.GENERATING)
        return True

    @staticmethod
    def _selected_debugger_feedback(context: RTLTaskContext) -> dict:
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
            if candidate.parent_id is None
            and candidate.candidate_id not in repaired_parent_ids
        }
        order = context.metadata.get(
            "initial_verification_order",
            context.metadata.get("verification_order", []),
        )
        fallback_id = next(
            (candidate_id for candidate_id in order if candidate_id in roots), None
        )
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
        return {
            "candidate_id": candidate.candidate_id,
            "parent_id": candidate.parent_id,
            "before_mismatches": before_count,
            "after_mismatches": after_count,
            "mismatch_reduction": delta,
            "no_progress": delta is not None and delta <= 0,
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
            module_family=self._module_family(context.raw_spec),
            benchmark_problem_id=context.metadata.get("benchmark_problem_id"),
            before_mismatches=before_count,
            after_mismatches=after_count,
            mismatch_reduction=reduction,
            passed=outcome.passed,
            rationale=str(candidate.metadata.get("rationale") or ""),
            expected_impact=str(candidate.metadata.get("expected_impact") or ""),
            diff_path=candidate.metadata.get("diff_path"),
            evidence_path=outcome.evidence_path,
            memory_ids=list(candidate.metadata.get("memory_ids", [])),
        )
        attempts.append(
            {
                "attempt_number": context.budget.repair_attempts,
                **repair_attempt.to_dict(),
            }
        )
        if self.memory_store is not None and self.memory_mode == "read_write":
            item = self.memory_store.record_repair_attempt(repair_attempt)
            if item is not None:
                context.metadata.setdefault("memory_consolidations", []).append(
                    item.to_dict()
                )
            if repair_attempt.memory_ids and (outcome.passed or reduction is not None):
                self.memory_store.record_use(
                    repair_attempt.memory_ids,
                    positive=outcome.passed or bool(reduction and reduction > 0),
                )

    def _retrieve_memories(self, context: RTLTaskContext, feedback: dict) -> None:
        if self.memory_store is None or self.memory_mode == "off":
            context.metadata.pop("retrieved_memories", None)
            return
        memories = self.memory_store.retrieve(
            error_signature=self._error_signature(context, feedback),
            module_family=self._module_family(context.raw_spec),
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
    def _module_family(raw_spec: str) -> str:
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

    def _repair(self, context: RTLTaskContext) -> None:
        from verilog_agent.agents.validation import RepairNoProgressError

        context.budget.record_repair()
        parent = context.candidate()
        try:
            repaired = self.debugger.repair(context, parent)
        except RepairNoProgressError:
            self._record(context, EventType.PATCH_ROLLED_BACK, {
                "candidate_id": parent.candidate_id, "reason": "unchanged repair",
            })
            self._transition(context, TaskStage.CLASSIFYING)
            if not self._regenerate(context, "Debugger returned an unchanged candidate"):
                self._budget_exhausted(context, "unchanged repair; no candidates remaining")
            return
        if repaired.parent_id != parent.candidate_id:
            raise ValueError("repair candidate must reference its parent candidate")
        repaired.metadata["memory_ids"] = [
            item["memory_id"]
            for item in context.metadata.get("retrieved_memories", [])
            if isinstance(item, dict) and item.get("memory_id")
        ]
        context.candidates.append(repaired)
        context.selected_candidate_id = repaired.candidate_id
        self._record(
            context,
            EventType.REPAIR_PROPOSED,
            {"candidate_id": repaired.candidate_id, "parent_id": repaired.parent_id},
        )
        self._transition(context, TaskStage.EVALUATING)

    def _transition(self, context: RTLTaskContext, target: TaskStage) -> None:
        source = transition(context, target)
        self._record(
            context,
            EventType.STAGE_CHANGED,
            {"from": source.value, "to": target.value},
        )
        self._checkpoint(context)

    def _cancel(self, context: RTLTaskContext) -> None:
        self._transition(context, TaskStage.CANCELLED)
        self._record(context, EventType.TASK_CANCELLED)

    def _budget_exhausted(self, context: RTLTaskContext, reason: str) -> None:
        self._record(context, EventType.BUDGET_EXHAUSTED, {"reason": reason})
        self._fail(context, "BUDGET_EXHAUSTED", reason)

    def _fail(self, context: RTLTaskContext, category: str, reason: str) -> None:
        context.failure_category = category
        if not context.current_stage.is_terminal:
            self._transition(context, TaskStage.FAILED)
        self._record(context, EventType.SYSTEM_FAILED, {"category": category, "reason": reason})
        self._checkpoint(context)

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
