from __future__ import annotations

import json
from pathlib import Path

import pytest

from verilog_agent.benchmarks.explain_demo import (
    _find_recorded_defect,
    _find_recorded_repair,
    _find_summary,
    _mismatch_count,
    _normalized_category,
)


def test_find_summary_uses_recorded_human_v2_source(tmp_path: Path) -> None:
    summary = tmp_path / "task_demo" / "showcase_summary.json"
    summary.parent.mkdir()
    summary.write_text(
        json.dumps(
            {
                "verification": {
                    "testbench": {"source": "VerilogEval-Human-v2/Prob035_count1to10"}
                }
            }
        ),
        encoding="utf-8",
    )

    assert _find_summary(tmp_path, "Prob035_count1to10") == summary


def test_find_summary_rejects_missing_agent_evidence(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="No stored Agent run"):
        _find_summary(tmp_path, "Prob154_fsm_ps2data")


def test_demo_normalizes_mismatch_evidence() -> None:
    result = {
        "category": "ASSERTION_FAILURE",
        "stdout": "Mismatches: 76 in 1000 samples\n",
    }

    assert _normalized_category(result) == "FUNCTIONAL_MISMATCH"
    assert _mismatch_count(result) == 76


def test_find_recorded_repair_matches_source_summary(tmp_path: Path) -> None:
    summary = tmp_path / "task_demo" / "showcase_summary.json"
    summary.parent.mkdir()
    summary.write_text("{}", encoding="utf-8")
    report = tmp_path / "repair_replay_demo" / "repair_replay.json"
    report.parent.mkdir()
    report.write_text(
        json.dumps(
            {
                "source_summary": str(summary),
                "repair": {
                    "metadata": {},
                }
            }
        ),
        encoding="utf-8",
    )

    result = _find_recorded_repair(tmp_path, summary)

    assert result is not None
    assert result["report_path"].endswith("repair_replay.json")


def test_find_recorded_defect_requires_successful_reverification(tmp_path: Path) -> None:
    failed = tmp_path / "repair_replay_failed" / "repair_replay.json"
    passed = tmp_path / "repair_replay_passed" / "repair_replay.json"
    failed.parent.mkdir()
    passed.parent.mkdir()
    failed.write_text(
        json.dumps(
            {
                "mode": "defect_injection_repair_replay",
                "repaired_verification_passed": False,
            }
        ),
        encoding="utf-8",
    )
    passed.write_text(
        json.dumps(
            {
                "mode": "defect_injection_repair_replay",
                "repaired_verification_passed": True,
            }
        ),
        encoding="utf-8",
    )

    assert _find_recorded_defect(tmp_path)["report_path"].endswith(
        "repair_replay_passed/repair_replay.json"
    )
