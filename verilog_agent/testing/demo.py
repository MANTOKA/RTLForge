from __future__ import annotations

import argparse
import json
from pathlib import Path

from verilog_agent.domain import Budget
from verilog_agent.orchestration import Orchestrator, SQLiteTaskStore
from verilog_agent.testing.fakes import (
    FakeDebugger,
    FakeEvaluator,
    FakeGenerator,
    FakePlanner,
    FakeVerifier,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the deterministic Fake Agent loop")
    parser.add_argument("--database", type=Path, default=Path("work/fake-orchestrator.sqlite"))
    parser.add_argument("--failures-before-pass", type=int, default=1)
    parser.add_argument("--max-repairs", type=int, default=3)
    args = parser.parse_args()

    store = SQLiteTaskStore(args.database)
    orchestrator = Orchestrator(
        planner=FakePlanner(),
        generator=FakeGenerator(),
        debugger=FakeDebugger(),
        evaluator=FakeEvaluator(),
        verifier=FakeVerifier(failures_before_pass=args.failures_before_pass),
        store=store,
    )
    context = orchestrator.create_task(
        "Create a four-bit synchronous counter",
        budget=Budget(max_candidates=2, max_repairs=args.max_repairs),
    )
    result = orchestrator.run_task(context.task_id)
    print(
        json.dumps(
            {
                "task": result.to_dict(),
                "events": [event.to_dict() for event in store.list_events(result.task_id)],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
