from __future__ import annotations

import argparse
import json
import sys
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

from verilog_agent.agents import ArtifactRepository, RealDebugger
from verilog_agent.domain import Budget, Candidate, RTLTaskContext, SpecIR, TaskTreeNode
from verilog_agent.providers import OpenAIResponsesProvider, ProviderSettings
from verilog_agent.services import (
    AdaptiveReflectionRouter,
    DockerEDARunner,
    GoldenTestbenchVerificationService,
    StaticCandidateEvaluator,
)


def run_repair_replay(
    *,
    source_summary: Path,
    faulty_rtl: Path,
    testbench: Path,
    reference: Path,
    provider: OpenAIResponsesProvider,
    runs_root: Path,
    docker_image: str,
    failure_source: str = "defect_injection",
    task_id: str | None = None,
) -> dict:
    """Replay one labelled defect without exposing benchmark oracle content to the LLM."""
    source = json.loads(source_summary.read_text(encoding="utf-8"))
    resolved_task_id = task_id or f"repair_replay_{uuid4().hex[:10]}"
    spec_ir = SpecIR.from_dict(source["spec_ir"])
    context = RTLTaskContext(
        task_id=resolved_task_id,
        raw_spec=str(source["input"]),
        spec_ir=spec_ir,
        task_tree_root=TaskTreeNode.from_dict(source["task_tree"]),
        budget=Budget(max_candidates=1, max_repairs=1, generated_candidates=1),
    )
    artifacts = ArtifactRepository(runs_root)
    rtl = faulty_rtl.read_text(encoding="utf-8")
    candidate_path = artifacts.write_candidate(
        resolved_task_id, "cand_01", spec_ir.module_name, rtl
    )
    candidate = Candidate(
        candidate_id="cand_01",
        artifact_path=candidate_path.as_posix(),
        code_hash=sha256(rtl.encode("utf-8")).hexdigest(),
        metadata={"source": failure_source, "source_summary": str(source_summary)},
    )
    context.candidates = [candidate]
    context.selected_candidate_id = candidate.candidate_id

    runner = DockerEDARunner(
        runs_root,
        image=docker_image,
        readonly_roots=(testbench.resolve().parent,),
    )
    evaluator = StaticCandidateEvaluator(runner, runs_root)
    verifier = GoldenTestbenchVerificationService(
        runner=runner,
        runs_root=runs_root,
        testbench_resolver=lambda _context: testbench,
        reference_resolver=lambda _context: reference,
        testbench_source="defect_injection_hidden_oracle",
    )
    initial_static = evaluator.evaluate(context)
    if not initial_static.accepted:
        raise RuntimeError(f"injected candidate failed static gates: {initial_static.reason}")
    initial = verifier.verify(context, candidate)
    if initial.passed:
        raise RuntimeError("injected candidate unexpectedly passed; no Debugger call was made")
    context.failure_category = initial.category
    decision = AdaptiveReflectionRouter().route(
        initial.category, _selected_feedback(context, candidate.candidate_id)
    )
    context.metadata["reflection_decision"] = decision.to_dict()
    if not decision.invokes_debugger:
        raise RuntimeError(f"Router blocked Debugger: {decision.reason}")

    context.budget.record_repair()
    repaired = RealDebugger(provider, artifacts=artifacts).repair(context, candidate)
    context.candidates.append(repaired)
    context.selected_candidate_id = repaired.candidate_id
    repaired_static = evaluator.evaluate(context)
    repaired_verification = (
        verifier.verify(context, repaired)
        if repaired_static.accepted
        else None
    )
    payload = {
        "mode": f"{failure_source}_repair_replay",
        "task_id": resolved_task_id,
        "baseline_included": failure_source == "natural_failure",
        "oracle_content_exposed_to_debugger": False,
        "initial": {
            "candidate_id": candidate.candidate_id,
            "static_accepted": initial_static.accepted,
            "verification_passed": initial.passed,
            "category": initial.category,
        },
        "reflection": decision.to_dict(),
        "repair": repaired.to_dict(),
        "repaired_static_accepted": repaired_static.accepted,
        "repaired_verification_passed": (
            repaired_verification.passed if repaired_verification else False
        ),
        "repaired_category": (
            repaired_verification.category
            if repaired_verification
            else repaired_static.category
        ),
    }
    output = runs_root / resolved_task_id / "repair_replay.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    payload["report_path"] = output.as_posix()
    return payload


def _selected_feedback(context: RTLTaskContext, candidate_id: str) -> dict:
    for attempt in context.metadata.get("verification_attempts", []):
        if attempt.get("candidate_id") == candidate_id:
            return dict(attempt.get("debugger_feedback", {}))
    return {}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Replay one labelled RTL defect through Router")
    parser.add_argument("--source-summary", type=Path, required=True)
    parser.add_argument("--faulty-rtl", type=Path, required=True)
    parser.add_argument("--testbench", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--runs-root", type=Path, default=Path("runs"))
    parser.add_argument("--docker-image", default="verilog-agent-sandbox:latest")
    parser.add_argument(
        "--failure-source",
        choices=("defect_injection", "natural_failure"),
        default="defect_injection",
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    load_dotenv(args.env_file, override=False)
    provider = OpenAIResponsesProvider(ProviderSettings.from_env())
    result = run_repair_replay(
        source_summary=args.source_summary,
        faulty_rtl=args.faulty_rtl,
        testbench=args.testbench,
        reference=args.reference,
        provider=provider,
        runs_root=args.runs_root,
        docker_image=args.docker_image,
        failure_source=args.failure_source,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
