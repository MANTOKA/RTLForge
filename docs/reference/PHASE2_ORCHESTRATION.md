# Phase 2 domain and orchestration baseline

> 2026-10-28 发布校对：本篇保留原设计/历史实验的日期、指标与协议；其中阶段性状态不代表当前实现。当前完成范围见[验收矩阵](../STATUS_ZH.md)。

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

Real Planner, Generator and Debugger now implement the same typed interfaces through a
structured model Provider. Replacing their Fakes did not change the state machine or persistence
format. Candidate Evaluation, Verification and the Reflection Router remain deterministic
services rather than LLM Agents. The current Human v2 path also supports candidate fallback,
repair budgets, rollback and verification-gated Memory MVP writes.

For real-agent configuration and the verification boundary, see
[`REAL_AGENTS.md`](REAL_AGENTS.md).
