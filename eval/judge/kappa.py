"""
eval/judge/kappa.py -- Pure stdlib Cohen's kappa for judge calibration.

Computes binary-collapsed Cohen's kappa (judge-vs-human) over a list of
(judge_grade, human_grade) pairs.

3-grade scale: irrelevant | related | direct-precedent
Binary collapse (only for kappa): related + direct-precedent -> relevant
                                   irrelevant                 -> irrelevant

Formula: k = (p_o - p_e) / (1 - p_e)
  p_o = observed agreement proportion
  p_e = chance agreement proportion (from marginals)

~10 lines core logic, no scipy, no numpy -- pure stdlib (collections.Counter).

Used by eval/judge/judge.py to gate the Sonnet->Opus escalation ladder.
"""

from __future__ import annotations

import collections


_RELEVANT = frozenset(("related", "direct-precedent", "relevant"))


def _binary_collapse(grade: str) -> str:
    """Collapse 3-grade (or already-binary) to binary.

    3-grade: related/direct-precedent -> relevant; irrelevant stays irrelevant.
    Already-binary: relevant stays relevant; irrelevant stays irrelevant.
    """
    return "relevant" if grade in _RELEVANT else "irrelevant"


def cohens_kappa(pairs: list[tuple[str, str]]) -> float:
    """
    Compute Cohen's kappa over (judge_grade, human_grade) pairs.

    Input grades may be raw 3-grade (irrelevant/related/direct-precedent)
    or already binary (irrelevant/relevant).  Binary collapse is applied
    internally before computing kappa so the caller need not pre-collapse.

    Args:
        pairs: List of (judge_grade, human_grade) tuples.

    Returns:
        Cohen's kappa as a float in [-1, 1].
        Returns 1.0 when p_e == 1.0 (trivial perfect-agreement edge case).

    Examples:
        >>> cohens_kappa([("relevant", "relevant")] * 10)
        1.0
        >>> cohens_kappa([("relevant", "irrelevant")] * 10)
        0.0
    """
    if not pairs:
        raise ValueError("pairs must be non-empty")

    n = len(pairs)

    # Binary-collapse all pairs
    collapsed = [(_binary_collapse(pair[0]), _binary_collapse(pair[1])) for pair in pairs]

    # Count agreements and marginals directly
    agree = 0
    judge_rel = 0
    human_rel = 0
    for pair in collapsed:
        jg = pair[0]
        hg = pair[1]
        if jg == hg:
            agree += 1
        if jg == "relevant":
            judge_rel += 1
        if hg == "relevant":
            human_rel += 1

    p_o = agree / n
    p_j = judge_rel / n
    p_h = human_rel / n

    # Expected agreement under independence
    p_e = p_j * p_h + (1.0 - p_j) * (1.0 - p_h)

    # Edge case: all in one category -> perfect chance agreement
    if p_e >= 1.0:
        return 1.0

    return (p_o - p_e) / (1.0 - p_e)
