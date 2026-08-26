from __future__ import annotations

import pytest

from verilog_agent.domain import Budget, PortDirection, PortSpec, RTLTaskContext, SpecIR
from verilog_agent.domain.models import BudgetExceeded


def test_task_context_round_trip_preserves_typed_domain_data() -> None:
    context = RTLTaskContext(
        task_id="task_round_trip",
        raw_spec="build a counter",
        spec_ir=SpecIR(
            module_name="counter",
            raw_spec="build a counter",
            ports=[PortSpec("clk", PortDirection.INPUT)],
        ),
        budget=Budget(max_candidates=3, max_repairs=2),
    )

    restored = RTLTaskContext.from_dict(context.to_dict())

    assert restored == context
    assert restored.spec_ir is not None
    assert restored.spec_ir.ports[0].direction is PortDirection.INPUT


def test_budget_rejects_candidate_overrun() -> None:
    budget = Budget(max_candidates=2)

    budget.record_candidates(2)

    assert budget.candidate_slots == 0
    with pytest.raises(BudgetExceeded):
        budget.record_candidates(1)


@pytest.mark.parametrize(
    ("max_candidates", "max_repairs"),
    [(0, 1), (4, 1), (2, -1), (2, 4)],
)
def test_budget_enforces_lightweight_architecture_caps(
    max_candidates: int, max_repairs: int
) -> None:
    with pytest.raises(ValueError):
        Budget(max_candidates=max_candidates, max_repairs=max_repairs)
