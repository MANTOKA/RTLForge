from __future__ import annotations

import json
from pathlib import Path

from verilog_agent.agents import PlannerGeneratorShowcase
from verilog_agent.testing import (
    OFFLINE_COUNTER_SPEC,
    ScriptedProvider,
    counter_candidate_payload,
    counter_plan_payload,
)


def test_showcase_exposes_plan_tree_candidates_and_event_trace(tmp_path: Path) -> None:
    output: list[str] = []
    showcase = PlannerGeneratorShowcase(
        provider=ScriptedProvider(counter_plan_payload(), counter_candidate_payload()),
        provider_label="scripted_test (NO LLM API CALL)",
        database=tmp_path / "showcase.sqlite",
        runs_root=tmp_path / "runs",
        emit=output.append,
    )

    summary = showcase.run(OFFLINE_COUNTER_SPEC, candidate_count=2)

    rendered = "\n".join(output)
    assert "阶段 0：用户自然语言需求" in rendered
    assert OFFLINE_COUNTER_SPEC in rendered
    assert "module_name: counter" in rendered
    assert "- [root] 实现并准备验证 counter" in rendered
    assert "- [rtl] 生成 counter RTL" in rendered
    assert "Candidate: cand_01" in rendered
    assert "design_pattern: 单时序进程" in rendered
    assert "Candidate: cand_02" in rendered
    assert "design_pattern: 独立的下一值数据通路" in rendered
    assert rendered.count("module counter(") == 2
    assert "EVENT_PLAN_DONE" in rendered
    assert "EVENT_CANDIDATES_GENERATED" in rendered
    assert "下一步尚未执行：候选评分、EDA 验证和 Debugger 修复" in rendered

    assert summary["current_stage"] == "EVALUATING"
    assert summary["verified_rtl"] is False
    assert len(summary["candidates"]) == 2
    task_root = tmp_path / "runs" / summary["task_id"]
    assert (task_root / "input.txt").read_text(encoding="utf-8").strip() == OFFLINE_COUNTER_SPEC
    assert json.loads((task_root / "spec_ir.json").read_text(encoding="utf-8"))[
        "module_name"
    ] == "counter"
    assert json.loads((task_root / "task_tree.json").read_text(encoding="utf-8"))[
        "node_id"
    ] == "root"
    saved_summary = json.loads(
        (task_root / "showcase_summary.json").read_text(encoding="utf-8")
    )
    assert saved_summary["verified_rtl"] is False
    assert all(Path(item["artifact_path"]).is_file() for item in summary["candidates"])
