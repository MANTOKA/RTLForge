from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol

from rtlforge.benchmarks.verilogeval import VerilogEvalCase
from rtlforge.services.candidate_evaluation import DockerToolError


class ReferenceRunner(Protocol):
    def run_files(self, action: str, input_paths: list[Path]) -> dict[str, Any]: ...


def preflight_references(
    cases: list[VerilogEvalCase],
    runner: ReferenceRunner,
    output_path: Path,
    *,
    limit: int | None = None,
    artifact_root: Path | None = None,
    reference_adapter: Callable[[VerilogEvalCase, Path], Path] | None = None,
    include_original_reference: bool = True,
    dataset_name: str = "VerilogEval-Human-v2",
) -> dict[str, Any]:
    """Verify golden references without exposing their source to any LLM agent."""
    selected = cases if limit is None else cases[:limit]
    artifact_root = artifact_root or output_path.parent / "reference_adapters"
    artifact_root.mkdir(parents=True, exist_ok=True)
    results = []
    for case in selected:
        try:
            adapter = reference_adapter or _adapt_reference_module
            adapted_reference = adapter(case, artifact_root)
            inputs = [case.testbench_path]
            if include_original_reference:
                inputs.append(case.reference_path)
            inputs.append(adapted_reference)
            tool_result = runner.run_files("simulate", inputs)
            status = str(tool_result.get("status", "FAIL"))
            tool_category = str(tool_result.get("category", "UNKNOWN"))
            category = _preflight_category(
                tool_result, testbench_name=case.testbench_path.name
            )
            duration_ms = int(tool_result.get("duration_ms", 0))
        except (DockerToolError, OSError, ValueError) as error:
            status = "FAIL"
            category = "SYSTEM_ERROR"
            tool_category = "SYSTEM_ERROR"
            duration_ms = 0
            tool_result = {"error": str(error)}
        results.append(
            {
                "problem_id": case.problem_id,
                "status": status,
                "category": category,
                "tool_category": tool_category,
                "duration_ms": duration_ms,
                "prompt_sha256": sha256(case.prompt_path.read_bytes()).hexdigest(),
                "testbench_sha256": sha256(case.testbench_path.read_bytes()).hexdigest(),
                "reference_sha256": sha256(case.reference_path.read_bytes()).hexdigest(),
                "diagnostics": tool_result.get("errors", []),
                "reason": tool_result.get("error"),
            }
        )

    passed = sum(item["status"] == "PASS" for item in results)
    failure_categories = Counter(
        item["category"] for item in results if item["status"] != "PASS"
    )
    payload = {
        "dataset": dataset_name,
        "mode": "reference_preflight",
        "case_count": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "pass_rate": passed / len(results) if results else 0.0,
        "failure_categories": dict(sorted(failure_categories.items())),
        "agent_calls": 0,
        "reference_content_exposed_to_agents": False,
        "results": results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return payload


def _adapt_reference_module(case: VerilogEvalCase, artifact_root: Path) -> Path:
    """Create the TopModule side required by the official differential testbench."""
    source = case.reference_path.read_text(encoding="utf-8")
    adapted, replacements = re.subn(
        r"\bmodule\s+RefModule\b", "module TopModule", source, count=1
    )
    if replacements != 1:
        raise ValueError(f"{case.problem_id} reference does not declare RefModule")
    path = artifact_root / f"{case.problem_id}_TopModule.sv"
    path.write_text(adapted, encoding="utf-8", newline="\n")
    return path


def _preflight_category(
    result: dict[str, Any], *, testbench_name: str | None = None
) -> str:
    if result.get("status") == "PASS":
        return "OK"
    messages = "\n".join(str(item.get("message", "")) for item in result.get("errors", []))
    if "not yet supported" in messages.lower():
        return "TOOL_INCOMPATIBILITY"
    diagnostic_files = {
        Path(str(item["file"])).name
        for item in result.get("errors", [])
        if item.get("file")
    }
    if (
        result.get("category") == "SYNTAX_ERROR"
        and testbench_name is not None
        and diagnostic_files == {testbench_name}
    ):
        return "TOOL_INCOMPATIBILITY"
    if result.get("category") == "TIMEOUT":
        return "REFERENCE_TIMEOUT"
    return "REFERENCE_OR_TEST_FAILURE"
