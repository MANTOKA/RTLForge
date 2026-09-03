from pathlib import Path

from verilog_agent.benchmarks.preflight import preflight_references
from verilog_agent.benchmarks.verilogeval import load_human_v2


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
