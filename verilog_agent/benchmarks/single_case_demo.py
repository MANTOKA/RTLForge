from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from dotenv import load_dotenv

from verilog_agent.agents.debugger import RealDebugger
from verilog_agent.agents.generator import ArtifactRepository
from verilog_agent.agents.showcase import PlannerGeneratorShowcase, ShowcaseRunError
from verilog_agent.benchmarks.preflight import preflight_references
from verilog_agent.benchmarks.rtllm import (
    adapt_rtllm_reference,
    load_rtllm,
    rtllm_run_root,
)
from verilog_agent.benchmarks.verilogeval import human_v2_run_root, load_human_v2
from verilog_agent.memory import SQLiteMemoryStore
from verilog_agent.providers import (
    OpenAIResponsesProvider,
    ProviderConfigurationError,
    ProviderSettings,
)
from verilog_agent.providers.usage import sampling_markdown
from verilog_agent.services import (
    DockerEDARunner,
    GoldenTestbenchVerificationService,
    StaticCandidateEvaluator,
)
from verilog_agent.testing import ScriptedProvider
from verilog_agent.testing.examples import (
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
    parser.add_argument("--database", type=Path)
    parser.add_argument("--task-id", help=argparse.SUPPRESS)
    parser.add_argument("--runs-root", type=Path, default=Path("runs"))
    parser.add_argument(
        "--memory-mode",
        choices=("off", "read_only", "read_write"),
        default="off",
        help="Retrieve/write verification-gated repair memories; defaults to off",
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
        help="Override VERILOG_AGENT_MODEL for this run; applies to all three LLM roles",
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
        default="verilog-agent-sandbox:latest",
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
    if args.max_repairs and args.testbench is None:
        parser.error("--max-repairs requires a golden --testbench or Human v2 case")
    if args.memory_mode != "off" and not args.max_repairs:
        parser.error("enabled Memory requires --max-repairs")

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
        raw_spec = raw_spec or _prompt_for_spec(parser)
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
        args.memory_database = (
            args.memory_database or group_root / "state" / "verilog_agent_memory.sqlite"
        )
    else:
        args.database = args.database or Path("work/showcase.sqlite")
        args.memory_database = args.memory_database or Path("work/verilog_agent_memory.sqlite")

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
            task_metadata=(
                {
                    "benchmark_problem_id": benchmark_case.problem_id,
                    "required_module_name": getattr(
                        benchmark_case, "module_name", "TopModule"
                    ),
                    "benchmark_dataset": dataset_label,
                }
                if benchmark_case
                else None
            ),
        )
    except ShowcaseRunError as error:
        parser.exit(1, f"showcase error: {error}\n")
    if reference_baseline is not None:
        _print_reference_comparison(summary, dataset_label=str(dataset_label))


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
