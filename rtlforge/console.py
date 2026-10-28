"""Interactive local RTL workbench, independent of benchmark/demo entrypoints."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = ROOT / "work" / "console_settings.json"


@dataclass
class Settings:
    candidates: int = 2
    max_repairs: int = 3
    memory_mode: str = "read_write"
    env_file: str = ".env"
    docker_image: str = "rtlforge-sandbox:latest"
    max_output_tokens: int | None = None
    synthesis: dict | None = None
    vivado_mcp_server: str = ""
    vivado_path: str = ""


def file_path(value: str) -> Path:
    return Path(value.strip().strip('"').strip("'")).expanduser().resolve()


def read_requirement() -> str:
    print("\n请输入 RTL 需求：可粘贴 txt 路径；文本可多行，单独输入 /run 开始。")
    print("输入 /back 返回菜单。")
    first = input("> ").strip()
    if first == "/back":
        return ""
    candidate = file_path(first) if first else None
    if candidate is not None and candidate.is_file():
        if candidate.suffix.lower() != ".txt":
            raise ValueError("需求文件必须是 txt 文件")
        text = candidate.read_text(encoding="utf-8-sig").strip()
        print(f"已读取需求文件：{candidate}")
    else:
        if first.lower().strip('"').endswith(".txt"):
            raise ValueError("找不到需求文件，请检查路径")
        lines = [first]
        while (line := input("... ")).strip() != "/run":
            if line.strip() == "/back":
                return ""
            lines.append(line)
        text = "\n".join(lines).strip()
    if not text:
        raise ValueError("RTL 需求不能为空")
    return text


def load_settings() -> Settings:
    if SETTINGS_PATH.exists():
        return Settings(**json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
    return Settings()


def save_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def edit_settings(settings: Settings) -> None:
    profiles = sorted(path.name for path in ROOT.glob(".env*")
                      if path.is_file() and path.name != ".env.example")
    print("本机配置文件：" + ("、".join(profiles) or "尚无配置文件"))
    for label, name, choices in (
        ("候选上限", "candidates", (1, 2, 3)),
        ("修复上限", "max_repairs", (0, 1, 2, 3)),
        ("Memory", "memory_mode", ("off", "read_only", "read_write")),
    ):
        while True:
            raw = input(f"{label} {choices} [{getattr(settings, name)}]：").strip()
            if not raw:
                break
            value = int(raw) if raw.isdecimal() else raw
            if value in choices:
                setattr(settings, name, value)
                break
            print("请输入列出的值。")
    for label, name in (("模型配置文件", "env_file"), ("Docker 镜像", "docker_image")):
        raw = input(f"{label} [{getattr(settings, name)}]：").strip()
        if raw:
            setattr(settings, name, raw.strip('"'))
    while True:
        raw = input(
            f"单次输出上限 [{settings.max_output_tokens or 'env/default=32768'}] "
            "（Enter 保留，env 恢复配置文件）："
        ).strip()
        if not raw:
            break
        if raw == "env" or (raw.isdecimal() and int(raw) > 0):
            settings.max_output_tokens = None if raw == "env" else int(raw)
            break
        print("请输入正整数或 env。")
    enabled = input(
        f"Vivado MCP 综合 [{'Y' if settings.synthesis is not None else 'N'}] (Y/N)："
    ).strip().lower()
    if enabled == "n":
        settings.synthesis = None
    elif enabled == "y" or settings.synthesis is not None:
        settings.synthesis = synthesis_form(settings.synthesis or {}, optional=True)
        for label, name in (("Ross MCP Server exe 路径", "vivado_mcp_server"),
                            ("Vivado 可执行文件路径", "vivado_path")):
            value = input(f"{label} [{getattr(settings, name)}]：").strip()
            if value:
                setattr(settings, name, str(file_path(value)))
    save_json(SETTINGS_PATH, asdict(settings))


def synthesis_form(previous: dict, *, optional=False) -> dict:
    from rtlforge.domain.synthesis import SynthesisConstraints

    values = dict(previous)
    print("综合设置：留空保留；新任务未指定的器件/周期可由 Planner 提取，仍缺少时再补问。")
    for key, label in (("part", "FPGA 器件"), ("clock_port", "时钟端口"),
                       ("clock_period_ns", "时钟周期 ns"), ("xdc_path", "XDC 文件路径")):
        raw = input(f"{label} [{values.get(key) or '待定'}]：").strip()
        if raw:
            values[key] = float(raw) if key == "clock_period_ns" else (
                str(file_path(raw)) if key == "xdc_path" else raw
            )
    raw = input("资源上限 JSON（如 {\"lut\":1000}；Enter 保留，无则不设）：").strip()
    if raw:
        values["resource_limits"] = json.loads(raw)
    checked = SynthesisConstraints.from_dict(values)
    if not optional and checked.missing():
        # Top is filled from the frozen SpecIR, so do not request it in this form.
        questions = [q for q in checked.missing() if "top" not in q]
        if questions:
            raise ValueError("；".join(questions))
    return checked.to_dict()


def confirm(settings: Settings, tb: Path | None, *, uses_model=True) -> bool:
    while True:
        print("\n运行配置：")
        for key, value in asdict(settings).items():
            if key == "max_output_tokens" and value is None:
                value = "使用 env 配置；未配置时默认 32768"
            print(f"  {key}: {value}")
        print(f"  TB: {tb or 'TB Agent 生成（未经独立验证）'}")
        label = "消耗模型额度" if uses_model else "不调用 LLM，重新检查并综合现有 RTL"
        choice = input(f"[Enter/Y] 确认运行（{label}）  [E] 修改设置  [N] 返回：").lower()
        if choice in {"", "y"}:
            return True
        if choice == "n":
            return False
        if choice == "e":
            edit_settings(settings)


def run_requirement(
    spec: str, settings: Settings, tb: Path | None,
    continuation: tuple[Path, str] | None = None,
    modules: list[dict] | None = None,
) -> None:
    from dotenv import dotenv_values

    from rtlforge.agents import ArtifactRepository, RealDebugger, RealGenerator, RealPlanner
    from rtlforge.agents.testbench import RealTestbenchGenerator
    from rtlforge.domain import Budget, TaskStage
    from rtlforge.domain.synthesis import SynthesisConstraints
    from rtlforge.memory import DEFAULT_MEMORY_DATABASE, SQLiteMemoryStore
    from rtlforge.orchestration.graph import LangGraphOrchestrator
    from rtlforge.orchestration.storage import SQLiteTaskStore
    from rtlforge.providers import OpenAIResponsesProvider, ProviderSettings
    from rtlforge.providers.interactive import InteractiveProvider
    from rtlforge.services import (
        DockerEDARunner,
        GoldenTestbenchVerificationService,
        StaticCandidateEvaluator,
    )

    env_path = file_path(settings.env_file)
    if not env_path.is_file():
        raise ValueError(f"找不到模型配置文件：{env_path}")
    environment = dict(os.environ)
    # Explicit profile selection must not retain a previous profile's key/model.
    environment.update({k: v for k, v in dotenv_values(env_path).items() if v is not None})
    provider_settings = ProviderSettings.from_env(environment)
    if settings.max_output_tokens is not None:
        provider_settings = replace(provider_settings, max_output_tokens=settings.max_output_tokens)
    run_root = ROOT / "runs" / "console" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run_root.mkdir(parents=True)
    provider = InteractiveProvider(OpenAIResponsesProvider(provider_settings), run_root)
    if tb:
        source = tb
        tb = run_root / "provided_tb.sv"
        tb.write_bytes(source.read_bytes())
    save_json(run_root / "run_config.json", {
        **asdict(settings), "provided_tb_source": str(source) if tb else None,
        "model": provider_settings.model,
        "effective_max_output_tokens": provider_settings.max_output_tokens,
    })
    runner = DockerEDARunner(
        run_root, image=settings.docker_image,
        readonly_roots=((run_root,) if tb else ()),
    )
    memory = (SQLiteMemoryStore(DEFAULT_MEMORY_DATABASE, toolchain_version=settings.docker_image)
              if settings.memory_mode != "off" else None)
    orchestrator = LangGraphOrchestrator(
        planner=RealPlanner(provider),
        generator=RealGenerator(provider, artifacts=ArtifactRepository(run_root)),
        debugger=RealDebugger(provider, artifacts=ArtifactRepository(run_root)),
        testbench_agent=RealTestbenchGenerator(provider),
        evaluator=StaticCandidateEvaluator(runner, run_root),
        verifier=(GoldenTestbenchVerificationService(
            runner, run_root, lambda _: tb, testbench_source="user_provided",
        ) if tb else None),
        generated_tb_runner=runner, has_golden_testbench=tb is not None,
        skip_generated_testbench=tb is not None, runs_root=run_root,
        store=SQLiteTaskStore(run_root / "tasks.sqlite"),
        memory_store=memory, memory_mode=settings.memory_mode,
        synthesis_runner=(make_synthesis_runner(settings, run_root)
                          if settings.synthesis is not None else None),
    )
    budget = Budget(
        max_candidates=settings.candidates, max_repairs=settings.max_repairs,
    )
    if continuation is not None:
        from rtlforge.continuation import read_source, seed_continuation

        try:
            source_path, prior = read_source(continuation[0])
            context = seed_continuation(orchestrator, source_path, prior, continuation[1], budget)
        except BaseException:
            orchestrator.close()
            raise
        print(f"从 {prior.task_id}/{continuation[1]} 续跑；本轮修复预算 {budget.max_repairs}。")
    else:
        context = orchestrator.create_task(
            spec, budget=budget,
            synthesis_constraints=(SynthesisConstraints.from_dict(settings.synthesis)
                                   if settings.synthesis is not None else None),
        )
    if continuation is not None:
        context.synthesis_constraints = (SynthesisConstraints.from_dict(settings.synthesis)
                                         if settings.synthesis is not None else None)
    task_root = run_root / context.task_id
    task_root.mkdir(exist_ok=True)
    if modules:
        from rtlforge.integration import attach_modules

        attach_modules(context, task_root, modules)
    (task_root / "input.txt").write_text(spec + "\n", encoding="utf-8")
    context.metadata["model_calls_path"] = str(task_root / "model_calls.jsonl")
    orchestrator.store.save_checkpoint(context)
    print(f"\n模型：{provider_settings.model}\n输出上限：{provider_settings.max_output_tokens}")
    print(f"任务：{context.task_id}\n证据目录：{run_root}")
    seen_events = 0
    seen_files: dict[Path, int] = {}
    stops = ["planner", "testbench", "coder", "static_evaluator", "static_policy",
             "advisory_verifier", "verifier", "synthesizer", "router", "debug_decision",
             "debug_tool", "debugger"]

    def report() -> None:
        nonlocal seen_events
        save_json(task_root / "context.json", context.to_dict())
        if context.metadata.get("planner_validation_failures"):
            save_json(task_root / "planner_validation_failures.json",
                      context.metadata["planner_validation_failures"])
        if context.spec_ir:
            save_json(task_root / "spec_ir.json", context.spec_ir.to_dict())
        if context.task_tree_root:
            save_json(task_root / "task_tree.json", context.task_tree_root.to_dict())
        if context.synthesis_constraints is not None:
            save_json(task_root / "synthesis_constraints.json",
                      context.synthesis_constraints.to_dict())
        events = orchestrator.store.list_events(context.task_id)
        save_json(task_root / "events.json", [event.to_dict() for event in events])
        for event in events[seen_events:]:
            print(f"[{event.event_type.value}] " + json.dumps(event.payload, ensure_ascii=False))
        seen_events = len(events)
        for path in sorted(task_root.rglob("*")):
            if path.is_file() and path.suffix in {".v", ".sv", ".json"}:
                if path.name in {"context.json", "events.json"}:
                    continue
                stamp = path.stat().st_mtime_ns
                if seen_files.get(path) != stamp:
                    print(f"  输出：{path}")
                    if path.parent.name == "tool_runs":
                        result = json.loads(path.read_text(encoding="utf-8"))
                        print("    " + json.dumps({
                            key: result[key] for key in ("action", "tool", "status", "category")
                            if key in result
                        }, ensure_ascii=False))
                    seen_files[path] = stamp
        print(f"当前阶段：{context.current_stage.value}", flush=True)

    try:
        while True:
            print("\n正在执行下一阶段，请等待……", flush=True)
            context = orchestrator.run_task(context.task_id, interrupt_after=stops)
            report()
            if context.current_stage.is_terminal:
                break
            if context.current_stage is TaskStage.WAITING_FOR_USER:
                if context.metadata.get("pause_reason") == "generated_tb_review":
                    print("\n生成测试通过，未经独立验证。结果与证据已保存。")
                    break
                print(context.metadata.get("clarification_question") or "请补充需求：")
                for question in context.metadata.get("pending_clarifications", []):
                    print(question)
                if context.metadata.get("pause_reason") == "synthesis_constraints":
                    answer = json.dumps(synthesis_form(context.synthesis_constraints.to_dict()))
                else:
                    answer = input("请输入补充说明（/pause 保留暂停状态）：").strip()
                if answer == "/pause":
                    break
                if not answer:
                    continue
                # A stage breakpoint can precede LangGraph's actual human interrupt.
                orchestrator.run_task(context.task_id)
                context = orchestrator.resume_task(
                    context.task_id, {"action": "answer", "message": answer},
                    interrupt_after=stops,
                )
                report()
                if context.current_stage.is_terminal:
                    break
        if context.current_stage is TaskStage.FAILED:
            print(f"\n运行失败：{context.failure_category}，请查看上述证据。")
        elif context.metadata.get("golden_tb_pass"):
            print("\n已通过用户提供的 TB。验证范围以该 TB 为准。")
        if context.selected_candidate_id and context.candidate().metadata.get("synthesis_result"):
            synth = context.candidate().metadata["synthesis_result"]
            print(f"Vivado 综合验收：{synth['status']}；时序：{synth['timing_status']}；"
                  f"资源：{synth['resources']}；WNS：{synth['wns_ns']} ns")
    finally:
        report()
        orchestrator.close()
        print(f"\n本次证据：{task_root}")


def make_synthesis_runner(settings: Settings, run_root: Path):
    from rtlforge.services.vivado_mcp import VivadoMCPRunner

    if not settings.vivado_mcp_server or not settings.vivado_path:
        raise ValueError("启用综合需要在设置中提供 Ross MCP Server 和 Vivado 路径")
    return VivadoMCPRunner(file_path(settings.vivado_mcp_server),
                           file_path(settings.vivado_path), run_root)


def continue_prompt(settings: Settings, *, synthesis_only=False) -> None:
    from rtlforge.continuation import read_source

    recent = sorted(
        (ROOT / "runs" / "console").glob("*/task_*/context.json"),
        key=lambda path: path.stat().st_mtime_ns, reverse=True,
    )[:10]
    for number, path in enumerate(recent, 1):
        print(f"[{number}] {path.parent}")
    raw = input("选择任务编号，或输入任务目录/context.json/候选 RTL 路径：").strip()
    if raw.isdecimal() and 1 <= int(raw) <= len(recent):
        path = recent[int(raw) - 1]
    else:
        path = file_path(raw)
    source_path, source = read_source(path)
    candidates = [c for c in source.candidates if Path(c.artifact_path).is_file()]
    if not candidates:
        raise ValueError("历史任务没有完整的候选 RTL，无法从候选续跑")
    for candidate in candidates:
        print(f"  {candidate.candidate_id}: {candidate.artifact_path}")
    default = next(
        (c.candidate_id for c in candidates if Path(c.artifact_path).resolve() == path.resolve()),
        candidates[-1].candidate_id,
    )
    candidate_id = input(f"续跑候选 [{default}]：").strip() or default
    source.candidate(candidate_id)
    prior_tb = source_path.parent.parent / "provided_tb.sv"
    print("续跑复用需求合同与候选，重新静态检查/仿真；不调用 Planner/Generator。")
    tb_hint = '复用 ' + str(prior_tb) if prior_tb.is_file() else '复用历史 Agent TB'
    raw_tb = input(f"TB 路径（Enter={tb_hint}）：").strip()
    tb = file_path(raw_tb) if raw_tb else (prior_tb if prior_tb.is_file() else None)
    if tb and not tb.is_file():
        raise ValueError(f"找不到 TB：{tb}")
    if synthesis_only:
        from rtlforge.domain.synthesis import SynthesisConstraints
        from rtlforge.synthesize import synthesize_existing

        if settings.synthesis is None:
            settings.synthesis = synthesis_form({}, optional=True)
        for label, name in (("Ross MCP Server exe", "vivado_mcp_server"),
                            ("Vivado 可执行文件", "vivado_path")):
            if not getattr(settings, name):
                setattr(settings, name, str(file_path(input(f"{label} 路径："))))
        if confirm(settings, tb, uses_model=False):
            output = ROOT / "runs" / "synthesis" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            summary = synthesize_existing(
                source_path, candidate_id, SynthesisConstraints.from_dict(settings.synthesis),
                file_path(settings.vivado_mcp_server), file_path(settings.vivado_path),
                output=output, testbench=tb, image=settings.docker_image,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            print(f"本次综合证据：{output}")
        return
    branch_settings = replace(settings, candidates=1)
    if confirm(branch_settings, tb):
        run_requirement(source.raw_spec, branch_settings, tb, (source_path, candidate_id))


def select_module() -> dict:
    from rtlforge.continuation import read_source
    from rtlforge.integration import load_module

    recent = sorted(
        (ROOT / "runs" / "console").glob("*/task_*/context.json"),
        key=lambda path: path.stat().st_mtime_ns, reverse=True,
    )[:20]
    for number, path in enumerate(recent, 1):
        print(f"[{number}] {path.parent}")
    raw = input("选择已有模块任务编号，或输入任务/context.json/候选 RTL 路径：").strip()
    path = recent[int(raw) - 1] if raw.isdecimal() and 1 <= int(raw) <= len(recent) else (
        file_path(raw)
    )
    source_path, context = read_source(path)
    for candidate in context.candidates:
        report = candidate.metadata.get("static_evaluation", {})
        print(f"  {candidate.candidate_id}: 静态分数 {report.get('quality_score')}；"
              f"{candidate.artifact_path}")
    default = next((c.candidate_id for c in context.candidates
                    if Path(c.artifact_path).resolve() == path.resolve()),
                   context.selected_candidate_id)
    candidate_id = input(f"选择候选 [{default}]：").strip() or default
    return load_module(source_path, candidate_id)


def integration_prompt(settings: Settings) -> None:
    from rtlforge.integration import load_module_list

    spec = read_requirement()
    if not spec:
        return
    raw_tb = input("请输入本次顶层的独立 TB 文件路径（必填）：").strip()
    if not raw_tb:
        raise ValueError("集成入口需要独立 TB 文件")
    tb = file_path(raw_tb)
    if not tb.is_file():
        raise ValueError(f"找不到 TB 文件：{tb}")
    raw = input("已有模块清单 JSON 路径（Enter 逐个选择已有候选）：").strip()
    if raw:
        modules = load_module_list(file_path(raw))
    else:
        modules = [select_module()]
        while input("继续添加模块？[Y/N，默认 N]：").strip().lower() == "y":
            modules.append(select_module())
    names = [module["module_name"] for module in modules]
    if len(names) != len(set(names)):
        raise ValueError("重复模块名：请选择互不重名的已有模块")
    print("\n本次冻结模块（仅生成顶层，独立 TB 不交给 Agent）：")
    for module in modules:
        print(f"  {module['module_name']} / {module['candidate_id']}")
        print(f"    来源：{module['source_context']}\n    RTL SHA256：{module['rtl_sha256']}")
        contract = module["spec_ir"]
        print("    接口：" + "; ".join(
            f"{p['direction']} {p['name']}[{p['width']}]" for p in contract["ports"]
        ))
        print(f"    时钟：{contract['clock']} / {contract.get('clock_edge')}；"
              f"复位：{contract['reset']} / {contract.get('reset_kind')} / "
              f"{contract.get('reset_polarity')}")
        print("    TaskTree：" + str(module["task_tree"].get("title", "")))
        print("    完整 SpecIR/TaskTree 将复制到新任务 dependencies/模块名/ 下。")
    if confirm(settings, tb):
        save_json(SETTINGS_PATH, asdict(settings))
        run_requirement(spec, settings, tb, modules=modules)


def main() -> None:
    os.chdir(ROOT)
    settings = load_settings()
    while True:
        print("\nRTLForge — RTL 生成与验证\n[1] 输入需求并运行  [2] 修改设置")
        print("[3] 从已有候选续跑（追加修复预算）  [0] 退出")
        print("[4] 综合已有候选（不调用 LLM）")
        print("[5] 复用已有模块生成顶层（通用集成）")
        try:
            choice = input("请选择 [1]：").strip()
            if choice == "0":
                return
            if choice == "2":
                edit_settings(settings)
                continue
            if choice == "3":
                continue_prompt(settings)
                continue
            if choice == "4":
                continue_prompt(settings, synthesis_only=True)
                continue
            if choice == "5":
                integration_prompt(settings)
                continue
            if choice not in {"", "1"}:
                continue
            spec = read_requirement()
            if not spec:
                continue
            raw_tb = input("请输入 TB 文件路径（留空则由 TB Agent 生成）：").strip()
            tb = file_path(raw_tb) if raw_tb else None
            if tb and not tb.is_file():
                raise ValueError(f"找不到 TB 文件：{tb}")
            if confirm(settings, tb):
                save_json(SETTINGS_PATH, asdict(settings))
                run_requirement(spec, settings, tb)
        except (EOFError, KeyboardInterrupt):
            print("\n已退出；已产生的任务证据保留。")
            return
        except Exception as error:
            # Provider exceptions can include sensitive request contents.
            if isinstance(error, (ValueError, FileNotFoundError, ImportError)):
                print(f"无法运行：{error}")
            else:
                print(f"运行中断：{type(error).__name__}。请查看任务目录中的证据。")


if __name__ == "__main__":
    main()
