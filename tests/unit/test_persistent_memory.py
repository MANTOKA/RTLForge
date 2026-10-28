from pathlib import Path

from rtlforge.memory import MemoryItem, SQLiteMemoryStore
from rtlforge.memory.bootstrap import bootstrap_memory
from rtlforge.orchestration.orchestrator import Orchestrator


def test_bootstrap_keeps_manual_evidence_separate_from_retrievable_rules(
    tmp_path: Path,
) -> None:
    human_path, rs_path, target = (
        tmp_path / "human.sqlite", tmp_path / "rs.sqlite", tmp_path / "persistent.sqlite"
    )
    human = SQLiteMemoryStore(human_path, toolchain_version="test")
    human.record_verified_design_pattern(
        task_id="human_task", candidate_id="original", module_family="counter",
        source_problem_id="Prob001", design_pattern="Use a bounded counter state.",
        evidence_path=None,
    )
    rs = SQLiteMemoryStore(rs_path, toolchain_version="test")
    rs.record_human_intervention(MemoryItem(
        memory_id="mem_manual_rs", memory_type="HUMAN_INTERVENTION",
        summary="RS KES signed-state transition needs finite-width review.",
        error_signature="FUNCTIONAL_MISMATCH case20 signed_state",
        module_family="rs_kes_ribm",
        applicability_conditions="Only when the recurrence and width match.",
        recommended_action="Derive the signed recurrence before implementing it.",
        source_task_id="manual_rs", source_candidate_id="manual_copy",
        source_problem_id="RS272257", before_score=None, after_score=None,
        verification_gate_reached="FUNCTIONAL_VERIFIED", toolchain_version="test",
        evidence_hashes=["a" * 64], status="EVIDENCE_ONLY",
        provenance={
            "source_repository": "local", "source_commit": "abc", "license": "local",
            "operator": "codex_assistant_manual", "human_authored": False,
            "verification_command": "pytest", "verification_result": "PASS",
            "failure_signature": "signed_state", "patch_hash": "b" * 64,
        },
    ))

    assert bootstrap_memory(target, human_path, rs_path) == {
        "human_active": 1, "rs_evidence": 1, "rs_curated_rules": 1, "total": 3,
    }
    assert bootstrap_memory(target, human_path, rs_path)["total"] == 3
    stored = SQLiteMemoryStore(target, toolchain_version="test")
    assert stored.retrieve_design_patterns(
        module_family="counter", exclude_problem_id="Prob001"
    ) == []
    rules = stored.retrieve_design_patterns(
        module_family="rs_kes_ribm", exclude_problem_id="RS272257"
    )
    assert len(rules) == 1
    assert rules[0]["memory_type"] == "PROJECT"
    assert rules[0]["recommended_action"] == (
        "Derive the signed recurrence before implementing it."
    )
    assert not any(item["memory_type"] == "HUMAN_INTERVENTION" for item in
                   stored.retrieve(error_signature="signed_state", module_family="rs_kes_ribm",
                                   exclude_problem_id=None))


def test_rs_module_family_uses_planned_top_name() -> None:
    spec = "RS(272,257) decoder with syndrome, BM KES and Chien/Forney"
    assert Orchestrator._module_family(spec, "rs_kes_bm") == "rs_kes_bm"
    assert Orchestrator._module_family(spec, "rs_kes_ribm") == "rs_kes_ribm"
    assert Orchestrator._module_family(spec, "rs272257_bm_decoder") == "rs_decoder_top_bm"
    assert Orchestrator._module_family(spec, "rs272257_ribm_decoder") == "rs_decoder_top_ribm"
