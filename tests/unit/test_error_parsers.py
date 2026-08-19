from tool_wrapper import parse_iverilog, parse_verilator


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

