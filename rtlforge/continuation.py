"""Start an audited repair branch from an immutable historical candidate."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from rtlforge.domain import Candidate, RTLTaskContext, TaskStage


def read_source(path: Path) -> tuple[Path, RTLTaskContext]:
    location = path if path.is_dir() else path.parent
    for directory in (location, *list(location.parents)[:4]):
        source = directory / "context.json"
        if source.is_file():
            task = RTLTaskContext.from_dict(json.loads(source.read_text(encoding="utf-8")))
            for candidate in task.candidates:
                recorded = Path(candidate.artifact_path)
                relocated = source.parent / "candidates" / candidate.candidate_id / recorded.name
                if (not recorded.is_file() and relocated.is_file()
                        and sha256(relocated.read_bytes()).hexdigest() == candidate.code_hash):
                    candidate.artifact_path = str(relocated.resolve())
            if task.metadata.get("integration_modules"):
                task.metadata["integration_task_root"] = str(source.parent.resolve())
            return source, task
    raise ValueError("找不到 context.json；请输入已有任务目录、context.json 或候选 RTL 路径")


def seed_continuation(engine, source_path: Path, source: RTLTaskContext, candidate_id: str, budget):
    selected = source.candidate(candidate_id)
    path = Path(selected.artifact_path)
    rtl = path.read_bytes()
    digest = sha256(rtl).hexdigest()
    if digest != selected.code_hash:
        raise ValueError("历史候选内容与记录的 hash 不符，请核对文件后再续跑")
    if source.spec_ir is None or source.task_tree_root is None:
        raise ValueError("历史任务缺少 SpecIR/Task Tree，无法从候选续跑")
    if source.spec_ir.missing_semantics():
        raise ValueError("历史需求合同尚未澄清，不能从候选直接续跑")
    old_tb = source.metadata.get("generated_testbench_path")
    if not engine.has_golden_testbench and (not old_tb or not Path(old_tb).is_file()):
        raise ValueError("找不到历史 Agent TB；请提供独立 TB 后续跑")
    from rtlforge.agents.generator import ArtifactRepository

    task = engine.create_task(source.raw_spec, budget=budget)
    target = engine.runs_root / task.task_id
    target.mkdir(parents=True, exist_ok=True)
    # A new seed ID avoids overwriting imported cand_repair_01 on repair attempt one.
    seed_path = ArtifactRepository(engine.runs_root).write_candidate(
        task.task_id, "cand_seed_01", source.spec_ir.module_name, rtl.decode("utf-8"),
    )
    task.spec_ir = source.spec_ir
    from rtlforge.integration import copy_modules

    copy_modules(source, task, target)
    if source.synthesis_constraints is not None:
        from rtlforge.domain.synthesis import SynthesisConstraints

        task.synthesis_constraints = SynthesisConstraints.from_dict(
            source.synthesis_constraints.to_dict()
        )
    task.task_tree_root = source.task_tree_root
    task.budget.max_candidates = 1
    task.budget.generated_candidates = 1
    task.candidates = [Candidate(
        "cand_seed_01", str(seed_path), sha256(seed_path.read_bytes()).hexdigest(),
        design_pattern=selected.design_pattern, assumptions=list(selected.assumptions),
        metadata={"contract_version": source.spec_ir.version},
    )]
    task.selected_candidate_id = "cand_seed_01"
    task.current_stage = TaskStage.EVALUATING
    task.metadata["continuation"] = {
        "source_context": str(source_path), "source_task_id": source.task_id,
        "source_candidate_id": candidate_id, "source_candidate_sha256": digest,
        "source_budget": source.budget.to_dict(),
        "new_repair_budget": task.budget.max_repairs,
    }
    if not engine.has_golden_testbench:
        copied = target / "generated_tb.sv"
        copied.write_bytes(Path(old_tb).read_bytes())
        task.metadata.update(
            generated_testbench_path=str(copied),
            generated_test_objectives=source.metadata.get("generated_test_objectives", []),
        )
    engine.store.save_checkpoint(task)
    return task
