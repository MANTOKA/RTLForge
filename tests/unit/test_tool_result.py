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
    monkeypatch.setenv("RTLFORGE_INPUT_ROOT", str(tmp_path))

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


def test_failure_marker_detects_human_v2_nonzero_mismatch_count() -> None:
    output = (
        "Hint: Output 'q' has 386 mismatches. First mismatch occurred at time 30.\n"
        "Hint: Total mismatched samples is 386 out of 439 samples\n"
        "Mismatches: 386 in 439 samples\n"
    )

    assert tool_wrapper.contains_failure_marker(output)
    assert not tool_wrapper.contains_failure_marker("Mismatches: 0 in 439 samples")
