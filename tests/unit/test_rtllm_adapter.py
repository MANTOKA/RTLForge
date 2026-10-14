from pathlib import Path

import pytest

from verilog_agent.benchmarks.rtllm import (
    adapt_rtllm_reference,
    load_rtllm,
    rtllm_run_root,
)


def _write_case(root: Path, *, module_name: str = "adder_8bit") -> Path:
    case_root = root / "Arithmetic" / "Adder" / module_name
    case_root.mkdir(parents=True)
    (case_root / "design_description.txt").write_text(
        f"Module name:\n    {module_name}\nGive me the complete code.\n",
        encoding="utf-8",
    )
    (case_root / "testbench.v").write_text(
        f"module testbench; {module_name} uut(); endmodule\n", encoding="utf-8"
    )
    (case_root / f"verified_{module_name}.v").write_text(
        f"module verified_{module_name}; endmodule\n"
        "module helper; endmodule\n",
        encoding="utf-8",
    )
    return case_root


def test_loader_discovers_nested_case_and_keeps_oracles_separate(tmp_path: Path) -> None:
    _write_case(tmp_path)

    case = load_rtllm(tmp_path, expected_count=1)[0]

    assert case.problem_id == "adder_8bit"
    assert case.category == "Arithmetic"
    assert case.module_name == "adder_8bit"
    assert case.reference_module_name == "verified_adder_8bit"
    assert "Module name" in case.read_agent_spec()


def test_reference_adapter_renames_only_reference_top(tmp_path: Path) -> None:
    _write_case(tmp_path)
    case = load_rtllm(tmp_path, expected_count=1)[0]

    adapted = adapt_rtllm_reference(case, tmp_path / "adapters")
    source = adapted.read_text(encoding="utf-8")

    assert "module adder_8bit" in source
    assert "module helper" in source
    assert "verified_adder_8bit" not in source


def test_loader_rejects_incomplete_case(tmp_path: Path) -> None:
    case_root = tmp_path / "Arithmetic" / "broken"
    case_root.mkdir(parents=True)
    (case_root / "design_description.txt").write_text(
        "Module name: broken", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="testbench.v"):
        load_rtllm(tmp_path, expected_count=None)


def test_rtllm_artifacts_are_grouped_by_dataset_and_model() -> None:
    assert rtllm_run_root(Path("runs"), "gpt-5.6-terra") == Path(
        "runs/rtllm-2.1/gpt-5.6-terra"
    )
