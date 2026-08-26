from __future__ import annotations

from pathlib import Path

from tests.unit.test_real_agents import candidate_payload, plan_payload
from verilog_agent.agents import ArtifactRepository, RealGenerator, RealPlanner
from verilog_agent.domain import TaskStage
from verilog_agent.orchestration import Orchestrator, SQLiteTaskStore
from verilog_agent.testing import (
    FakeDebugger,
    FakeEvaluator,
    FakeVerifier,
    ScriptedProvider,
)


def test_real_agent_classes_complete_offline_scripted_loop(tmp_path: Path) -> None:
    provider = ScriptedProvider(plan_payload(), candidate_payload())
    store = SQLiteTaskStore(tmp_path / "real-agents.sqlite")
    orchestrator = Orchestrator(
        planner=RealPlanner(provider),
        generator=RealGenerator(
            provider, artifacts=ArtifactRepository(tmp_path / "runs")
        ),
        debugger=FakeDebugger(),
        evaluator=FakeEvaluator(),
        verifier=FakeVerifier(),
        store=store,
    )
    context = orchestrator.create_task(
        "Create a four-bit synchronous counter", task_id="task_real_classes"
    )

    result = orchestrator.run_task(context.task_id)

    assert result.current_stage is TaskStage.COMPLETED
    assert len(provider.requests) == 2
    assert len(result.candidates) == 2
    assert all(Path(candidate.artifact_path).is_file() for candidate in result.candidates)
