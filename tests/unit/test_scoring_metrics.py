import pytest

from rtlforge.evaluation import ScoredCandidateObservation, summarize_scoring


def test_scoring_metrics_compare_score_order_with_generation_order() -> None:
    observations = [
        ScoredCandidateObservation("task_1", "cand_01", 70, False, 1),
        ScoredCandidateObservation("task_1", "cand_02", 90, True, 2),
        ScoredCandidateObservation("task_2", "cand_01", 95, False, 1),
        ScoredCandidateObservation("task_2", "cand_02", 80, True, 2),
    ]

    metrics = summarize_scoring(observations)

    assert metrics.task_count == 2
    assert metrics.candidate_count == 4
    assert metrics.final_success_rate == 1.0
    assert metrics.scored_top1_accuracy == 0.5
    assert metrics.generation_top1_accuracy == 0.0
    assert metrics.average_simulations_to_first_pass_scored == 1.5
    assert metrics.average_simulations_to_first_pass_generation == 2.0
    assert metrics.false_rejection_rate == 0.0
    assert metrics.score_functional_spearman == pytest.approx(0.0)


def test_scoring_metrics_reports_functionally_correct_threshold_rejection() -> None:
    observations = [
        ScoredCandidateObservation("task_1", "cand_01", 74, True, 1),
        ScoredCandidateObservation("task_1", "cand_02", 90, False, 2),
    ]

    metrics = summarize_scoring(observations)

    assert metrics.false_rejection_rate == 1.0
    assert metrics.scored_top1_accuracy == 0.0
    assert metrics.score_functional_spearman == pytest.approx(-1.0)


def test_scoring_metrics_requires_observations() -> None:
    with pytest.raises(ValueError, match="at least one"):
        summarize_scoring([])
