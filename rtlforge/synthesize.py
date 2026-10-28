"""Recheck and synthesize an existing candidate through Ross, without LLM regeneration."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from rtlforge.continuation import read_source
from rtlforge.domain import Candidate, RTLTaskContext, TaskStage
from rtlforge.domain.synthesis import SynthesisConstraints
from rtlforge.services import (
    DockerEDARunner,
    GoldenTestbenchVerificationService,
    StaticCandidateEvaluator,
)
from rtlforge.services.vivado_mcp import VivadoMCPRunner


def synthesize_existing(source, candidate_id, constraints, server, vivado, *, output,
                        testbench=None, image="rtlforge-sandbox:latest"):
    source_path, previous = read_source(Path(source))
    old = previous.candidate(candidate_id or previous.selected_candidate_id)
    rtl = Path(old.artifact_path).read_bytes()
    if sha256(rtl).hexdigest() != old.code_hash:
        raise ValueError("Historical RTL hash mismatch")
    if previous.spec_ir is None or previous.spec_ir.missing_semantics():
        raise ValueError("A confirmed SpecIR is required")
    tb = Path(testbench) if testbench else source_path.parent.parent / "provided_tb.sv"
    if not tb.is_file():
        raise ValueError("Provide an independent TB for this synthesis acceptance run")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    context = RTLTaskContext(
        previous.task_id, previous.raw_spec, spec_ir=previous.spec_ir,
        current_stage=TaskStage.EVALUATING,
        task_tree_root=previous.task_tree_root,
        synthesis_constraints=SynthesisConstraints.from_dict(constraints.to_dict()),
        metadata={"source_context": str(source_path), "source_candidate": old.candidate_id,
                  "candidate_policy": "sequential_repair_first_v1"},
    )
    destination = output / context.task_id / "candidates" / old.candidate_id / Path(
        old.artifact_path
    ).name
    destination.parent.mkdir(parents=True)
    destination.write_bytes(rtl)
    from rtlforge.integration import copy_modules

    copy_modules(previous, context, output / context.task_id)
    context.candidates = [Candidate(old.candidate_id, str(destination), old.code_hash)]
    context.selected_candidate_id = old.candidate_id
    snapshot_tb = output / "provided_tb.sv"
    snapshot_tb.write_bytes(tb.read_bytes())
    runner = DockerEDARunner(output, image=image, readonly_roots=(output,))
    static = StaticCandidateEvaluator(runner, output).evaluate(context)
    static_passed = static.accepted and static.scores.get(old.candidate_id, 0) >= 75
    summary = {"source": str(source_path), "rtl_sha256": old.code_hash,
               "static_passed": static_passed, "repair_loop_executed": False}
    context.current_stage = TaskStage.FAILED
    context.failure_category = static.category or "QUALITY_ERROR"
    if static_passed:
        verification = GoldenTestbenchVerificationService(
            runner, output, lambda _: snapshot_tb, testbench_source="user_provided",
        ).verify(context, context.candidate())
        summary["functional_passed"] = verification.passed
        context.failure_category = verification.category
        if verification.passed:
            context.metadata.update(
                golden_tb_pass=True, functional_verified_candidate=old.candidate_id,
            )
            context.synthesis_constraints.top = context.spec_ir.module_name
            context.synthesis_constraints.clock_port = (
                context.synthesis_constraints.clock_port or context.spec_ir.clock
            )
            result = VivadoMCPRunner(server, vivado, output).synthesize(
                context, context.candidate(),
            )
            context.candidate().metadata["synthesis_result"] = result.to_dict()
            context.failure_category = result.category if result.status != "PASS" else None
            context.current_stage = (
                TaskStage.COMPLETED if result.status == "PASS" else TaskStage.FAILED
            )
            summary["synthesis"] = result.to_dict()
    task_root = output / context.task_id
    (task_root / "context.json").write_text(
        json.dumps(context.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8",
    )
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--candidate")
    parser.add_argument("--constraints", type=Path, required=True)
    parser.add_argument("--server", type=Path, required=True)
    parser.add_argument("--vivado", type=Path, required=True)
    parser.add_argument("--tb", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    constraints = SynthesisConstraints.from_dict(json.loads(args.constraints.read_text()))
    output = args.output or Path("runs/synthesis") / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    summary = synthesize_existing(
        args.source, args.candidate, constraints, args.server, args.vivado,
        output=output, testbench=args.tb,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Evidence: {output.resolve()}")
    return 0 if summary.get("synthesis", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
