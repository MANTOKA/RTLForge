"""Narrow debugger investigations: current RTL, safe diagnostics and gated Memory only."""

from __future__ import annotations

from pathlib import Path

from rtlforge.domain.models import RTLTaskContext
from rtlforge.orchestration.operations import WorkflowOperations
from rtlforge.orchestration.policy import DebugAction


class DebugTools:
    def __init__(self, operations: WorkflowOperations, runs_root: Path) -> None:
        self.operations = operations
        self.runs_root = runs_root.resolve()

    def call(self, action: DebugAction, context: RTLTaskContext) -> dict:
        if action is DebugAction.READ_SYNTHESIS_REPORT:
            report = context.candidate().metadata.get("synthesis_result")
            if not report or report["rtl_sha256"] != context.candidate().code_hash:
                raise PermissionError("No source-matched synthesis report")
            return {key: value for key, value in report.items() if key not in {
                "evidence", "mcp_server_info", "mcp_server_sha256",
            }}
        if action is DebugAction.READ_RTL:
            path = Path(context.candidate().artifact_path).resolve(strict=True)
            if not path.is_relative_to(self.runs_root) or path.suffix not in {".v", ".sv"}:
                raise PermissionError("Debugger may read only the current RTL artifact")
            with path.open(encoding="utf-8") as stream:
                source = stream.read(16385)
            return {"rtl": source[:16384], "truncated": len(source) > 16384}
        if action is DebugAction.READ_DIAGNOSTICS:
            feedback = self.operations._selected_debugger_feedback(context)
            # Do not open report paths or raw simulator/golden transcripts.
            allowed = {
                "category",
                "mismatch_summary",
                "error_count",
                "warning_count",
                "agent_tb_advisory",
                "synthesis",
            }
            result = {key: value for key, value in feedback.items() if key in allowed}
            result["category"] = context.failure_category
            static = context.candidate().metadata.get("static_evaluation", {}).get("results", {})
            result["static_diagnostics"] = [
                {
                    "type": item.get("type"),
                    "severity": item.get("severity"),
                    "line": item.get("line"),
                    "message": str(item.get("message", ""))[:300],
                }
                for report in static.values()
                if isinstance(report, dict)
                for item in report.get("errors", [])[:8]
                if isinstance(item, dict)
            ][:8]
            return result
        categories = {
            DebugAction.RETRIEVE_REPAIR_STRATEGIES: ("REPAIR_STRATEGY",),
            DebugAction.RETRIEVE_ERROR_PATTERNS: ("ERROR_PATTERN",),
            DebugAction.RETRIEVE_PROJECT_RULES: ("PROJECT",),
        }
        if action is DebugAction.RETRIEVE_MEMORY or action in categories:
            self.operations._retrieve_memories(
                context, self.operations._selected_debugger_feedback(context),
                **({"memory_types": categories[action]} if action in categories else {}),
            )
            return {"memories": context.metadata.get("retrieved_memories", [])}
        raise PermissionError("Unknown investigation tool")
