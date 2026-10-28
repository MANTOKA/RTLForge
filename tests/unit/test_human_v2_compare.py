from __future__ import annotations

import pytest

from rtlforge.benchmarks.human_v2_compare import compare_reports


def _report(model: str, statuses: list[str]) -> dict:
    passed = sum(status == "PASS" for status in statuses)
    return {
        "protocol": "oracle_assisted_closed_loop",
        "configuration": {
            "model": model,
            "candidate_count": 2,
            "max_repairs": 1,
            "docker_image": "sandbox:test",
        },
        "metrics": {
            "evaluable": len(statuses),
            "passed": passed,
            "agent_pass_rate_evaluable": passed / len(statuses),
            "passed_after_repair": 0,
        },
        "results": [
            {"problem_id": f"Prob{index:03d}", "status": status}
            for index, status in enumerate(statuses, start=1)
        ],
    }


def test_compare_reports_calculates_paired_model_difference() -> None:
    comparison = compare_reports(
        _report("model-a", ["PASS", "FAIL"]),
        _report("model-b", ["PASS", "PASS"]),
    )

    assert comparison["pass_rate_delta"] == 0.5
    assert comparison["b_pass_a_fail"] == ["Prob002"]


def test_compare_reports_rejects_changed_candidate_budget() -> None:
    report_a = _report("model-a", ["PASS"])
    report_b = _report("model-b", ["PASS"])
    report_b["configuration"]["candidate_count"] = 3

    with pytest.raises(ValueError, match="candidate_count"):
        compare_reports(report_a, report_b)


def test_compare_reports_rejects_changed_memory_mode() -> None:
    report_a = _report("model-a", ["PASS"])
    report_b = _report("model-b", ["PASS"])
    report_a["configuration"]["memory_mode"] = "off"
    report_b["configuration"]["memory_mode"] = "read_write"

    with pytest.raises(ValueError, match="memory_mode"):
        compare_reports(report_a, report_b)
