from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path


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
    args = parser.parse_args()
    if not args.dataset_root:
        parser.error("--dataset-root or VERILOG_EVAL_HUMAN_V2_ROOT is required")
    cases = load_human_v2(Path(args.dataset_root), expected_count=args.expected_count)
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
