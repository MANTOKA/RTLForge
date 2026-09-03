from __future__ import annotations

import difflib
import json
import re
from hashlib import sha256
from pathlib import Path
from typing import Any

from verilog_agent.agents.generator import ArtifactRepository
from verilog_agent.agents.validation import AgentOutputError, require_nonempty_string
from verilog_agent.domain.models import Candidate, RTLTaskContext
from verilog_agent.providers.base import StructuredModelProvider

DEBUGGER_INSTRUCTIONS = """You are the RTL Debugger in a Verilog multi-agent system.
Make the smallest synthesizable Verilog-2005 repair that addresses the supplied structured
failure. Preserve the exact confirmed module interface and all behavior unrelated to the
failure. Return the complete patched RTL as plain text in patched_rtl, never Markdown fences.
Do not create or modify a testbench, oracle, reference implementation, specification, or port.
You cannot see hidden tests: reason only from the specification, current RTL, reflection policy,
and sanitized diagnostics. Explain the change, expected impact, and remaining risks."""


class RealDebugger:
    """Produce an immutable child candidate and an auditable locally computed diff."""

    def __init__(
        self,
        provider: StructuredModelProvider,
        *,
        artifacts: ArtifactRepository | None = None,
    ) -> None:
        self.provider = provider
        self.artifacts = artifacts or ArtifactRepository()

    def repair(self, context: RTLTaskContext, candidate: Candidate) -> Candidate:
        if context.spec_ir is None:
            raise AgentOutputError("Debugger requires SpecIR")
        source_path = Path(candidate.artifact_path).resolve(strict=True)
        artifact_root = self.artifacts.root.resolve()
        if not source_path.is_relative_to(artifact_root):
            raise AgentOutputError("repair source is outside the artifact root")
        source_rtl = source_path.read_text(encoding="utf-8")
        reflection = context.metadata.get("reflection_decision")
        if not isinstance(reflection, dict) or not reflection.get("invokes_debugger"):
            raise AgentOutputError("Debugger requires an explicit repair route")
        feedback = self._feedback_for(context, candidate.candidate_id)
        prompt = json.dumps(
            {
                "spec_ir": context.spec_ir.to_dict(),
                "current_candidate": {
                    "candidate_id": candidate.candidate_id,
                    "generation": candidate.generation,
                    "rtl": source_rtl,
                },
                "failure_category": context.failure_category,
                "reflection": reflection,
                "debugger_feedback": feedback,
                "remaining_repair_attempts": (
                    context.budget.max_repairs - context.budget.repair_attempts
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        payload = self.provider.generate_structured(
            instructions=DEBUGGER_INSTRUCTIONS,
            input_text=prompt,
            schema_name="verilog_rtl_repair",
            schema=self._schema(),
        )
        patched_rtl = self._validate_rtl(payload.get("patched_rtl"), context.spec_ir.module_name)
        if patched_rtl == source_rtl:
            raise AgentOutputError("Debugger returned an unchanged candidate")

        repair_number = context.budget.repair_attempts
        candidate_id = f"cand_repair_{repair_number:02d}"
        destination = self.artifacts.write_candidate(
            context.task_id,
            candidate_id,
            context.spec_ir.module_name,
            patched_rtl,
        )
        diff = "".join(
            difflib.unified_diff(
                source_rtl.splitlines(keepends=True),
                patched_rtl.splitlines(keepends=True),
                fromfile=f"{candidate.candidate_id}/{source_path.name}",
                tofile=f"{candidate_id}/{destination.name}",
            )
        )
        diff_path = destination.parent / "repair.patch"
        diff_path.write_text(diff, encoding="utf-8", newline="\n")
        rationale = require_nonempty_string(payload.get("rationale"), "rationale")
        expected_impact = require_nonempty_string(
            payload.get("expected_impact"), "expected_impact"
        )
        risks = payload.get("risks")
        if not isinstance(risks, list) or not all(
            isinstance(item, str) and item.strip() for item in risks
        ):
            raise AgentOutputError("risks must be a list of non-empty strings")
        return Candidate(
            candidate_id=candidate_id,
            artifact_path=destination.as_posix(),
            code_hash=sha256(patched_rtl.encode("utf-8")).hexdigest(),
            parent_id=candidate.candidate_id,
            generation=candidate.generation + 1,
            design_pattern=candidate.design_pattern,
            assumptions=list(candidate.assumptions),
            metadata={
                "generated_by": "real_debugger",
                "reflection": reflection,
                "rationale": rationale,
                "expected_impact": expected_impact,
                "risks": [item.strip() for item in risks],
                "diff_path": diff_path.as_posix(),
            },
        )

    @staticmethod
    def _feedback_for(context: RTLTaskContext, candidate_id: str) -> dict[str, Any]:
        attempts = context.metadata.get("verification_attempts", [])
        if not isinstance(attempts, list):
            return {}
        for attempt in attempts:
            if isinstance(attempt, dict) and attempt.get("candidate_id") == candidate_id:
                feedback = attempt.get("debugger_feedback", {})
                return dict(feedback) if isinstance(feedback, dict) else {}
        return {}

    @staticmethod
    def _validate_rtl(value: object, module_name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise AgentOutputError("patched_rtl must not be empty")
        rtl = value.strip() + "\n"
        if "```" in rtl:
            raise AgentOutputError("patched_rtl must not contain Markdown fences")
        if re.search(rf"\bmodule\s+{re.escape(module_name)}\b", rtl) is None:
            raise AgentOutputError(f"patched_rtl must declare module {module_name!r}")
        return rtl

    @staticmethod
    def _schema() -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "patched_rtl": {"type": "string", "minLength": 1},
                "rationale": {"type": "string", "minLength": 1},
                "expected_impact": {"type": "string", "minLength": 1},
                "risks": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                },
            },
            "required": ["patched_rtl", "rationale", "expected_impact", "risks"],
        }
