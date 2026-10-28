from __future__ import annotations

from typing import Any

from rtlforge.providers.usage import summarize_calls

MISSING = "当时未记录"
BLOCKED_KEYS = ("testbench", "reference", "golden", "api_key", "secret",
                "stdout", "stderr", "raw_output")


def safe_data(value: Any) -> Any:
    """Keep oracle material and credentials out of every browser payload."""
    if isinstance(value, dict):
        return {
            key: safe_data(item)
            for key, item in value.items()
            if not any(blocked in key.lower() for blocked in BLOCKED_KEYS)
        }
    if isinstance(value, list):
        return [safe_data(item) for item in value]
    return value


def field(source: dict, name: str, source_name: str) -> dict:
    if name not in source:
        return {"value": None, "availability": "not_recorded", "source": None}
    return {"value": safe_data(source[name]), "availability": "recorded",
            "source": f"{source_name}.{name}"}


def classify(summary: dict | None) -> str:
    if not summary:
        return "UNKNOWN_LEGACY"
    stage = summary.get("current_stage")
    verification = summary.get("verification") or {}
    category = verification.get("category") or summary.get("failure_category")
    if category in {"SYSTEM_ERROR", "PIPELINE_ERROR", "TOOL_INCOMPATIBILITY"}:
        return "INFRASTRUCTURE_ERROR"
    if stage == "FAILED" and category in {"SYSTEM_ERROR", "PIPELINE_ERROR"}:
        return "INFRASTRUCTURE_ERROR"
    if "verification" in summary and summary["verification"] is None and stage != "FAILED":
        return "UNVERIFIED"
    if verification.get("passed") is not True:
        return "UNRESOLVED" if verification or stage == "FAILED" else "UNKNOWN_LEGACY"
    candidates = summary.get("candidates") or []
    selected = next((c for c in candidates if c.get("candidate_id") ==
                     summary.get("selected_candidate_id")), None)
    if selected is None:
        return "UNKNOWN_LEGACY"
    if selected.get("parent_id"):
        return "REPAIR_RESCUE"
    independent = [c for c in candidates if not c.get("parent_id")]
    if not independent:
        return "UNKNOWN_LEGACY"
    return ("RANK1_PASS" if selected.get("candidate_id") ==
            independent[0].get("candidate_id") else "FALLBACK_RESCUE")


def task_view(summary: dict | None, *, task_id: str, events: list[dict],
              calls: list[dict], artifacts: list[dict], status: str,
              completeness: str, error: str | None = None) -> dict:
    source = summary or {}
    spec = source.get("spec_ir") or {}
    candidates = []
    regenerations = iter(source.get("candidate_regenerations") or [])
    for candidate in source.get("candidates") or []:
        metadata = candidate.get("metadata") or {}
        is_independent = not candidate.get("parent_id")
        generation_reason = ("初始候选" if is_independent and not candidates else
                             (next(regenerations, {}) or {}).get("reason") if is_independent
                             else "Debugger 修复")
        candidates.append({
            "candidate_id": candidate.get("candidate_id"),
            "parent_id": candidate.get("parent_id"),
            "generation": candidate.get("generation"),
            "generation_reason": generation_reason,
            "design_pattern": candidate.get("design_pattern"),
            "static": safe_data(metadata.get("static_evaluation")),
            "selected": candidate.get("candidate_id") == source.get("selected_candidate_id"),
            "artifact": next((a for a in artifacts if a.get("candidate_id") ==
                              candidate.get("candidate_id")), None),
        })
    verification = source.get("verification") or {}
    attempts = [{"candidate_id": a.get("candidate_id"), "status": a.get("status"),
                 "category": a.get("category"), "duration_ms": a.get("duration_ms"),
                 "debugger_feedback": safe_data(a.get("debugger_feedback"))}
                for a in verification.get("attempts") or []]
    usage = source.get("token_usage") if "token_usage" in source else (
        summarize_calls(calls) if calls else None
    )
    result = classify(summary)
    return {
        "id": task_id, "kind": "task", "status": status,
        "completeness": completeness, "error": error,
        "fields": {name: field(source, name, "showcase_summary") for name in (
            "input", "sampling", "prompt_version", "candidate_policy", "budget",
            "candidate_regenerations", "selected_candidate_id", "timings_ms",
        )},
        "planner": {name: field(spec, name, "showcase_summary.spec_ir") for name in (
            "module_name", "raw_spec", "assumptions", "acceptance_criteria",
            "ports", "clock", "reset", "key_behaviors", "ambiguities",
        )},
        "task_tree": safe_data(source.get("task_tree")),
        "events": safe_data(events), "candidates": candidates,
        "verification": {"configured": source.get("verification", MISSING) is not None,
                         "passed": verification.get("passed"),
                         "category": verification.get("category"), "attempts": attempts},
        "selected_static_evaluation": safe_data(source.get("selected_static_evaluation")),
        "repair_attempts": safe_data(source.get("repair_attempts")),
        "repair_fallback": safe_data(source.get("repair_fallback")),
        "memory": safe_data(source.get("memory")),
        "model_calls": safe_data(calls) if calls else (None if "model_calls" not in source else []),
        "token_usage": safe_data(usage), "result_type": result,
        "artifacts": artifacts,
        "summary_markdown": task_summary(source, result, calls, artifacts),
        "protocol": "Oracle：仅 Verifier 可见",
    }


def task_summary(source: dict, result: str, calls: list[dict], artifacts: list[dict]) -> str:
    verification = source.get("verification") or {}
    static = source.get("selected_static_evaluation") or {}
    usage = source.get("token_usage") or (summarize_calls(calls) if calls else {})
    def token(key):
        return usage.get(key) if usage.get(key) is not None else "NOT_REPORTED"
    generated = sum(c.get("role") == "generator" for c in calls)
    repaired = sum(c.get("role") == "debugger" for c in calls)
    if result == "FALLBACK_RESCUE":
        reasons = source.get("candidate_regenerations") or []
        reason = reasons[0].get("reason") if reasons else MISSING
        path = f"首个独立候选未成为最终选择 → 重新生成（{reason}）→ 功能通过"
    elif result == "REPAIR_RESCUE":
        path = "功能或静态检查失败 → Debugger 修复 → 功能通过"
    elif result == "RANK1_PASS":
        path = "首个独立候选 → 静态检查 → 功能通过"
    else:
        path = " → ".join(str(event.get("event_type", ""))
                          for event in source.get("events") or [])
    return "\n".join((
        f"结果：{'PASS' if verification.get('passed') else source.get('current_stage', MISSING)}"
        f" · {result}",
        f"路径：{path or MISSING}",
        f"最终候选：{source.get('selected_candidate_id') or MISSING}",
        f"静态质量：{static.get('quality_score', MISSING)}",
        f"功能验证：{verification.get('category', MISSING)}",
        f"成本：Generator {generated if calls else MISSING} 次，"
        f"Debugger {repaired if calls else MISSING} 次；"
        f"已知输入/输出 Token {token('input_tokens')}/{token('output_tokens')}",
        "限制：oracle-assisted closed-loop；golden 仅由 Verifier 使用",
        "产物：" + ("、".join(a["name"] for a in artifacts) or MISSING),
    )) + "\n"
