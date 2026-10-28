from __future__ import annotations

from dataclasses import asdict, dataclass
from math import sqrt


@dataclass(frozen=True, slots=True)
class ScoredCandidateObservation:
    task_id: str
    candidate_id: str
    static_score: float
    functional_pass: bool
    generation_order: int
    threshold: float = 75.0


@dataclass(frozen=True, slots=True)
class ScoringMetrics:
    task_count: int
    candidate_count: int
    final_success_rate: float
    scored_top1_accuracy: float
    generation_top1_accuracy: float
    average_simulations_to_first_pass_scored: float | None
    average_simulations_to_first_pass_generation: float | None
    score_functional_spearman: float | None
    false_rejection_rate: float

    def to_dict(self) -> dict[str, int | float | None]:
        return asdict(self)


def summarize_scoring(observations: list[ScoredCandidateObservation]) -> ScoringMetrics:
    """Measure score utility against functional-oracle results, grouped by task."""
    if not observations:
        raise ValueError("at least one scored candidate observation is required")
    tasks: dict[str, list[ScoredCandidateObservation]] = {}
    for observation in observations:
        tasks.setdefault(observation.task_id, []).append(observation)

    scored_top1_passes = 0
    generation_top1_passes = 0
    successful_tasks = 0
    scored_simulations: list[int] = []
    generation_simulations: list[int] = []
    for candidates in tasks.values():
        scored_order = sorted(candidates, key=lambda item: (-item.static_score, item.candidate_id))
        generation_order = sorted(candidates, key=lambda item: item.generation_order)
        scored_top1_passes += int(scored_order[0].functional_pass)
        generation_top1_passes += int(generation_order[0].functional_pass)
        if any(item.functional_pass for item in candidates):
            successful_tasks += 1
            scored_simulations.append(_first_pass_position(scored_order))
            generation_simulations.append(_first_pass_position(generation_order))

    functional_candidates = [item for item in observations if item.functional_pass]
    false_rejections = sum(
        item.functional_pass and item.static_score < item.threshold for item in observations
    )
    task_count = len(tasks)
    return ScoringMetrics(
        task_count=task_count,
        candidate_count=len(observations),
        final_success_rate=successful_tasks / task_count,
        scored_top1_accuracy=scored_top1_passes / task_count,
        generation_top1_accuracy=generation_top1_passes / task_count,
        average_simulations_to_first_pass_scored=_mean_or_none(scored_simulations),
        average_simulations_to_first_pass_generation=_mean_or_none(generation_simulations),
        score_functional_spearman=_spearman(
            [item.static_score for item in observations],
            [float(item.functional_pass) for item in observations],
        ),
        false_rejection_rate=(
            false_rejections / len(functional_candidates) if functional_candidates else 0.0
        ),
    )


def _first_pass_position(candidates: list[ScoredCandidateObservation]) -> int:
    return next(index for index, item in enumerate(candidates, start=1) if item.functional_pass)


def _mean_or_none(values: list[int]) -> float | None:
    return sum(values) / len(values) if values else None


def _rank(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(indexed):
        end = cursor + 1
        while end < len(indexed) and indexed[end][1] == indexed[cursor][1]:
            end += 1
        average_rank = (cursor + 1 + end) / 2
        for index in range(cursor, end):
            ranks[indexed[index][0]] = average_rank
        cursor = end
    return ranks


def _spearman(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_rank = _rank(left)
    right_rank = _rank(right)
    left_mean = sum(left_rank) / len(left_rank)
    right_mean = sum(right_rank) / len(right_rank)
    covariance = sum(
        (left_value - left_mean) * (right_value - right_mean)
        for left_value, right_value in zip(left_rank, right_rank, strict=True)
    )
    left_variance = sum((value - left_mean) ** 2 for value in left_rank)
    right_variance = sum((value - right_mean) ** 2 for value in right_rank)
    denominator = sqrt(left_variance * right_variance)
    return covariance / denominator if denominator else None
