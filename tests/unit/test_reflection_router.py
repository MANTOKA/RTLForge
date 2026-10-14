from verilog_agent.services import (
    AdaptiveReflectionRouter,
    ReflectionAction,
    ReflectionPolicy,
)


def test_router_selects_specialized_debugger_policies() -> None:
    router = AdaptiveReflectionRouter()

    assert router.route("SYNTAX_ERROR").policy is ReflectionPolicy.SYNTAX
    assert router.route("FUNCTIONAL_MISMATCH").policy is ReflectionPolicy.FUNCTIONAL
    timeout = router.route("TIMEOUT")
    assert timeout.policy is ReflectionPolicy.DEADLOCK_TERMINATION
    assert timeout.action is ReflectionAction.DEBUG


def test_router_specializes_isolated_single_sample_mismatch() -> None:
    decision = AdaptiveReflectionRouter().route(
        "FUNCTIONAL_MISMATCH",
        {"mismatch_summary": {"temporal_pattern": "isolated_single_sample"}},
    )

    assert decision.category == "INITIAL_STATE_MISMATCH"
    assert decision.policy is ReflectionPolicy.FUNCTIONAL
    assert "initial-state" in decision.reason


def test_router_never_sends_infrastructure_failures_to_debugger() -> None:
    decision = AdaptiveReflectionRouter().route("TOOL_INCOMPATIBILITY")

    assert decision.policy is ReflectionPolicy.INFRASTRUCTURE
    assert decision.action is ReflectionAction.RECOVER_INFRASTRUCTURE
    assert decision.invokes_debugger is False


def test_router_requires_user_for_constraint_tradeoff() -> None:
    decision = AdaptiveReflectionRouter().route("BENCHMARK_BOUNDS_EXCEEDED")

    assert decision.policy is ReflectionPolicy.REQUIREMENT
    assert decision.action is ReflectionAction.WAIT_FOR_USER


def test_router_fails_closed_for_unknown_category() -> None:
    decision = AdaptiveReflectionRouter().route("NEW_UNCLASSIFIED_FAILURE")

    assert decision.action is ReflectionAction.RECOVER_INFRASTRUCTURE
    assert decision.invokes_debugger is False
