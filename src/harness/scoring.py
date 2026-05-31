"""
harness.scoring — Pure composite score function.

This module is intentionally dependency-free: no DB, no I/O, no external packages.
It is shared by the Indexer (Phase 1), the CLI search command, and the MCP Server
(Phase 2) — all three must be able to import it without side effects.

Decision D-09: score = problem_weight × problem_sim + solution_weight × solution_sim
Default weights 0.6/0.4 from config (SearchConfig.problem_weight / solution_weight).

Security: T-04-SC — no new packages; pure arithmetic only.
"""

from __future__ import annotations


def composite_score(
    problem_sim: float,
    solution_sim: float,
    problem_weight: float = 0.6,
    solution_weight: float = 0.4,
) -> float:
    """Compute the composite similarity score for a search result.

    Score = problem_weight × problem_sim + solution_weight × solution_sim

    Both similarity values are expected to be in [0.0, 1.0] (cosine similarities
    computed as 1 - cosine_distance from pgvector <=> operator).

    Default weights (0.6/0.4) reflect that the problem description (title + PR body)
    carries more semantic signal for "what kind of problem was solved" than the raw
    diff; the diff adds complementary signal about the solution approach.

    Weights are configurable via SearchConfig in harness.yaml (D-09).

    Args:
        problem_sim: Cosine similarity between query and problem embedding (1 - distance).
        solution_sim: Cosine similarity between query and solution embedding (1 - distance).
        problem_weight: Weight for the problem similarity component. Default 0.6.
        solution_weight: Weight for the solution similarity component. Default 0.4.

    Returns:
        Weighted composite score as a float.

    Examples:
        >>> composite_score(0.72, 0.31)        # D-11 example → 0.556
        0.556
        >>> composite_score(1.0, 1.0)           # Perfect match → 1.0
        1.0
        >>> composite_score(1.0, 0.0)           # Problem-only match → 0.6
        0.6
    """
    return problem_weight * problem_sim + solution_weight * solution_sim
