# VerilogAgent

VerilogAgent is an experimental multi-agent system for generating, checking, simulating, and repairing Verilog/SystemVerilog RTL.

## Current status

The repository currently contains an EDA sandbox prototype:

- Ubuntu 22.04 Docker image
- Icarus Verilog compilation and simulation
- Verilator linting
- structured JSON diagnostics in `tool_wrapper.py`
- small counter RTL and testbench fixtures

The agent orchestration and browser UI are not implemented yet. The empty `agents/`, `config/`, `core/`, and `tools/` directories are placeholders and will only be populated as working vertical slices are added.

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
docker run --rm -v "${PWD}:/workspace" verilog-agent-sandbox \
  python3 /workspace/tool_wrapper.py compile /workspace/counter.v
```

## Planned milestones

1. Make the sandbox API deterministic, timeout-safe, and fully tested.
2. Add typed request/result models and a single-task orchestrator.
3. Add planner, RTL generator, evaluator, and debugger roles incrementally.
4. Add a local API and chat-style browser UI.
5. Package the complete stack with Docker Compose and document one-command startup.
6. Add benchmark regression tests and reproducible evaluation reports.

## Repository policy

- Generated binaries, waveforms, logs, run directories, secrets, and local virtual environments are not committed.
- LLM credentials must be supplied at runtime through environment variables or local secret files.
- Each milestone should remain runnable and should be developed on a branch through a pull request.

