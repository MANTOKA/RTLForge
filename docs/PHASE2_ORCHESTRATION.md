# Phase 2 domain and orchestration baseline

## What the domain model means

The domain layer is the shared vocabulary of the product. `SpecIR` records a structured
interpretation of the request; `TaskTreeNode` records decomposed work; `Candidate` records
an RTL artifact and lineage; `RTLTaskContext` records the current task; `Budget` limits
candidate and repair work; and `Event` provides an ordered audit trail. These objects do not
import FastAPI or a model SDK, so the API, UI, agents, and tests use the same definitions.

Domain schema version 1 is serialized into SQLite checkpoints. Incompatible changes must
increment the schema version and include a migration.

## What the Orchestrator state machine means

The Orchestrator is the workflow controller, not another LLM Agent. It is the only component
allowed to advance a task through these validated stages:

```text
CREATED -> SPECIFYING -> PLANNED -> GENERATING -> EVALUATING -> VERIFYING
                                                             | PASS -> COMPLETED
                                                             | FAIL -> CLASSIFYING
                                                                       -> REPAIRING
                                                                       -> EVALUATING
```

Any non-terminal stage can end as `CANCELLED` or `FAILED`. Every transition emits an event
and writes a SQLite checkpoint. Candidate and repair budgets prevent an uncontrolled loop.

## Why Fake Agents come first

Fake Agents are deterministic test doubles, not the final product. They prove that state
transitions, dependency interfaces, cancellation, budgets, event ordering, repair loops,
rollback, and recovery work without network access, API keys, model cost, prompt drift, or
random model output. A failure therefore identifies an orchestration defect instead of an
ambiguous model/provider problem.

Real Planner, Generator, and Debugger implementations will implement the same typed
interfaces. Replacing a Fake does not change the state machine or persistence format.
Candidate Evaluation and Verification remain deterministic services rather than LLM Agents.

## Run the demonstrator

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m verilog_agent.testing.demo `
  --database work\fake-orchestrator.sqlite `
  --failures-before-pass 1
```

The command deliberately fails verification once, creates one repaired candidate, then
completes. It prints the final task context and the ordered persisted events as JSON. The
database is under the Git-ignored `work/` directory.
