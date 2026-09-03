from tool_wrapper import (
    _classify_command_failure,
    parse_iverilog,
    parse_verilator,
    score_diagnostics,
)


def test_parse_iverilog_syntax_error_without_explicit_severity() -> None:
    errors = parse_iverilog("counter_bad.v:7: syntax error\nI give up.\n")

    assert errors == [
        {
            "tool": "iverilog",
            "severity": "ERROR",
            "type": "ERROR",
            "file": "counter_bad.v",
            "line": 7,
            "message": "syntax error",
        }
    ]


def test_parse_iverilog_warning_preserves_severity() -> None:
    errors = parse_iverilog("counter.v:10: warning: example warning\n")

    assert errors[0]["type"] == "WARNING"
    assert errors[0]["line"] == 10


def test_parse_verilator_width_diagnostic() -> None:
    errors = parse_verilator("%Error-WIDTH: counter.v:10:5: Width mismatch\n")

    assert errors == [
        {
            "tool": "verilator",
            "severity": "ERROR",
            "type": "WIDTH",
            "file": "counter.v",
            "line": 10,
            "message": "Width mismatch",
        }
    ]


def test_verilator_unsupported_construct_is_tool_incompatibility() -> None:
    result = {
        "returncode": 1,
        "stdout": "",
        "stderr": (
            "%Error-BLKLOOPINIT: predictor.v:30:24: Unsupported: "
            "Delayed assignment to array inside for loops\n"
        ),
        "timed_out": False,
        "system_error": None,
        "duration_ms": 3,
    }

    classified = _classify_command_failure("LINT", result, "verilator", "LINT_ERROR")

    assert classified["category"] == "TOOL_INCOMPATIBILITY"
    assert classified["errors"][0]["type"] == "BLKLOOPINIT"


def test_score_diagnostics_uses_versioned_rule_weights() -> None:
    diagnostics = [
        {
            "tool": "verilator",
            "severity": "WARNING",
            "type": "LATCH",
            "file": "counter.v",
            "line": 8,
            "message": "Latch inferred",
        },
        {
            "tool": "verilator",
            "severity": "WARNING",
            "type": "UNUSEDSIGNAL",
            "file": "counter.v",
            "line": 3,
            "message": "Signal is not used",
        },
    ]
    config = {
        "initial_score": 100,
        "default_warning_penalty": 2,
        "default_error_penalty": 25,
        "penalties": {"LATCH": 30, "UNUSED": 5},
    }

    score, deductions = score_diagnostics(diagnostics, config)

    assert score == 65
    assert [item["penalty"] for item in deductions] == [30, 5]
    assert deductions[0]["evidence"]["line"] == 8

