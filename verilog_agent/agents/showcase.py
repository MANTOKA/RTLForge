from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from time import perf_counter
from typing import NoReturn

from verilog_agent.agents.generator import ArtifactRepository, RealGenerator
from verilog_agent.agents.planner import RealPlanner
from verilog_agent.domain import Budget, RTLTaskContext, TaskStage, TaskTreeNode
from verilog_agent.orchestration import Orchestrator, SQLiteTaskStore
from verilog_agent.providers import StructuredModelProvider


class ShowcaseRunError(RuntimeError):
    pass


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
        emit: Callable[[str], None] = print,
    ) -> None:
        self.provider = provider
        self.provider_label = provider_label
        self.database = database
        self.runs_root = runs_root
        self.emit = emit

    def run(self, raw_spec: str, *, candidate_count: int = 2) -> dict:
        store = SQLiteTaskStore(self.database)
        stop_after_generation = _StopAfterGeneration()
        orchestrator = Orchestrator(
            planner=RealPlanner(self.provider),
            generator=RealGenerator(
                self.provider, artifacts=ArtifactRepository(self.runs_root)
            ),
            debugger=stop_after_generation,
            evaluator=stop_after_generation,
            verifier=stop_after_generation,
            store=store,
        )
        context = orchestrator.create_task(
            raw_spec,
            budget=Budget(max_candidates=candidate_count, max_repairs=0),
        )
        task_root = self.runs_root / context.task_id
        task_root.mkdir(parents=True, exist_ok=True)
        (task_root / "input.txt").write_text(raw_spec + "\n", encoding="utf-8", newline="\n")

        self._heading("VerilogAgent Planner / Generator 可观察演示")
        self.emit(f"Provider: {self.provider_label}")
        self.emit(f"Task ID: {context.task_id}")
        self.emit("verified_rtl: false（此演示有意停在候选生成后）")
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

        self._heading(f"阶段 2：Generator 正在生成 {candidate_count} 个差异候选")
        started = perf_counter()
        orchestrator.advance_task(context.task_id)
        generator_ms = round((perf_counter() - started) * 1000)
        self._expect_stage(context, TaskStage.EVALUATING, store, "Generator")
        self._render_candidates(context)
        self.emit(f"Generator elapsed: {generator_ms} ms")

        events = store.list_events(context.task_id)
        self._heading("阶段 3：Orchestrator 事件轨迹")
        for event in events:
            self.emit(
                f"{event.sequence:02d}. {event.event_type.value} "
                f"[stage={event.stage.value}]"
            )

        summary = {
            "provider": self.provider_label,
            "task_id": context.task_id,
            "input": raw_spec,
            "current_stage": context.current_stage.value,
            "next_stage": "Candidate Evaluation Service (Phase 2 Week 6)",
            "verified_rtl": False,
            "timings_ms": {"planner": planner_ms, "generator": generator_ms},
            "spec_ir": context.spec_ir.to_dict(),
            "task_tree": context.task_tree_root.to_dict(),
            "candidates": [candidate.to_dict() for candidate in context.candidates],
            "events": [event.to_dict() for event in events],
        }
        summary_path = task_root / "showcase_summary.json"
        self._write_json(summary_path, summary)
        self._heading("演示结束：产物位置")
        self.emit(f"输入需求: {(task_root / 'input.txt').as_posix()}")
        self.emit(f"SpecIR: {(task_root / 'spec_ir.json').as_posix()}")
        self.emit(f"Task Tree: {(task_root / 'task_tree.json').as_posix()}")
        self.emit(f"完整摘要: {summary_path.as_posix()}")
        self.emit("下一步尚未执行：候选评分、EDA 验证和 Debugger 修复。")
        return summary

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
            f"{phase} failed at {context.current_stage.value}: {reason}"
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
