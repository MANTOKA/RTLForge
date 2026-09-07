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
