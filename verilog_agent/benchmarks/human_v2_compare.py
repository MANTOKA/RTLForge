from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare two compatible Human v2 batch reports"
    )
    parser.add_argument("report_a", type=Path)
    parser.add_argument("report_b", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("runs/benchmarks/human_v2_model_comparison.md"),
    )
    args = parser.parse_args()
    report_a = json.loads(args.report_a.read_text(encoding="utf-8"))
    report_b = json.loads(args.report_b.read_text(encoding="utf-8"))
    comparison = compare_reports(report_a, report_b)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(_markdown(comparison), encoding="utf-8", newline="\n")
    print(f"Model comparison: {args.output}")
    print(
        f"Pass-rate delta (B - A): {comparison['pass_rate_delta'] * 100:+.2f} points"
    )
    return 0


def compare_reports(
    report_a: dict[str, Any], report_b: dict[str, Any]
) -> dict[str, Any]:
    _validate_compatible(report_a, report_b)
    results_a = {item["problem_id"]: item for item in report_a["results"]}
    results_b = {item["problem_id"]: item for item in report_b["results"]}
    a_only = []
    b_only = []
    for problem_id in sorted(results_a):
        passed_a = results_a[problem_id]["status"] == "PASS"
        passed_b = results_b[problem_id]["status"] == "PASS"
        if passed_a and not passed_b:
            a_only.append(problem_id)
        elif passed_b and not passed_a:
            b_only.append(problem_id)
    metrics_a = report_a["metrics"]
    metrics_b = report_b["metrics"]
    return {
        "model_a": report_a["configuration"]["model"],
        "model_b": report_b["configuration"]["model"],
        "metrics_a": metrics_a,
        "metrics_b": metrics_b,
        "pass_rate_delta": (
            metrics_b["agent_pass_rate_evaluable"]
            - metrics_a["agent_pass_rate_evaluable"]
        ),
        "a_pass_b_fail": a_only,
        "b_pass_a_fail": b_only,
    }


def _validate_compatible(report_a: dict[str, Any], report_b: dict[str, Any]) -> None:
    config_a = report_a["configuration"]
    config_b = report_b["configuration"]
    controlled_fields = (
        "base_url",
        "candidate_count",
        "max_repairs",
        "docker_image",
        "memory_mode",
    )
    changed = [
        field for field in controlled_fields if config_a.get(field) != config_b.get(field)
    ]
    ids_a = [item["problem_id"] for item in report_a["results"]]
    ids_b = [item["problem_id"] for item in report_b["results"]]
    if ids_a != ids_b:
        changed.append("problem_ids")
    if report_a.get("protocol") != report_b.get("protocol"):
        changed.append("protocol")
    if changed:
        raise ValueError(
            "reports are not a controlled model comparison; changed: "
            + ", ".join(changed)
        )


def _markdown(comparison: dict[str, Any]) -> str:
    metrics_a = comparison["metrics_a"]
    metrics_b = comparison["metrics_b"]
    lines = [
        "# Human v2 Model Comparison",
        "",
        "| Metric | Model A | Model B |",
        "|---|---:|---:|",
        f"| Model | `{comparison['model_a']}` | `{comparison['model_b']}` |",
        f"| Evaluable cases | {metrics_a['evaluable']} | {metrics_b['evaluable']} |",
        f"| Passed | {metrics_a['passed']} | {metrics_b['passed']} |",
        "| Pass rate | "
        f"{metrics_a['agent_pass_rate_evaluable']:.2%} | "
        f"{metrics_b['agent_pass_rate_evaluable']:.2%} |",
        f"| Passed after repair | {metrics_a['passed_after_repair']} | "
        f"{metrics_b['passed_after_repair']} |",
        "",
        f"Pass-rate delta (B - A): {comparison['pass_rate_delta'] * 100:+.2f} percentage points.",
        "",
        "## Paired disagreements",
        "",
        f"- A pass / B fail: {', '.join(comparison['a_pass_b_fail']) or 'None'}",
        f"- B pass / A fail: {', '.join(comparison['b_pass_a_fail']) or 'None'}",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
