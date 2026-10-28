import json
from pathlib import Path

import pytest

from rtlforge.agents.planner import RealPlanner
from rtlforge.domain.models import RTLTaskContext
from rtlforge.services.read_only_tools import ReadOnlyReferenceTools
from rtlforge.testing import ScriptedProvider, counter_plan_payload


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


def test_graph_planner_reads_then_explicitly_finishes_investigation(tmp_path: Path) -> None:
    reference = tmp_path / "spec.txt"
    reference.write_text("public requirements", encoding="utf-8")
    provider = ScriptedProvider(
        {"tool": "read_file", "path": str(reference)},
        {"tool": "none", "path": ""}, counter_plan_payload()
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
        "planner_reference_tool", "planner_reference_tool", "verilog_requirement_plan",
    ]
    assert "public requirements" in provider.requests[-1]["input_text"]
    assert context.metadata["read_tool_calls"][0]["tool"] == "read_file"


def test_planner_lists_reads_and_follows_up_on_relevant_lines(tmp_path):
    reference = tmp_path / "protocol.txt"
    reference.write_text(
        "Protocol index\nReset details below\nActive low asynchronous\n", encoding="utf-8",
    )
    provider = ScriptedProvider(
        {"tool": "list_dir", "path": str(tmp_path)},
        {"tool": "read_file", "path": str(reference), "start_line": 1, "max_lines": 2},
        {"tool": "read_file", "path": str(reference), "start_line": 3, "max_lines": 1},
        {"tool": "none", "path": ""}, counter_plan_payload(),
    )
    context = RTLTaskContext(task_id="investigate", raw_spec="counter", metadata={
        "experiment_tags": ["orchestrator=langgraph"], "reference_files": [str(reference)],
    })
    RealPlanner(provider).plan(context)
    assert len(context.metadata["read_tool_calls"]) == 3
    assert "protocol.txt" in provider.requests[1]["input_text"]
    assert "Reset details below" in provider.requests[2]["input_text"]
    assert "Active low asynchronous" in provider.requests[3]["input_text"]
    evidence = json.loads(provider.requests[-1]["input_text"])["reference_observations"]
    assert evidence[-1]["start_line"] == 3
    assert evidence[-1]["content"] == "Active low asynchronous\n"


@pytest.mark.parametrize("limit_kind", ["calls", "characters"])
def test_planner_tool_budgets_force_planning_without_another_tool_request(tmp_path, limit_kind):
    reference = tmp_path / "spec.txt"
    reference.write_text("abcdef", encoding="utf-8")
    provider = ScriptedProvider(
        {"tool": "read_file", "path": str(reference)}, counter_plan_payload(),
    )
    context = RTLTaskContext(task_id="bounded", raw_spec="counter", metadata={
        "experiment_tags": ["orchestrator=langgraph"], "reference_files": [str(reference)],
    })
    kwargs = {"max_reference_calls": 1} if limit_kind == "calls" else {
        "max_reference_characters": 3,
    }
    RealPlanner(provider, **kwargs).plan(context)
    assert len(provider.requests) == 2
    assert context.metadata["read_tool_calls"][0]["characters"] == (
        6 if limit_kind == "calls" else 3
    )


@pytest.mark.parametrize("start_line,max_lines", [(0, 1), (True, 1), (1, 201)])
def test_reference_line_ranges_are_validated(tmp_path, start_line, max_lines):
    reference = tmp_path / "spec.txt"
    reference.write_text("line", encoding="utf-8")
    with pytest.raises(ValueError):
        ReadOnlyReferenceTools([str(reference)]).call(
            "read_file", str(reference), start_line=start_line, max_lines=max_lines,
        )


def test_followup_cannot_read_an_unlisted_golden_file(tmp_path):
    allowed, golden = tmp_path / "spec.txt", tmp_path / "golden.sv"
    allowed.write_text("spec", encoding="utf-8")
    golden.write_text("SECRET", encoding="utf-8")
    provider = ScriptedProvider(
        {"tool": "list_dir", "path": str(tmp_path)},
        {"tool": "read_file", "path": str(golden)},
    )
    context = RTLTaskContext(task_id="blocked", raw_spec="counter", metadata={
        "experiment_tags": ["orchestrator=langgraph"], "reference_files": [str(allowed)],
    })
    with pytest.raises(PermissionError):
        RealPlanner(provider).plan(context)
    assert "SECRET" not in str(provider.requests)


def test_physical_line_limit_does_not_silently_renumber_document(tmp_path):
    reference = tmp_path / "long.txt"
    reference.write_text("x" * 9000 + "\nreset\n", encoding="utf-8")
    with pytest.raises(ValueError, match="physical line"):
        ReadOnlyReferenceTools([str(reference)]).call("read_file", str(reference), start_line=2)
