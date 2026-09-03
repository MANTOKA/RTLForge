from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from verilog_agent.domain import EvaluationOutcome, RTLTaskContext
from verilog_agent.services.interface_check import check_rtl_interface


class DockerToolError(RuntimeError):
    """A deterministic sandbox invocation could not produce a ToolResult."""


class EDARunner(Protocol):
    def run(self, action: str, candidate_path: Path) -> dict[str, Any]: ...


class MultiFileEDARunner(EDARunner, Protocol):
    def run_files(self, action: str, input_paths: list[Path]) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class DockerEDARunner:
    """Invoke the existing restricted Docker wrapper for one generated RTL file."""

    runs_root: Path
    image: str = "verilog-agent-sandbox:latest"
    docker_bin: str | None = None
    timeout_seconds: int = 120
    readonly_roots: tuple[Path, ...] = ()

    def run(self, action: str, candidate_path: Path) -> dict[str, Any]:
        host_root = self.runs_root.resolve()
        resolved_candidate = candidate_path.resolve(strict=True)
        try:
            resolved_candidate.relative_to(host_root)
        except ValueError as error:
            raise DockerToolError(
                "candidate artifact is outside the configured runs root"
            ) from error
        return self.run_files(action, [resolved_candidate])

    def run_files(self, action: str, input_paths: list[Path]) -> dict[str, Any]:
        if not input_paths:
            raise DockerToolError("at least one EDA input file is required")
        host_root = self.runs_root.resolve()
        allowed_roots = [host_root, *(root.resolve() for root in self.readonly_roots)]
        resolved_inputs = [path.resolve(strict=True) for path in input_paths]
        mount_targets: dict[Path, str] = {host_root.parent: "/inputs/repo"}
        container_inputs: list[str] = []
        for resolved_input in resolved_inputs:
            matched_root: Path | None = None
            target = ""
            try:
                relative = resolved_input.relative_to(host_root.parent)
                if resolved_input.is_relative_to(host_root):
                    matched_root = host_root
                    target = "/inputs/repo"
            except ValueError:
                relative = Path()
            if matched_root is None:
                for index, root in enumerate(allowed_roots[1:]):
                    try:
                        relative = resolved_input.relative_to(root)
                    except ValueError:
                        continue
                    matched_root = root
                    target = f"/inputs/oracle_{index}"
                    mount_targets[root] = target
                    break
            if matched_root is None:
                raise DockerToolError("EDA input is outside configured read-only roots")
            container_inputs.append(f"{target}/{relative.as_posix()}")

        docker = self._docker_binary()
        command = [
            docker,
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            "/work:rw,exec,size=64m,uid=10001,gid=10001,mode=0700",
            "--workdir",
            "/work",
            "--env",
            "TMPDIR=/work",
            "--env",
            "VERILOG_AGENT_INPUT_ROOT=/inputs",
            "--pids-limit",
            "128",
            "--memory",
            "512m",
            "--cpus",
            "1",
        ]
        for source, target in mount_targets.items():
            command.extend(
                ["--mount", f"type=bind,source={source},target={target},readonly"]
            )
        command.extend(
            [
                self.image,
                "python3",
                "-u",
                "/opt/verilog-agent/tool_wrapper.py",
                action,
                *container_inputs,
            ]
        )
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise DockerToolError(
                f"Docker {action} exceeded {self.timeout_seconds} seconds"
            ) from error
        except OSError as error:
            raise DockerToolError(f"could not start Docker: {error}") from error
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout).strip()
            raise DockerToolError(f"Docker {action} failed before ToolResult: {message}")
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise DockerToolError("Docker wrapper did not return JSON") from error
        if not isinstance(payload, dict) or payload.get("action") != action:
            raise DockerToolError("Docker wrapper returned an unexpected ToolResult")
        return payload

    def _docker_binary(self) -> str:
        configured = self.docker_bin or os.environ.get("DOCKER_BIN")
        if configured:
            return configured
        discovered = shutil.which("docker")
        if discovered:
            return discovered
        raise DockerToolError("Docker CLI was not found; set DOCKER_BIN to docker.exe")


@dataclass(slots=True)
class StaticCandidateEvaluator:
    """Gate candidates with deterministic compile/lint checks and choose a stable winner."""

    runner: EDARunner
    runs_root: Path

    def evaluate(self, context: RTLTaskContext) -> EvaluationOutcome:
        if not context.candidates:
            return EvaluationOutcome(False, None, reason="no generated candidates")
        if context.spec_ir is None:
            return EvaluationOutcome(False, None, reason="SpecIR is required for interface gating")

        task_root = self.runs_root / context.task_id
        evidence_root = task_root / "tool_runs"
        evidence_root.mkdir(parents=True, exist_ok=True)
        reports: dict[str, dict[str, Any]] = {}
        scores: dict[str, float] = {}

        for candidate in context.candidates:
            report = self._evaluate_candidate(
                candidate.artifact_path,
                candidate.candidate_id,
                evidence_root,
                context.spec_ir,
            )
            reports[candidate.candidate_id] = report
            candidate.metadata["static_evaluation"] = report
            scores[candidate.candidate_id] = (
                float(report["quality_score"])
                if report["eligible_for_verification"]
                else 0.0
            )

        report_path = task_root / "static_evaluation.json"
        report_path.write_text(
            json.dumps({"candidates": reports}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        accepted = [
            candidate
            for candidate in context.candidates
            if reports[candidate.candidate_id]["eligible_for_verification"]
        ]
        if not accepted:
            reasons = "; ".join(
                f"{candidate_id}: {report['reason']}" for candidate_id, report in reports.items()
            )
            return EvaluationOutcome(False, None, scores=scores, reason=reasons)

        ranked = sorted(
            accepted,
            key=lambda item: (
                not reports[item.candidate_id]["threshold_passed"],
                -scores[item.candidate_id],
                item.candidate_id,
            ),
        )
        context.metadata["verification_order"] = [item.candidate_id for item in ranked]
        context.metadata["static_evaluation_report"] = str(report_path)
        selected = ranked[0]
        return EvaluationOutcome(True, selected.candidate_id, scores=scores)

    def _evaluate_candidate(
        self, artifact_path: str, candidate_id: str, evidence_root: Path, spec_ir: Any
    ) -> dict[str, Any]:
        path = Path(artifact_path)
        results: dict[str, dict[str, Any]] = {}
        try:
            interface_result = check_rtl_interface(path, spec_ir)
        except (OSError, UnicodeError, ValueError) as error:
            interface_result = {
                "status": "FAIL",
                "category": "INTERFACE_MISMATCH",
                "errors": [str(error)],
            }
        results["interface"] = interface_result
        (evidence_root / f"{candidate_id}_interface.json").write_text(
            json.dumps(interface_result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        if interface_result["status"] != "PASS":
            return self._report(
                candidate_id,
                results,
                accepted=False,
                hard_gates_passed=False,
                gate_stage="PATH_VALIDATED",
                reason="interface gate failed",
            )

        try:
            for action in ("lint", "compile", "eval_quality"):
                result = self.runner.run(action, path)
                results[action] = result
                (evidence_root / f"{candidate_id}_{action}.json").write_text(
                    json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
        except (DockerToolError, OSError) as error:
            return self._report(
                candidate_id,
                results,
                accepted=False,
                hard_gates_passed=False,
                gate_stage=self._gate_stage(results),
                reason=str(error),
            )

        failed = [
            action
            for action in ("interface", "lint", "compile", "eval_quality")
            if results.get(action, {}).get("status") != "PASS"
        ]
        metrics = results["eval_quality"].get("metrics", {})
        quality_score = float(metrics.get("score", 0))
        threshold = float(metrics.get("threshold", 75))
        hard_gates_passed = not failed
        accepted = hard_gates_passed and quality_score >= threshold
        if failed:
            reason = f"failed static gates: {', '.join(failed)}"
        elif quality_score < threshold:
            reason = f"static quality score {quality_score:g} is below threshold {threshold:g}"
        else:
            reason = ""
        return self._report(
            candidate_id,
            results,
            accepted=accepted,
            hard_gates_passed=hard_gates_passed,
            gate_stage=self._gate_stage(results),
            reason=reason,
        )

    @staticmethod
    def _gate_stage(results: dict[str, dict[str, Any]]) -> str:
        stage = "PATH_VALIDATED"
        for action, passed_stage in (
            ("interface", "INTERFACE_PASSED"),
            ("lint", "LINT_PASSED"),
            ("compile", "COMPILE_PASSED"),
            ("eval_quality", "STATIC_SCORED"),
        ):
            if results.get(action, {}).get("status") != "PASS":
                break
            stage = passed_stage
        return stage

    @staticmethod
    def _report(
        candidate_id: str,
        results: dict[str, dict[str, Any]],
        *,
        accepted: bool,
        hard_gates_passed: bool,
        gate_stage: str,
        reason: str,
    ) -> dict[str, Any]:
        metrics = results.get("eval_quality", {}).get("metrics", {})
        quality_score = float(metrics.get("score", 0))
        threshold = float(metrics.get("threshold", 75))
        scorecard = {
            "candidate_id": candidate_id,
            "static_quality_score": quality_score,
            "threshold": threshold,
            "is_acceptable": accepted,
            "deductions": list(metrics.get("deductions", [])),
            "gate_stage": gate_stage,
            "config_version": str(metrics.get("config_version", "unknown")),
        }
        return {
            "accepted": accepted,
            "eligible_for_verification": hard_gates_passed,
            "threshold_passed": quality_score >= threshold,
            "reason": reason,
            "quality_score": quality_score,
            "scorecard": scorecard,
            "results": results,
        }
