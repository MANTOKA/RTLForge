from __future__ import annotations

from uuid import uuid4

from verilog_agent.domain.enums import EventType, TaskStage
from verilog_agent.domain.models import Budget, BudgetExceeded, Event, RTLTaskContext
from verilog_agent.orchestration.event_bus import EventBus
from verilog_agent.orchestration.interfaces import (
    CandidateEvaluator,
    Debugger,
    Generator,
    Planner,
    Verifier,
)
from verilog_agent.orchestration.state_machine import transition
from verilog_agent.orchestration.storage import SQLiteTaskStore


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
    ) -> None:
        self.planner = planner
        self.generator = generator
        self.debugger = debugger
        self.evaluator = evaluator
        self.verifier = verifier
        self.store = store
        self.event_bus = event_bus or EventBus(store)
        self._contexts: dict[str, RTLTaskContext] = {}

    def create_task(
        self,
        raw_spec: str,
        *,
        task_id: str | None = None,
        budget: Budget | None = None,
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
        while not context.current_stage.is_terminal:
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
                self._run_current_stage(context)
        except BudgetExceeded as error:
            self._budget_exhausted(context, str(error))
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
        self._record(
            context,
            EventType.PLAN_DONE,
            {"module_name": plan.spec_ir.module_name, "spec_ir_version": plan.spec_ir.version},
        )
        self._transition(context, TaskStage.PLANNED)

    def _generate(self, context: RTLTaskContext) -> None:
        candidates = self.generator.generate(context, context.budget.candidate_slots)
        context.budget.record_candidates(len(candidates))
        context.candidates.extend(candidates)
        self._record(
            context,
            EventType.CANDIDATES_GENERATED,
            {"candidate_ids": [candidate.candidate_id for candidate in candidates]},
        )
        self._transition(context, TaskStage.EVALUATING)

    def _evaluate(self, context: RTLTaskContext) -> None:
        outcome = self.evaluator.evaluate(context)
        if not outcome.accepted or outcome.selected_candidate_id is None:
            self._record(context, EventType.EVAL_REJECTED, {"reason": outcome.reason})
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
        if outcome.passed:
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
        self._budget_exhausted(context, "repair budget exhausted")

    def _repair(self, context: RTLTaskContext) -> None:
        context.budget.record_repair()
        parent = context.candidate()
        repaired = self.debugger.repair(context, parent)
        if repaired.parent_id != parent.candidate_id:
            raise ValueError("repair candidate must reference its parent candidate")
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
