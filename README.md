# VerilogAgent

VerilogAgent is an experimental multi-agent system for generating, checking, simulating, and repairing Verilog/SystemVerilog RTL.

## Current status

The repository currently contains a restricted EDA sandbox baseline:

- Ubuntu 22.04 Docker image
- Icarus Verilog compilation and simulation
- Verilator linting
- Yosys synthesis statistics
- structured JSON diagnostics in `tool_wrapper.py`
- non-root execution, disabled network, read-only root filesystem, resource limits, input
  path validation, subprocess timeouts, and isolated temporary outputs
- small counter RTL and testbench fixtures

The hand-written RTL files under `tests/fixtures/` are stable regression inputs. They are
not intended to represent Agent output. Future generated RTL belongs under `runs/<task_id>/`,
which is excluded from Git.

The Phase 2 implementation now includes versioned domain models, an explicit task state
machine, SQLite events/checkpoints, budgets, cancellation, deterministic Fake Agents, and
real model-backed Planner and Generator implementations. The real agents use an
OpenAI-compatible Responses endpoint with strict structured output. Deterministic candidate
evaluation, golden-testbench verification, the deterministic Adaptive Reflection Router, and
the first real Debugger repair path are implemented; the browser UI is a later slice. See
[`docs/PHASE2_ORCHESTRATION.md`](docs/PHASE2_ORCHESTRATION.md) and
[`docs/REAL_AGENTS.md`](docs/REAL_AGENTS.md). A step-by-step Chinese demonstration guide is
available in [`docs/PLANNER_GENERATOR_SHOWCASE_ZH.md`](docs/PLANNER_GENERATOR_SHOWCASE_ZH.md).
The first Human v2 development baseline and repair evidence are recorded in
[`docs/HUMAN_V2_DEV_BASELINE_ZH.md`](docs/HUMAN_V2_DEV_BASELINE_ZH.md).

Run the classroom demonstrations separately in PowerShell:

```powershell
# Correct Agent result: Planner -> Generator -> Docker Verification PASS
.\.venv\Scripts\python.exe -m verilog_agent.benchmarks.explain_demo `
  --dataset-root "D:\AI\VerilogMulti\VerilogCoder-main\hardware_agent\examples\VerilogCoder\verilog-eval-v2\dataset_dumpall" `
  --scenario correct

# Two official-reference Icarus incompatibilities -> infrastructure route
.\.venv\Scripts\python.exe -m verilog_agent.benchmarks.explain_demo `
  --dataset-root "D:\AI\VerilogMulti\VerilogCoder-main\hardware_agent\examples\VerilogCoder\verilog-eval-v2\dataset_dumpall" `
  --scenario icarus

# Two natural Agent failures -> Router -> recorded Debugger outcomes
.\.venv\Scripts\python.exe -m verilog_agent.benchmarks.explain_demo `
  --dataset-root "D:\AI\VerilogMulti\VerilogCoder-main\hardware_agent\examples\VerilogCoder\verilog-eval-v2\dataset_dumpall" `
  --scenario natural

# Injected decrement defect -> Router -> Debugger -> repaired PASS
.\.venv\Scripts\python.exe -m verilog_agent.benchmarks.explain_demo `
  --dataset-root "D:\AI\VerilogMulti\VerilogCoder-main\hardware_agent\examples\VerilogCoder\verilog-eval-v2\dataset_dumpall" `
  --scenario defect
```

These commands make no LLM request: they re-run deterministic Docker verification
and display recorded real-Debugger evidence. Add `--with-debugger` to a `natural`
or `defect` command to call the configured real Debugger again; this consumes API
tokens and may produce a different repair candidate. Use `--scenario all` for the
combined demonstration.

## Intended product

The target deliverable is a local application that a user can start with Docker Compose and open in a browser. The browser will provide a chat-style interface for entering an RTL requirement, viewing generated Verilog, running EDA checks, reviewing diagnostics, and downloading the final artifacts.

```text
Browser UI -> API/orchestrator -> planning/generation/debug loop -> EDA sandbox
```

## Prototype sandbox

Build the current image from this directory:

```bash
docker build -t verilog-agent-sandbox .
```

The current image is a command-line prototype rather than the final web application. Tool
calls accept `compile`, `lint`, `simulate`, `eval_quality`, or `benchmark`, followed by one
or more Verilog files. All actions return the same versioned JSON contract documented in
[`docs/TOOL_INTERFACE.md`](docs/TOOL_INTERFACE.md).

Example when mounting the repository into the container:

```bash
docker run --rm --network none --read-only \
  --tmpfs /work:rw,exec,size=64m,uid=10001,gid=10001,mode=0700 \
  --pids-limit 128 --memory 512m --cpus 1 \
  --mount type=bind,source="${PWD}",target=/workspace,readonly \
  -e VERILOG_AGENT_INPUT_ROOT=/workspace/tests/fixtures \
  verilog-agent-sandbox \
  python3 /workspace/tool_wrapper.py compile /workspace/tests/fixtures/counter.v
```

## Planned milestones

1. Make the sandbox API deterministic, timeout-safe, and fully tested.
2. Add typed request/result models and a single-task orchestrator.
3. Add planner, RTL generator, evaluator, and debugger roles incrementally.
4. Add a local API and chat-style browser UI.
5. Package the complete stack with Docker Compose and document one-command startup.
6. Add benchmark regression tests and reproducible evaluation reports.

## Tests

Install the development dependencies:

```bash
python -m pip install -e ".[dev]"
```

Install the optional model-provider dependency when you want to call a real model:

```bash
python -m pip install -e ".[dev,llm]"
```

Run the fast parser unit tests:

```bash
pytest
```

Run the Docker EDA regression suite, which automatically builds the sandbox image and
calls `tool_wrapper.py` for compile, lint, and simulation checks:

```bash
pytest --run-integration -m integration
```

The same commands run automatically in GitHub Actions for pull requests and pushes to
`main`.

Unit and CI tests use `ScriptedProvider`; they never call a model endpoint and do not consume
API tokens. Run `python -m verilog_agent.agents.demo --offline-example` to inspect the complete
display format without an API. Running the same module without `--offline-example` is the
explicit live-model path. Live mode automatically loads an ignored local `.env`; use
`--check-config` to display the effective non-secret endpoint and model without making an API
call. See [`docs/PLANNER_GENERATOR_SHOWCASE_ZH.md`](docs/PLANNER_GENERATOR_SHOWCASE_ZH.md).

To deterministically compile, lint, and statically score every generated candidate in the
restricted Docker sandbox, then select one candidate (without claiming functional simulation), run:

```powershell
python -m verilog_agent.agents.demo --static-evaluation --candidates 3
```

The default is two candidates; the live Generator supports either two or three. The offline
scripted example contains exactly two candidates.

Human v2 dataset integrity/reference preflight and one-case model-backed verification are
documented in [`docs/VERIFICATION_HUMAN_V2_ZH.md`](docs/VERIFICATION_HUMAN_V2_ZH.md). The benchmark
remains external to the product repository, and golden testbench/reference source is never sent
to Planner, Generator, or Debugger.

To enable sparse real Debugger repair for a Human v2 case, add `--max-repairs 1` (up to 3).
The Debugger is called only after a repairable deterministic failure; system/tool failures never
trigger an RTL edit.

Repository configuration, logging, generated-artifact, and contribution conventions are
defined in [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md). Safe committed defaults live in
[`config/default.toml`](config/default.toml); credentials never belong in that file.

## Repository policy

- Generated binaries, waveforms, logs, run directories, secrets, and local virtual environments are not committed.
- LLM credentials must be supplied at runtime through environment variables or local secret files.
- Each milestone should remain runnable and should be developed on a branch through a pull request.
