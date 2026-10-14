from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

_MODULE_NAME_RE = re.compile(
    r"^\s*Module\s+name\s*:\s*(?:\r?\n\s*)?([A-Za-z_][A-Za-z0-9_$]*)",
    re.IGNORECASE | re.MULTILINE,
)
_MODULE_DECL_RE = re.compile(
    r"\bmodule\s+([A-Za-z_][A-Za-z0-9_$]*)\b",
    re.IGNORECASE,
)


def rtllm_run_root(base: Path, model: str) -> Path:
    safe_model = "".join(
        character if character.isalnum() or character in "._-" else "_"
        for character in model
    ).strip("_")
    return base / "rtllm-2.1" / (safe_model.lower() or "model")


@dataclass(frozen=True, slots=True)
class RTLLMCase:
    problem_id: str
    category: str
    prompt_path: Path
    testbench_path: Path
    reference_path: Path
    module_name: str
    reference_module_name: str

    def read_agent_spec(self) -> str:
        return self.prompt_path.read_text(encoding="utf-8")


def load_rtllm(root: Path, *, expected_count: int | None = 50) -> list[RTLLMCase]:
    resolved_root = root.resolve(strict=True)
    if not resolved_root.is_dir():
        raise ValueError(f"benchmark root is not a directory: {resolved_root}")
    cases: list[RTLLMCase] = []
    errors: list[str] = []
    seen_ids: set[str] = set()
    for prompt_path in sorted(resolved_root.rglob("design_description.txt")):
        case_root = prompt_path.parent
        problem_id = case_root.name
        if problem_id in seen_ids:
            errors.append(f"duplicate problem id: {problem_id}")
            continue
        seen_ids.add(problem_id)
        testbench_path = case_root / "testbench.v"
        references = sorted(case_root.glob("verified*.v"))
        if not testbench_path.is_file() or len(references) != 1:
            errors.append(
                f"{problem_id}: expected testbench.v and exactly one verified*.v"
            )
            continue
        prompt = prompt_path.read_text(encoding="utf-8")
        module_match = _MODULE_NAME_RE.search(prompt)
        if module_match is None:
            errors.append(f"{problem_id}: module name not found in design_description.txt")
            continue
        module_name = module_match.group(1)
        reference_path = references[0]
        reference_source = reference_path.read_text(encoding="utf-8")
        declared_modules = _MODULE_DECL_RE.findall(reference_source)
        try:
            reference_module_name = _reference_top(module_name, declared_modules)
        except ValueError as error:
            errors.append(f"{problem_id}: {error}")
            continue
        relative_parts = case_root.relative_to(resolved_root).parts
        category = relative_parts[0] if relative_parts else "Uncategorized"
        cases.append(
            RTLLMCase(
                problem_id=problem_id,
                category=category,
                prompt_path=prompt_path,
                testbench_path=testbench_path,
                reference_path=reference_path,
                module_name=module_name,
                reference_module_name=reference_module_name,
            )
        )
    if errors:
        raise ValueError("invalid RTLLM dataset: " + "; ".join(errors[:5]))
    if expected_count is not None and len(cases) != expected_count:
        raise ValueError(f"expected {expected_count} RTLLM cases, found {len(cases)}")
    return cases


def adapt_rtllm_reference(case: RTLLMCase, artifact_root: Path) -> Path:
    """Rename only the reference top for preflight; never use this in Agent context."""
    source = case.reference_path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"\bmodule\s+{re.escape(case.reference_module_name)}\b",
        re.IGNORECASE,
    )
    adapted, replacements = pattern.subn(
        f"module {case.module_name}", source, count=1
    )
    if replacements != 1:
        raise ValueError(
            f"{case.problem_id}: reference top {case.reference_module_name} not found"
        )
    artifact_root.mkdir(parents=True, exist_ok=True)
    path = artifact_root / f"{case.problem_id}_reference_adapter.v"
    path.write_text(adapted, encoding="utf-8", newline="\n")
    return path


def _reference_top(required: str, declared_modules: list[str]) -> str:
    exact = next((name for name in declared_modules if name == required), None)
    if exact is not None:
        return exact
    case_insensitive = next(
        (name for name in declared_modules if name.lower() == required.lower()), None
    )
    if case_insensitive is not None:
        return case_insensitive
    verified = [name for name in declared_modules if name.lower().startswith("verified_")]
    preferred = next(
        (
            name
            for name in verified
            if name.removeprefix("verified_").lower() == required.lower()
        ),
        None,
    )
    if preferred is not None:
        return preferred
    if len(verified) == 1:
        return verified[0]
    raise ValueError(
        f"cannot identify reference top for {required}; declared={declared_modules}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate RTLLM 2.1 layout or preflight verified RTL"
    )
    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=Path(os.environ.get("RTLLM_ROOT", "data/benchmarks/rtllm-2.1")),
    )
    parser.add_argument("--expected-count", type=int, default=50)
    parser.add_argument("--preflight-reference", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--summary-only", action="store_true")
    parser.add_argument("--docker-image", default="verilog-agent-sandbox:latest")
    parser.add_argument("--runs-root", type=Path, default=Path("runs"))
    args = parser.parse_args()
    cases = load_rtllm(args.dataset_root, expected_count=args.expected_count)
    if args.preflight_reference:
        from verilog_agent.benchmarks.preflight import preflight_references
        from verilog_agent.services import DockerEDARunner

        reference_root = args.runs_root / "rtllm-2.1" / "reference"
        runner_root = reference_root / "work"
        runner = DockerEDARunner(
            runner_root,
            image=args.docker_image,
            readonly_roots=(args.dataset_root.resolve(),),
        )
        payload = preflight_references(
            cases,
            runner,
            reference_root / "reference_preflight.json",
            limit=args.limit,
            artifact_root=runner_root / "_reference_adapters",
            reference_adapter=adapt_rtllm_reference,
            include_original_reference=False,
            dataset_name="RTLLM-2.1",
        )
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
                "dataset": "RTLLM-2.1",
                "case_count": len(cases),
                "categories": sorted({case.category for case in cases}),
                "first_problem_id": cases[0].problem_id if cases else None,
                "visibility_policy": {
                    "planner_generator": "design_description_only",
                    "debugger": "structured_diagnostics_only",
                    "verification": "hidden_testbench",
                    "verified_rtl": "preflight_only_never_agent_context",
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
