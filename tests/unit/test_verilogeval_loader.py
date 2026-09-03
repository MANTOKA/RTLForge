from pathlib import Path

import pytest

from verilog_agent.benchmarks.verilogeval import load_human_v2


def _write_case(root: Path, problem_id: str) -> None:
    (root / f"{problem_id}_prompt.txt").write_text("Design a module.", encoding="utf-8")
    (root / f"{problem_id}_test.sv").write_text("module tb; endmodule", encoding="utf-8")
    (root / f"{problem_id}_ref.sv").write_text("module top; endmodule", encoding="utf-8")


def test_loader_keeps_agent_spec_separate_from_oracle_and_reference(tmp_path: Path) -> None:
    _write_case(tmp_path, "Prob001_demo")

    case = load_human_v2(tmp_path, expected_count=1)[0]

    assert case.read_agent_spec() == "Design a module."
    assert case.testbench_path.name == "Prob001_demo_test.sv"
    assert case.reference_path.name == "Prob001_demo_ref.sv"


def test_loader_rejects_incomplete_triplet(tmp_path: Path) -> None:
    (tmp_path / "Prob001_demo_prompt.txt").write_text("Design a module.", encoding="utf-8")

    with pytest.raises(ValueError, match="missing"):
        load_human_v2(tmp_path, expected_count=None)


def test_loader_rejects_unexpected_case_count(tmp_path: Path) -> None:
    _write_case(tmp_path, "Prob001_demo")

    with pytest.raises(ValueError, match="expected 156"):
        load_human_v2(tmp_path)
