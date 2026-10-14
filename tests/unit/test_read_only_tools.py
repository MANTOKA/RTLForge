from pathlib import Path

import pytest

from verilog_agent.agents.planner import RealPlanner
from verilog_agent.domain.models import RTLTaskContext
from verilog_agent.services.read_only_tools import ReadOnlyReferenceTools
from verilog_agent.testing import ScriptedProvider, counter_plan_payload


def test_reference_tools_expose_only_allowlisted_files(tmp_path: Path) -> None:
    allowed = tmp_path / "spec.txt"
    secret = tmp_path / "golden_tb.sv"
    allowed.write_text("public requirements", encoding="utf-8")
    secret.write_text("SECRET", encoding="utf-8")
    tools = ReadOnlyReferenceTools([str(allowed)])

    assert tools.call("read_file", str(allowed)) == "public requirements"
    assert tools.call("list_dir", str(tmp_path)) == "spec.txt"
    with pytest.raises(PermissionError):
        tools.call("read_file", str(secret))


def test_graph_planner_requests_one_bounded_read(tmp_path: Path) -> None:
    reference = tmp_path / "spec.txt"
    reference.write_text("public requirements", encoding="utf-8")
    provider = ScriptedProvider(
        {"tool": "read_file", "path": str(reference)}, counter_plan_payload()
    )
    context = RTLTaskContext(
        task_id="tool_test", raw_spec="four bit counter",
        metadata={
            "experiment_tags": ["orchestrator=langgraph"],
            "reference_files": [str(reference)],
        },
    )

    result = RealPlanner(provider).plan(context)

    assert result.spec_ir.coding_style_constraint == "strict_sv"
    assert [row["schema_name"] for row in provider.requests] == [
        "planner_reference_tool", "verilog_requirement_plan",
    ]
    assert "public requirements" in provider.requests[1]["input_text"]
    assert context.metadata["read_tool_calls"][0]["tool"] == "read_file"
