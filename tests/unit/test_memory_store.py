from __future__ import annotations

from pathlib import Path

from verilog_agent.memory import RepairAttempt, SQLiteMemoryStore


def _attempt(
    *,
    passed: bool,
    problem_id: str = "Prob001_zero",
    after_mismatches: int = 12,
    memory_ids: list[str] | None = None,
) -> RepairAttempt:
    return RepairAttempt(
        task_id="task_memory",
        candidate_id="cand_repair_01",
        parent_candidate_id="cand_01",
        failure_category="FUNCTIONAL_MISMATCH",
        error_signature="FUNCTIONAL_MISMATCH outputs=done",
        module_family="counter",
        benchmark_problem_id=problem_id,
        before_mismatches=20,
        after_mismatches=after_mismatches,
        mismatch_reduction=20 - after_mismatches,
        passed=passed,
        rationale="Correct the terminal-count boundary condition.",
        expected_impact="Remove the off-by-one mismatch at counter wrap.",
        diff_path=None,
        evidence_path=None,
        memory_ids=list(memory_ids or []),
    )


def test_failed_repair_is_episodic_but_not_retrievable(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite", toolchain_version="sandbox:test")

    item = store.record_repair_attempt(_attempt(passed=False))

    assert item is None
    assert len(store.list_attempts()) == 1
    assert store.list_memories() == []


def test_verified_repair_is_retrievable_only_for_another_problem(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite", toolchain_version="sandbox:test")
    item = store.record_repair_attempt(_attempt(passed=True, after_mismatches=0))

    assert item is not None
    same_problem = store.retrieve(
        error_signature="FUNCTIONAL_MISMATCH outputs=done",
        module_family="counter",
        exclude_problem_id="Prob001_zero",
    )
    another_problem = store.retrieve(
        error_signature="FUNCTIONAL_MISMATCH outputs=done",
        module_family="counter",
        exclude_problem_id="Prob002_one",
    )

    assert same_problem == []
    assert another_problem[0]["memory_id"] == item.memory_id
    assert "patched_rtl" not in another_problem[0]


def test_negative_transfer_quarantines_memory_after_two_uses(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite", toolchain_version="sandbox:test")
    item = store.record_repair_attempt(_attempt(passed=True, after_mismatches=0))
    assert item is not None

    store.record_use([item.memory_id], positive=False)
    store.record_use([item.memory_id], positive=False)

    assert store.list_memories()[0]["status"] == "QUARANTINED"
    assert store.retrieve(
        error_signature="FUNCTIONAL_MISMATCH",
        module_family="counter",
        exclude_problem_id="Prob003_two",
    ) == []


def test_failure_becomes_error_pattern_not_repair_strategy(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite", toolchain_version="sandbox:test")

    item = store.record_error_observation(
        task_id="task_failed",
        candidate_id="cand_01",
        error_signature=(
            "FUNCTIONAL_MISMATCH outputs=Q pattern=isolated_single_sample"
        ),
        module_family="flip_flop",
        source_problem_id="Prob104_mt2015_muxdff",
        summary="Observed one mismatch at the first sample.",
        evidence_path=None,
    )

    assert item.memory_type == "ERROR_PATTERN"
    assert store.retrieve(
        error_signature="isolated_single_sample outputs Q",
        module_family="flip_flop",
        exclude_problem_id="Prob104_mt2015_muxdff",
    ) == []
    retrieved = store.retrieve(
        error_signature="isolated_single_sample outputs Q",
        module_family="flip_flop",
        exclude_problem_id="Prob200_other",
    )
    assert retrieved[0]["memory_type"] == "ERROR_PATTERN"
    assert "repair" not in retrieved[0]["summary"].lower()


def test_verified_design_pattern_has_generator_only_retrieval(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite", toolchain_version="sandbox:test")
    item = store.record_verified_design_pattern(
        task_id="task_passed",
        candidate_id="cand_02",
        module_family="counter",
        source_problem_id="Prob002_one",
        design_pattern="Synchronous counter with an explicit terminal-count branch.",
        evidence_path=None,
    )

    assert item is not None
    assert item.memory_type == "DESIGN_PATTERN"
    assert store.retrieve(
        error_signature="counter",
        module_family="counter",
        exclude_problem_id="Prob003_two",
    ) == []
    patterns = store.retrieve_design_patterns(
        module_family="counter",
        exclude_problem_id="Prob003_two",
    )
    assert patterns[0]["memory_type"] == "DESIGN_PATTERN"


def test_consolidation_counts_distinct_tasks_only(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite", toolchain_version="sandbox:test")
    kwargs = {
        "candidate_id": "cand_01",
        "error_signature": "FUNCTIONAL_MISMATCH outputs=Q",
        "module_family": "flip_flop",
        "source_problem_id": "Prob104_mt2015_muxdff",
        "summary": "Observed a mismatch.",
        "evidence_path": None,
    }
    store.record_error_observation(task_id="task_a", **kwargs)
    store.record_error_observation(task_id="task_a", **kwargs)
    store.record_error_observation(
        task_id="task_b", **{**kwargs, "source_problem_id": "Prob200_other"}
    )

    memory = store.list_memories()[0]
    assert memory["support_count"] == 2
    assert memory["source_task_ids"] == ["task_a", "task_b"]
    assert memory["source_problem_ids"] == [
        "Prob104_mt2015_muxdff",
        "Prob200_other",
    ]


def test_refined_repair_strategy_supersedes_generic_copy(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite", toolchain_version="sandbox:test")
    generic = RepairAttempt(
        **{
            **_attempt(passed=True, after_mismatches=0).to_dict(),
            "module_family": "generic_rtl",
        }
    )
    store.record_repair_attempt(generic)
    refined = RepairAttempt(
        **{
            **generic.to_dict(),
            "attempt_id": "repair_refined",
            "module_family": "flip_flop",
        }
    )
    store.record_repair_attempt(refined)

    assert store.supersede_coarse_repair_strategies() == 1
    assert store.supersede_coarse_repair_strategies() == 0
    strategies = {
        item["module_family"]: item["status"] for item in store.list_memories()
    }
    assert strategies == {"generic_rtl": "SUPERSEDED", "flip_flop": "ACTIVE"}
