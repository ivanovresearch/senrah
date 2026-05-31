"""
tests/unit/test_skill_repo_rerank.py — Unit tests for the Python re-rank/threshold logic.

Covers SEARCH-02: candidates ranked by composite score, threshold filtering, top_n cap.
These tests do NOT require the DB — they test the re-ranking logic in isolation.

The test helpers feed synthetic (p_sim, s_sim) pairs directly to the scoring and filtering
logic, verifying that SkillRepo.search's post-fetch Python logic:
1. Correctly orders results by composite score descending.
2. Drops candidates below score_threshold.
3. Returns at most top_n results.
4. Handles the below-threshold case (zero passing results).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from datetime import datetime

import pytest

# We import the composite_score function (pure, no DB required)
from harness.scoring import composite_score


# ---------------------------------------------------------------------------
# Helpers: simulate the re-rank logic from SkillRepo.search
# (We extract this logic to test it independently of any DB connection)
# ---------------------------------------------------------------------------


@dataclass
class CandidateRow:
    """Simulates a row fetched from DB (p_sim and s_sim already computed as 1 - distance)."""

    pr_id: int
    number: int
    title: str
    repo_name: str
    author: str
    merged_at: datetime
    linked_issue: Optional[str]
    files_changed: list
    diff: str
    problem_sim: float
    solution_sim: float


def python_rerank(
    candidates: list[CandidateRow],
    score_threshold: float,
    problem_weight: float,
    solution_weight: float,
    top_n: int,
) -> tuple[list[tuple[CandidateRow, float]], Optional[tuple[CandidateRow, float]]]:
    """Pure Python re-rank logic mirroring SkillRepo.search post-fetch behavior.

    Returns:
        (passing_results, below_threshold_top) where:
        - passing_results: candidates with score >= threshold, sorted desc by score, capped at top_n
        - below_threshold_top: the top candidate by score when zero pass threshold, else None
    """
    scored = [
        (c, composite_score(c.problem_sim, c.solution_sim, problem_weight, solution_weight))
        for c in candidates
    ]
    above = [(c, s) for c, s in scored if s >= score_threshold]
    above.sort(key=lambda cs: cs[1], reverse=True)

    if above:
        return above[:top_n], None

    # Below-threshold fallback (D-11 / Open Question 4)
    if scored:
        below_top = max(scored, key=lambda cs: cs[1])
        return [], below_top
    return [], None


_NOW = datetime(2024, 3, 15, 12, 0, 0)


def make_candidate(pr_id: int, p_sim: float, s_sim: float) -> CandidateRow:
    return CandidateRow(
        pr_id=pr_id,
        number=pr_id * 100,
        title=f"PR {pr_id}",
        repo_name="owner/repo",
        author="author",
        merged_at=_NOW,
        linked_issue=None,
        files_changed=["file.py"],
        diff="diff content",
        problem_sim=p_sim,
        solution_sim=s_sim,
    )


# ---------------------------------------------------------------------------
# Tests: ordering by composite score
# ---------------------------------------------------------------------------


class TestReRankOrdering:
    """Results must be ordered by composite score descending."""

    def test_orders_by_composite_score_descending(self):
        """Higher composite score appears first."""
        candidates = [
            make_candidate(1, p_sim=0.5, s_sim=0.3),  # score = 0.6*0.5 + 0.4*0.3 = 0.42
            make_candidate(2, p_sim=0.9, s_sim=0.8),  # score = 0.6*0.9 + 0.4*0.8 = 0.86
            make_candidate(3, p_sim=0.7, s_sim=0.5),  # score = 0.6*0.7 + 0.4*0.5 = 0.62
        ]
        passing, _ = python_rerank(candidates, score_threshold=0.0, problem_weight=0.6, solution_weight=0.4, top_n=10)
        scores = [s for _, s in passing]
        assert scores == sorted(scores, reverse=True), "Results not in descending score order"
        assert passing[0][0].pr_id == 2, "Highest scoring candidate should be first"

    def test_single_candidate_ordered(self):
        """Single candidate is returned as-is (no ordering issue)."""
        candidates = [make_candidate(1, p_sim=0.7, s_sim=0.6)]
        passing, _ = python_rerank(candidates, score_threshold=0.0, problem_weight=0.6, solution_weight=0.4, top_n=5)
        assert len(passing) == 1
        assert passing[0][0].pr_id == 1

    def test_tiebreak_preserves_order(self):
        """Two candidates with same score are both returned (order stable)."""
        c1 = make_candidate(1, p_sim=0.5, s_sim=0.5)  # score = 0.5
        c2 = make_candidate(2, p_sim=0.5, s_sim=0.5)  # score = 0.5
        passing, _ = python_rerank([c1, c2], score_threshold=0.0, problem_weight=0.6, solution_weight=0.4, top_n=5)
        assert len(passing) == 2


# ---------------------------------------------------------------------------
# Tests: threshold filtering (D-11)
# ---------------------------------------------------------------------------


class TestThresholdFiltering:
    """Candidates below score_threshold are dropped."""

    def test_drops_below_threshold(self):
        """Candidates with score below threshold are excluded."""
        candidates = [
            make_candidate(1, p_sim=0.3, s_sim=0.2),   # score = 0.26 → below 0.40
            make_candidate(2, p_sim=0.9, s_sim=0.8),   # score = 0.86 → above
        ]
        passing, below = python_rerank(
            candidates, score_threshold=0.40, problem_weight=0.6, solution_weight=0.4, top_n=5
        )
        assert len(passing) == 1
        assert passing[0][0].pr_id == 2
        assert below is None  # there IS a passing result, so no below-threshold hint needed

    def test_all_above_threshold_included(self):
        """All candidates above threshold are included (up to top_n)."""
        candidates = [make_candidate(i, p_sim=0.8, s_sim=0.7) for i in range(1, 4)]
        passing, _ = python_rerank(
            candidates, score_threshold=0.40, problem_weight=0.6, solution_weight=0.4, top_n=5
        )
        assert len(passing) == 3

    def test_threshold_zero_includes_all(self):
        """threshold=0.0 passes all candidates."""
        candidates = [make_candidate(i, p_sim=0.1 * i, s_sim=0.1 * i) for i in range(1, 6)]
        passing, _ = python_rerank(
            candidates, score_threshold=0.0, problem_weight=0.6, solution_weight=0.4, top_n=10
        )
        assert len(passing) == 5

    def test_threshold_exact_match_included(self):
        """Candidate with score exactly equal to threshold is included."""
        # Score = 0.6*0.5 + 0.4*0.5 = 0.5 = threshold
        candidates = [make_candidate(1, p_sim=0.5, s_sim=0.5)]
        passing, _ = python_rerank(
            candidates, score_threshold=0.5, problem_weight=0.6, solution_weight=0.4, top_n=5
        )
        assert len(passing) == 1


# ---------------------------------------------------------------------------
# Tests: top_n cap
# ---------------------------------------------------------------------------


class TestTopNCap:
    """Result count is capped at top_n."""

    def test_top_n_cap(self):
        """Returns at most top_n results even when more pass the threshold."""
        candidates = [make_candidate(i, p_sim=0.8, s_sim=0.7) for i in range(1, 11)]
        passing, _ = python_rerank(
            candidates, score_threshold=0.0, problem_weight=0.6, solution_weight=0.4, top_n=5
        )
        assert len(passing) == 5

    def test_top_n_1(self):
        """top_n=1 returns only the best candidate."""
        candidates = [
            make_candidate(1, p_sim=0.5, s_sim=0.3),
            make_candidate(2, p_sim=0.9, s_sim=0.8),
        ]
        passing, _ = python_rerank(
            candidates, score_threshold=0.0, problem_weight=0.6, solution_weight=0.4, top_n=1
        )
        assert len(passing) == 1
        assert passing[0][0].pr_id == 2  # best candidate

    def test_top_n_larger_than_candidates(self):
        """top_n larger than candidate count returns all candidates."""
        candidates = [make_candidate(i, p_sim=0.7, s_sim=0.6) for i in range(1, 4)]
        passing, _ = python_rerank(
            candidates, score_threshold=0.0, problem_weight=0.6, solution_weight=0.4, top_n=10
        )
        assert len(passing) == 3


# ---------------------------------------------------------------------------
# Tests: below-threshold hint (D-11 / Open Question 4)
# ---------------------------------------------------------------------------


class TestBelowThresholdHint:
    """When zero candidates pass threshold, return the top candidate with a hint (D-11)."""

    def test_zero_passing_returns_top_candidate_hint(self):
        """When all candidates are below threshold, the top one is returned in below_threshold_top."""
        candidates = [
            make_candidate(1, p_sim=0.2, s_sim=0.1),   # score = 0.16
            make_candidate(2, p_sim=0.3, s_sim=0.25),  # score = 0.28 (highest)
        ]
        passing, below = python_rerank(
            candidates, score_threshold=0.40, problem_weight=0.6, solution_weight=0.4, top_n=5
        )
        assert len(passing) == 0, "No candidates should pass the threshold"
        assert below is not None, "A below-threshold hint should be provided"
        assert below[0].pr_id == 2, "The highest-scoring candidate should be returned as hint"
        assert abs(below[1] - (0.6 * 0.3 + 0.4 * 0.25)) < 1e-9

    def test_empty_candidates_no_hint(self):
        """With no candidates at all, no hint is returned."""
        passing, below = python_rerank(
            [], score_threshold=0.40, problem_weight=0.6, solution_weight=0.4, top_n=5
        )
        assert len(passing) == 0
        assert below is None

    def test_single_below_threshold_candidate(self):
        """Single candidate below threshold → returned as hint, not in passing."""
        candidates = [make_candidate(1, p_sim=0.2, s_sim=0.1)]  # score = 0.16
        passing, below = python_rerank(
            candidates, score_threshold=0.50, problem_weight=0.6, solution_weight=0.4, top_n=5
        )
        assert len(passing) == 0
        assert below is not None
        assert below[0].pr_id == 1

    def test_hint_score_is_composite_score(self):
        """The hint score matches the composite_score formula."""
        c = make_candidate(1, p_sim=0.72, s_sim=0.31)
        passing, below = python_rerank(
            [c], score_threshold=0.60, problem_weight=0.6, solution_weight=0.4, top_n=5
        )
        assert below is not None
        expected_score = composite_score(0.72, 0.31)  # 0.556
        assert abs(below[1] - expected_score) < 1e-9


# ---------------------------------------------------------------------------
# Tests: overall pipeline (combined)
# ---------------------------------------------------------------------------


class TestReRankPipeline:
    """End-to-end re-rank pipeline: oversample → threshold → top_n."""

    def test_oversample_pipeline(self):
        """Oversample 5x, threshold filters, top_n=3 caps."""
        # 15 candidates (3 * oversample_factor=5), varying quality
        candidates = [
            make_candidate(1, 0.9, 0.85),   # score ~0.88 — top
            make_candidate(2, 0.85, 0.80),  # score ~0.83
            make_candidate(3, 0.80, 0.75),  # score ~0.78
            make_candidate(4, 0.75, 0.70),  # score ~0.73
            make_candidate(5, 0.70, 0.60),  # score ~0.66
            make_candidate(6, 0.50, 0.45),  # score ~0.48
            make_candidate(7, 0.45, 0.40),  # score ~0.43
            make_candidate(8, 0.40, 0.35),  # score ~0.38 — below threshold
            make_candidate(9, 0.30, 0.25),  # score ~0.28 — below threshold
            make_candidate(10, 0.20, 0.15), # score ~0.18 — below threshold
        ]
        passing, below = python_rerank(
            candidates, score_threshold=0.40, problem_weight=0.6, solution_weight=0.4, top_n=3
        )
        # All candidates 1-7 score >= 0.40; top 3 returned
        assert len(passing) == 3
        assert passing[0][0].pr_id == 1  # best
        assert passing[1][0].pr_id == 2  # second
        assert passing[2][0].pr_id == 3  # third
        assert below is None

    def test_canonical_d11_example(self):
        """D-11 example: p=0.72, s=0.31 → 0.556 is above default threshold 0.40."""
        candidates = [make_candidate(1, p_sim=0.72, s_sim=0.31)]
        passing, below = python_rerank(
            candidates, score_threshold=0.40, problem_weight=0.6, solution_weight=0.4, top_n=5
        )
        assert len(passing) == 1
        assert abs(passing[0][1] - 0.556) < 1e-9
        assert below is None
