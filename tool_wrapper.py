#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

IVERILOG_RE = re.compile(
    r"^(?P<file>[^:]+):(?P<line>\d+):\s*"
    r"(?:(?P<severity>error|warning|Warning):\s*)?"
    r"(?P<msg>.*)",
    re.IGNORECASE,
)

VERILATOR_RE = re.compile(
    r"^%(?P<severity>Error|Warning)"
    r"(?:-(?P<code>[A-Z0-9_]+))?:\s*"
    r"(?P<file>[^:]+):"
    r"(?P<line>\d+):"
    r"(?:(?P<col>\d+):)?\s*"
    r"(?P<msg>.*)"
)

SUPPORTED_SUFFIXES = {".v", ".sv", ".vh", ".svh"}
SCHEMA_VERSION = 1
FAILURE_TEXT_RE = re.compile(
    r"(?:\bFAIL(?:ED|URE)?\b|\bFATAL\b|ASSERT(?:ION)?.*(?:FAIL|ERROR))",
    re.IGNORECASE,
)
ZERO_FAILURE_COUNT_RE = re.compile(
    r"(?:\bFAIL(?:ED|URES?)?\s*[:=]\s*0\b|\b0\s+FAILURES?\b)",
    re.IGNORECASE,
)
MISMATCH_COUNT_RE = re.compile(
    r"(?:\bMismatches?\s*:\s*|\bhas\s+|\bTotal mismatched samples is\s+)(\d+)",
    re.IGNORECASE,
)
YOSYS_METRICS = {
    "wire_count": re.compile(r"Number of wires:\s+(\d+)"),
    "wire_bits": re.compile(r"Number of wire bits:\s+(\d+)"),
    "cell_count": re.compile(r"Number of cells:\s+(\d+)"),
}
DEFAULT_SCORE_CONFIG = {
    "config_version": "score-v1-fallback",
    "initial_score": 100,
    "threshold": 75,
    "default_warning_penalty": 2,
    "default_error_penalty": 25,
    "penalties": {},
}

VERILATOR_UNSUPPORTED_CODES = {"BLKLOOPINIT"}


def parse_iverilog(stderr: str) -> list[dict[str, Any]]:
    """Parse Icarus diagnostics into stable fields for downstream repair."""
    errors = []
    for line in stderr.splitlines():
        match = IVERILOG_RE.match(line)
        if not match:
            continue
        data = match.groupdict()
        errors.append(
            {
                "tool": "iverilog",
                "severity": (data["severity"] or "ERROR").upper(),
                "type": (data["severity"] or "ERROR").upper(),
                "file": data["file"],
                "line": int(data["line"]),
                "message": data["msg"].strip(),
            }
        )
    return errors


def parse_verilator(stderr: str) -> list[dict[str, Any]]:
    """Parse Verilator diagnostics into stable fields for downstream repair."""
    errors = []
    for line in stderr.splitlines():
        match = VERILATOR_RE.match(line)
        if not match:
            continue
        data = match.groupdict()
        errors.append(
            {
                "tool": "verilator",
                "severity": data["severity"].upper(),
                "type": data["code"] or data["severity"].upper(),
                "file": data["file"],
                "line": int(data["line"]),
                "message": data["msg"].strip(),
            }
        )
    return errors


def parse_error(tool: str, stderr: str) -> list[dict[str, Any]]:
    if tool == "iverilog":
        return parse_iverilog(stderr)
    if tool == "verilator":
        return parse_verilator(stderr)
    return []


def load_score_config() -> dict[str, Any]:
    """Load a versioned quality configuration without adding runtime dependencies."""
    configured = os.environ.get("RTLFORGE_SCORE_CONFIG")
    candidates = [Path(configured)] if configured else []
    candidates.extend(
        [
            Path(__file__).with_name("static_score_v1.json"),
            Path(__file__).parent / "config" / "static_score_v1.json",
        ]
    )
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("penalties"), dict):
            return payload
    return dict(DEFAULT_SCORE_CONFIG)


def score_diagnostics(
    diagnostics: list[dict[str, Any]], config: dict[str, Any]
) -> tuple[int, list[dict[str, Any]]]:
    """Produce an explainable, deterministic deduction list from Verilator diagnostics."""
    configured_penalties = {
        str(key).upper(): int(value) for key, value in config.get("penalties", {}).items()
    }
    deductions = []
    total_penalty = 0
    for diagnostic in diagnostics:
        rule = str(diagnostic.get("type", "UNKNOWN")).upper()
        penalty = configured_penalties.get(rule)
        if penalty is None:
            matching_prefixes = [key for key in configured_penalties if rule.startswith(key)]
            if matching_prefixes:
                penalty = configured_penalties[max(matching_prefixes, key=len)]
        if penalty is None:
            severity_key = (
                "default_error_penalty"
                if diagnostic.get("severity") == "ERROR"
                else "default_warning_penalty"
            )
            penalty = int(config.get(severity_key, 0))
        total_penalty += penalty
        deductions.append(
            {
                "rule": rule,
                "penalty": penalty,
                "evidence": {
                    "file": diagnostic.get("file"),
                    "line": diagnostic.get("line"),
                    "message": diagnostic.get("message", ""),
                },
            }
        )
    initial_score = int(config.get("initial_score", 100))
    return max(0, initial_score - total_penalty), deductions


def _timeout(name: str, default: int) -> int:
    raw_value = os.environ.get(f"RTLFORGE_{name.upper()}_TIMEOUT", str(default))
    try:
        value = int(raw_value)
    except ValueError:
        return default
    return max(1, value)


def _output_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def contains_failure_marker(output: str) -> bool:
    if any(int(match.group(1)) > 0 for match in MISMATCH_COUNT_RE.finditer(output)):
        return True
    normalized = ZERO_FAILURE_COUNT_RE.sub("", output)
    return FAILURE_TEXT_RE.search(normalized) is not None


def run_command(cmd: list[str], *, timeout_seconds: int) -> dict[str, Any]:
    """Run an EDA subprocess with bounded time and structured system failures."""
    started = time.perf_counter()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "timed_out": False,
            "system_error": None,
            "duration_ms": round((time.perf_counter() - started) * 1000),
        }
    except subprocess.TimeoutExpired as error:
        return {
            "returncode": -1,
            "stdout": _output_text(error.stdout),
            "stderr": _output_text(error.stderr),
            "timed_out": True,
            "system_error": None,
            "duration_ms": round((time.perf_counter() - started) * 1000),
        }
    except OSError as error:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": str(error),
            "timed_out": False,
            "system_error": str(error),
            "duration_ms": round((time.perf_counter() - started) * 1000),
        }


def _result(
    stage: str,
    status: str,
    category: str,
    command_result: dict[str, Any] | None = None,
    *,
    tool: str | None = None,
    errors: list[dict[str, Any]] | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    command_result = command_result or {}
    action_by_stage = {
        "COMPILE": "compile",
        "LINT": "lint",
        "SIMULATION": "simulate",
        "QUALITY": "eval_quality",
        "BENCHMARK": "benchmark",
        "INPUT": "input",
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "action": action_by_stage[stage],
        "stage": stage,
        "status": status,
        "category": category,
        "tool": tool,
        "returncode": command_result.get("returncode"),
        "duration_ms": command_result.get("duration_ms", 0),
        "errors": errors or [],
        "metrics": metrics or {},
        "stdout": command_result.get("stdout", ""),
        "stderr": command_result.get("stderr", ""),
    }


def _classify_command_failure(
    stage: str,
    result: dict[str, Any],
    tool: str,
    default_category: str,
) -> dict[str, Any]:
    if result["timed_out"]:
        return _result(stage, "FAIL", "TIMEOUT", result, tool=tool)
    if result["system_error"]:
        return _result(stage, "FAIL", "SYSTEM_ERROR", result, tool=tool)
    if result["returncode"] != 0:
        errors = parse_error(tool, result["stderr"])
        if tool == "verilator" and errors and all(
            item.get("type") in VERILATOR_UNSUPPORTED_CODES
            or "unsupported" in str(item.get("message", "")).lower()
            for item in errors
        ):
            return _result(
                stage,
                "FAIL",
                "TOOL_INCOMPATIBILITY",
                result,
                tool=tool,
                errors=errors,
            )
        return _result(
            stage,
            "FAIL",
            default_category,
            result,
            tool=tool,
            errors=errors,
        )
    return _result(stage, "PASS", "OK", result, tool=tool)


def _workspace() -> tempfile.TemporaryDirectory[str]:
    temp_root = os.environ.get("TMPDIR")
    return tempfile.TemporaryDirectory(prefix="rtlforge-", dir=temp_root)


def validate_input_files(files: list[str]) -> tuple[list[str], dict[str, Any] | None]:
    """Resolve RTL inputs and reject files outside the configured read-only input root."""
    root = Path(os.environ.get("RTLFORGE_INPUT_ROOT", os.getcwd())).resolve()
    resolved_files = []

    for raw_file in files:
        try:
            resolved = Path(raw_file).resolve(strict=True)
        except OSError as error:
            return [], _result(
                "INPUT", "FAIL", "INPUT_NOT_FOUND", {"stderr": str(error)}
            )

        if not resolved.is_file() or resolved.suffix.lower() not in SUPPORTED_SUFFIXES:
            return [], _result(
                "INPUT",
                "FAIL",
                "UNSUPPORTED_INPUT",
                {"stderr": f"Unsupported RTL input: {raw_file}"},
            )
        if not resolved.is_relative_to(root):
            return [], _result(
                "INPUT",
                "FAIL",
                "PATH_VIOLATION",
                {"stderr": f"Input path is outside allowed root: {raw_file}"},
            )
        resolved_files.append(str(resolved))

    return resolved_files, None


def compile_verilog(files: list[str]) -> dict[str, Any]:
    with _workspace() as workspace:
        output = str(Path(workspace) / "compile.out")
        result = run_command(
            ["iverilog", "-g2012", "-o", output, *files],
            timeout_seconds=_timeout("compile", 30),
        )
    return _classify_command_failure("COMPILE", result, "iverilog", "SYNTAX_ERROR")


def lint_verilog(files: list[str]) -> dict[str, Any]:
    result = run_command(
        ["verilator", "--lint-only", "-Wall", "--Wno-fatal", *files],
        timeout_seconds=_timeout("lint", 30),
    )
    return _classify_command_failure("LINT", result, "verilator", "LINT_ERROR")


def simulate(files: list[str]) -> dict[str, Any]:
    with _workspace() as workspace:
        output = str(Path(workspace) / "simulation.out")
        compile_result = run_command(
            ["iverilog", "-g2012", "-o", output, *files],
            timeout_seconds=_timeout("compile", 30),
        )
        classified = _classify_command_failure(
            "SIMULATION", compile_result, "iverilog", "SYNTAX_ERROR"
        )
        if classified["status"] == "FAIL":
            return classified

        simulation_result = run_command(
            ["vvp", output],
            timeout_seconds=_timeout("simulate", 60),
        )
    classified = _classify_command_failure(
        "SIMULATION", simulation_result, "vvp", "SIMULATION_ERROR"
    )
    combined_output = simulation_result["stdout"] + "\n" + simulation_result["stderr"]
    if classified["status"] == "PASS" and contains_failure_marker(combined_output):
        return _result(
            "SIMULATION",
            "FAIL",
            "ASSERTION_FAILURE",
            simulation_result,
            tool="vvp",
            errors=[
                {
                    "tool": "vvp",
                    "severity": "ERROR",
                    "type": "ASSERTION_FAILURE",
                    "file": None,
                    "line": None,
                    "message": "Simulation output contains an assertion or FAIL marker",
                }
            ],
        )
    return classified


def eval_quality(files: list[str]) -> dict[str, Any]:
    result = run_command(
        ["verilator", "--lint-only", "-Wall", "--Wno-fatal", *files],
        timeout_seconds=_timeout("lint", 30),
    )
    if result["timed_out"] or result["system_error"]:
        return _classify_command_failure("QUALITY", result, "verilator", "QUALITY_ERROR")

    diagnostics = parse_verilator(result["stderr"])
    config = load_score_config()
    error_count = sum(item["severity"] == "ERROR" for item in diagnostics)
    warning_count = sum(item["severity"] == "WARNING" for item in diagnostics)
    score, deductions = score_diagnostics(diagnostics, config)
    metrics = {
        "score": score,
        "threshold": int(config.get("threshold", 75)),
        "config_version": str(config.get("config_version", "unknown")),
        "error_count": error_count,
        "warning_count": warning_count,
        "deductions": deductions,
    }
    if result["returncode"] != 0 or error_count:
        category = (
            "TOOL_INCOMPATIBILITY"
            if diagnostics
            and all(
                item.get("type") in VERILATOR_UNSUPPORTED_CODES
                or "unsupported" in str(item.get("message", "")).lower()
                for item in diagnostics
            )
            else "QUALITY_ERROR"
        )
        return _result(
            "QUALITY",
            "FAIL",
            category,
            result,
            tool="verilator",
            errors=diagnostics,
            metrics=metrics,
        )
    return _result(
        "QUALITY",
        "PASS",
        "OK",
        result,
        tool="verilator",
        errors=diagnostics,
        metrics=metrics,
    )


def _yosys_quote(path: str) -> str:
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def benchmark(files: list[str]) -> dict[str, Any]:
    with _workspace() as workspace:
        script_path = Path(workspace) / "benchmark.ys"
        inputs = " ".join(_yosys_quote(path) for path in files)
        script_path.write_text(
            f"read_verilog -sv {inputs}\n"
            "hierarchy -auto-top\n"
            "proc\n"
            "opt\n"
            "stat\n",
            encoding="utf-8",
        )
        result = run_command(
            ["yosys", "-s", str(script_path)],
            timeout_seconds=_timeout("benchmark", 120),
        )

    if result["timed_out"] or result["system_error"] or result["returncode"] != 0:
        return _classify_command_failure(
            "BENCHMARK", result, "yosys", "SYNTHESIS_ERROR"
        )

    metrics = {
        name: int(match.group(1)) if (match := pattern.search(result["stdout"])) else None
        for name, pattern in YOSYS_METRICS.items()
    }
    metrics["scope"] = "structural_only_no_sta"
    return _result(
        "BENCHMARK", "PASS", "OK", result, tool="yosys", metrics=metrics
    )


def dispatch(mode: str, files: list[str]) -> dict[str, Any]:
    validated_files, validation_error = validate_input_files(files)
    if validation_error:
        return validation_error

    actions = {
        "compile": compile_verilog,
        "lint": lint_verilog,
        "simulate": simulate,
        "eval_quality": eval_quality,
        "benchmark": benchmark,
    }
    action = actions.get(mode)
    if action is None:
        return _result(
            "INPUT", "FAIL", "UNKNOWN_ACTION", {"stderr": f"Unknown action: {mode}"}
        )
    return action(validated_files)


def main() -> None:
    if len(sys.argv) < 3:
        result = _result(
            "INPUT",
            "FAIL",
            "INVALID_ARGUMENT",
            {
                "stderr": (
                    "Usage: tool_wrapper.py "
                    "[compile|lint|simulate|eval_quality|benchmark] file.v"
                )
            },
        )
    else:
        result = dispatch(sys.argv[1], sys.argv[2:])
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
