"""Deterministic test doubles for orchestration development."""

from rtlforge.testing.examples import (
    OFFLINE_COUNTER_SPEC,
    counter_candidate_payload,
    counter_plan_payload,
)
from rtlforge.testing.fakes import (
    FakeDebugger,
    FakeEvaluator,
    FakeGenerator,
    FakePlanner,
    FakeVerifier,
)
from rtlforge.testing.scripted_provider import ScriptedProvider

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
