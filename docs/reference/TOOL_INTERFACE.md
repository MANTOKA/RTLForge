# Deterministic tool interface

> 2026-10-28 发布校对：本篇保留原设计/历史实验的日期、指标与协议；其中阶段性状态不代表当前实现。当前完成范围见[验收矩阵](../STATUS_ZH.md)。

`tool_wrapper.py` is the only command contract the later orchestrator and agents should
depend on. EDA-specific text remains available in `stdout` and `stderr`, but decisions use
the stable JSON fields below.

## Actions

- `compile`: Icarus Verilog syntax/elaboration compile.
- `lint`: Verilator lint with warnings enabled.
- `simulate`: Icarus compile followed by bounded `vvp` execution.
- `eval_quality`: deterministic Verilator diagnostic counts and a lightweight static score.
- `benchmark`: Yosys elaboration/synthesis statistics.

## ToolResult schema version 1

Every action returns `schema_version`, `action`, `stage`, `status`, `category`, `tool`,
`returncode`, `duration_ms`, `errors`, `metrics`, `stdout`, and `stderr`. A tool failure is
returned as valid JSON; callers do not parse process prose or infer success from missing
output.

Current categories include `OK`, `INVALID_ARGUMENT`, `UNKNOWN_ACTION`, `INPUT_NOT_FOUND`,
`UNSUPPORTED_INPUT`, `PATH_VIOLATION`, `TIMEOUT`, `SYSTEM_ERROR`, `SYNTAX_ERROR`,
`LINT_ERROR`, `SIMULATION_ERROR`, `ASSERTION_FAILURE`, `QUALITY_ERROR`, and
`SYNTHESIS_ERROR`.

The simulation action treats an assertion/FAIL marker as failure even when the testbench
exits with code zero. This protects the orchestrator from false PASS results.

## Metric interpretation

`eval_quality` is a deterministic heuristic, not an LLM judgment. Its score starts at 100
and subtracts 25 per parsed error and 5 per warning.

`benchmark` reports structural Yosys wire and cell statistics. It does not run static timing
analysis and must not be presented as real Fmax, power, area, or commercial PPA evidence.
