from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from verilog_agent.agents.debugger import RealDebugger
from verilog_agent.agents.generator import ArtifactRepository
from verilog_agent.agents.showcase import PlannerGeneratorShowcase, ShowcaseRunError
from verilog_agent.benchmarks.preflight import preflight_references
from verilog_agent.benchmarks.verilogeval import VerilogEvalCase, load_human_v2
from verilog_agent.providers import (
    OpenAIResponsesProvider,
    ProviderConfigurationError,
    ProviderSettings,
)
from verilog_agent.services import (
    DockerEDARunner,
    GoldenTestbenchVerificationService,
    StaticCandidateEvaluator,
)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Run VerilogAgent on Human v2 and continuously write JSON/Markdown reports"
    )
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--candidates", type=int, choices=(2, 3), default=2)
    parser.add_argument("--max-repairs", type=int, choices=(0, 1, 2, 3), default=1)
    parser.add_argument("--model", help="Override VERILOG_AGENT_MODEL for all LLM roles")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--docker-image", default="verilog-agent-sandbox:reflection")
    parser.add_argument("--runs-root", type=Path, default=Path("runs"))
    parser.add_argument("--database", type=Path, default=Path("work/human_v2_batch.sqlite"))
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Continue an existing --output report and skip completed problem IDs",
    )
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")

    load_dotenv(args.env_file, override=False)
    try:
        settings = ProviderSettings.from_env()
        if args.model:
            settings = replace(settings, model=args.model)
        provider = OpenAIResponsesProvider(settings)
    except ProviderConfigurationError as error:
        parser.error(str(error))

    cases = load_human_v2(args.dataset_root)
    if args.limit is not None:
        cases = cases[: args.limit]
    output = args.output or (
        args.runs_root / "benchmarks" / f"human_v2_batch_{_safe_name(settings.model)}.json"
    )
    report = _initial_report(args, settings, len(cases))
    if args.resume and output.is_file():
        report = json.loads(output.read_text(encoding="utf-8"))
        try:
            _validate_resume(report, args, settings)
        except ValueError as error:
            parser.error(str(error))
        report["configuration"]["requested_case_count"] = len(cases)
    completed_ids = {item["problem_id"] for item in report.get("results", [])}

    runner = DockerEDARunner(
        args.runs_root,
        image=args.docker_image,
        readonly_roots=(args.dataset_root.resolve(),),
    )
    for index, case in enumerate(cases, start=1):
        if case.problem_id in completed_ids:
            print(f"[{index}/{len(cases)}] {case.problem_id}: already recorded")
            continue
        print(f"[{index}/{len(cases)}] {case.problem_id}: reference preflight", flush=True)
        reference = preflight_references(
            [case],
            runner,
            args.runs_root / "benchmarks" / "reference" / f"{case.problem_id}.json",
        )["results"][0]
        if reference["category"] == "SYSTEM_ERROR":
            report["last_error"] = {
                "problem_id": case.problem_id,
                "category": "SYSTEM_ERROR",
                "reason": _reference_reason(reference),
            }
            _write_reports(output, report)
            print(
                f"Infrastructure error; stopped before model call: "
                f"{report['last_error']['reason']}",
                file=sys.stderr,
            )
            return 2
        if reference["status"] != "PASS":
            result = {
                "problem_id": case.problem_id,
                "status": "SKIPPED_REFERENCE",
                "failure_category": reference["category"],
                "reason": _reference_reason(reference),
                "reference": reference,
                "repair_attempts": 0,
                "verification_cycles": 0,
                "passed_after_repair": False,
            }
        else:
            print(f"[{index}/{len(cases)}] {case.problem_id}: Agent pipeline", flush=True)
            result = _run_case(
                case=case,
                provider=provider,
                provider_label=f"openai_responses model={settings.model}",
                runner=runner,
                args=args,
                reference=reference,
            )
        report.pop("last_error", None)
        report.setdefault("results", []).append(result)
        _write_reports(output, report)
        print(
            f"[{index}/{len(cases)}] {case.problem_id}: {result['status']} "
            f"[{result.get('failure_category') or 'OK'}] repairs={result['repair_attempts']}",
            flush=True,
        )

    _write_reports(output, report)
    metrics = _metrics(report.get("results", []))
    print(
        f"Completed: {metrics['passed']}/{metrics['evaluable']} evaluable cases passed "
        f"({metrics['agent_pass_rate_evaluable']:.2%})"
    )
    print(f"JSON report: {output}")
    print(f"Markdown report: {output.with_suffix('.md')}")
    return 0


def _run_case(
    *,
    case: VerilogEvalCase,
    provider: OpenAIResponsesProvider,
    provider_label: str,
    runner: DockerEDARunner,
    args: argparse.Namespace,
    reference: dict[str, Any],
) -> dict[str, Any]:
    showcase = PlannerGeneratorShowcase(
        provider=provider,
        provider_label=provider_label,
        database=args.database,
        runs_root=args.runs_root,
        evaluator=StaticCandidateEvaluator(runner=runner, runs_root=args.runs_root),
        verifier=GoldenTestbenchVerificationService(
            runner=runner,
            runs_root=args.runs_root,
            testbench_resolver=lambda _context: case.testbench_path,
            reference_resolver=lambda _context: case.reference_path,
            testbench_source=f"VerilogEval-Human-v2/{case.problem_id}",
        ),
        debugger=(
            RealDebugger(provider, artifacts=ArtifactRepository(args.runs_root))
            if args.max_repairs
            else None
        ),
        emit=lambda _line: None,
    )
    try:
        summary = showcase.run(
            case.read_agent_spec(),
            candidate_count=args.candidates,
            max_repairs=args.max_repairs,
        )
    except ShowcaseRunError as error:
        return {
            "problem_id": case.problem_id,
            "status": "ERROR",
            "failure_category": "PIPELINE_ERROR",
            "reason": str(error),
            "reference": reference,
            "repair_attempts": 0,
            "verification_cycles": 0,
            "passed_after_repair": False,
        }
    return _result_from_summary(
        case.problem_id, summary, reference, runs_root=args.runs_root
    )


def _result_from_summary(
    problem_id: str,
    summary: dict[str, Any],
    reference: dict[str, Any],
    *,
    runs_root: Path = Path("runs"),
) -> dict[str, Any]:
    events = summary.get("events", [])
    repair_attempts = sum(
        event.get("event_type") == "EVENT_REPAIR_PROPOSED" for event in events
    )
    verification_cycles = sum(
        event.get("event_type") in {"EVENT_VERIFY_PASSED", "EVENT_VERIFY_FAILED"}
        for event in events
    )
    verification = summary.get("verification") or {}
    attempts = verification.get("attempts", [])
    mismatch = {}
    if attempts:
        mismatch = attempts[-1].get("debugger_feedback", {}).get("mismatch_summary", {})
    failure_category = None if summary.get("verified_rtl") else _failure_category(summary)
    return {
        "problem_id": problem_id,
        "status": "PASS" if summary.get("verified_rtl") else "FAIL",
        "failure_category": failure_category,
        "reason": "" if summary.get("verified_rtl") else _failure_reason(summary, mismatch),
        "task_id": summary.get("task_id"),
        "selected_candidate_id": summary.get("selected_candidate_id"),
        "candidate_count": len(summary.get("candidates", [])),
        "repair_attempts": repair_attempts,
        "verification_cycles": verification_cycles,
        "passed_after_repair": bool(summary.get("verified_rtl") and repair_attempts),
        "mismatch_summary": mismatch,
        "timings_ms": summary.get("timings_ms", {}),
        "reference": reference,
        "summary_path": str(runs_root / str(summary.get("task_id")) / "showcase_summary.json"),
    }


def _failure_category(summary: dict[str, Any]) -> str:
    verification = summary.get("verification") or {}
    category = verification.get("category")
    if category and category != "OK":
        return str(category)
    for event in reversed(summary.get("events", [])):
        if event.get("event_type") == "EVENT_SYSTEM_FAILED":
            return str(event.get("payload", {}).get("category", "UNKNOWN"))
    return "UNKNOWN"


def _failure_reason(summary: dict[str, Any], mismatch: dict[str, Any]) -> str:
    total = mismatch.get("total_mismatched_samples")
    samples = mismatch.get("total_samples")
    if total is not None:
        return f"functional mismatch: {total}/{samples} samples"
    for event in reversed(summary.get("events", [])):
        if event.get("event_type") == "EVENT_SYSTEM_FAILED":
            return str(event.get("payload", {}).get("reason", "unspecified failure"))
    return "unspecified failure"


def _reference_reason(reference: dict[str, Any]) -> str:
    if reference.get("reason"):
        return str(reference["reason"])
    diagnostics = reference.get("diagnostics", [])
    if diagnostics:
        return str(diagnostics[0].get("message", reference.get("category", "UNKNOWN")))
    return str(reference.get("category", "UNKNOWN"))


def _initial_report(
    args: argparse.Namespace, settings: ProviderSettings, case_count: int
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "dataset": "VerilogEval-Human-v2",
        "protocol": "oracle_assisted_closed_loop",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "configuration": {
            "model": settings.model,
            "base_url": settings.base_url,
            "candidate_count": args.candidates,
            "max_repairs": args.max_repairs,
            "docker_image": args.docker_image,
            "dataset_root": str(args.dataset_root.resolve()),
            "requested_case_count": case_count,
            "oracle_visibility": {
                "planner_generator": "prompt_only",
                "debugger": "structured_diagnostics_only",
                "verification": "golden_testbench_and_reference_black_box",
            },
            "sampling": {
                "temperature": "provider_default",
                "seed": "not_configured",
            },
        },
        "results": [],
    }


def _validate_resume(
    report: dict[str, Any], args: argparse.Namespace, settings: ProviderSettings
) -> None:
    configuration = report.get("configuration", {})
    expected = {
        "model": settings.model,
        "base_url": settings.base_url,
        "candidate_count": args.candidates,
        "max_repairs": args.max_repairs,
        "docker_image": args.docker_image,
        "dataset_root": str(args.dataset_root.resolve()),
    }
    mismatched = [key for key, value in expected.items() if configuration.get(key) != value]
    if mismatched:
        raise ValueError(f"cannot resume with changed settings: {', '.join(mismatched)}")


def _metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    passed = sum(item.get("status") == "PASS" for item in results)
    skipped = sum(item.get("status") == "SKIPPED_REFERENCE" for item in results)
    evaluable = len(results) - skipped
    categories = Counter(
        str(item.get("failure_category"))
        for item in results
        if item.get("failure_category")
    )
    return {
        "completed": len(results),
        "evaluable": evaluable,
        "passed": passed,
        "failed": evaluable - passed,
        "reference_skipped": skipped,
        "agent_pass_rate_evaluable": passed / evaluable if evaluable else 0.0,
        "passed_after_repair": sum(item.get("passed_after_repair", False) for item in results),
        "failure_categories": dict(sorted(categories.items())),
    }


def _write_reports(output: Path, report: dict[str, Any]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    report["updated_at"] = datetime.now(timezone.utc).isoformat()
    report["metrics"] = _metrics(report.get("results", []))
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
        "# VerilogEval-Human-v2 Evaluation Report",
        "",
        f"- Model: `{config['model']}`",
        f"- Protocol: `{report['protocol']}`",
        f"- Candidates per task: {config['candidate_count']}",
        f"- Maximum repairs: {config['max_repairs']}",
        f"- Docker image: `{config['docker_image']}`",
        f"- Completed: {metrics['completed']}",
        f"- Evaluable: {metrics['evaluable']}",
        f"- Passed: {metrics['passed']}",
        f"- Pass rate: {metrics['agent_pass_rate_evaluable']:.2%}",
        f"- Passed after repair: {metrics['passed_after_repair']}",
        f"- Reference/tool anomalies: {metrics['reference_skipped']}",
        "",
        "| Problem | Result | Failure category | Reason | Repairs | Verification cycles |",
        "|---|---|---|---|---:|---:|",
    ]
    for result in report.get("results", []):
        reason = _markdown_cell(result.get("reason", ""))
        lines.append(
            f"| {result['problem_id']} | {result['status']} | "
            f"{result.get('failure_category') or ''} | {reason} | "
            f"{result['repair_attempts']} | "
            f"{result['verification_cycles']} |"
        )
    lines.extend(["", "## Failure categories", ""])
    if metrics["failure_categories"]:
        for category, count in metrics["failure_categories"].items():
            lines.append(f"- `{category}`: {count}")
    else:
        lines.append("- None")
    return "\n".join(lines) + "\n"


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "model"


def _markdown_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
