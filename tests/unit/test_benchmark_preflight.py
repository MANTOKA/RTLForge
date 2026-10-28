from pathlib import Path

from rtlforge.benchmarks.preflight import preflight_references
from rtlforge.benchmarks.verilogeval import load_human_v2


class ReferenceRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run_files(self, action: str, input_paths: list[Path]) -> dict:
        self.calls.append([path.name for path in input_paths])
        return {
            "status": "PASS",
            "category": "OK",
            "duration_ms": 4,
            "errors": [],
        }


def test_reference_preflight_records_hashes_without_agent_exposure(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    for problem_id in ("Prob001_a", "Prob002_b"):
        (dataset / f"{problem_id}_prompt.txt").write_text("spec", encoding="utf-8")
        (dataset / f"{problem_id}_test.sv").write_text("module tb; endmodule", encoding="utf-8")
        (dataset / f"{problem_id}_ref.sv").write_text(
            "module RefModule; endmodule", encoding="utf-8"
        )
    cases = load_human_v2(dataset, expected_count=2)
    runner = ReferenceRunner()
    output = tmp_path / "report.json"

    report = preflight_references(cases, runner, output, limit=1)

    assert report["case_count"] == 1
    assert report["passed"] == 1
    assert report["agent_calls"] == 0
    assert report["reference_content_exposed_to_agents"] is False
    assert runner.calls == [
        ["Prob001_a_test.sv", "Prob001_a_ref.sv", "Prob001_a_TopModule.sv"]
    ]
    assert len(report["results"][0]["reference_sha256"]) == 64
    assert output.is_file()


def test_reference_preflight_separates_tool_incompatibility(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "Prob001_a_prompt.txt").write_text("spec", encoding="utf-8")
    (dataset / "Prob001_a_test.sv").write_text("module tb; endmodule", encoding="utf-8")
    (dataset / "Prob001_a_ref.sv").write_text(
        "module RefModule; endmodule", encoding="utf-8"
    )
    cases = load_human_v2(dataset, expected_count=1)

    class UnsupportedRunner:
        def run_files(self, action: str, input_paths: list[Path]) -> dict:
            return {
                "status": "FAIL",
                "category": "SYNTAX_ERROR",
                "duration_ms": 1,
                "errors": [{"message": "sorry: This cast operation is not yet supported."}],
            }

    report = preflight_references(cases, UnsupportedRunner(), tmp_path / "report.json")

    assert report["results"][0]["category"] == "TOOL_INCOMPATIBILITY"
    assert report["results"][0]["tool_category"] == "SYNTAX_ERROR"


def test_reference_preflight_accepts_a_separate_adapter_root(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "Prob001_a_prompt.txt").write_text("spec", encoding="utf-8")
    (dataset / "Prob001_a_test.sv").write_text("module tb; endmodule", encoding="utf-8")
    (dataset / "Prob001_a_ref.sv").write_text(
        "module RefModule; endmodule", encoding="utf-8"
    )
    cases = load_human_v2(dataset, expected_count=1)
    runner = ReferenceRunner()
    artifact_root = tmp_path / "tasks" / "_reference_adapters"

    preflight_references(
        cases,
        runner,
        tmp_path / "reports" / "report.json",
        artifact_root=artifact_root,
    )

    assert (artifact_root / "Prob001_a_TopModule.sv").is_file()


def test_preflight_can_replace_reference_instead_of_compiling_both(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "Prob001_a_prompt.txt").write_text("spec", encoding="utf-8")
    (dataset / "Prob001_a_test.sv").write_text("module tb; endmodule", encoding="utf-8")
    (dataset / "Prob001_a_ref.sv").write_text(
        "module ReferenceTop; endmodule", encoding="utf-8"
    )
    cases = load_human_v2(dataset, expected_count=1)
    runner = ReferenceRunner()

    def adapter(case, root: Path) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        path = root / "adapted.v"
        path.write_text("module TopModule; endmodule", encoding="utf-8")
        return path

    preflight_references(
        cases,
        runner,
        tmp_path / "report.json",
        reference_adapter=adapter,
        include_original_reference=False,
        dataset_name="RTLLM-2.1",
    )

    assert runner.calls == [["Prob001_a_test.sv", "adapted.v"]]


def test_reference_preflight_classifies_testbench_syntax_as_tool_incompatibility(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "Prob001_a_prompt.txt").write_text("spec", encoding="utf-8")
    (dataset / "Prob001_a_test.sv").write_text("module tb; endmodule", encoding="utf-8")
    (dataset / "Prob001_a_ref.sv").write_text(
        "module RefModule; endmodule", encoding="utf-8"
    )
    cases = load_human_v2(dataset, expected_count=1)

    class TestbenchSyntaxRunner:
        def run_files(self, action: str, input_paths: list[Path]) -> dict:
            return {
                "status": "FAIL",
                "category": "SYNTAX_ERROR",
                "duration_ms": 1,
                "errors": [
                    {
                        "file": "/inputs/oracle/Prob001_a_test.sv",
                        "message": "syntax error",
                    }
                ],
            }

    report = preflight_references(
        cases, TestbenchSyntaxRunner(), tmp_path / "report.json"
    )

    assert report["failure_categories"] == {"TOOL_INCOMPATIBILITY": 1}
