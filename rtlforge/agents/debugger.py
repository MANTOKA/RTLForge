from __future__ import annotations

import difflib
import json
import re
from hashlib import sha256
from pathlib import Path
from typing import Any

from rtlforge.agents.generator import ArtifactRepository
from rtlforge.agents.validation import AgentOutputError, require_nonempty_string
from rtlforge.domain.models import Candidate, RTLTaskContext
from rtlforge.providers.base import StructuredModelProvider

DEBUGGER_INSTRUCTIONS = """You are the RTL Debugger in a Verilog multi-agent system.
Make the smallest synthesizable Verilog-2005 repair that addresses the supplied structured
failure. Preserve the exact confirmed module interface and all behavior unrelated to the
failure. Return the complete patched RTL as plain text in patched_rtl, never Markdown fences.
Do not create or modify a testbench, oracle, reference implementation, specification, or port.
You cannot see hidden tests: reason only from the specification, current RTL, reflection policy,
and sanitized diagnostics. Explain the change, expected impact, and remaining risks.
Treat agent-generated testbench diagnostics as advisory, never as the authoritative oracle.
Any agent-TB expected value is an unverified claim; derive it from the contract before
changing RTL to match it.
If prior repair analysis reports no progress, do not repeat an equivalent edit. Change the
root-cause hypothesis and explain why the new patch should reduce the observed mismatch.
For an isolated single-sample mismatch, inspect initialization, reset assumptions, and
first-cycle/nonblocking timing before rewriting steady-state datapath logic.
For a completion timeout, trace the FSM and loop counters from start to done on a legal
input. Estimate worst-case cycles against the contract bound, find the first unreachable
terminal count or over-budget loop, and change the RTL to address it. For iterative
recurrences, verify that each source operand index follows the iteration variable,
rather than accidentally reading a fixed first term. Include coefficient-wise loops
and all branch paths in the cycle budget; do not merely relax the testbench timeout.
For GF(2^m) inversion under a cycle bound, do not use 2^m-2 serial multiplications
or one giant combinational multiplication chain; schedule exact exponentiation
with a short square-and-multiply/addition chain and count its cycles."""

DEBUGGER_INSTRUCTIONS += (
    " When frozen_modules is nonempty, repair only the top module. Instantiate each module "
    "marked required_instance=true; the others are transitive dependencies. Never redefine "
    "or modify frozen source, interface or timing contracts."
)


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

    def decide_next_action(self, observation: dict) -> dict:
        """Choose a bounded investigation or repair action; never declare RTL correctness."""
        return self.provider.generate_structured(
            instructions=(
                "You are the RTL Debugger. Choose the next action from the allowed list. "
                "Use read_diagnostics, read_rtl or retrieve_memory when more evidence is needed; "
                "For targeted memory queries choose retrieve_repair_strategies, "
                "retrieve_error_patterns, or retrieve_project_rules; "
                "each queries only that category. "
                "Use read_synthesis_report to inspect source-bound synthesis, timing and "
                "resource evidence. Preserve the confirmed part, clock and resource constraints; "
                "repair RTL instead of relaxing constraints. "
                "run_synthesis requests a real synthesis check only when it appears in "
                "allowed_actions. Do not repeat synthesis of unchanged RTL without a reason. "
                "repair when the cause is understood; ask_user for ambiguity; stop if unable "
                "to proceed. Do not repeat an investigation whose result is already present. "
                "Agent-generated TB feedback is advisory and may be wrong. Respect the frozen "
                "contract. Never request shell, arbitrary paths, golden tests or reference RTL. "
                "A repair is always followed by independent tool verification. Return JSON."
            ),
            input_text=json.dumps(observation, ensure_ascii=False),
            schema_name="rtl_debug_action",
            schema={
                "type": "object", "additionalProperties": False,
                "properties": {
                    "action": {"type": "string", "enum": [
                        "read_diagnostics", "read_rtl", "retrieve_memory",
                        "retrieve_repair_strategies", "retrieve_error_patterns",
                        "retrieve_project_rules",
                        "read_synthesis_report",
                        "run_synthesis",
                        "repair", "ask_user", "stop",
                    ]},
                    "reason": {"type": "string"},
                },
                "required": ["action", "reason"],
            },
        )

    def repair(self, context: RTLTaskContext, candidate: Candidate) -> Candidate:
        from rtlforge.integration import module_contracts

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
        if context.failure_category == "FUNCTIONAL_MISMATCH":
            for attempt in reversed(context.metadata.get("generated_tb_attempts", [])):
                if attempt.get("candidate_id") != candidate.candidate_id:
                    continue
                advisory = attempt.get("advisory_feedback", {})
                allowed_outputs = {
                    port.name for port in context.spec_ir.ports
                    if port.direction.value == "output"
                }
                feedback["agent_tb_advisory"] = {
                    "source": "agent_generated_tb",
                    "authority": "advisory",
                    "category": advisory.get("category") if advisory.get("category") in {
                        "OK", "SIMULATION_ERROR", "ASSERTION_FAILURE", "SYNTAX_ERROR", "TIMEOUT"
                    } else "UNKNOWN",
                    "mismatched_outputs": [
                        name for name in advisory.get("mismatched_outputs", [])
                        if name in allowed_outputs
                    ],
                    "unasserted_outputs": [
                        name for name in advisory.get("unasserted_outputs", [])
                        if name in allowed_outputs
                    ],
                    "completion_timeout": advisory.get("completion_timeout") is True,
                }
                value_mismatch = advisory.get("first_value_mismatch")
                if (
                    isinstance(value_mismatch, dict)
                    and value_mismatch.get("output") in allowed_outputs
                ):
                    observed = value_mismatch.get("observed")
                    expected = value_mismatch.get("agent_tb_expected")
                    if all(
                        isinstance(value, str) and len(value) <= 64
                        and re.fullmatch(r"[0-9A-Fa-f_xXzZ]+", value)
                        for value in (observed, expected)
                    ):
                        feedback["agent_tb_advisory"]["first_value_mismatch"] = {
                            "output": value_mismatch["output"],
                            "observed": observed,
                            "agent_tb_expected": expected,
                        }
                break
        if context.failure_category == "QUALITY_ERROR":
            report = candidate.metadata.get("static_evaluation", {})
            scorecard = report.get("scorecard", {}) if isinstance(report, dict) else {}
            if isinstance(scorecard, dict) and scorecard:
                deductions = []
                for item in scorecard.get("deductions", [])[:20]:
                    evidence = item.get("evidence", {})
                    deductions.append({
                        "rule": item.get("rule"),
                        "penalty": item.get("penalty"),
                        "file": Path(str(evidence["file"])).name if evidence.get("file") else None,
                        "line": evidence.get("line"),
                        "message": str(evidence.get("message", ""))[:300],
                    })
                feedback["static_quality"] = {
                    "score": scorecard.get("static_quality_score"),
                    "threshold": scorecard.get("threshold"),
                    "deductions": deductions,
                }
        if context.failure_category in {"LINT_ERROR", "LINT_RISK_ERROR", "SYNTAX_ERROR"}:
            report = candidate.metadata.get("static_evaluation", {})
            results = report.get("results", {}) if isinstance(report, dict) else {}
            stage = "compile" if context.failure_category == "SYNTAX_ERROR" else "lint"
            tool_result = results.get(stage, {}) if isinstance(results, dict) else {}
            errors = tool_result.get("errors", []) if isinstance(tool_result, dict) else []
            ordered = sorted(
                (item for item in errors if isinstance(item, dict)),
                key=lambda item: item.get("severity") != "ERROR",
            )
            feedback["static_diagnostics"] = {
                "stage": stage,
                "items": [{
                    "severity": item.get("severity"),
                    "type": str(item.get("type", ""))[:40],
                    "file": Path(str(item["file"])).name if item.get("file") else None,
                    "line": item.get("line"),
                    "message": " ".join(str(item.get("message", "")).split())[:240],
                } for item in ordered[:8]],
            }
        prompt = json.dumps(
            {
                "spec_ir": context.spec_ir.to_dict(),
                "frozen_modules": module_contracts(context),
                "synthesis_constraints": (context.synthesis_constraints.to_dict()
                                          if context.synthesis_constraints else None),
                "synthesis_feedback": (candidate.metadata.get("synthesis_result", {}).get(
                    "diagnostics", []
                )),
                "current_candidate": {
                    "candidate_id": candidate.candidate_id,
                    "generation": candidate.generation,
                    "rtl": source_rtl,
                },
                "failure_category": context.failure_category,
                "reflection": reflection,
                "debugger_feedback": feedback,
                "prior_repair_attempts": context.metadata.get("repair_attempts", [])[-3:],
                "last_repair_analysis": context.metadata.get("last_repair_analysis"),
                "candidate_fallback": context.metadata.get("repair_fallback"),
                "retrieved_memories": context.metadata.get(
                    "retrieved_memories", []
                ),
                "investigations": [
                    item for item in context.metadata.get("debug_observations", [])[-6:]
                    if item.get("candidate_id") == candidate.candidate_id
                ],
                "remaining_repair_attempts": (
                    context.budget.max_repairs - context.budget.repair_attempts
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        payload = self.provider.generate_structured(
            instructions=(
                DEBUGGER_INSTRUCTIONS
                if context.spec_ir.coding_style_constraint == "legacy_verilog"
                else DEBUGGER_INSTRUCTIONS
                + " For strict_sv, keep synthesizable always_ff/always_comb and no DUT initial."
            ),
            input_text=prompt,
            schema_name="verilog_rtl_repair",
            schema=self._schema(),
        )
        patched_rtl = self._validate_rtl(payload.get("patched_rtl"), context.spec_ir.module_name)
        if patched_rtl == source_rtl:
            from rtlforge.agents.validation import RepairNoProgressError

            raise RepairNoProgressError("Debugger returned an unchanged candidate")
        if context.failure_category == "FUNCTIONAL_MISMATCH":
            source_lines = [
                line.strip() for line in source_rtl.splitlines()
                if line.strip() and not line.lstrip().startswith("//")
            ]
            patched_lines = [
                line.strip() for line in patched_rtl.splitlines()
                if line.strip() and not line.lstrip().startswith("//")
            ]
            if patched_lines == source_lines:
                from rtlforge.agents.validation import RepairNoProgressError

                raise RepairNoProgressError(
                    "Debugger changed only standalone comments or whitespace"
                )

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
                "format_fallback": payload.get("_format_fallback"),
            },
        )

    @staticmethod
    def _feedback_for(context: RTLTaskContext, candidate_id: str) -> dict[str, Any]:
        if (context.failure_category or "").startswith("SYNTHESIS_"):
            from rtlforge.orchestration.operations import WorkflowOperations

            return WorkflowOperations._selected_debugger_feedback(context)
        attempts = context.metadata.get("verification_attempts", [])
        if not isinstance(attempts, list):
            return {}
        for attempt in attempts:
            if isinstance(attempt, dict) and attempt.get("candidate_id") == candidate_id:
                feedback = attempt.get("debugger_feedback", {})
                return dict(feedback) if isinstance(feedback, dict) else {}
        history = context.metadata.get("verification_history", [])
        if isinstance(history, list):
            for cycle in reversed(history):
                if not isinstance(cycle, dict):
                    continue
                for attempt in cycle.get("attempts", []):
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
