from __future__ import annotations

import pytest

from rtlforge.domain import RTLTaskContext, TaskStage
from rtlforge.orchestration.state_machine import InvalidTransition, transition


def test_state_machine_accepts_legal_transition() -> None:
    context = RTLTaskContext(task_id="task_legal", raw_spec="counter")

    source = transition(context, TaskStage.SPECIFYING)

    assert source is TaskStage.CREATED
    assert context.current_stage is TaskStage.SPECIFYING


def test_state_machine_rejects_skipping_planning_and_generation() -> None:
    context = RTLTaskContext(task_id="task_illegal", raw_spec="counter")

    with pytest.raises(InvalidTransition, match="CREATED -> VERIFYING"):
        transition(context, TaskStage.VERIFYING)
