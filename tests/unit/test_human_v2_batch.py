from __future__ import annotations

from pathlib import Path

from verilog_agent.benchmarks.human_v2_batch import (
    _markdown_report,
    _metrics,
    _result_from_summary,
)


def test_result_records_success_after_one_repair() -> None:
    summary = {
        "task_id": "task_demo",
        "verified_rtl": True,
        "selected_candidate_id": "cand_repair_01",
        "candidates": [{}, {}, {}],
        "events": [
            {"event_type": "EVENT_VERIFY_FAILED"},
            {"event_type": "EVENT_REPAIR_PROPOSED"},
            {"event_type": "EVENT_VERIFY_PASSED"},
        ],
        "verification": {
            "category": "OK",
            "attempts": [
                {
                    "candidate_id": "cand_repair_01",
                    "debugger_feedback": {
                        "mismatch_summary": {
                            "total_mismatched_samples": 0,
                            "total_samples": 100,
                        }
                    },
                }
            ],
        },
        "timings_ms": {"planner": 10},
    }

    result = _result_from_summary(
        "Prob001_zero", summary, {"status": "PASS"}, runs_root=Path("artifacts")
    )

    assert result["status"] == "PASS"
    assert result["repair_attempts"] == 1
    assert result["verification_cycles"] == 2
    assert result["passed_after_repair"] is True
    assert result["summary_path"] == str(
        Path("artifacts/task_demo/showcase_summary.json")
    )


def test_metrics_exclude_reference_tool_anomalies_from_agent_denominator() -> None:
    results = [
        {"status": "PASS", "passed_after_repair": False},
        {
            "status": "FAIL",
            "failure_category": "FUNCTIONAL_MISMATCH",
            "passed_after_repair": False,
        },
        {
            "status": "SKIPPED_REFERENCE",
            "failure_category": "TOOL_INCOMPATIBILITY",
            "passed_after_repair": False,
        },
    ]

    metrics = _metrics(results)

    assert metrics["evaluable"] == 2
    assert metrics["passed"] == 1
    assert metrics["agent_pass_rate_evaluable"] == 0.5
    assert metrics["reference_skipped"] == 1


def test_markdown_report_contains_failure_reason_and_repair_count() -> None:
    report = {
        "protocol": "oracle_assisted_closed_loop",
        "configuration": {
            "model": "model-a",
            "candidate_count": 2,
            "max_repairs": 1,
            "docker_image": "sandbox:test",
        },
        "metrics": {
            "completed": 1,
            "evaluable": 1,
            "passed": 0,
            "agent_pass_rate_evaluable": 0.0,
            "passed_after_repair": 0,
            "reference_skipped": 0,
            "failure_categories": {"FUNCTIONAL_MISMATCH": 1},
        },
        "results": [
            {
                "problem_id": "Prob154_fsm_ps2data",
                "status": "FAIL",
                "failure_category": "FUNCTIONAL_MISMATCH",
                "reason": "functional mismatch: 978/1619 samples",
                "repair_attempts": 1,
                "verification_cycles": 2,
            }
        ],
    }

    markdown = _markdown_report(report)

    assert "978/1619" in markdown
    assert "| 1 | 2 |" in markdown
