from __future__ import annotations

from verilog_agent.domain.enums import TaskStage
from verilog_agent.domain.models import RTLTaskContext


class InvalidTransition(RuntimeError):
    pass


ALLOWED_TRANSITIONS: dict[TaskStage, frozenset[TaskStage]] = {
    TaskStage.CREATED: frozenset(
        {TaskStage.SPECIFYING, TaskStage.CANCELLED, TaskStage.FAILED}
    ),
    TaskStage.SPECIFYING: frozenset(
        {
            TaskStage.PLANNED,
            TaskStage.WAITING_FOR_USER,
            TaskStage.CANCELLED,
            TaskStage.FAILED,
        }
    ),
    TaskStage.PLANNED: frozenset(
        {TaskStage.GENERATING, TaskStage.CANCELLED, TaskStage.FAILED}
    ),
    TaskStage.GENERATING: frozenset(
        {TaskStage.EVALUATING, TaskStage.CANCELLED, TaskStage.FAILED}
    ),
    TaskStage.EVALUATING: frozenset(
        {
            TaskStage.GENERATING,
            TaskStage.VERIFYING,
            TaskStage.CLASSIFYING,
            TaskStage.CANCELLED,
            TaskStage.FAILED,
        }
    ),
    TaskStage.VERIFYING: frozenset(
        {
            TaskStage.COMPLETED,
            TaskStage.CLASSIFYING,
            TaskStage.CANCELLED,
            TaskStage.FAILED,
        }
    ),
    TaskStage.CLASSIFYING: frozenset(
        {
            TaskStage.GENERATING,
            TaskStage.REPAIRING,
            TaskStage.WAITING_FOR_USER,
            TaskStage.CANCELLED,
            TaskStage.FAILED,
        }
    ),
    TaskStage.REPAIRING: frozenset(
        {TaskStage.EVALUATING, TaskStage.CLASSIFYING, TaskStage.CANCELLED, TaskStage.FAILED}
    ),
    TaskStage.WAITING_FOR_USER: frozenset(
        {TaskStage.SPECIFYING, TaskStage.CANCELLED, TaskStage.FAILED}
    ),
    TaskStage.COMPLETED: frozenset(),
    TaskStage.CANCELLED: frozenset(),
    TaskStage.FAILED: frozenset(),
}


def transition(context: RTLTaskContext, target: TaskStage) -> TaskStage:
    source = context.current_stage
    if target not in ALLOWED_TRANSITIONS[source]:
        raise InvalidTransition(f"invalid task transition: {source.value} -> {target.value}")
    context.current_stage = target
    return source
