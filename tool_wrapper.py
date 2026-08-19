#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
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


def _timeout(name: str, default: int) -> int:
    raw_value = os.environ.get(f"VERILOG_AGENT_{name.upper()}_TIMEOUT", str(default))
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


def run_command(cmd: list[str], *, timeout_seconds: int) -> dict[str, Any]:
    """Run an EDA subprocess with bounded time and structured system failures."""
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
        }
    except subprocess.TimeoutExpired as error:
        return {
            "returncode": -1,
            "stdout": _output_text(error.stdout),
            "stderr": _output_text(error.stderr),
            "timed_out": True,
            "system_error": None,
        }
    except OSError as error:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": str(error),
            "timed_out": False,
            "system_error": str(error),
        }


def _result(
    stage: str,
    status: str,
    category: str,
    command_result: dict[str, Any] | None = None,
    *,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    command_result = command_result or {}
    return {
        "stage": stage,
        "status": status,
        "category": category,
        "errors": errors or [],
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
        return _result(stage, "FAIL", "TIMEOUT", result)
    if result["system_error"]:
        return _result(stage, "FAIL", "SYSTEM_ERROR", result)
    if result["returncode"] != 0:
        return _result(
            stage,
            "FAIL",
            default_category,
            result,
            errors=parse_error(tool, result["stderr"]),
        )
    return _result(stage, "PASS", "OK", result)


def _workspace() -> tempfile.TemporaryDirectory[str]:
    temp_root = os.environ.get("TMPDIR")
    return tempfile.TemporaryDirectory(prefix="verilog-agent-", dir=temp_root)


def validate_input_files(files: list[str]) -> tuple[list[str], dict[str, Any] | None]:
    """Resolve RTL inputs and reject files outside the configured read-only input root."""
    root = Path(os.environ.get("VERILOG_AGENT_INPUT_ROOT", os.getcwd())).resolve()
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
        ["verilator", "--lint-only", "-Wall", *files],
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
    return _classify_command_failure(
        "SIMULATION", simulation_result, "iverilog", "SIMULATION_ERROR"
    )


def dispatch(mode: str, files: list[str]) -> dict[str, Any]:
    validated_files, validation_error = validate_input_files(files)
    if validation_error:
        return validation_error

    actions = {
        "compile": compile_verilog,
        "lint": lint_verilog,
        "simulate": simulate,
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
            {"stderr": "Usage: tool_wrapper.py [compile|lint|simulate] file.v"},
        )
    else:
        result = dispatch(sys.argv[1], sys.argv[2:])
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
