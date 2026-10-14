from __future__ import annotations

import argparse
import json
from dataclasses import fields
from pathlib import Path
from typing import Any

from verilog_agent.memory.models import RepairAttempt
from verilog_agent.memory.store import SQLiteMemoryStore


def consolidate_run_artifacts(
    tasks_root: Path,
    store: SQLiteMemoryStore,
) -> dict[str, Any]:
    """Import only verification-grounded summaries from completed task artifacts."""
    counts = {
        "summaries": 0,
        "error_patterns": 0,
        "design_patterns": 0,
        "repair_attempts": 0,
        "repair_strategies": 0,
    }
    seen_design_sources: set[str] = set()
    summary_paths = sorted(
        tasks_root.glob("*/showcase_summary.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for summary_path in summary_paths:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        counts["summaries"] += 1
        task_id = str(payload.get("task_id") or summary_path.parent.name)
        problem_id = _problem_id(payload)
        module_family = classify_module_family(str(payload.get("input") or ""))
        verification = payload.get("verification")
        if not isinstance(verification, dict):
            continue

        attempts = verification.get("attempts", [])
        if isinstance(attempts, list):
            for attempt in attempts:
                if not isinstance(attempt, dict) or attempt.get("status") == "PASS":
                    continue
                feedback = attempt.get("debugger_feedback", {})
                feedback = feedback if isinstance(feedback, dict) else {}
                store.record_error_observation(
                    task_id=task_id,
                    candidate_id=str(attempt.get("candidate_id") or "unknown"),
                    error_signature=_error_signature(
                        str(attempt.get("category") or "UNKNOWN"), feedback
                    ),
                    module_family=module_family,
                    source_problem_id=problem_id,
                    summary=_error_summary(str(attempt.get("category") or "UNKNOWN"), feedback),
                    evidence_path=_optional_string(attempt.get("evidence_path")),
                )
                counts["error_patterns"] += 1

        if _has_clean_functional_pass(payload, attempts):
            selected_id = str(payload.get("selected_candidate_id") or "")
            design_source = problem_id or task_id
            candidate = next(
                (
                    item
                    for item in payload.get("candidates", [])
                    if isinstance(item, dict) and item.get("candidate_id") == selected_id
                ),
                None,
            )
            if (
                isinstance(candidate, dict)
                and candidate.get("parent_id") is None
                and design_source not in seen_design_sources
            ):
                evidence_path = next(
                    (
                        _optional_string(item.get("evidence_path"))
                        for item in attempts
                        if isinstance(item, dict) and item.get("candidate_id") == selected_id
                    ),
                    None,
                )
                item = store.record_verified_design_pattern(
                    task_id=task_id,
                    candidate_id=selected_id,
                    module_family=module_family,
                    source_problem_id=problem_id,
                    design_pattern=str(candidate.get("design_pattern") or ""),
                    evidence_path=evidence_path,
                )
                counts["design_patterns"] += int(item is not None)
                if item is not None:
                    seen_design_sources.add(design_source)

        for raw_attempt in payload.get("repair_attempts", []):
            if not isinstance(raw_attempt, dict):
                continue
            accepted = {
                field.name: raw_attempt[field.name]
                for field in fields(RepairAttempt)
                if field.name in raw_attempt
            }
            accepted["module_family"] = module_family
            if not {
                "task_id",
                "candidate_id",
                "parent_candidate_id",
                "failure_category",
                "error_signature",
                "module_family",
                "benchmark_problem_id",
                "before_mismatches",
                "after_mismatches",
                "mismatch_reduction",
                "passed",
                "rationale",
                "expected_impact",
                "diff_path",
                "evidence_path",
            }.issubset(accepted):
                continue
            repair_item = store.record_repair_attempt(RepairAttempt(**accepted))
            counts["repair_attempts"] += 1
            counts["repair_strategies"] += int(repair_item is not None)
    counts["superseded_coarse_strategies"] = store.supersede_coarse_repair_strategies()
    memories = store.list_memories()
    counts["unique_memory_items"] = len(memories)
    counts["unique_by_type"] = {
        memory_type: sum(item.get("memory_type") == memory_type for item in memories)
        for memory_type in ("DESIGN_PATTERN", "ERROR_PATTERN", "REPAIR_STRATEGY")
    }
    counts["active_by_type"] = {
        memory_type: sum(
            item.get("memory_type") == memory_type and item.get("status") == "ACTIVE"
            for item in memories
        )
        for memory_type in ("DESIGN_PATTERN", "ERROR_PATTERN", "REPAIR_STRATEGY")
    }
    return counts


def classify_module_family(raw_spec: str) -> str:
    lowered = raw_spec.lower()
    for family, terms in (
        ("fsm", ("state machine", "fsm", "状态机")),
        ("counter", ("counter", "计数器")),
        ("fifo", ("fifo", "queue", "队列")),
        ("shift_register", ("shift register", "移位寄存器")),
        ("flip_flop", ("flip-flop", "flip flop", "flipflop", "dff", "触发器")),
        ("arithmetic", ("adder", "subtractor", "multiplier", "加法", "乘法")),
    ):
        if any(term in lowered for term in terms):
            return family
    return "generic_rtl"


def _problem_id(payload: dict[str, Any]) -> str | None:
    verification = payload.get("verification", {})
    if not isinstance(verification, dict):
        return None
    source = verification.get("testbench", {}).get("source")
    if isinstance(source, str) and source.startswith("VerilogEval-Human-v2/"):
        return source.rsplit("/", 1)[-1]
    return None


def _has_clean_functional_pass(
    payload: dict[str, Any], attempts: object
) -> bool:
    if payload.get("verified_rtl") is not True or not isinstance(attempts, list):
        return False
    selected_id = payload.get("selected_candidate_id")
    selected = next(
        (
            item
            for item in attempts
            if isinstance(item, dict) and item.get("candidate_id") == selected_id
        ),
        None,
    )
    if not isinstance(selected, dict) or selected.get("status") != "PASS":
        return False
    mismatch = selected.get("debugger_feedback", {}).get("mismatch_summary", {})
    count = mismatch.get("total_mismatched_samples") if isinstance(mismatch, dict) else None
    return not isinstance(count, int) or count == 0


def _error_signature(category: str, feedback: dict[str, Any]) -> str:
    mismatch = feedback.get("mismatch_summary", {})
    outputs = mismatch.get("outputs", []) if isinstance(mismatch, dict) else []
    names = sorted(
        str(item["output"])
        for item in outputs
        if isinstance(item, dict) and item.get("output")
    )
    parts = [category]
    if names:
        parts.append("outputs=" + ",".join(names))
    temporal_pattern = _temporal_pattern(mismatch)
    if temporal_pattern:
        parts.append(f"pattern={temporal_pattern}")
    return " ".join(parts)


def _error_summary(category: str, feedback: dict[str, Any]) -> str:
    mismatch = feedback.get("mismatch_summary", {})
    if not isinstance(mismatch, dict):
        return f"Observed {category}."
    count = mismatch.get("total_mismatched_samples")
    total = mismatch.get("total_samples")
    pattern = _temporal_pattern(mismatch)
    detail = f"{count}/{total} mismatched samples" if count is not None else "failure"
    if pattern:
        detail += f", temporal pattern {pattern}"
    return f"Observed {category}: {detail}."


def _temporal_pattern(mismatch: object) -> str | None:
    if not isinstance(mismatch, dict):
        return None
    existing = mismatch.get("temporal_pattern")
    if existing:
        return str(existing)
    if (
        mismatch.get("total_mismatched_samples") == 1
        and isinstance(mismatch.get("total_samples"), int)
        and mismatch["total_samples"] > 1
    ):
        return "isolated_single_sample"
    return None


def _optional_string(value: object) -> str | None:
    return str(value) if value else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Consolidate verification-grounded task artifacts into SQLite Memory"
    )
    parser.add_argument("--tasks-root", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--toolchain-version", required=True)
    args = parser.parse_args()
    store = SQLiteMemoryStore(args.database, toolchain_version=args.toolchain_version)
    counts = consolidate_run_artifacts(args.tasks_root, store)
    print(json.dumps(counts, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
