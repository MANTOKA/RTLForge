import json
from pathlib import Path

import pytest

from verilog_agent.benchmarks.rtllm import RTLLMCase
from verilog_agent.benchmarks.rtllm_batch import (
    _initial_report,
    _is_transient_error,
    _markdown_report,
    _metrics,
    _prepare_transient_retry,
    _reference_skip,
    _validate_resume,
    _write_reports,
)
from verilog_agent.providers import ProviderSettings


def _settings() -> ProviderSettings:
    return ProviderSettings(
        api_key="test",
        model="model-a",
        base_url=None,
        timeout_seconds=30,
        max_output_tokens=100,
        max_retries=0,
    )


def test_retry_usage_includes_failed_attempt_and_is_stable_when_report_rewritten(tmp_path):
    report = _initial_report(_args(tmp_path), _settings(), 1)
    def result(status, tokens):
        return {
            "problem_id": "adder", "status": status,
            "repair_attempts": 0, "verification_cycles": 1,
            "model_calls": [{"role": "generator", "status": status,
                             "usage_status": "REPORTED", "input_tokens": tokens,
                             "output_tokens": 5, "cached_input_tokens": 0}],
        }
    report["results"] = [result("PASS", 30)]
    report["retry_history"] = [{"replaced_results": [result("ERROR", 20)]}]
    path = tmp_path / "report.json"
    for _ in range(2):
        _write_reports(path, report)
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["metrics"]["token_usage"]["call_count"] == 2
        assert saved["metrics"]["token_usage"]["input_tokens"] == 50
        assert saved["metrics"]["token_usage"]["failed_calls"] == 1


def _args(tmp_path: Path):
    return type(
        "Args",
        (),
        {
            "candidates": 3,
            "max_repairs": 2,
            "docker_image": "sandbox:test",
            "dataset_root": tmp_path,
            "memory_mode": "off",
            "memory_database": tmp_path / "memory.sqlite",
        },
    )()


def test_rtllm_report_records_protocol_and_metrics(tmp_path: Path) -> None:
    report = _initial_report(_args(tmp_path), _settings(), 2)
    report["results"] = [
        {
            "problem_id": "adder_8bit",
            "category": "Arithmetic",
            "status": "PASS",
            "failure_category": None,
            "repair_attempts": 0,
            "verification_cycles": 1,
            "passed_after_repair": False,
        },
        {
            "problem_id": "fsm",
            "category": "Control",
            "status": "FAIL",
            "failure_category": "FUNCTIONAL_MISMATCH",
            "repair_attempts": 2,
            "verification_cycles": 3,
            "passed_after_repair": False,
        },
    ]
    report["metrics"] = _metrics(report["results"])

    markdown = _markdown_report(report)

    assert report["dataset"] == "RTLLM-2.1"
    assert report["metrics"]["passed"] == 1
    assert report["metrics"]["categories"] == {"Arithmetic": 1, "Control": 1}
    assert "# RTLLM-2.1 Evaluation Report" in markdown
    assert "adder_8bit" in markdown


def test_reference_skip_retains_rtllm_category(tmp_path: Path) -> None:
    path = tmp_path / "unused"
    case = RTLLMCase(
        problem_id="adder_8bit",
        category="Arithmetic",
        prompt_path=path,
        testbench_path=path,
        reference_path=path,
        module_name="adder_8bit",
        reference_module_name="verified_adder_8bit",
    )

    result = _reference_skip(
        case,
        {"category": "TOOL_INCOMPATIBILITY", "reason": "unsupported construct"},
    )

    assert result["status"] == "SKIPPED_REFERENCE"
    assert result["category"] == "Arithmetic"


def test_resume_rejects_changed_candidate_count(tmp_path: Path) -> None:
    args = _args(tmp_path)
    report = _initial_report(args, _settings(), 1)
    args.candidates = 2

    with pytest.raises(ValueError, match="candidate_count"):
        _validate_resume(report, args, _settings())


@pytest.mark.parametrize(
    "reason",
    [
        "Planner failed: APITimeoutError: Request timed out.",
        "Generator failed: APIConnectionError: Connection error.",
        "provider request failed: RateLimitError: HTTP 429",
        "provider request failed: HTTP 503",
    ],
)
def test_transient_provider_failures_are_retryable(reason: str) -> None:
    assert _is_transient_error(
        {
            "status": "ERROR",
            "failure_category": "PIPELINE_ERROR",
            "reason": reason,
        }
    )


def test_agent_and_functional_failures_are_not_transient() -> None:
    assert not _is_transient_error(
        {
            "status": "ERROR",
            "failure_category": "PIPELINE_ERROR",
            "reason": "Planner failed: clock must be a simple Verilog identifier",
        }
    )
    assert not _is_transient_error(
        {
            "status": "FAIL",
            "failure_category": "FUNCTIONAL_MISMATCH",
            "reason": "request timed out in generated RTL simulation",
        }
    )


def test_prepare_transient_retry_preserves_history_and_stable_results() -> None:
    report = {
        "results": [
            {"problem_id": "pass", "status": "PASS"},
            {
                "problem_id": "timeout",
                "status": "ERROR",
                "failure_category": "PIPELINE_ERROR",
                "reason": "APITimeoutError: Request timed out.",
            },
            {
                "problem_id": "mismatch",
                "status": "FAIL",
                "failure_category": "FUNCTIONAL_MISMATCH",
                "reason": "mismatch",
            },
        ]
    }

    prepared = _prepare_transient_retry(report)

    assert [item["problem_id"] for item in prepared["results"]] == ["pass", "mismatch"]
    history = prepared["retry_history"][0]
    assert history["policy"] == "transient_api_provider_errors_only"
    assert history["replaced_results"][0]["problem_id"] == "timeout"


def test_markdown_report_includes_transient_retry_history(tmp_path: Path) -> None:
    report = _initial_report(_args(tmp_path), _settings(), 1)
    report["results"] = [
        {
            "problem_id": "adder_8bit",
            "category": "Arithmetic",
            "status": "PASS",
            "failure_category": None,
            "repair_attempts": 0,
            "verification_cycles": 1,
            "passed_after_repair": False,
        }
    ]
    report["retry_history"] = [
        {
            "replaced_results": [
                {
                    "problem_id": "adder_8bit",
                    "status": "ERROR",
                    "reason": "APITimeoutError: Request timed out.",
                }
            ]
        }
    ]
    report["metrics"] = _metrics(report["results"])

    markdown = _markdown_report(report)

    assert "Transient retry rounds: 1" in markdown
    assert "APITimeoutError" in markdown
    assert "| PASS |" in markdown
