from __future__ import annotations

from rtlforge.domain.enums import EventType, TaskStage
from rtlforge.domain.models import RTLTaskContext
from rtlforge.orchestration.operations import Outcome, WorkflowOperations
from rtlforge.orchestration.policy import WorkflowPolicy
from rtlforge.orchestration.state_machine import transition

_OUTCOME_STAGE = {
    Outcome.PLANNED: TaskStage.PLANNED,
    Outcome.GENERATED: TaskStage.EVALUATING,
    Outcome.REPAIRED: TaskStage.EVALUATING,
    Outcome.STATIC_PASSED: TaskStage.VERIFYING,
    Outcome.REJECTED: TaskStage.CLASSIFYING,
    Outcome.VERIFIED: TaskStage.COMPLETED,
    Outcome.FUNCTIONALLY_VERIFIED: TaskStage.SYNTHESIZING,
    Outcome.REPAIR_ALLOWED: TaskStage.REPAIRING,
    Outcome.REGENERATE: TaskStage.GENERATING,
    Outcome.WAIT: TaskStage.WAITING_FOR_USER,
    Outcome.FAILED: TaskStage.FAILED,
    Outcome.CANCELLED: TaskStage.CANCELLED,
}
_STAGE_ACTION = {
    TaskStage.SPECIFYING: "plan",
    TaskStage.GENERATING: "generate",
    TaskStage.EVALUATING: "evaluate",
    TaskStage.VERIFYING: "verify",
    TaskStage.SYNTHESIZING: "synthesize",
    TaskStage.CLASSIFYING: "classify",
    TaskStage.REPAIRING: "repair",
}


class Orchestrator(WorkflowOperations):
    """Compatibility scheduler for the legacy entrypoint and baseline experiments."""

    def run_task(self, task_id: str) -> RTLTaskContext:
        context = self._context(task_id)
        while not context.current_stage.is_terminal and (
            context.current_stage is not TaskStage.WAITING_FOR_USER
        ):
            self.advance_task(task_id)
        return context

    def resume_task(self, task_id: str, response: dict) -> RTLTaskContext:
        context = self._context(task_id)
        if context.current_stage is not TaskStage.WAITING_FOR_USER:
            raise ValueError("Task is not paused for user input")
        if not isinstance(response, dict) or response.get("action") != "answer":
            raise ValueError("Expected an answer to the pending requirement question")
        message = response.get("message")
        if not isinstance(message, str) or not message.strip() or len(message) > 20000:
            raise ValueError("Expected a nonempty answer of at most 20000 characters")
        context.metadata.setdefault("user_responses", []).append(response)
        if context.metadata.get("pause_reason") == "synthesis_constraints":
            import json

            from rtlforge.domain.synthesis import SynthesisConstraints

            context.synthesis_constraints = SynthesisConstraints.from_dict({
                **context.synthesis_constraints.to_dict(), **json.loads(message),
            })
            self._transition(context, TaskStage.SYNTHESIZING)
            return self.run_task(task_id)
        self.apply_spec_answer(context, message)
        self._transition(context, TaskStage.SPECIFYING)
        return self.run_task(task_id)

    def advance_task(self, task_id: str) -> RTLTaskContext:
        context = self._context(task_id)
        if context.current_stage.is_terminal:
            return context
        if context.cancel_requested:
            result = self._cancel(context)
        elif context.current_stage is TaskStage.CREATED:
            self._transition(context, TaskStage.SPECIFYING)
            return context
        elif context.current_stage is TaskStage.PLANNED:
            self._transition(context, TaskStage.GENERATING)
            return context
        elif context.current_stage is TaskStage.WAITING_FOR_USER:
            return context
        else:
            policy = WorkflowPolicy(self)
            if context.current_stage is TaskStage.CLASSIFYING:
                result = policy.after_failure(context)
                if result.outcome is Outcome.REPAIR_ALLOWED:
                    self._retrieve_memories(context, self._selected_debugger_feedback(context))
            else:
                result = self.perform(_STAGE_ACTION[context.current_stage], context)
                if result.outcome is Outcome.EVALUATED:
                    result = policy.after_static(context, result.details)
        target = _OUTCOME_STAGE[result.outcome]
        if context.current_stage is TaskStage.REPAIRING and target is TaskStage.GENERATING:
            self._transition(context, TaskStage.CLASSIFYING)
        self._transition(context, target)
        if result.outcome is Outcome.VERIFIED:
            self._record(
                context,
                EventType.SYSTEM_SUCCESS,
                {
                    "selected_candidate_id": context.selected_candidate_id,
                },
            )
        self._checkpoint(context)
        return context

    def _transition(self, context: RTLTaskContext, target: TaskStage) -> None:
        source = transition(context, target)
        self._record(context, EventType.STAGE_CHANGED, {"from": source.value, "to": target.value})
        self._checkpoint(context)
