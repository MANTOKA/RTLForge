from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from verilog_agent.domain import EvaluationOutcome, RTLTaskContext


class DockerToolError(RuntimeError):
    """A deterministic sandbox invocation could not produce a ToolResult."""


class EDARunner(Protocol):
    def run(self, action: str, candidate_path: Path) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class DockerEDARunner:
    """Invoke the existing restricted Docker wrapper for one generated RTL file."""

    runs_root: Path
    image: str = "verilog-agent-sandbox:latest"
    docker_bin: str | None = None
    timeout_seconds: int = 120

    def run(self, action: str, candidate_path: Path) -> dict[str, Any]:
        host_root = self.runs_root.resolve()
        resolved_candidate = candidate_path.resolve(strict=True)
        try:
            relative_candidate = resolved_candidate.relative_to(host_root)
        except ValueError as error:
            raise DockerToolError(
                "candidate artifact is outside the configured runs root"
            ) from error

        docker = self._docker_binary()
        mount_root = host_root.parent
        container_input_root = f"/repo/{host_root.name}"
        container_candidate = f"/repo/{host_root.name}/{relative_candidate.as_posix()}"
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
            f"VERILOG_AGENT_INPUT_ROOT={container_input_root}",
            "--pids-limit",
            "128",
            "--memory",
            "512m",
            "--cpus",
            "1",
            "--mount",
            f"type=bind,source={mount_root},target=/repo,readonly",
            self.image,
            "python3",
            "-u",
            "/opt/verilog-agent/tool_wrapper.py",
            action,
            container_candidate,
        ]
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

        task_root = self.runs_root / context.task_id
        evidence_root = task_root / "tool_runs"
        evidence_root.mkdir(parents=True, exist_ok=True)
        reports: dict[str, dict[str, Any]] = {}
        scores: dict[str, float] = {}

        for candidate in context.candidates:
            report = self._evaluate_candidate(
                candidate.artifact_path, candidate.candidate_id, evidence_root
            )
            reports[candidate.candidate_id] = report
            candidate.metadata["static_evaluation"] = report
            scores[candidate.candidate_id] = (
                float(report["quality_score"]) if report["accepted"] else 0.0
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
            if reports[candidate.candidate_id]["accepted"]
        ]
        if not accepted:
            reasons = "; ".join(
                f"{candidate_id}: {report['reason']}" for candidate_id, report in reports.items()
            )
            return EvaluationOutcome(False, None, scores=scores, reason=reasons)

        selected = min(accepted, key=lambda item: (-scores[item.candidate_id], item.candidate_id))
        return EvaluationOutcome(True, selected.candidate_id, scores=scores)

    def _evaluate_candidate(
        self, artifact_path: str, candidate_id: str, evidence_root: Path
    ) -> dict[str, Any]:
        path = Path(artifact_path)
        results: dict[str, dict[str, Any]] = {}
        try:
            for action in ("compile", "lint", "eval_quality"):
                result = self.runner.run(action, path)
                results[action] = result
                (evidence_root / f"{candidate_id}_{action}.json").write_text(
                    json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
        except (DockerToolError, OSError) as error:
            return {
                "accepted": False,
                "reason": str(error),
                "quality_score": 0,
                "results": results,
            }

        failed = [action for action, result in results.items() if result.get("status") != "PASS"]
        quality_score = float(results["eval_quality"].get("metrics", {}).get("score", 0))
        return {
            "accepted": not failed,
            "reason": "" if not failed else f"failed static gates: {', '.join(failed)}",
            "quality_score": quality_score,
            "results": results,
        }
