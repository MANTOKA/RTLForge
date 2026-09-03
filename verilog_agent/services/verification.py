from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from verilog_agent.domain import Candidate, RTLTaskContext, VerificationOutcome
from verilog_agent.services.candidate_evaluation import DockerToolError, MultiFileEDARunner

_CATEGORY_MAP = {
    "SYNTAX_ERROR": "SYNTAX_ERROR",
    "LINT_ERROR": "LINT_ERROR",
    "ASSERTION_FAILURE": "FUNCTIONAL_MISMATCH",
    "SIMULATION_ERROR": "FUNCTIONAL_MISMATCH",
    "TIMEOUT": "TIMEOUT",
    "SYSTEM_ERROR": "SYSTEM_ERROR",
    "PATH_VIOLATION": "SYSTEM_ERROR",
    "INPUT_NOT_FOUND": "SYSTEM_ERROR",
    "UNSUPPORTED_INPUT": "SYSTEM_ERROR",
}


@dataclass(slots=True)
class GoldenTestbenchVerificationService:
    """Verify ranked candidates against one authoritative, hidden testbench."""

    runner: MultiFileEDARunner
    runs_root: Path
    testbench_resolver: Callable[[RTLTaskContext], Path]
    reference_resolver: Callable[[RTLTaskContext], Path] | None = None
    testbench_source: str = "benchmark_golden"
    confidence: str = "HIGH"

    def verify(self, context: RTLTaskContext, candidate: Candidate) -> VerificationOutcome:
        testbench = self.testbench_resolver(context).resolve(strict=True)
        reference = (
            self.reference_resolver(context).resolve(strict=True)
            if self.reference_resolver is not None
            else None
        )
        task_root = self.runs_root / context.task_id
        evidence_root = task_root / "tool_runs"
        evidence_root.mkdir(parents=True, exist_ok=True)
        order = self._verification_order(context, candidate)
        attempts: list[dict[str, Any]] = []

        for candidate_id in order:
            current = context.candidate(candidate_id)
            try:
                inputs = [testbench]
                if reference is not None:
                    inputs.append(reference)
                inputs.append(Path(current.artifact_path))
                result = self.runner.run_files("simulate", inputs)
            except (DockerToolError, OSError) as error:
                attempt = {
                    "candidate_id": candidate_id,
                    "status": "FAIL",
                    "category": "SYSTEM_ERROR",
                    "reason": str(error),
                }
                attempts.append(attempt)
                return self._finish(
                    context,
                    attempts,
                    passed=False,
                    category="SYSTEM_ERROR",
                    selected_candidate_id=candidate_id,
                    testbench=testbench,
                    reference=reference,
                    task_root=task_root,
                )

            raw_path = evidence_root / f"{candidate_id}_simulate.json"
            raw_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            category = _verification_category(result, Path(current.artifact_path))
            attempt = {
                "candidate_id": candidate_id,
                "status": result.get("status", "FAIL"),
                "category": category,
                "duration_ms": result.get("duration_ms", 0),
                "evidence_path": str(raw_path),
                "debugger_feedback": _debugger_feedback(result, category),
            }
            attempts.append(attempt)
            if category == "SYSTEM_ERROR":
                context.selected_candidate_id = candidate_id
                return self._finish(
                    context,
                    attempts,
                    passed=False,
                    category="SYSTEM_ERROR",
                    selected_candidate_id=candidate_id,
                    testbench=testbench,
                    reference=reference,
                    task_root=task_root,
                )
            if result.get("status") == "PASS":
                context.selected_candidate_id = candidate_id
                return self._finish(
                    context,
                    attempts,
                    passed=True,
                    category="OK",
                    selected_candidate_id=candidate_id,
                    testbench=testbench,
                    reference=reference,
                    task_root=task_root,
                )

        active_candidate_id = order[0]
        context.selected_candidate_id = active_candidate_id
        return self._finish(
            context,
            attempts,
            passed=False,
            category=attempts[0]["category"] if attempts else "UNKNOWN",
            selected_candidate_id=active_candidate_id,
            testbench=testbench,
            reference=reference,
            task_root=task_root,
        )

    def _finish(
        self,
        context: RTLTaskContext,
        attempts: list[dict[str, Any]],
        *,
        passed: bool,
        category: str,
        selected_candidate_id: str,
        testbench: Path,
        reference: Path | None,
        task_root: Path,
    ) -> VerificationOutcome:
        summary_path = task_root / "verification.json"
        payload = {
            "task_id": context.task_id,
            "passed": passed,
            "category": category,
            "selected_candidate_id": selected_candidate_id,
            "attempts": attempts,
            "testbench": {
                "source": self.testbench_source,
                "confidence": self.confidence,
                "path": str(testbench),
                "sha256": sha256(testbench.read_bytes()).hexdigest(),
                "content_exposed_to_agents": False,
            },
            "reference": (
                {
                    "path": str(reference),
                    "sha256": sha256(reference.read_bytes()).hexdigest(),
                    "usage": "simulator_black_box",
                    "content_exposed_to_agents": False,
                }
                if reference is not None
                else None
            ),
        }
        summary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        context.metadata["verification_report"] = str(summary_path)
        context.metadata["verification_attempts"] = attempts
        return VerificationOutcome(
            passed=passed,
            category=category,
            evidence_path=str(summary_path),
            details={
                "selected_candidate_id": selected_candidate_id,
                "attempted_candidate_ids": [item["candidate_id"] for item in attempts],
                "confidence": self.confidence,
                "debugger_feedback": attempts[0].get("debugger_feedback", {})
                if attempts
                else {},
            },
        )

    @staticmethod
    def _verification_order(context: RTLTaskContext, selected: Candidate) -> list[str]:
        configured = context.metadata.get("verification_order", [])
        valid_ids = {candidate.candidate_id for candidate in context.candidates}
        order = [candidate_id for candidate_id in configured if candidate_id in valid_ids]
        if selected.candidate_id not in order:
            order.insert(0, selected.candidate_id)
        return order


def _verification_category(result: dict[str, Any], candidate_path: Path) -> str:
    if result.get("status") == "PASS":
        return "OK"
    if result.get("category") == "SYNTAX_ERROR":
        diagnostic_files = {
            Path(str(error["file"])).name
            for error in result.get("errors", [])
            if error.get("file")
        }
        if diagnostic_files and candidate_path.name not in diagnostic_files:
            return "SYSTEM_ERROR"
    return _CATEGORY_MAP.get(str(result.get("category", "UNKNOWN")), "UNKNOWN")


def _debugger_feedback(result: dict[str, Any], category: str) -> dict[str, Any]:
    """Expose diagnostics, never the golden testbench source or full simulator transcript."""
    errors = []
    for error in result.get("errors", [])[:10]:
        errors.append(
            {
                "tool": error.get("tool"),
                "severity": error.get("severity"),
                "type": error.get("type"),
                "file": Path(str(error["file"])).name if error.get("file") else None,
                "line": error.get("line"),
                "message": str(error.get("message", ""))[:500],
            }
        )
    return {
        "category": category,
        "tool": result.get("tool"),
        "errors": errors,
        "timed_out": result.get("category") == "TIMEOUT",
    }
