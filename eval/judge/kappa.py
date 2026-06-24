"""
eval/judge/kappa.py -- Pure stdlib Cohen's kappa for judge calibration.

Computes multi-category (no-collapse) Cohen's kappa (judge-vs-human) over a
list of (judge_grade, human_grade) pairs on the raw 3-grade scale:

    irrelevant | related | direct-precedent

Earlier revisions binary-collapsed {related, direct-precedent} -> relevant
before computing kappa. That collapse is degenerate for this judge: the LLM
never emits "irrelevant", so its collapsed column is constant and kappa is
forced to 0 regardless of agreement (the Cohen's-kappa prevalence paradox).
The judge is therefore calibrated on the full 3-grade scale instead.

Formula: k = (p_o - p_e) / (1 - p_e)
  p_o = observed agreement proportion
  p_e = chance agreement proportion summed over every category's marginals

~15 lines core logic, no scipy, no numpy -- pure stdlib (collections.Counter).

Used by eval/judge/judge.py to gate the Sonnet->Opus escalation ladder.
"""

from __future__ import annotations

import collections


def cohens_kappa(pairs: list[tuple[str, str]]) -> float:
    """
    Compute multi-category Cohen's kappa over (judge_grade, human_grade) pairs.

    Grades are compared on their raw scale with NO collapse, so related and
    direct-precedent are distinct categories.

    Args:
        pairs: List of (judge_grade, human_grade) tuples.

    Returns:
        Cohen's kappa as a float in [-1, 1].
        Returns 1.0 when p_e == 1.0 (trivial perfect-agreement edge case, e.g.
        every pair is the same single category).

    Examples:
        >>> cohens_kappa([("related", "related")] * 10)
        1.0
        >>> cohens_kappa([("direct-precedent", "irrelevant")] * 10)
        0.0
    """
    if not pairs:
        raise ValueError("pairs must be non-empty")

    n = len(pairs)
    agree = sum(1 for jg, hg in pairs if jg == hg)
    judge_counts = collections.Counter(jg for jg, _ in pairs)
    human_counts = collections.Counter(hg for _, hg in pairs)

    p_o = agree / n

    # Expected agreement under independence: sum over every observed category of
    # P(judge=c) * P(human=c).
    categories = set(judge_counts) | set(human_counts)
    p_e = sum((judge_counts[c] / n) * (human_counts[c] / n) for c in categories)

    # Edge case: all mass in one shared category -> perfect chance agreement.
    if p_e >= 1.0:
        return 1.0

    return (p_o - p_e) / (1.0 - p_e)
