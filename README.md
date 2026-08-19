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

The agent orchestration and browser UI are not implemented yet. Their package directories
will be added only when the corresponding working vertical slices are implemented.

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

The current image is a command-line prototype rather than the final web application. Tool calls accept one of `compile`, `lint`, or `simulate`, followed by one or more Verilog files.

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

Repository configuration, logging, generated-artifact, and contribution conventions are
defined in [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md). Safe committed defaults live in
[`config/default.toml`](config/default.toml); credentials never belong in that file.

## Repository policy

- Generated binaries, waveforms, logs, run directories, secrets, and local virtual environments are not committed.
- LLM credentials must be supplied at runtime through environment variables or local secret files.
- Each milestone should remain runnable and should be developed on a branch through a pull request.
