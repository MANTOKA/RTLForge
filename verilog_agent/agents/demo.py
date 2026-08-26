from __future__ import annotations

import argparse
import json
from pathlib import Path

from verilog_agent.agents.generator import ArtifactRepository, RealGenerator
from verilog_agent.agents.planner import RealPlanner
from verilog_agent.domain import Budget
from verilog_agent.orchestration import Orchestrator, SQLiteTaskStore
from verilog_agent.providers import (
    OpenAIResponsesProvider,
    ProviderConfigurationError,
    ProviderSettings,
)
from verilog_agent.testing import FakeDebugger, FakeEvaluator, FakeVerifier


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Call a real model for Planner and Generator (uses API tokens)"
    )
    parser.add_argument("spec", help="Natural-language RTL requirement")
    parser.add_argument("--database", type=Path, default=Path("work/real-agents.sqlite"))
    parser.add_argument("--runs-root", type=Path, default=Path("runs"))
    parser.add_argument("--candidates", type=int, choices=(2, 3), default=2)
    args = parser.parse_args()

    try:
        provider = OpenAIResponsesProvider(ProviderSettings.from_env())
    except ProviderConfigurationError as error:
        parser.error(str(error))
    store = SQLiteTaskStore(args.database)
    orchestrator = Orchestrator(
        planner=RealPlanner(provider),
        generator=RealGenerator(provider, artifacts=ArtifactRepository(args.runs_root)),
        debugger=FakeDebugger(),
        evaluator=FakeEvaluator(),
        verifier=FakeVerifier(),
        store=store,
    )
    context = orchestrator.create_task(
        args.spec,
        budget=Budget(max_candidates=args.candidates, max_repairs=0),
    )
    result = orchestrator.run_task(context.task_id)
    print(
        json.dumps(
            {
                "task": result.to_dict(),
                "events": [event.to_dict() for event in store.list_events(result.task_id)],
                "verified_rtl": False,
                "verification_mode": "phase2_week5_test_double",
                "warning": (
                    "Planner and Generator used the real model; evaluation and verification "
                    "are still Phase 2 test doubles in this Week 5 demo."
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
