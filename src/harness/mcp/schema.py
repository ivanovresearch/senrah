"""
harness.mcp.schema — Versioned _v1 output schema for search_prs_v1 (MCP-02).

Provides:
- PRResultV1: Per-result fields (MCP-02). p_sim/s_sim are debug-only (default None).
- BelowThresholdV1: Best sub-threshold candidate (D-02 envelope).
- SearchResponseV1: Top-level envelope with status, results, best_below_threshold.
- score_to_confidence_label: SINGLE source of truth mapping a composite score to a
  calibrated human-readable confidence string (D-01). Used by both the structured
  score field context and the text rendering layer — they must never diverge.

Design notes:
- merged_at typed as Optional[str] — callers convert datetime.isoformat() before
  building PRResultV1 (Pitfall 4: no datetime serialization in the Pydantic model).
- p_sim/s_sim are Optional[float] = None on the model; the formatting layer sets
  them only when debug=True (MCP-02 debug-gating). This keeps the schema stable
  (agents always see the same outputSchema shape) while keeping debug data out of
  normal responses.
- SearchResponseV1.model_json_schema() is the outputSchema advertised by FastMCP.
- from __future__ import annotations: defers evaluation of forward references
  (required for nested Pydantic models in Python 3.12+ with TYPE_CHECKING patterns).

Security:
- T-02-02: All wire fields are typed; merged_at as Optional[str] eliminates datetime
  serialization ambiguity.
- T-02-03: score_to_confidence_label returns only numeric score + qualifier text;
  no PII or internal details.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# score_to_confidence_label — D-01 single source of truth
# ---------------------------------------------------------------------------

# Calibration thresholds for text-embedding-3-small on code corpora.
# Practical ceiling: cosine similarity rarely exceeds ~0.80 on code queries;
# most strong matches land in 0.60–0.75 range.
_BAND_STRONG = 0.65   # >= this: strong / near practical ceiling
_BAND_MODERATE = 0.45  # >= this (and < _BAND_STRONG): moderate match
# < _BAND_MODERATE: weak match


def score_to_confidence_label(score: float) -> str:
    """Map a composite score to a calibrated human-readable confidence string.

    This is the SINGLE source of truth for the confidence signal (D-01).
    Both the structured output and the text rendering layer call this function —
    they cannot diverge because they share one implementation.

    The returned string ALWAYS includes the numeric score so agents reading the
    text block carry judgment, not a bare number. The qualifier reflects the
    practical ceiling of text-embedding-3-small on code corpora (~0.80 max).

    Args:
        score: Composite similarity score in [0.0, 1.0].

    Returns:
        Human-readable confidence label including the score value.

    Examples:
        >>> "0.30" in score_to_confidence_label(0.30) or "0.3" in score_to_confidence_label(0.30)
        True
        >>> score_to_confidence_label(0.30) != score_to_confidence_label(0.72)
        True
        >>> score_to_confidence_label(0.50) == score_to_confidence_label(0.50)
        True
    """
    rounded = f"{score:.2f}"

    if score >= _BAND_STRONG:
        return (
            f"{rounded} — strong match "
            f"(near the practical ceiling for text-embedding-3-small)"
        )
    elif score >= _BAND_MODERATE:
        return f"{rounded} — moderate match"
    else:
        return f"{rounded} — weak match"


# ---------------------------------------------------------------------------
# _v1 Pydantic models (MCP-02 contract)
# ---------------------------------------------------------------------------


class PRResultV1(BaseModel):
    """Single PR result in a successful search_prs_v1 response.

    All MCP-02 fields are present. p_sim/s_sim are debug-only — they default
    to None and are populated only when debug=True.

    merged_at is typed as Optional[str]; callers must convert datetime to
    ISO 8601 string before constructing (Pitfall 4).
    """

    pr_number: int
    title: str
    score: float
    repo: str
    author: str
    merged_at: Optional[str]
    linked_issue: Optional[str]
    files: list[str]
    files_truncated: int
    pr_link: str
    diff_excerpt: str
    # Debug-only (MCP-02): present only when debug=True, else None
    p_sim: Optional[float] = None
    s_sim: Optional[float] = None


class BelowThresholdV1(BaseModel):
    """Best sub-threshold candidate for the no_matches_above_threshold case (D-02).

    Deliberately minimal: enough for the agent to decide whether to use it.
    pr_number derives from SearchResult.number (same derivation discipline as pr_link).
    """

    pr_number: int
    title: str
    score: float
    repo: str
    pr_link: str


class SearchResponseV1(BaseModel):
    """Top-level MCP tool response envelope (D-02).

    status values:
    - "ok": results list is populated; best_below_threshold is None.
    - "no_matches_above_threshold": results is empty; best_below_threshold carries
      the single closest near-miss (may itself be None if the index is empty).

    model_json_schema() is used by FastMCP to advertise outputSchema.
    model_dump(mode="json") is JSON-serializable (no datetime objects — Pitfall 4).
    """

    status: str
    results: list[PRResultV1]
    best_below_threshold: Optional[BelowThresholdV1] = None
