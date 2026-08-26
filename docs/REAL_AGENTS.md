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
sets `store=false`, accepts an optional `OPENAI_BASE_URL`, and reads credentials from the process
environment after optionally loading an ignored local `.env`. A compatible custom endpoint must implement the Responses API and strict JSON
Schema output; an endpoint that implements only Chat Completions is not supported in this slice.
The request shape follows the official
[Responses API reference](https://developers.openai.com/api/reference/resources/responses/methods/create)
and [Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs).

Install the optional dependency from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,llm]"
```

Create the ignored local configuration once:

```powershell
Copy-Item .env.example .env
notepad .env
```

Set `OPENAI_API_KEY`, `VERILOG_AGENT_MODEL`, and, for a compatible relay,
`OPENAI_BASE_URL` in `.env`. The relay URL must be a base URL such as
`https://provider.example/v1`, not the full `/responses` endpoint. Values already present in the
shell take precedence. Validate the effective non-secret configuration without an API call:

```powershell
.\.venv\Scripts\python.exe -m verilog_agent.agents.demo --check-config
```

Do not paste the real key into `.env.example`, Git, an Issue, a screenshot, or chat.

## Safe offline verification

The normal test suite substitutes `ScriptedProvider`, so it verifies prompts, schemas, domain
validation, artifact writing, hashing, and the Orchestrator loop without network access or cost:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

To see the actual Planner/Generator display rather than only the test count, run:

```powershell
.\.venv\Scripts\python.exe -m verilog_agent.agents.demo --offline-example
```

This uses a fixed, clearly labeled scripted counter response. It displays the same SpecIR,
Task Tree, candidate RTL, hashes, event sequence, and saved files as live mode, but it does not
prove that an LLM understood new text. See the Chinese walkthrough in
[`PLANNER_GENERATOR_SHOWCASE_ZH.md`](PLANNER_GENERATOR_SHOWCASE_ZH.md).

## Explicit live-model smoke run

The following command prompts for a requirement, calls the configured model twice (Planner and
Generator), and may consume paid API tokens:

```powershell
.\.venv\Scripts\python.exe -m verilog_agent.agents.demo
```

You can also pass the requirement in quotes after `demo`. The command prints each stage and
saves `input.txt`, `spec_ir.json`, `task_tree.json`, `showcase_summary.json`, and candidate RTL
under `runs/<task_id>/`; `runs/` is ignored by Git.

Important: this command proves real planning and generation, not final RTL correctness. It now
stops at `EVALUATING` immediately after generation and does not invoke a Fake Evaluator or Fake
Verifier. The report says `verified_rtl=false`. Phase 2 Weeks 6-7 will connect the static scorer,
EDA verification, failure router, and real Debugger before a generated candidate can be labeled
verified RTL.
