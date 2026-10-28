from __future__ import annotations

from pathlib import Path

from rtlforge.benchmarks.human_v2_batch import (
    _default_output,
    _markdown_report,
    _metrics,
    _rerunnable_failure_ids,
    _result_from_summary,
    _terminal_result,
)
from rtlforge.benchmarks.verilogeval import human_v2_run_root
from rtlforge.providers import ProviderSettings


def test_human_v2_artifacts_are_grouped_by_dataset_and_model() -> None:
    root = human_v2_run_root(Path("runs"), "gpt-5.6-terra")

    assert root == Path("runs/human-v2/gpt-5.6-terra")


def test_default_batch_report_uses_group_reports_directory() -> None:
    args = type("Args", (), {})()
    args.rerun_failures_from = None
    args.runs_root = Path("runs/human-v2/gpt-5.6-terra/tasks")
    settings = ProviderSettings(
        api_key="test",
        model="gpt-5.6-terra",
        base_url=None,
        timeout_seconds=30,
        max_output_tokens=100,
        max_retries=0,
    )

    assert _default_output(args, settings) == Path(
        "runs/human-v2/gpt-5.6-terra/reports/human_v2_batch.json"
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


def test_regenerated_winner_is_not_counted_as_repair_success() -> None:
    summary = {
        "task_id": "regenerated", "verified_rtl": True,
        "selected_candidate_id": "cand_02",
        "candidates": [
            {"candidate_id": "cand_01", "parent_id": None},
            {"candidate_id": "cand_repair_01", "parent_id": "cand_01"},
            {"candidate_id": "cand_02", "parent_id": None},
        ],
        "budget": {"repair_attempts": 2, "generated_candidates": 2},
        "events": [{"event_type": "EVENT_REPAIR_PROPOSED"}],
    }
    result = _result_from_summary("Prob001_zero", summary, {"status": "PASS"})
    assert result["passed_after_repair"] is False
    assert result["repair_attempts"] == 2
    assert result["generated_candidates"] == 2


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
                "task_id": "task_failed",
                "selected_candidate_id": "cand_02",
                "repair_attempts": 1,
                "verification_cycles": 2,
            }
        ],
    }

    markdown = _markdown_report(report)

    assert "978/1619" in markdown
    assert "| 1 | 2 |" in markdown
    assert "task_failed" in markdown
    assert "cand_02" in markdown


def test_terminal_result_keeps_status_and_adds_traceability() -> None:
    line = _terminal_result(
        2,
        10,
        {
            "problem_id": "Prob002_one",
            "status": "FAIL",
            "failure_category": "FUNCTIONAL_MISMATCH",
            "repair_attempts": 2,
            "task_id": "task_abc",
            "selected_candidate_id": "cand_repair_02",
            "reason": "functional mismatch: 4/20 samples",
            "stop_reason": "repair budget exhausted",
        },
    )

    assert "FAIL [FUNCTIONAL_MISMATCH] repairs=2" in line
    assert "task_id=task_abc" in line
    assert "selected=cand_repair_02" in line
    assert "reason=functional mismatch: 4/20 samples" in line


def test_terminal_pass_keeps_compact_status_and_selected_candidate() -> None:
    line = _terminal_result(
        1,
        3,
        {
            "problem_id": "Prob001_zero",
            "status": "PASS",
            "failure_category": None,
            "repair_attempts": 0,
            "task_id": "task_pass",
            "selected_candidate_id": "cand_02",
        },
    )

    assert "PASS [OK] repairs=0" in line
    assert "task_id=task_pass selected=cand_02" in line


def test_failure_rerun_selects_functional_and_repairable_interface_failures(
    tmp_path: Path,
) -> None:
    summary_path = tmp_path / "system_summary.json"
    summary_path.write_text(
        '{"verification":{"attempts":[{"debugger_feedback":{"errors":['
        '{"message":"Unknown module type: TopModule"}]}}]}}',
        encoding="utf-8",
    )
    report = {
        "results": [
            {
                "problem_id": "Prob001",
                "status": "FAIL",
                "failure_category": "FUNCTIONAL_MISMATCH",
            },
            {
                "problem_id": "Prob002",
                "status": "SKIPPED_REFERENCE",
                "failure_category": "TOOL_INCOMPATIBILITY",
            },
            {
                "problem_id": "Prob003",
                "status": "FAIL",
                "failure_category": "SYSTEM_ERROR",
                "summary_path": str(summary_path),
            },
            {
                "problem_id": "Prob004",
                "status": "FAIL",
                "failure_category": "SYSTEM_ERROR",
            },
            {"problem_id": "Prob005", "status": "PASS", "failure_category": None},
        ]
    }

    assert _rerunnable_failure_ids(report) == {"Prob001", "Prob003"}
