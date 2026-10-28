from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from dotenv import load_dotenv

from rtlforge.agents.debugger import RealDebugger
from rtlforge.agents.generator import ArtifactRepository
from rtlforge.agents.showcase import PlannerGeneratorShowcase, ShowcaseRunError
from rtlforge.benchmarks.preflight import preflight_references
from rtlforge.benchmarks.rtllm import (
    adapt_rtllm_reference,
    load_rtllm,
    rtllm_run_root,
)
from rtlforge.benchmarks.verilogeval import human_v2_run_root, load_human_v2
from rtlforge.memory import DEFAULT_MEMORY_DATABASE, SQLiteMemoryStore
from rtlforge.providers import (
    OpenAIResponsesProvider,
    ProviderConfigurationError,
    ProviderSettings,
)
from rtlforge.providers.usage import sampling_markdown
from rtlforge.services import (
    DockerEDARunner,
    GoldenTestbenchVerificationService,
    StaticCandidateEvaluator,
)
from rtlforge.testing import ScriptedProvider
from rtlforge.testing.examples import (
    OFFLINE_COUNTER_SPEC,
    counter_candidate_payload,
    counter_plan_payload,
)


def main(*, dataset_kind: str = "human_v2") -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description=(
            f"Show {dataset_kind} Planner SpecIR/Task Tree and Generator RTL candidates "
            "step by step. "
            "Live mode consumes model API tokens."
        )
    )
    parser.add_argument(
        "spec",
        nargs="?",
        help="Natural-language RTL requirement; omit it to enter the requirement interactively",
    )
    parser.add_argument("--offline-example", action="store_true")
    parser.add_argument(
        "--interactive", action="store_true",
        help="Prompt for clarification and resume the same task in this terminal",
    )
    parser.add_argument(
        "--orchestrator", choices=("legacy", "langgraph"), default="legacy",
        help="Select the existing Orchestrator or the M1 LangGraph scheduling entry",
    )
    parser.add_argument("--database", type=Path)
    parser.add_argument("--resume-task", help="Resume an existing LangGraph task ID")
    parser.add_argument("--resume-action", choices=("answer", "accept", "reject", "cancel"))
    parser.add_argument("--resume-message", help="User answer for a paused Graph task")
    parser.add_argument(
        "--coding-style", choices=("strict_sv", "legacy_verilog"),
        help="Task-level DUT coding policy; benchmark tasks default to legacy_verilog",
    )
    parser.add_argument(
        "--reference-file", type=Path, action="append", default=[],
        help="Allowlist a read-only reference for the LangGraph Planner",
    )
    parser.add_argument("--task-id", help=argparse.SUPPRESS)
    parser.add_argument("--runs-root", type=Path, default=Path("runs"))
    parser.add_argument(
        "--memory-mode",
        choices=("off", "read_only", "read_write"),
        default="read_write",
        help="Retrieve/write persistent Memory by default; use off for a controlled ablation",
    )
    parser.add_argument(
        "--memory-database", type=Path
    )
    parser.add_argument(
        "--candidates", type=int, choices=(1, 2, 3), default=2,
        help="Independent candidate cap; start with one, regenerate on demand (3 is opt-in)",
    )
    parser.add_argument(
        "--model",
        help="Override RTLFORGE_MODEL for this run; applies to all three LLM roles",
    )
    parser.add_argument(
        "--max-repairs",
        type=int,
        choices=(0, 1, 2, 3),
        default=0,
        help="Enable the real routed Debugger with this maximum repair count",
    )
    parser.add_argument(
        "--static-evaluation",
        action="store_true",
        help=(
            "Run compile, lint, and static quality checks for every candidate in the Docker "
            "sandbox, then select a candidate. Add benchmark options for simulation."
        ),
    )
    parser.add_argument(
        "--docker-image",
        default="rtlforge-sandbox:latest",
        help="Docker image used by --static-evaluation",
    )
    parser.add_argument(
        "--testbench",
        type=Path,
        help="authoritative testbench; implies functional verification after static evaluation",
    )
    if dataset_kind == "rtllm":
        parser.add_argument(
            "--dataset-root",
            type=Path,
            default=Path("data/benchmarks/rtllm-2.1"),
            help="RTLLM 2.1 dataset root",
        )
        parser.add_argument(
            "--case",
            required=True,
            help="RTLLM design id such as adder_8bit",
        )
    else:
        parser.add_argument(
            "--human-v2-root",
            type=Path,
            help=(
                "VerilogEval-Human v2 dataset directory "
                "(for example data/benchmarks/verilog-eval-human-v2)"
            ),
        )
        parser.add_argument(
            "--human-v2-case",
            help="Human v2 problem id such as Prob001_zero; requires --human-v2-root",
        )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Local env file for live mode; defaults to .env in the current directory",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Validate and display non-secret live provider settings without making an API call",
    )
    args = parser.parse_args()

    benchmark_case = None
    dataset_label = None
    if dataset_kind == "rtllm":
        if args.spec is not None or args.offline_example:
            parser.error("RTLLM mode supplies its own prompt; omit spec and --offline-example")
        cases = load_rtllm(args.dataset_root)
        benchmark_case = next(
            (case for case in cases if case.problem_id == args.case), None
        )
        if benchmark_case is None:
            parser.error(f"RTLLM case not found: {args.case}")
        if args.testbench is not None:
            parser.error("RTLLM mode selects its own golden testbench; omit --testbench")
        args.testbench = benchmark_case.testbench_path
        dataset_label = "RTLLM-2.1"
    else:
        if bool(args.human_v2_root) != bool(args.human_v2_case):
            parser.error("--human-v2-root and --human-v2-case must be used together")
        if args.human_v2_root:
            if args.spec is not None or args.offline_example:
                parser.error(
                    "Human v2 mode supplies its own prompt; omit spec and --offline-example"
                )
            cases = load_human_v2(args.human_v2_root)
            benchmark_case = next(
                (case for case in cases if case.problem_id == args.human_v2_case), None
            )
            if benchmark_case is None:
                parser.error(f"Human v2 case not found: {args.human_v2_case}")
            if args.testbench is not None:
                parser.error(
                    "Human v2 mode selects its own golden testbench; omit --testbench"
                )
            args.testbench = benchmark_case.testbench_path
            dataset_label = "VerilogEval-Human-v2"
    if (
        args.testbench is not None
        and not args.static_evaluation
        and not args.check_config
    ):
        parser.error("functional verification requires --static-evaluation")
    if args.orchestrator == "langgraph" and not args.static_evaluation:
        parser.error("LangGraph mode requires --static-evaluation")
    if args.resume_task and args.orchestrator != "langgraph":
        parser.error("--resume-task requires --orchestrator langgraph")
    if args.resume_action and not args.resume_task:
        parser.error("--resume-action requires --resume-task")
    if args.resume_action == "answer" and not args.resume_message:
        parser.error("--resume-action answer requires --resume-message")
    if args.resume_task and args.task_id:
        parser.error("--resume-task and --task-id cannot be combined")
    if args.reference_file and (args.orchestrator != "langgraph" or benchmark_case):
        parser.error("--reference-file is only for non-benchmark LangGraph tasks")
    if any(not path.is_file() for path in args.reference_file):
        parser.error("every --reference-file must be an existing file")
    if args.max_repairs and args.testbench is None and args.orchestrator != "langgraph":
        parser.error("--max-repairs requires a golden --testbench or Human v2 case")

    if args.offline_example:
        if args.env_file is not None or args.check_config or args.model:
            parser.error("--env-file, --check-config, and --model require live mode")
        if args.spec is not None:
            parser.error("--offline-example uses its built-in counter requirement; omit spec")
        if args.candidates != 2:
            parser.error("--offline-example currently contains exactly two scripted candidates")
        raw_spec = OFFLINE_COUNTER_SPEC
        provider = ScriptedProvider(
            counter_plan_payload(),
            *(
                ({
                    "testbench": (
                        "module tb; reg clk=0, rst=1; wire [3:0] q; counter dut(clk,rst,q); "
                        "always #5 clk=~clk; initial begin @(negedge clk); "
                        "if(q!==0) $fatal(1,\"reset\"); rst=0; @(negedge clk); "
                        "if(q!==1) $fatal(1,\"increment\"); "
                        "$display(\"GENERATED_TB_PASS\"); $finish; end "
                        "initial begin #100; $fatal(1,\"timeout\"); end endmodule"
                    ),
                    "test_objectives": ["同步复位", "递增"],
                },)
                if args.orchestrator == "langgraph" else ()
            ),
            *({"candidates": [item]} for item in counter_candidate_payload()["candidates"]),
        )
        provider_label = "scripted_offline_example (NO LLM API CALL)"
    else:
        loaded_env_file = _load_provider_environment(parser, args.env_file)
        try:
            settings = ProviderSettings.from_env()
            if args.model:
                settings = replace(settings, model=args.model)
            provider = OpenAIResponsesProvider(settings)
        except ProviderConfigurationError as error:
            parser.error(str(error))
        if args.check_config:
            _print_provider_config(settings, loaded_env_file)
            return
        raw_spec = benchmark_case.read_agent_spec() if benchmark_case else args.spec
        raw_spec = raw_spec or ("Resuming task" if args.resume_task else _prompt_for_spec(parser))
        if not raw_spec.strip():
            parser.error("RTL requirement must not be empty")
        endpoint = settings.base_url or "https://api.openai.com/v1"
        config_source = f" env_file={loaded_env_file}" if loaded_env_file else ""
        provider_label = (
            f"openai_responses model={settings.model} endpoint={endpoint}{config_source}"
        )

    if benchmark_case is not None:
        group_root = (
            rtllm_run_root(args.runs_root, settings.model)
            if dataset_kind == "rtllm"
            else human_v2_run_root(args.runs_root, settings.model)
        )
        args.runs_root = group_root / "tasks"
        args.database = args.database or group_root / "state" / "showcase.sqlite"
        args.memory_database = args.memory_database or DEFAULT_MEMORY_DATABASE
    else:
        args.database = args.database or Path("work/showcase.sqlite")
        args.memory_database = args.memory_database or DEFAULT_MEMORY_DATABASE

    if args.resume_task:
        from rtlforge.orchestration import SQLiteTaskStore

        saved = SQLiteTaskStore(args.database).load_latest_checkpoint(args.resume_task)
        if saved is None:
            parser.error("No saved task in the selected --database")
        args.max_repairs = saved.budget.max_repairs

    runner = (
        DockerEDARunner(
            args.runs_root,
            image=args.docker_image,
            readonly_roots=((args.testbench.resolve().parent,) if args.testbench else ()),
        )
        if args.static_evaluation
        else None
    )
    showcase = PlannerGeneratorShowcase(
        provider=provider,
        provider_label=provider_label,
        database=args.database,
        runs_root=args.runs_root,
        evaluator=(
            StaticCandidateEvaluator(
                runner=runner,
                runs_root=args.runs_root,
            )
            if runner is not None
            else None
        ),
        verifier=(
            GoldenTestbenchVerificationService(
                runner=runner,
                runs_root=args.runs_root,
                testbench_resolver=lambda _context: args.testbench,
                reference_resolver=(
                    (lambda _context: benchmark_case.reference_path)
                    if benchmark_case and dataset_kind == "human_v2"
                    else None
                ),
                testbench_source=(
                    f"{dataset_label}/{benchmark_case.problem_id}"
                    if benchmark_case
                    else "user_provided"
                ),
            )
            if runner is not None and args.testbench is not None
            else None
        ),
        debugger=(
            RealDebugger(provider, artifacts=ArtifactRepository(args.runs_root))
            if args.max_repairs
            else None
        ),
        memory_store=(
            SQLiteMemoryStore(
                args.memory_database, toolchain_version=args.docker_image
            )
            if args.memory_mode != "off"
            else None
        ),
        memory_mode=args.memory_mode,
        orchestrator_kind=args.orchestrator,
        skip_generated_testbench=args.testbench is not None,
    )
    reference_baseline = None
    if benchmark_case is not None and runner is not None:
        print("\n" + "=" * 72)
        print(f"{dataset_label} 输入与官方参考基线")
        print("=" * 72)
        print(f"prompt_file: {benchmark_case.prompt_path}")
        print(f"golden_testbench: {benchmark_case.testbench_path}")
        print("Agent visibility: prompt only; testbench/ref contents are hidden")
        reference_baseline = preflight_references(
            [benchmark_case],
            runner,
            args.runs_root.parent
            / "single-case"
            / f"{benchmark_case.problem_id}_reference.json",
            artifact_root=args.runs_root / "_reference_adapters",
            reference_adapter=(
                adapt_rtllm_reference if dataset_kind == "rtllm" else None
            ),
            include_original_reference=dataset_kind == "human_v2",
            dataset_name=str(dataset_label),
        )
        reference_result = reference_baseline["results"][0]
        print(
            "official_ref_on_golden_tb: "
            f"{reference_result['status']} [{reference_result['category']}]"
        )
        if reference_result["status"] != "PASS":
            parser.exit(
                2,
                "official reference does not pass this toolchain; "
                "Agent evaluation was not started\n",
            )
    try:
        summary = showcase.run(
            raw_spec,
            candidate_count=args.candidates,
            max_repairs=args.max_repairs,
            task_id=args.task_id,
            resume_task_id=args.resume_task,
            resume_response=(
                {"action": args.resume_action, "message": args.resume_message}
                if args.resume_action else None
            ),
            task_metadata=(
                {
                    "benchmark_problem_id": benchmark_case.problem_id,
                    "required_module_name": getattr(
                        benchmark_case, "module_name", "TopModule"
                    ),
                    "benchmark_dataset": dataset_label,
                    "coding_style_constraint": args.coding_style or "legacy_verilog",
                }
                if benchmark_case
                else {
                    "reference_files": [str(path.resolve()) for path in args.reference_file],
                    **({"coding_style_constraint": args.coding_style}
                       if args.coding_style else ({"coding_style_constraint": "legacy_verilog"}
                                                  if args.offline_example else {})),
                }
            ),
        )
    except ShowcaseRunError as error:
        parser.exit(1, f"showcase error: {error}\n")
    if args.orchestrator == "langgraph" and (args.interactive or sys.stdin.isatty()):
        while summary.get("current_stage") == "WAITING_FOR_USER":
            answer = _prompt_for_clarification(summary)
            if answer is None:
                print("任务保持暂停；可通过 --resume-task 继续。")
                break
            summary = showcase.run(
                raw_spec, resume_task_id=summary["task_id"],
                resume_response={"action": "answer", "message": answer},
            )
    if reference_baseline is not None:
        _print_reference_comparison(summary, dataset_label=str(dataset_label))


def _prompt_for_clarification(summary: dict) -> str | None:
    print("\n请补充以下信息：")
    print(summary.get("clarification_question") or "请说明需要补充或修改的需求。")
    options = summary.get("clarification_options", [])
    if summary.get("pause_reason") != "contract_clarification":
        options = []
    for index, option in enumerate(options, 1):
        print(f"  {index}. {option}")
    print("输入选项编号或直接输入文字，按 Enter 继续；输入 /pause 暂停。")
    while True:
        try:
            answer = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if answer == "/pause":
            return None
        if answer.isdecimal():
            index = int(answer) - 1
            if 0 <= index < len(options):
                return options[index]
            print("编号无效，请选择显示的编号或输入文字说明。")
        elif answer:
            return answer


def _prompt_for_spec(parser: argparse.ArgumentParser) -> str:
    print("请输入一行 RTL 自然语言需求，然后按 Enter：")
    raw_spec = input("> ").strip()
    if not raw_spec:
        parser.error("RTL requirement must not be empty")
    return raw_spec


def _load_provider_environment(
    parser: argparse.ArgumentParser, requested_path: Path | None
) -> Path | None:
    """Load a private env file without overriding values already set by the shell."""

    env_path = requested_path or Path(".env")
    if not env_path.is_file():
        if requested_path is not None:
            parser.error(f"env file does not exist: {env_path}")
        return None
    try:
        load_dotenv(dotenv_path=env_path, override=False)
    except (OSError, UnicodeError) as error:
        parser.error(f"cannot read env file {env_path}: {error}")
    return env_path.resolve()


def _print_provider_config(settings: ProviderSettings, env_file: Path | None) -> None:
    endpoint = settings.base_url or "https://api.openai.com/v1"
    print("Provider configuration OK (NO API CALL)")
    print("provider: openai_responses")
    print(f"model: {settings.model}")
    print(f"base_url: {endpoint}")
    print(f"env_file: {env_file or 'not used; values came from the shell'}")
    print("api_key: configured (value hidden)")
    print(sampling_markdown(settings.sampling_configuration()))
    route = "chat/completions" if settings.uses_chat_completions else "responses"
    print(f"required endpoint: POST {endpoint.rstrip('/')}/{route}")


def _print_reference_comparison(summary: dict, *, dataset_label: str) -> None:
    verification = summary.get("verification") or {}
    attempts = verification.get("attempts", [])
    selected_id = summary.get("selected_candidate_id")
    selected = next(
        (attempt for attempt in attempts if attempt.get("candidate_id") == selected_id),
        attempts[-1] if attempts else {},
    )
    mismatch = selected.get("debugger_feedback", {}).get("mismatch_summary", {})
    mismatch_count = mismatch.get("total_mismatched_samples")
    print("\n" + "=" * 72)
    print("Agent RTL 与官方参考 RTL 对比结论")
    print("=" * 72)
    print("official_reference: PASS")
    print(f"agent_candidate: {'PASS' if summary.get('verified_rtl') else 'FAIL'}")
    print(f"selected_candidate: {selected_id}")
    print(f"mismatches: {mismatch_count if mismatch_count is not None else 'unavailable'}")
    if dataset_label == "RTLLM-2.1":
        print(
            "acceptance_rule: official reference PASS 且 Agent testbench 无失败标记才算功能通过"
        )
    else:
        print("acceptance_rule: official reference PASS 且 Agent mismatch=0 才算功能通过")


if __name__ == "__main__":
    main()
