import tool_wrapper

REQUIRED_KEYS = {
    "schema_version",
    "action",
    "stage",
    "status",
    "category",
    "tool",
    "returncode",
    "duration_ms",
    "errors",
    "metrics",
    "stdout",
    "stderr",
}


def test_unknown_action_uses_tool_result_contract(tmp_path, monkeypatch) -> None:
    rtl = tmp_path / "design.v"
    rtl.write_text("module design; endmodule\n", encoding="utf-8")
    monkeypatch.setenv("VERILOG_AGENT_INPUT_ROOT", str(tmp_path))

    result = tool_wrapper.dispatch("unknown", [str(rtl)])

    assert REQUIRED_KEYS <= result.keys()
    assert result["schema_version"] == 1
    assert result["status"] == "FAIL"
    assert result["category"] == "UNKNOWN_ACTION"


def test_failure_marker_does_not_match_successful_assertion_text() -> None:
    assert tool_wrapper.contains_failure_marker("ASSERTION FAILED")
    assert tool_wrapper.contains_failure_marker("FAIL: mismatch")
    assert not tool_wrapper.contains_failure_marker("all assertions passed")
    assert not tool_wrapper.contains_failure_marker("FAILED=0")
    assert not tool_wrapper.contains_failure_marker("0 failures")
