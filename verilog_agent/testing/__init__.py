"""Deterministic test doubles for orchestration development."""

from verilog_agent.testing.examples import (
    OFFLINE_COUNTER_SPEC,
    counter_candidate_payload,
    counter_plan_payload,
)
from verilog_agent.testing.fakes import (
    FakeDebugger,
    FakeEvaluator,
    FakeGenerator,
    FakePlanner,
    FakeVerifier,
)
from verilog_agent.testing.scripted_provider import ScriptedProvider

__all__ = [
    "FakeDebugger",
    "FakeEvaluator",
    "FakeGenerator",
    "FakePlanner",
    "FakeVerifier",
    "OFFLINE_COUNTER_SPEC",
    "ScriptedProvider",
    "counter_candidate_payload",
    "counter_plan_payload",
]
