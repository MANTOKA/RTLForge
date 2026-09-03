from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from verilog_agent.benchmarks.debugger_replay import run_repair_replay
from verilog_agent.benchmarks.preflight import preflight_references
from verilog_agent.benchmarks.verilogeval import VerilogEvalCase, load_human_v2
from verilog_agent.providers import (
    ModelProviderError,
    OpenAIResponsesProvider,
    ProviderConfigurationError,
    ProviderSettings,
)
from verilog_agent.services import AdaptiveReflectionRouter, DockerEDARunner

NORMAL_CASE = "Prob035_count1to10"
TOOL_INCOMPATIBLE_CASES = ("Prob151_review2015_fsm", "Prob156_review2015_fancytimer")
NATURAL_FAILURE_CASES = ("Prob154_fsm_ps2data", "Prob155_lemmings4")
MISMATCH_RE = re.compile(r"Mismatches:\s*(\d+)", re.IGNORECASE)


def _heading(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def _find_summary(runs_root: Path, problem_id: str) -> Path:
    matches: list[Path] = []
    for path in runs_root.glob("task_*/showcase_summary.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        source = payload.get("verification", {}).get("testbench", {}).get("source")
        if source == f"VerilogEval-Human-v2/{problem_id}":
            matches.append(path)
    if not matches:
        raise FileNotFoundError(
            f"No stored Agent run for {problem_id}. Run verilog_agent.agents.demo "
            "for that case first."
        )
    return max(matches, key=lambda item: item.stat().st_mtime)


def _case_by_id(cases: list[VerilogEvalCase]) -> dict[str, VerilogEvalCase]:
    return {case.problem_id: case for case in cases}


def _mismatch_count(result: dict[str, Any]) -> int | None:
    match = MISMATCH_RE.search(str(result.get("stdout", "")))
    return int(match.group(1)) if match else None


def _find_recorded_repair(
    runs_root: Path,
    source_summary: Path,
    *,
    initial_mismatches: int | None = None,
) -> dict[str, Any] | None:
    matches: list[tuple[float, dict[str, Any]]] = []
    expected = source_summary.resolve()
    for path in runs_root.glob("repair_replay_*/repair_replay.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        recorded_source = payload.get("source_summary")
        if recorded_source is None:
            recorded_source = payload.get("repair", {}).get("metadata", {}).get(
                "source_summary"
            )
        source_matches = bool(recorded_source) and Path(recorded_source).resolve() == expected
        legacy_mismatches = (
            payload.get("initial", {})
            .get("debugger_feedback", {})
            .get("mismatch_summary", {})
            .get("total_mismatched_samples")
        )
        legacy_matches = recorded_source is None and legacy_mismatches == initial_mismatches
        if source_matches or legacy_matches:
            payload["report_path"] = path.as_posix()
            matches.append((path.stat().st_mtime, payload))
    return max(matches, key=lambda item: item[0])[1] if matches else None


def _print_recorded_repair(repair: dict[str, Any] | None) -> None:
    if repair is None:
        print("Recorded Debugger -> 无历史修复证据")
        return
    metadata = repair["repair"]["metadata"]
    feedback = repair.get("repaired_feedback", {}).get("mismatch_summary", {})
    mismatches = feedback.get("total_mismatched_samples")
    samples = feedback.get("total_samples")
    status = "PASS" if repair.get("repaired_verification_passed") else "FAIL"
    print(f"Recorded Debugger -> {repair['repair']['candidate_id']}")
    print(f"Recorded diff -> {metadata.get('diff_path')}")
    print(f"Recorded Re-Verification -> {status}; mismatches={mismatches}/{samples}")


def _normalized_category(result: dict[str, Any]) -> str:
    category = str(result.get("category", "UNKNOWN"))
    if category in {"ASSERTION_FAILURE", "SIMULATION_ERROR"}:
        return "FUNCTIONAL_MISMATCH"
    return category


def _show_agent_case(
    *,
    problem_id: str,
    case: VerilogEvalCase,
    summary_path: Path,
    runner: DockerEDARunner,
    expect_failure: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    spec = summary["spec_ir"]
    _heading(f"Agent 场景：{problem_id}")
    print(f"历史任务: {summary['task_id']}")
    print(f"Planner -> module={spec['module_name']}, ports={len(spec['ports'])}")
    print("Task Tree -> " + " -> ".join(
        child["title"] for child in summary["task_tree"].get("children", [])
    ))
    print(f"Generator -> {len(summary['candidates'])} 个候选")
    rows = []
    first_result: dict[str, Any] | None = None
    for candidate in summary["candidates"]:
        path = Path(candidate["artifact_path"])
        static = candidate.get("metadata", {}).get("static_evaluation", {})
        result = runner.run_files(
            "simulate", [case.testbench_path, case.reference_path, path]
        )
        first_result = first_result or result
        row = {
            "candidate_id": candidate["candidate_id"],
            "static_score": static.get("quality_score"),
            "status": result.get("status"),
            "category": _normalized_category(result),
            "mismatches": _mismatch_count(result),
            "duration_ms": result.get("duration_ms"),
        }
        rows.append(row)
        print(
            f"  {row['candidate_id']}: static={row['static_score']}, "
            f"simulate={row['status']}, category={row['category']}, "
            f"mismatches={row['mismatches']}"
        )
    assert first_result is not None
    observed_failure = all(row["status"] == "FAIL" for row in rows)
    print(f"Verification -> {'FAIL' if observed_failure else 'PASS'}")
    if observed_failure:
        decision = AdaptiveReflectionRouter().route(_normalized_category(first_result))
        print(
            f"Router -> {decision.policy.value}; action={decision.action.value}; "
            f"Debugger={'YES' if decision.invokes_debugger else 'NO'}"
        )
    else:
        print("Router/Debugger -> 未触发（PASS 应直接完成）")
    if observed_failure != expect_failure:
        print("警告：当前复验结果与演示预期不同，请检查镜像或生成产物版本。")
    return summary, {"problem_id": problem_id, "candidates": rows}


def _show_tool_incompatibility(
    *,
    case: VerilogEvalCase,
    runner: DockerEDARunner,
    runs_root: Path,
) -> dict[str, Any]:
    _heading(f"工具兼容场景：{case.problem_id}")
    print("此处验证官方 reference 与 golden testbench，不评价 Agent RTL。")
    output = runs_root / "demos" / f"{case.problem_id}_reference_preflight.json"
    payload = preflight_references([case], runner, output)
    result = payload["results"][0]
    decision = AdaptiveReflectionRouter().route(result["category"])
    message = ""
    diagnostics = result.get("diagnostics", [])
    if diagnostics:
        message = str(diagnostics[0].get("message", ""))
    print(
        f"Icarus -> {result['status']}; category={result['category']}; "
        f"diagnostic={message}"
    )
    print(
        f"Router -> {decision.policy.value}; action={decision.action.value}; "
        "Debugger=NO（不能让模型修改正确 RTL 来迎合工具缺口）"
    )
    return result


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Explain the Human v2 repair pipeline")
    parser.add_argument(
        "--dataset-root",
        default=os.environ.get("VERILOG_EVAL_HUMAN_V2_ROOT"),
        help="Human v2 dataset_dumpall directory",
    )
    parser.add_argument("--runs-root", type=Path, default=Path("runs"))
    parser.add_argument("--docker-image", default="verilog-agent-sandbox:reflection")
    parser.add_argument(
        "--with-debugger",
        action="store_true",
        help="Call the real Debugger once for each natural failure (uses API tokens)",
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    load_dotenv(args.env_file, override=False)
    if not args.dataset_root:
        parser.error("--dataset-root or VERILOG_EVAL_HUMAN_V2_ROOT is required")

    cases = load_human_v2(Path(args.dataset_root))
    by_id = _case_by_id(cases)
    required = (NORMAL_CASE, *TOOL_INCOMPATIBLE_CASES, *NATURAL_FAILURE_CASES)
    missing = [problem_id for problem_id in required if problem_id not in by_id]
    if missing:
        parser.error(f"dataset is missing required demo cases: {', '.join(missing)}")
    runner = DockerEDARunner(
        args.runs_root,
        image=args.docker_image,
        readonly_roots=(Path(args.dataset_root).resolve(),),
    )

    _heading("Human v2 可解释端到端演示")
    print("oracle boundary: test.sv/ref.sv 仅由确定性 Verification 使用")
    print(f"real Debugger: {'ENABLED' if args.with_debugger else 'DISABLED'}")
    report: dict[str, Any] = {
        "normal": None,
        "tool_incompatibilities": [],
        "natural_failures": [],
        "debugger_repairs": [],
    }
    normal_summary_path = _find_summary(args.runs_root, NORMAL_CASE)
    _, report["normal"] = _show_agent_case(
        problem_id=NORMAL_CASE,
        case=by_id[NORMAL_CASE],
        summary_path=normal_summary_path,
        runner=runner,
        expect_failure=False,
    )

    for problem_id in TOOL_INCOMPATIBLE_CASES:
        report["tool_incompatibilities"].append(
            _show_tool_incompatibility(
                case=by_id[problem_id], runner=runner, runs_root=args.runs_root
            )
        )

    natural_inputs = []
    for problem_id in NATURAL_FAILURE_CASES:
        summary_path = _find_summary(args.runs_root, problem_id)
        summary, result = _show_agent_case(
            problem_id=problem_id,
            case=by_id[problem_id],
            summary_path=summary_path,
            runner=runner,
            expect_failure=True,
        )
        report["natural_failures"].append(result)
        candidate_path = Path(summary["candidates"][0]["artifact_path"])
        natural_inputs.append((problem_id, summary_path, candidate_path))
        recorded = _find_recorded_repair(
            args.runs_root,
            summary_path,
            initial_mismatches=result["candidates"][0]["mismatches"],
        )
        _print_recorded_repair(recorded)

    if args.with_debugger:
        try:
            provider = OpenAIResponsesProvider(ProviderSettings.from_env())
        except ProviderConfigurationError as error:
            parser.error(str(error))
        for problem_id, summary_path, candidate_path in natural_inputs:
            _heading(f"真实 Debugger：{problem_id}")
            try:
                repair = run_repair_replay(
                    source_summary=summary_path,
                    faulty_rtl=candidate_path,
                    testbench=by_id[problem_id].testbench_path,
                    reference=by_id[problem_id].reference_path,
                    provider=provider,
                    runs_root=args.runs_root,
                    docker_image=args.docker_image,
                    failure_source="natural_failure",
                )
            except (ModelProviderError, RuntimeError, OSError) as error:
                repair = {"problem_id": problem_id, "status": "INTERRUPTED", "reason": str(error)}
                print(f"Debugger -> INTERRUPTED: {error}")
            else:
                print(f"Debugger candidate -> {repair['repair']['candidate_id']}")
                print(f"diff -> {repair['repair']['metadata']['diff_path']}")
                print(
                    "Re-Verification -> "
                    f"{'PASS' if repair['repaired_verification_passed'] else 'FAIL'}"
                )
                mismatch = repair.get("repaired_feedback", {}).get("mismatch_summary")
                if mismatch:
                    print(
                        "Repaired mismatches -> "
                        f"{mismatch.get('total_mismatched_samples')}/"
                        f"{mismatch.get('total_samples')}"
                    )
            report["debugger_repairs"].append(repair)
    else:
        _heading("Debugger 未调用")
        print("添加 --with-debugger 才会产生模型调用和修复候选。")

    output = args.runs_root / "demos" / "human_v2_explain_demo.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _heading("演示结束")
    print(f"完整结构化报告: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
