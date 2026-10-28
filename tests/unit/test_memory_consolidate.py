from rtlforge.memory.consolidate import _has_clean_functional_pass


def _summary(mismatches: int) -> tuple[dict, list[dict]]:
    attempts = [
        {
            "candidate_id": "cand_01",
            "status": "PASS",
            "debugger_feedback": {
                "mismatch_summary": {"total_mismatched_samples": mismatches}
            },
        }
    ]
    return {
        "verified_rtl": True,
        "selected_candidate_id": "cand_01",
    }, attempts


def test_clean_functional_pass_rejects_stale_wrapper_false_pass() -> None:
    summary, attempts = _summary(1)

    assert _has_clean_functional_pass(summary, attempts) is False


def test_clean_functional_pass_accepts_zero_mismatches() -> None:
    summary, attempts = _summary(0)

    assert _has_clean_functional_pass(summary, attempts) is True
