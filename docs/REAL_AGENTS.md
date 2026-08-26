# Real Planner and Generator

## What “real Agent” means here

An Agent is a role implementation with its own instructions, input contract, output schema,
and local validation. It is not a second Docker container and it does not control the workflow.
The deterministic Orchestrator calls each Agent at the correct state and owns budgets,
checkpoints, events, cancellation, and failure handling.

This Week 5 slice replaces two test doubles:

```text
raw requirement
    -> RealPlanner -> validated SpecIR + Task Tree
    -> RealGenerator -> 2-3 distinct RTL files + hashes
    -> Candidate Evaluation / Verification (still test doubles in the live demo)
```

`RealPlanner` may interpret ambiguous text only when it records the interpretation as an
assumption. It cannot emit RTL. `RealGenerator` receives the validated plan, cannot change the
interface, and writes only below the configured artifact root. Duplicate RTL, Markdown fences,
a wrong module name, an unsafe task path, or an invalid schema result stops the task instead of
being silently accepted.

## Provider and secrets

The first provider uses the OpenAI Python SDK and Responses API. It sends a strict JSON Schema,
sets `store=false`, accepts an optional `OPENAI_BASE_URL`, and reads credentials only from the
environment. A compatible custom endpoint must implement the Responses API and strict JSON
Schema output; an endpoint that implements only Chat Completions is not supported in this slice.
The request shape follows the official
[Responses API reference](https://developers.openai.com/api/reference/resources/responses/methods/create)
and [Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs).

Install the optional dependency from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,llm]"
```

Set configuration for the current PowerShell window. Replace both example values:

```powershell
$env:OPENAI_API_KEY = "your-key"
$env:VERILOG_AGENT_MODEL = "your-model-id"
# Optional for a compatible provider:
# $env:OPENAI_BASE_URL = "https://provider.example/v1"
```

Do not paste the real key into `.env.example`, Git, an Issue, a screenshot, or chat. Closing the
PowerShell window removes these temporary environment variables.

## Safe offline verification

The normal test suite substitutes `ScriptedProvider`, so it verifies prompts, schemas, domain
validation, artifact writing, hashing, and the Orchestrator loop without network access or cost:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Explicit live-model smoke run

The following command is the only current path that actually calls the configured model and may
consume paid API tokens:

```powershell
.\.venv\Scripts\python.exe -m verilog_agent.agents.demo `
  "Create a four-bit synchronous active-high reset counter named counter with clk, rst, and q"
```

The final JSON contains the task record and artifact paths. Generated files appear under
`runs/<task_id>/candidates/cand_01/rtl.v` and `cand_02/rtl.v`; `runs/` is ignored by Git.

Important: this command proves real planning and generation, not final RTL correctness. Its
Evaluator and Verifier are still deterministic test doubles. Phase 2 Weeks 6-7 will connect the
static scorer, EDA verification, failure router, and real Debugger before a generated candidate
can be labeled verified RTL.
