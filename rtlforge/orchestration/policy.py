"""Deterministic RTL routing policy shared by the legacy and graph schedulers."""

from __future__ import annotations

from enum import Enum

from rtlforge.domain.enums import EventType
from rtlforge.domain.models import EvaluationOutcome, RTLTaskContext
from rtlforge.orchestration.operations import OperationResult, Outcome, WorkflowOperations
from rtlforge.services.reflection_router import ReflectionAction


class DebugAction(str, Enum):
    READ_DIAGNOSTICS = "read_diagnostics"
    READ_RTL = "read_rtl"
    READ_SYNTHESIS_REPORT = "read_synthesis_report"
    RUN_SYNTHESIS = "run_synthesis"
    RETRIEVE_MEMORY = "retrieve_memory"
    RETRIEVE_REPAIR_STRATEGIES = "retrieve_repair_strategies"
    RETRIEVE_ERROR_PATTERNS = "retrieve_error_patterns"
    RETRIEVE_PROJECT_RULES = "retrieve_project_rules"
    REPAIR = "repair"
    ASK_USER = "ask_user"
    STOP = "stop"


def validate_debug_action(context: RTLTaskContext, decision: dict, limit: int) -> DebugAction:
    """The model proposes; code enforces permissions, budgets and valid targets."""
    if not isinstance(decision, dict):
        raise ValueError("Debugger decision must be an object")
    action = DebugAction(decision.get("action"))
    if not isinstance(decision.get("reason"), str) or not decision["reason"].strip():
        raise ValueError("Debugger must state a reason")
    if context.metadata.get("debug_action_count", 0) >= limit:
        raise ValueError("Debugger action budget exhausted")
    reflection = context.metadata.get("reflection_decision", {})
    if not reflection.get("invokes_debugger"):
        raise ValueError("This failure does not allow Debugger actions")
    context.candidate()
    if action is DebugAction.RUN_SYNTHESIS and (
        context.synthesis_constraints is None
        or context.metadata.get("functional_verified_candidate") != context.selected_candidate_id
    ):
        raise ValueError("Synthesis needs configured constraints and fresh functional checks")
    if action is DebugAction.READ_SYNTHESIS_REPORT and not context.candidate().metadata.get(
        "synthesis_result"
    ):
        raise ValueError("No synthesis report for the current candidate")
    if action is DebugAction.REPAIR and not context.budget.can_repair:
        raise ValueError("Repair budget exhausted")
    return action


class WorkflowPolicy:
    """Choose allowed domain actions; never run a scheduler or invoke an LLM."""

    def __init__(self, operations: WorkflowOperations) -> None:
        self.operations = operations

    def after_static(self, context: RTLTaskContext, result: dict) -> OperationResult:
        ops = self.operations
        outcome = EvaluationOutcome(**result)
        score = outcome.scores.get(outcome.selected_candidate_id)
        static_failure = not outcome.accepted or (score is not None and score < 75)
        infrastructure_failure = outcome.category in {
            "SYSTEM_ERROR",
            "TOOL_INCOMPATIBILITY",
            "PATH_VIOLATION",
            "INPUT_NOT_FOUND",
        }
        selected = (
            context.candidate(outcome.selected_candidate_id)
            if outcome.selected_candidate_id is not None else None
        )
        if (
            static_failure
            and not infrastructure_failure
            and (selected is None or selected.parent_id is None)
            and (ops._regenerate(context, "static gate failure or static score below 75"))
        ):
            return OperationResult(Outcome.REGENERATE)
        if outcome.accepted and score is not None and score < 75:
            context.selected_candidate_id = outcome.selected_candidate_id
            context.failure_category = "QUALITY_ERROR"
            ops._record(context, EventType.EVAL_REJECTED, {"reason": "static score below 75"})
            return OperationResult(Outcome.REJECTED)
        if not outcome.accepted or outcome.selected_candidate_id is None:
            ops._record(
                context,
                EventType.EVAL_REJECTED,
                {"reason": outcome.reason, "category": outcome.category},
            )
            if outcome.selected_candidate_id is not None and outcome.category is not None:
                context.selected_candidate_id = outcome.selected_candidate_id
                context.failure_category = outcome.category
                return OperationResult(Outcome.REJECTED)
            else:
                return ops._fail(context, "EVALUATION_REJECTED", outcome.reason)

        context.selected_candidate_id = outcome.selected_candidate_id
        ops._record(
            context,
            EventType.EVAL_PASSED,
            {
                "selected_candidate_id": outcome.selected_candidate_id,
                "scores": outcome.scores,
            },
        )
        return OperationResult(Outcome.STATIC_PASSED)

    def after_failure(self, context: RTLTaskContext) -> OperationResult:
        ops = self.operations
        candidate = context.candidate()
        previous_analysis = context.metadata.get("last_repair_analysis")
        unchanged_analysis = (
            previous_analysis
            if isinstance(previous_analysis, dict)
            and previous_analysis.get("reason") == "unchanged_rtl"
            and previous_analysis.get("candidate_id") == candidate.candidate_id
            else None
        )
        if candidate.parent_id is not None:
            analysis = ops._repair_progress(context, candidate)
            context.metadata["last_repair_analysis"] = analysis
            if analysis.get("no_progress"):
                ops._record(
                    context,
                    EventType.PATCH_ROLLED_BACK,
                    {"candidate_id": candidate.candidate_id, "restored_id": candidate.parent_id},
                )
                context.selected_candidate_id = candidate.parent_id
        ops._select_repair_target(context)
        if (
            unchanged_analysis is not None
            and context.selected_candidate_id == candidate.candidate_id
        ):
            context.metadata["last_repair_analysis"] = unchanged_analysis
        feedback = ops._selected_debugger_feedback(context)
        decision = ops.router.route(context.failure_category, feedback)
        context.metadata["reflection_decision"] = decision.to_dict()
        ops._record(context, EventType.REFLECTION_ROUTED, decision.to_dict())
        if decision.action is ReflectionAction.RECOVER_INFRASTRUCTURE:
            return ops._fail(
                context,
                context.failure_category or "SYSTEM_ERROR",
                f"{decision.reason} Debugger was not invoked.",
            )
        if decision.action is ReflectionAction.WAIT_FOR_USER:
            return OperationResult(Outcome.WAIT)
        if context.budget.can_repair:
            return OperationResult(Outcome.REPAIR_ALLOWED)

        candidate = context.candidate()
        if candidate.parent_id is not None:
            context.selected_candidate_id = candidate.parent_id
            ops._record(
                context,
                EventType.PATCH_ROLLED_BACK,
                {"candidate_id": candidate.candidate_id, "restored_id": candidate.parent_id},
            )
        return ops._budget_exhausted(context, "repair budget exhausted")
