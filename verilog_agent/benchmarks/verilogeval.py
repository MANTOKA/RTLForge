from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path


def human_v2_run_root(base: Path, model: str) -> Path:
    safe_model = "".join(
        character if character.isalnum() or character in "._-" else "_"
        for character in model
    ).strip("_")
    return base / "human-v2" / (safe_model.lower() or "model")


@dataclass(frozen=True, slots=True)
class VerilogEvalCase:
    problem_id: str
    prompt_path: Path
    testbench_path: Path
    reference_path: Path

    def read_agent_spec(self) -> str:
        """Return only the specification that Planner/Generator are allowed to see."""
        return self.prompt_path.read_text(encoding="utf-8")

    def verification_inputs(self, generated_rtl: Path) -> tuple[Path, Path]:
        """Return candidate plus golden testbench for the deterministic verifier only."""
        return generated_rtl, self.testbench_path


def load_human_v2(root: Path, *, expected_count: int | None = 156) -> list[VerilogEvalCase]:
    """Load the external Human v2 triplets without copying benchmark answers into the repo."""
    resolved_root = root.resolve(strict=True)
    if not resolved_root.is_dir():
        raise ValueError(f"benchmark root is not a directory: {resolved_root}")
    cases = []
    missing: list[str] = []
    for prompt_path in sorted(resolved_root.glob("*_prompt.txt")):
        problem_id = prompt_path.name.removesuffix("_prompt.txt")
        testbench_path = resolved_root / f"{problem_id}_test.sv"
        reference_path = resolved_root / f"{problem_id}_ref.sv"
        absent = [path.name for path in (testbench_path, reference_path) if not path.is_file()]
        if absent:
            missing.extend(absent)
            continue
        cases.append(
            VerilogEvalCase(
                problem_id=problem_id,
                prompt_path=prompt_path,
                testbench_path=testbench_path,
                reference_path=reference_path,
            )
        )
    if missing:
        raise ValueError(f"incomplete benchmark triplets; missing: {', '.join(missing[:5])}")
    if expected_count is not None and len(cases) != expected_count:
        raise ValueError(f"expected {expected_count} Human v2 cases, found {len(cases)}")
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an external VerilogEval-Human v2 copy")
    parser.add_argument(
        "--dataset-root",
        default=os.environ.get("VERILOG_EVAL_HUMAN_V2_ROOT"),
        help="dataset_dumpall directory (or set VERILOG_EVAL_HUMAN_V2_ROOT)",
    )
    parser.add_argument("--expected-count", type=int, default=156)
    parser.add_argument(
        "--preflight-reference",
        action="store_true",
        help="simulate official reference RTL against golden testbenches without model calls",
    )
    parser.add_argument("--limit", type=int, help="limit reference preflight to the first N cases")
    parser.add_argument(
        "--summary-only", action="store_true", help="omit per-case rows from terminal JSON"
    )
    parser.add_argument("--docker-image", default="verilog-agent-sandbox:latest")
    parser.add_argument("--runs-root", type=Path, default=Path("runs"))
    args = parser.parse_args()
    if not args.dataset_root:
        parser.error("--dataset-root or VERILOG_EVAL_HUMAN_V2_ROOT is required")
    cases = load_human_v2(Path(args.dataset_root), expected_count=args.expected_count)
    if args.preflight_reference:
        from verilog_agent.benchmarks.preflight import preflight_references
        from verilog_agent.services import DockerEDARunner

        dataset_root = Path(args.dataset_root).resolve()
        runner = DockerEDARunner(
            runs_root=args.runs_root,
            image=args.docker_image,
            readonly_roots=(dataset_root,),
        )
        output_path = (
            args.runs_root / "human-v2" / "reference" / "reference_preflight.json"
        )
        payload = preflight_references(cases, runner, output_path, limit=args.limit)
        terminal_payload = (
            {key: value for key, value in payload.items() if key != "results"}
            if args.summary_only
            else payload
        )
        print(json.dumps(terminal_payload, ensure_ascii=False, indent=2))
        return 0 if payload["failed"] == 0 else 1
    print(
        json.dumps(
            {
                "dataset": "VerilogEval-Human-v2",
                "case_count": len(cases),
                "first_problem_id": cases[0].problem_id if cases else None,
                "visibility_policy": {
                    "planner_generator": "prompt_only",
                    "debugger": "prompt_and_structured_diagnostics_only",
                    "verification": "golden_testbench",
                    "reference_rtl": "preflight_only_never_agent_context",
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
