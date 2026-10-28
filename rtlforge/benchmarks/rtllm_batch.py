from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from rtlforge.benchmarks.human_v2_batch import (
    _reference_reason,
    _run_case,
    _terminal_result,
)
from rtlforge.benchmarks.preflight import preflight_references
from rtlforge.benchmarks.rtllm import (
    RTLLMCase,
    adapt_rtllm_reference,
    load_rtllm,
    rtllm_run_root,
)
from rtlforge.memory import DEFAULT_MEMORY_DATABASE, SQLiteMemoryStore
from rtlforge.providers import (
    OpenAIResponsesProvider,
    ProviderConfigurationError,
    ProviderSettings,
)
from rtlforge.providers.usage import (
    batch_usage,
    prompt_source_version,
    sampling_markdown,
    usage_markdown,
)
from rtlforge.services import DockerEDARunner


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Run RTLForge sequentially on RTLLM 2.1"
    )
    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=Path("data/benchmarks/rtllm-2.1"),
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--candidates", type=int, choices=(1, 2, 3), default=2,
        help="Independent candidate cap; start with one, regenerate on demand (3 is opt-in)",
    )
    parser.add_argument("--max-repairs", type=int, choices=(0, 1, 2, 3), default=2)
    parser.add_argument("--model")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--docker-image", default="rtlforge-sandbox:latest")
    parser.add_argument("--runs-root", type=Path, default=Path("runs"))
    parser.add_argument("--database", type=Path)
    parser.add_argument(
        "--memory-mode",
        choices=("off", "read_only", "read_write"),
        default="read_write",
    )
    parser.add_argument("--memory-database", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Continue the same report and skip already recorded designs",
    )
    parser.add_argument(
        "--retry-transient-errors",
        action="store_true",
        help=(
            "With --resume, rerun recorded transient API/provider failures while "
            "keeping PASS and stable outcomes"
        ),
    )
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.retry_transient_errors and not args.resume:
        parser.error("--retry-transient-errors requires --resume")

    load_dotenv(args.env_file, override=False)
    try:
        settings = ProviderSettings.from_env()
        if args.model:
            settings = replace(settings, model=args.model)
        provider = OpenAIResponsesProvider(settings)
    except ProviderConfigurationError as error:
        parser.error(str(error))

    group_root = rtllm_run_root(args.runs_root, settings.model)
    args.runs_root = group_root / "tasks"
    args.database = args.database or group_root / "state" / "rtllm_batch.sqlite"
    args.memory_database = args.memory_database or DEFAULT_MEMORY_DATABASE
    output = args.output or group_root / "reports" / "rtllm_batch.json"
    cases = load_rtllm(args.dataset_root)
    if args.limit is not None:
        cases = cases[: args.limit]

    report = _initial_report(args, settings, len(cases))
    if args.resume and output.is_file():
        report = json.loads(output.read_text(encoding="utf-8"))
        try:
            _validate_resume(report, args, settings)
        except ValueError as error:
            parser.error(str(error))
        if args.retry_transient_errors:
            report = _prepare_transient_retry(report)
    completed_ids = {
        str(item["problem_id"]) for item in report.get("results", [])
    }
    runner = DockerEDARunner(
        args.runs_root,
        image=args.docker_image,
        readonly_roots=(args.dataset_root.resolve(),),
    )
    memory_store = (
        SQLiteMemoryStore(args.memory_database, toolchain_version=args.docker_image)
        if args.memory_mode != "off"
        else None
    )

    for index, case in enumerate(cases, start=1):
        if case.problem_id in completed_ids:
            print(f"[{index}/{len(cases)}] {case.problem_id}: already recorded")
            continue
        reference = preflight_references(
            [case],
            runner,
            group_root / "reference" / f"{case.problem_id}.json",
            artifact_root=args.runs_root / "_reference_adapters",
            reference_adapter=adapt_rtllm_reference,
            include_original_reference=False,
            dataset_name="RTLLM-2.1",
        )["results"][0]
        if reference["category"] == "SYSTEM_ERROR":
            report["last_error"] = {
                "problem_id": case.problem_id,
                "category": "SYSTEM_ERROR",
                "reason": _reference_reason(reference),
            }
            _write_reports(output, report)
            print(
                "Infrastructure error; stopped before model call: "
                f"{report['last_error']['reason']}",
                file=sys.stderr,
            )
            return 2
        if reference["status"] != "PASS":
            result = _reference_skip(case, reference)
        else:
            result = _run_case(
                case=case,
                provider=provider,
                provider_label=f"openai_responses model={settings.model}",
                runner=runner,
                args=args,
                reference=reference,
                memory_store=memory_store,
                required_module_name=case.module_name,
                testbench_source=f"RTLLM-2.1/{case.problem_id}",
                include_reference_in_verification=False,
            )
            result["category"] = case.category
        report.pop("last_error", None)
        report.setdefault("results", []).append(result)
        _write_reports(output, report)
        print(_terminal_result(index, len(cases), result), flush=True)

    _write_reports(output, report)
    metrics = _metrics(report.get("results", []))
    print(
        f"Completed: {metrics['passed']}/{metrics['evaluable']} evaluable designs passed "
        f"({metrics['agent_pass_rate_evaluable']:.2%})"
    )
    print(f"JSON report: {output}")
    print(f"Markdown report: {output.with_suffix('.md')}")
    return 0


def _reference_skip(case: RTLLMCase, reference: dict[str, Any]) -> dict[str, Any]:
    return {
        "problem_id": case.problem_id,
        "category": case.category,
        "status": "SKIPPED_REFERENCE",
        "failure_category": reference["category"],
        "reason": _reference_reason(reference),
        "reference": reference,
        "repair_attempts": 0,
        "verification_cycles": 0,
        "passed_after_repair": False,
    }


def _initial_report(
    args: argparse.Namespace, settings: ProviderSettings, case_count: int
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "dataset": "RTLLM-2.1",
        "protocol": "oracle_assisted_closed_loop",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "configuration": {
            "model": settings.model,
            "base_url": settings.base_url,
            "candidate_count": args.candidates,
            "candidate_policy": "sequential_repair_first_v1",
            "initial_candidates": 1,
            "regeneration_threshold": 75,
            "max_repairs": args.max_repairs,
            "docker_image": args.docker_image,
            "dataset_root": str(args.dataset_root.resolve()),
            "memory_mode": args.memory_mode,
            "memory_database": str(args.memory_database),
            "requested_case_count": case_count,
            "oracle_visibility": {
                "planner_generator": "design_description_only",
                "debugger": "structured_diagnostics_only",
                "verification": "hidden_testbench",
                "verified_rtl": "preflight_only_never_agent_context",
            },
            "sampling": settings.sampling_configuration(),
            "prompt_version": prompt_source_version(),
        },
        "results": [],
    }


def _validate_resume(
    report: dict[str, Any], args: argparse.Namespace, settings: ProviderSettings
) -> None:
    configuration = report.get("configuration", {})
    expected = {
        "sampling": settings.sampling_configuration(),
        "prompt_version": prompt_source_version(),
        "model": settings.model,
        "base_url": settings.base_url,
        "candidate_count": args.candidates,
        "candidate_policy": "sequential_repair_first_v1",
        "max_repairs": args.max_repairs,
        "docker_image": args.docker_image,
        "dataset_root": str(args.dataset_root.resolve()),
        "memory_mode": args.memory_mode,
        "memory_database": str(args.memory_database),
    }
    changed = [key for key, value in expected.items() if configuration.get(key) != value]
    if changed:
        raise ValueError(f"cannot resume with changed settings: {', '.join(changed)}")


def _metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    passed = sum(item.get("status") == "PASS" for item in results)
    skipped = sum(item.get("status") == "SKIPPED_REFERENCE" for item in results)
    evaluable = len(results) - skipped
    return {
        "completed": len(results),
        "token_usage": batch_usage(results),
        "evaluable": evaluable,
        "passed": passed,
        "failed": evaluable - passed,
        "reference_skipped": skipped,
        "agent_pass_rate_evaluable": passed / evaluable if evaluable else 0.0,
        "passed_after_repair": sum(item.get("passed_after_repair", False) for item in results),
        "failure_categories": dict(
            sorted(
                Counter(
                    str(item.get("failure_category"))
                    for item in results
                    if item.get("failure_category")
                ).items()
            )
        ),
        "categories": dict(
            sorted(Counter(str(item.get("category", "Unknown")) for item in results).items())
        ),
    }


_TRANSIENT_ERROR_MARKERS = (
    "apitimeouterror",
    "request timed out",
    "apiconnectionerror",
    "connection error",
    "ratelimiterror",
    "rate limit",
    "http 429",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
)


def _is_transient_error(result: dict[str, Any]) -> bool:
    if result.get("status") != "ERROR":
        return False
    if result.get("failure_category") not in {"PIPELINE_ERROR", "SYSTEM_ERROR"}:
        return False
    reason = str(result.get("reason", "")).lower()
    return any(marker in reason for marker in _TRANSIENT_ERROR_MARKERS)


def _prepare_transient_retry(report: dict[str, Any]) -> dict[str, Any]:
    results = list(report.get("results", []))
    retryable = [item for item in results if _is_transient_error(item)]
    if not retryable:
        return report
    report["results"] = [item for item in results if not _is_transient_error(item)]
    report.setdefault("retry_history", []).append(
        {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "policy": "transient_api_provider_errors_only",
            "replaced_results": retryable,
        }
    )
    return report


def _write_reports(output: Path, report: dict[str, Any]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    report["updated_at"] = datetime.now(timezone.utc).isoformat()
    report["metrics"] = _metrics(report.get("results", []))
    previous_results = [
        row for retry in report.get("retry_history", [])
        for row in retry.get("replaced_results", [])
    ]
    report["metrics"]["token_usage"] = batch_usage(report.get("results", []) + previous_results)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    output.with_suffix(".md").write_text(
        _markdown_report(report), encoding="utf-8", newline="\n"
    )


def _markdown_report(report: dict[str, Any]) -> str:
    config = report["configuration"]
    metrics = report["metrics"]
    lines = [
        "# RTLLM-2.1 Evaluation Report",
        "",
        f"- Model: `{config['model']}`",
        sampling_markdown(config.get("sampling", {})),
        f"- Prompt source version: `{config.get('prompt_version', 'UNKNOWN')}`",
        f"- Protocol: `{report['protocol']}`",
        f"- Candidate cap per design: {config['candidate_count']}",
        f"- Candidate policy: {config.get('candidate_policy', 'legacy_batch')}",
        f"- Maximum repairs: {config['max_repairs']}",
        f"- Docker image: `{config['docker_image']}`",
        f"- Memory mode: `{config['memory_mode']}`",
        f"- Completed: {metrics['completed']}",
        f"- Evaluable: {metrics['evaluable']}",
        f"- Passed: {metrics['passed']}",
        f"- Pass rate: {metrics['agent_pass_rate_evaluable']:.2%}",
        f"- Passed after repair: {metrics['passed_after_repair']}",
        f"- Reference/tool anomalies: {metrics['reference_skipped']}",
        usage_markdown(metrics.get("token_usage", {})),
    ]
    retry_history = report.get("retry_history", [])
    if retry_history:
        lines.extend(
            [
                f"- Transient retry rounds: {len(retry_history)}",
                "",
                "## Transient retry history",
                "",
                "| Round | Design | Previous result | Previous failure | Current result |",
                "|---:|---|---|---|---|",
            ]
        )
        current_by_id = {
            str(item.get("problem_id")): item for item in report.get("results", [])
        }
        for round_number, retry in enumerate(retry_history, start=1):
            for previous in retry.get("replaced_results", []):
                problem_id = str(previous.get("problem_id", ""))
                current = current_by_id.get(problem_id, {})
                reason = str(previous.get("reason", "")).replace("|", "\\|").replace(
                    "\n", " "
                )
                lines.append(
                    f"| {round_number} | {problem_id} | {previous.get('status', '')} | "
                    f"{reason} | {current.get('status', 'PENDING')} |"
                )
        lines.extend(["", "## Final per-design results", ""])
    else:
        lines.append("")
    lines.extend(
        [
        "| Design | Category | Result | Task ID | Selected | Failure | Reason | Repairs | Cycles |",
        "|---|---|---|---|---|---|---|---:|---:|",
        ]
    )
    for result in report.get("results", []):
        reason = str(result.get("reason", "")).replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {result['problem_id']} | {result.get('category', '')} | {result['status']} | "
            f"{result.get('task_id') or ''} | {result.get('selected_candidate_id') or ''} | "
            f"{result.get('failure_category') or ''} | {reason} | "
            f"{result['repair_attempts']} | {result['verification_cycles']} |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
