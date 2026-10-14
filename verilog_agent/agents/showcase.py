from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from time import perf_counter
from typing import NoReturn

from verilog_agent.agents.generator import ArtifactRepository, RealGenerator
from verilog_agent.agents.planner import RealPlanner
from verilog_agent.agents.testbench import RealTestbenchGenerator
from verilog_agent.domain import Budget, RTLTaskContext, TaskStage, TaskTreeNode
from verilog_agent.memory import SQLiteMemoryStore
from verilog_agent.orchestration import Orchestrator, SQLiteTaskStore
from verilog_agent.orchestration.interfaces import Debugger
from verilog_agent.providers import StructuredModelProvider
from verilog_agent.providers.usage import prompt_source_version, sampling_markdown, summarize_calls
from verilog_agent.services import (
    GoldenTestbenchVerificationService,
    StaticCandidateEvaluator,
)


class ShowcaseRunError(RuntimeError):
    def __init__(self, message: str, *, task_id: str | None = None, calls=None) -> None:
        super().__init__(message)
        self.task_id = task_id
        self.model_calls = list(calls or [])


class _StopAfterGeneration:
    """Fail closed if the showcase accidentally enters a later Phase 2 service."""

    def evaluate(self, *_args: object) -> NoReturn:
        raise ShowcaseRunError("Candidate Evaluation is not part of this showcase")

    def verify(self, *_args: object) -> NoReturn:
        raise ShowcaseRunError("EDA Verification is not part of this showcase")

    def repair(self, *_args: object) -> NoReturn:
        raise ShowcaseRunError("Debugger is not part of this showcase")


class PlannerGeneratorShowcase:
    """Run only through generation and expose every intermediate artifact."""

    def __init__(
        self,
        *,
        provider: StructuredModelProvider,
        provider_label: str,
        database: Path,
        runs_root: Path,
        evaluator: StaticCandidateEvaluator | None = None,
        verifier: GoldenTestbenchVerificationService | None = None,
        debugger: Debugger | None = None,
        memory_store: SQLiteMemoryStore | None = None,
        memory_mode: str = "off",
        orchestrator_kind: str = "legacy",
        emit: Callable[[str], None] = print,
    ) -> None:
        self.provider = provider
        self.provider_label = provider_label
        self.database = database
        self.runs_root = runs_root
        self.evaluator = evaluator
        self.verifier = verifier
        self.debugger = debugger
        self.memory_store = memory_store
        self.memory_mode = memory_mode
        if orchestrator_kind not in {"legacy", "langgraph"}:
            raise ValueError("orchestrator_kind must be legacy or langgraph")
        self.orchestrator_kind = orchestrator_kind
        self.emit = emit

    def run(
        self,
        raw_spec: str,
        *,
        candidate_count: int = 2,
        max_repairs: int = 0,
        task_metadata: dict | None = None,
        task_id: str | None = None,
    ) -> dict:
        if self.orchestrator_kind == "langgraph":
            return self._run_graph(
                raw_spec,
                candidate_count=candidate_count,
                max_repairs=max_repairs,
                task_metadata=task_metadata,
                task_id=task_id,
            )
        store = SQLiteTaskStore(self.database)
        stop_after_generation = _StopAfterGeneration()
        orchestrator = Orchestrator(
            planner=RealPlanner(self.provider),
            generator=RealGenerator(
                self.provider, artifacts=ArtifactRepository(self.runs_root)
            ),
            debugger=self.debugger or stop_after_generation,
            evaluator=self.evaluator or stop_after_generation,
            verifier=self.verifier or stop_after_generation,
            store=store,
            memory_store=self.memory_store,
            memory_mode=self.memory_mode,
        )
        context = orchestrator.create_task(
            raw_spec,
            budget=Budget(max_candidates=candidate_count, max_repairs=max_repairs),
            metadata={
                **(task_metadata or {}),
                "experiment_tags": [
                    *(task_metadata or {}).get("experiment_tags", []),
                    "orchestrator=legacy_orchestrator",
                ],
            },
            task_id=task_id,
        )
        task_root = self.runs_root / context.task_id
        task_root.mkdir(parents=True, exist_ok=True)
        context.metadata["model_calls_path"] = str(task_root / "model_calls.jsonl")
        (task_root / "input.txt").write_text(raw_spec + "\n", encoding="utf-8", newline="\n")

        self._heading("VerilogAgent Planner / Generator 可观察演示")
        self.emit(f"Provider: {self.provider_label}")
        settings = getattr(self.provider, "settings", None)
        sampling = (settings.sampling_configuration()
                    if hasattr(settings, "sampling_configuration") else {})
        self.emit(sampling_markdown(sampling))
        self.emit(f"Task ID: {context.task_id}")
        if self.evaluator is None:
            self.emit("verified_rtl: false（此演示有意停在候选生成后）")
        elif self.verifier is not None:
            self.emit("verified_rtl: false（初始状态；本次将继续执行静态门禁与功能仿真）")
        else:
            self.emit("verified_rtl: false（本次只完成静态 EDA 门禁，尚未功能仿真）")
        self._heading("阶段 0：用户自然语言需求")
        self.emit(raw_spec)

        orchestrator.advance_task(context.task_id)
        self._expect_stage(context, TaskStage.SPECIFYING, store, "进入 Planner")

        self._heading("阶段 1：Planner 正在生成 SpecIR 与 Task Tree")
        started = perf_counter()
        orchestrator.advance_task(context.task_id)
        planner_ms = round((perf_counter() - started) * 1000)
        self._expect_stage(context, TaskStage.PLANNED, store, "Planner")
        assert context.spec_ir is not None
        assert context.task_tree_root is not None
        self._write_json(task_root / "spec_ir.json", context.spec_ir.to_dict())
        self._write_json(task_root / "task_tree.json", context.task_tree_root.to_dict())
        self._render_spec(context)
        self.emit("Task Tree:")
        self._render_tree(context.task_tree_root)
        self.emit(f"Planner elapsed: {planner_ms} ms")

        orchestrator.advance_task(context.task_id)
        self._expect_stage(context, TaskStage.GENERATING, store, "进入 Generator")

        self._heading(f"阶段 2：Generator 先生成 1 个候选，独立候选总上限 {candidate_count}")
        started = perf_counter()
        orchestrator.advance_task(context.task_id)
        generator_ms = round((perf_counter() - started) * 1000)
        self._expect_stage(context, TaskStage.EVALUATING, store, "Generator")
        self._render_candidates(context)
        self.emit(f"Generator elapsed: {generator_ms} ms")

        static_evaluation: dict | None = None
        verification: dict | None = None
        verification_ms: int | None = None
        repair_ms: int | None = None
        evaluator_ms: int | None = None
        if self.evaluator is not None:
            self._heading("阶段 3：Candidate Evaluation 正在执行 Docker 静态门禁")
            started = perf_counter()
            orchestrator.advance_task(context.task_id)
            while context.current_stage in {TaskStage.GENERATING, TaskStage.EVALUATING}:
                orchestrator.advance_task(context.task_id)
            evaluator_ms = round((perf_counter() - started) * 1000)
            if context.current_stage not in {TaskStage.VERIFYING, TaskStage.CLASSIFYING}:
                self._expect_stage(context, TaskStage.VERIFYING, store, "Candidate Evaluation")
            selected = context.candidate()
            static_evaluation = dict(selected.metadata.get("static_evaluation", {}))
            self._render_static_evaluation(context)
            self.emit(f"selected_candidate: {selected.candidate_id}")
            self.emit(f"selected_artifact: {selected.artifact_path}")
            self.emit(f"Candidate Evaluation elapsed: {evaluator_ms} ms")
            if context.current_stage is TaskStage.VERIFYING:
                self.emit("静态门禁已通过；尚未执行 testbench 仿真。")
            else:
                self.emit(
                    f"静态门禁失败；类别为 {context.failure_category}，进入 Reflection Router。"
                )

        if self.verifier is not None and context.current_stage is TaskStage.VERIFYING:
            self._heading("阶段 4：Verification 正在执行 golden testbench 仿真")
            started = perf_counter()
            orchestrator.advance_task(context.task_id)
            verification_ms = round((perf_counter() - started) * 1000)
            if context.current_stage not in {TaskStage.COMPLETED, TaskStage.CLASSIFYING}:
                self._expect_stage(context, TaskStage.COMPLETED, store, "Verification")
            report_path = context.metadata.get("verification_report")
            if report_path:
                verification = json.loads(Path(report_path).read_text(encoding="utf-8"))
                self._render_verification(verification)
            self.emit(f"Verification elapsed: {verification_ms} ms")
            if context.current_stage is TaskStage.COMPLETED:
                self.emit(f"verified_rtl: true，最终候选为 {context.selected_candidate_id}")
            else:
                self.emit(
                    f"verified_rtl: false，失败类别为 {context.failure_category}；"
                    "下一步才会稀疏触发 Debugger。"
                )

        if context.current_stage is TaskStage.CLASSIFYING and self.debugger is not None:
            self._heading("阶段 5：Adaptive Reflection Router / Debugger 修复闭环")
            started = perf_counter()
            orchestrator.run_task(context.task_id)
            repair_ms = round((perf_counter() - started) * 1000)
            decision = context.metadata.get("reflection_decision", {})
            if decision:
                self.emit(f"reflection_policy: {decision.get('policy')}")
                self.emit(f"reflection_action: {decision.get('action')}")
            for candidate in context.candidates:
                if candidate.parent_id is not None:
                    self.emit(
                        f"repair: {candidate.candidate_id} <- {candidate.parent_id}; "
                        f"diff={candidate.metadata.get('diff_path', '(none)')}"
                    )
            self.emit(f"Repair loop elapsed: {repair_ms} ms")
            if context.current_stage is TaskStage.COMPLETED:
                self.emit(f"verified_rtl: true，修复后候选为 {context.selected_candidate_id}")
            else:
                self.emit(
                    f"verified_rtl: false，闭环停止于 {context.current_stage.value}；"
                    f"类别为 {context.failure_category}"
                )
            report_path = context.metadata.get("verification_report")
            if report_path:
                verification = json.loads(Path(report_path).read_text(encoding="utf-8"))
                self._render_verification(verification)

        if self.evaluator is not None and context.selected_candidate_id is not None:
            static_evaluation = dict(context.candidate().metadata.get("static_evaluation", {}))
        events = store.list_events(context.task_id)
        self._heading("阶段 6：Orchestrator 事件轨迹")
        for event in events:
            self.emit(
                f"{event.sequence:02d}. {event.event_type.value} "
                f"[stage={event.stage.value}]"
            )

        summary = {
            "provider": self.provider_label,
            "sampling": sampling,
            "prompt_version": prompt_source_version(),
            "model_calls": context.metadata.get("model_calls", []),
            "token_usage": summarize_calls(context.metadata.get("model_calls", [])),
            "task_id": context.task_id,
            "input": raw_spec,
            "current_stage": context.current_stage.value,
            "next_stage": self._next_stage(context),
            "verified_rtl": context.current_stage is TaskStage.COMPLETED,
            "static_gate_passed": self.evaluator is not None,
            "timings_ms": {
                "planner": planner_ms,
                "generator": generator_ms,
                "candidate_evaluation": evaluator_ms,
                "verification": verification_ms,
                "repair_loop": repair_ms,
            },
            "spec_ir": context.spec_ir.to_dict(),
            "task_tree": context.task_tree_root.to_dict(),
            "candidates": [candidate.to_dict() for candidate in context.candidates],
            "selected_candidate_id": context.selected_candidate_id,
            "candidate_policy": context.metadata.get("candidate_policy"),
            "candidate_regenerations": context.metadata.get("candidate_regenerations", []),
            "budget": context.budget.to_dict(),
            "selected_static_evaluation": static_evaluation,
            "verification": verification,
            "repair_attempts": context.metadata.get("repair_attempts", []),
            "repair_fallback": context.metadata.get("repair_fallback"),
            "last_repair_analysis": context.metadata.get("last_repair_analysis"),
            "memory": {
                "mode": self.memory_mode,
                "retrieved": context.metadata.get("retrieved_memories", []),
                "consolidated": context.metadata.get("memory_consolidations", []),
            },
            "events": [event.to_dict() for event in events],
        }
        summary_path = task_root / "showcase_summary.json"
        self._write_json(summary_path, summary)
        self._heading("演示结束：产物位置")
        self.emit(f"输入需求: {(task_root / 'input.txt').as_posix()}")
        self.emit(f"SpecIR: {(task_root / 'spec_ir.json').as_posix()}")
        self.emit(f"Task Tree: {(task_root / 'task_tree.json').as_posix()}")
        self.emit(f"完整摘要: {summary_path.as_posix()}")
        if self.evaluator is None:
            self.emit("下一步尚未执行：候选评分、EDA 验证和 Debugger 修复。")
        elif (
            self.verifier is not None
            and context.current_stage is TaskStage.COMPLETED
            and not context.metadata.get("repair_attempts")
        ):
            self.emit("本次候选已通过指定 golden testbench；未触发 Debugger。")
        elif self.verifier is not None and self.debugger is None:
            self.emit("下一步尚未执行：按结构化失败类别调用真实 Debugger。")
        elif self.verifier is not None:
            self.emit("真实 Debugger 闭环已结束；结果与 diff 见完整摘要和候选目录。")
        else:
            self.emit("下一步尚未执行：testbench 仿真、失败分类和 Debugger 修复。")
        return summary

    def _run_graph(
        self,
        raw_spec: str,
        *,
        candidate_count: int,
        max_repairs: int,
        task_metadata: dict | None,
        task_id: str | None,
    ) -> dict:
        from verilog_agent.orchestration.graph import LangGraphOrchestrator

        if self.evaluator is None:
            raise ShowcaseRunError("LangGraph mode requires static evaluation and an EDA runner")
        runner = self.evaluator.runner
        if not hasattr(runner, "run_files"):
            raise ShowcaseRunError("LangGraph mode requires a multi-file EDA runner")
        stop_after_generation = _StopAfterGeneration()
        store = SQLiteTaskStore(self.database)
        orchestrator = LangGraphOrchestrator(
            planner=RealPlanner(self.provider),
            generator=RealGenerator(self.provider, artifacts=ArtifactRepository(self.runs_root)),
            testbench_agent=RealTestbenchGenerator(self.provider),
            debugger=self.debugger or stop_after_generation,
            evaluator=self.evaluator,
            verifier=self.verifier or stop_after_generation,
            generated_tb_runner=runner,
            has_golden_testbench=self.verifier is not None,
            runs_root=self.runs_root,
            store=store,
            memory_store=self.memory_store,
            memory_mode=self.memory_mode,
        )
        context = orchestrator.create_task(
            raw_spec,
            budget=Budget(max_candidates=candidate_count, max_repairs=max_repairs),
            metadata=task_metadata,
            task_id=task_id,
        )
        task_root = self.runs_root / context.task_id
        task_root.mkdir(parents=True, exist_ok=True)
        context.metadata["model_calls_path"] = str(task_root / "model_calls.jsonl")
        (task_root / "input.txt").write_text(raw_spec + "\n", encoding="utf-8", newline="\n")
        context = orchestrator.run_task(context.task_id)
        verification = None
        report_path = context.metadata.get("verification_report")
        if report_path:
            verification = json.loads(Path(report_path).read_text(encoding="utf-8"))
        summary = {
            "task_id": context.task_id,
            "input": raw_spec,
            "orchestrator": "langgraph",
            "experiment_tags": context.metadata.get("experiment_tags", []),
            "current_stage": context.current_stage.value,
            "final_status": context.metadata.get("final_status"),
            "verified_rtl": context.metadata.get("golden_tb_pass") is True,
            "generated_tb_pass": context.metadata.get("generated_tb_pass"),
            "generated_testbench_path": context.metadata.get("generated_testbench_path"),
            "test_objectives": context.metadata.get("generated_test_objectives", []),
            "model_calls": context.metadata.get("model_calls", []),
            "token_usage": summarize_calls(context.metadata.get("model_calls", [])),
            "spec_ir": context.spec_ir.to_dict() if context.spec_ir else None,
            "task_tree": context.task_tree_root.to_dict() if context.task_tree_root else None,
            "candidates": [candidate.to_dict() for candidate in context.candidates],
            "selected_candidate_id": context.selected_candidate_id,
            "candidate_policy": context.metadata.get("candidate_policy"),
            "candidate_regenerations": context.metadata.get("candidate_regenerations", []),
            "budget": context.budget.to_dict(),
            "verification": verification,
            "repair_attempts": context.metadata.get("repair_attempts", []),
            "memory": {
                "mode": self.memory_mode,
                "retrieved": context.metadata.get("retrieved_memories", []),
                "consolidated": context.metadata.get("memory_consolidations", []),
            },
            "events": [event.to_dict() for event in store.list_events(context.task_id)],
        }
        path = task_root / "showcase_summary.json"
        self._write_json(path, summary)
        self.emit(f"LangGraph task: {context.task_id}")
        self.emit(f"final_status: {summary['final_status']}")
        self.emit(f"generated_tb_pass: {summary['generated_tb_pass']}")
        self.emit(f"golden_verified: {summary['verified_rtl']}")
        self.emit(f"summary: {path.as_posix()}")
        return summary

    @staticmethod
    def _next_stage(context: RTLTaskContext) -> str | None:
        if context.current_stage is TaskStage.EVALUATING:
            return "Candidate Evaluation Service"
        if context.current_stage is TaskStage.VERIFYING:
            return "Functional Verification Service"
        if context.current_stage is TaskStage.CLASSIFYING:
            return "Adaptive Reflection Router / Debugger"
        return None

    def _render_spec(self, context: RTLTaskContext) -> None:
        assert context.spec_ir is not None
        spec = context.spec_ir
        self.emit("SpecIR:")
        self.emit(f"  module_name: {spec.module_name}")
        self.emit(f"  clock: {spec.clock or '(none)'}")
        self.emit(f"  reset: {spec.reset or '(none)'}")
        self.emit("  ports:")
        for port in spec.ports:
            self.emit(f"    - {port.direction.value:6s} {port.name} [width={port.width}]")
        self.emit("  assumptions:")
        self._render_items(spec.assumptions)
        self.emit("  acceptance_criteria:")
        self._render_items(spec.acceptance_criteria)

    def _render_tree(self, node: TaskTreeNode, prefix: str = "") -> None:
        self.emit(f"{prefix}- [{node.node_id}] {node.title}")
        self.emit(f"{prefix}  {node.description}")
        for criterion in node.acceptance_criteria:
            self.emit(f"{prefix}  acceptance: {criterion}")
        for child in node.children:
            self._render_tree(child, prefix + "  ")

    def _render_candidates(self, context: RTLTaskContext) -> None:
        for candidate in context.candidates:
            self.emit("")
            self.emit(f"Candidate: {candidate.candidate_id}")
            self.emit(f"  design_pattern: {candidate.design_pattern}")
            self.emit(f"  assumptions: {candidate.assumptions}")
            self.emit(f"  sha256: {candidate.code_hash}")
            self.emit(f"  artifact: {candidate.artifact_path}")
            rtl = Path(candidate.artifact_path).read_text(encoding="utf-8")
            self.emit(f"----- {candidate.candidate_id} RTL BEGIN -----")
            self.emit(rtl.rstrip())
            self.emit(f"----- {candidate.candidate_id} RTL END -----")

    def _render_static_evaluation(self, context: RTLTaskContext) -> None:
        for candidate in context.candidates:
            report = candidate.metadata.get("static_evaluation", {})
            self.emit("")
            self.emit(f"Candidate: {candidate.candidate_id}")
            self.emit(f"  static_accepted: {report.get('accepted', False)}")
            self.emit(f"  quality_score: {report.get('quality_score', 0)}")
            if report.get("reason"):
                self.emit(f"  reason: {report['reason']}")
            for action, result in report.get("results", {}).items():
                self.emit(
                    f"  {action}: {result.get('status')} "
                    f"[{result.get('tool')}, {result.get('duration_ms')} ms, "
                    f"{result.get('category')}]"
                )

    def _render_verification(self, report: dict) -> None:
        self.emit(f"verification_passed: {report.get('passed', False)}")
        self.emit(f"verification_category: {report.get('category', 'UNKNOWN')}")
        self.emit(f"confidence: {report.get('testbench', {}).get('confidence', 'UNKNOWN')}")
        for attempt in report.get("attempts", []):
            mismatch = attempt.get("debugger_feedback", {}).get("mismatch_summary", {})
            mismatch_text = ""
            if mismatch.get("total_mismatched_samples") is not None:
                mismatch_text = (
                    f", mismatches={mismatch['total_mismatched_samples']}/"
                    f"{mismatch.get('total_samples')}"
                )
            self.emit(
                f"  {attempt.get('candidate_id')}: {attempt.get('status')} "
                f"[{attempt.get('category')}, {attempt.get('duration_ms', 0)} ms"
                f"{mismatch_text}]"
            )

    def _expect_stage(
        self,
        context: RTLTaskContext,
        expected: TaskStage,
        store: SQLiteTaskStore,
        phase: str,
    ) -> None:
        if context.current_stage is expected:
            return
        events = store.list_events(context.task_id)
        reason = "unexpected state"
        for event in reversed(events):
            if event.event_type.value == "EVENT_SYSTEM_FAILED":
                reason = str(event.payload.get("reason", reason))
                break
        raise ShowcaseRunError(
            f"{phase} failed at {context.current_stage.value}: {reason}",
            task_id=context.task_id,
            calls=context.metadata.get("model_calls", []),
        )

    def _render_items(self, items: list[str]) -> None:
        if not items:
            self.emit("    - (none)")
            return
        for item in items:
            self.emit(f"    - {item}")

    def _heading(self, text: str) -> None:
        self.emit("")
        self.emit("=" * 72)
        self.emit(text)
        self.emit("=" * 72)

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
