"""
senrah.mcp.formatting — Pure formatting/rendering layer for search_prs_v1 output.

Provides:
- fmt_files_mcp: Cap files list at 6 and return omitted count (MCP-02 / D-02).
- fmt_diff_excerpt_mcp: Head-truncate diff to configurable character limit + marker (D-03).
- build_envelope: Convert list[SearchResult] → SearchResponseV1 with all field derivations
  (pr_link, merged_at ISO conversion, debug gating, files cap, diff excerpt).
- render_text_response: Human-readable text block for the MCP content list (D-01 / D-02).

Design principles:
- NO SQL, NO DB access, NO print() calls — pure rendering/transformation only.
- score_to_confidence_label is imported from schema (D-01 single source of truth).
- merged_at is converted to ISO string here; PRResultV1 stays typed as Optional[str].
- p_sim/s_sim are gated on debug flag (MCP-02); absent when debug=False.
- pr_link derived as https://github.com/{repo_name}/pull/{number} (D-01).

Security:
- T-02-02: Type safety enforced by Pydantic models (schema.py).
- No stdout output from this module — callers use the returned strings.
"""

from __future__ import annotations

from typing import Optional

from senrah.db.repos.skill import SearchResult
from senrah.mcp.schema import (
    BelowThresholdV1,
    PRResultV1,
    SearchResponseV1,
    score_to_confidence_label,
)

# Maximum files in the MCP output files list (MCP-02 spec: "max 6 + +K more").
# Note: the CLI uses 5 (_MAX_FILES_DISPLAYED) — MCP uses 6 per the spec.
_MCP_MAX_FILES = 6

# Placeholder returned when diff is empty or unavailable.
_EMPTY_DIFF_PLACEHOLDER = "(no diff available)"

# Truncation marker appended when diff exceeds the character limit (D-03).
_TRUNCATION_MARKER = "\n[... truncated ...]"


def fmt_files_mcp(files: list[str]) -> tuple[list[str], int]:
    """Cap the files list at 6 and return (visible_files, omitted_count).

    MCP-02 specifies "files (max 6 + +K more)". The structured field carries
    the raw capped list; the omitted count is a separate integer field.
    The "+K more" string is for text rendering only (not the structured field).

    Args:
        files: Full list of changed file paths.

    Returns:
        Tuple of (list of up to 6 file paths, count of omitted files).

    Examples:
        >>> fmt_files_mcp([])
        ([], 0)
        >>> files, omitted = fmt_files_mcp(["a.py"] * 9)
        >>> len(files), omitted
        (6, 3)
    """
    visible = files[:_MCP_MAX_FILES]
    omitted = max(0, len(files) - _MCP_MAX_FILES)
    return visible, omitted


def fmt_diff_excerpt_mcp(diff: str, limit: int) -> str:
    """Head-truncate diff to `limit` characters, appending a marker if truncated (D-03).

    This is the MCP-layer analog of the CLI's _fmt_diff_excerpt, but uses the
    configurable OUTPUT_DIFF_LIMIT (in characters) instead of the CLI's hard-coded
    500-char limit.

    An empty diff returns a clear placeholder string (not an empty string, so
    the agent's diff_excerpt field always carries meaningful content).

    Note: Head-truncation is accepted conscious debt (D-03). On multi-file PRs it
    deterministically shows predominantly the first file(s). Per-file-balanced
    excerpting is a known TODO with real impact on agent usefulness.

    Args:
        diff: Raw diff string from pull_requests.diff.
        limit: Maximum number of characters to include before the truncation marker.

    Returns:
        Diff excerpt string (at most limit chars + optional marker), or a placeholder
        for empty diffs.

    Examples:
        >>> "truncated" not in fmt_diff_excerpt_mcp("short", 100)
        True
        >>> "truncated" in fmt_diff_excerpt_mcp("x" * 200, 100)
        True
    """
    if not diff:
        return _EMPTY_DIFF_PLACEHOLDER

    if len(diff) <= limit:
        return diff

    return diff[:limit] + _TRUNCATION_MARKER


def build_envelope(
    results: list[SearchResult],
    best: Optional[SearchResult],
    debug: bool,
    output_diff_limit: int,
) -> SearchResponseV1:
    """Convert search results into a validated SearchResponseV1 envelope (D-02).

    Status logic:
    - results non-empty → status="ok", results populated, best_below_threshold=None.
    - results empty + best is not None → status="no_matches_above_threshold",
      results=[], best_below_threshold populated.
    - results empty + best is None → status="no_matches_above_threshold",
      results=[], best_below_threshold=None (index is empty).

    Field derivations:
    - pr_link = f"https://github.com/{repo_name}/pull/{number}" (D-01)
    - merged_at = result.merged_at.isoformat() if result.merged_at else None (Pitfall 4)
    - p_sim/s_sim present only when debug=True (MCP-02 debug-gating)
    - files = fmt_files_mcp(result.files_changed)[0] (capped at 6)
    - files_truncated = fmt_files_mcp(result.files_changed)[1]
    - diff_excerpt = fmt_diff_excerpt_mcp(result.diff, output_diff_limit)

    Args:
        results: Above-threshold SearchResult list from SkillRepo.search.
        best: Best below-threshold candidate (or None) for the no-matches case.
        debug: When True, expose p_sim/s_sim on each PRResultV1 (MCP-02).
        output_diff_limit: Maximum characters for diff_excerpt (D-04 / McpConfig).

    Returns:
        Validated SearchResponseV1 instance ready for model_dump(mode="json").
    """
    if results:
        pr_results = [_build_pr_result(r, debug=debug, output_diff_limit=output_diff_limit) for r in results]
        return SearchResponseV1(status="ok", results=pr_results)

    # No matches above threshold (D-02)
    below = None
    if best is not None:
        below = BelowThresholdV1(
            pr_number=best.number,
            title=best.title,
            score=best.score,
            repo=best.repo_name,
            pr_link=f"https://github.com/{best.repo_name}/pull/{best.number}",
        )

    return SearchResponseV1(
        status="no_matches_above_threshold",
        results=[],
        best_below_threshold=below,
    )


def _build_pr_result(result: SearchResult, debug: bool, output_diff_limit: int) -> PRResultV1:
    """Build a single PRResultV1 from a SearchResult.

    Internal helper for build_envelope. Handles all field derivations in one place.
    """
    files, files_truncated = fmt_files_mcp(result.files_changed)

    merged_at_str: Optional[str] = None
    if result.merged_at is not None:
        merged_at_str = result.merged_at.isoformat()

    return PRResultV1(
        pr_number=result.number,
        title=result.title,
        score=result.score,
        repo=result.repo_name,
        author=result.author,
        merged_at=merged_at_str,
        linked_issue=result.linked_issue,
        files=files,
        files_truncated=files_truncated,
        pr_link=f"https://github.com/{result.repo_name}/pull/{result.number}",
        diff_excerpt=fmt_diff_excerpt_mcp(result.diff, output_diff_limit),
        p_sim=result.problem_sim if debug else None,
        s_sim=result.solution_sim if debug else None,
    )


def render_text_response(envelope: SearchResponseV1, debug: bool = False) -> str:
    """Render a SearchResponseV1 envelope as a human-readable plain-text string.

    For status="ok": Each result gets a block with its calibrated confidence label
    (via score_to_confidence_label — D-01 single source of truth), PR metadata,
    and a diff excerpt. No fenced code blocks — plain text only.

    For status="no_matches_above_threshold": Explicitly states no precedent was found
    above the threshold. Frames best_below_threshold as a WEAK LEAD (not a precedent)
    and conveys that absence is an expected, common signal on novel tasks (D-02).

    Args:
        envelope: Validated SearchResponseV1 from build_envelope.
        debug: When True, include p_sim/s_sim values in the text (MCP-02).

    Returns:
        Human-readable plain-text string suitable for MCP TextContent.
    """
    if envelope.status == "ok":
        return _render_ok(envelope, debug=debug)
    return _render_no_matches(envelope, debug=debug)


def _render_ok(envelope: SearchResponseV1, debug: bool) -> str:
    """Render the ok-status text block."""
    lines: list[str] = []
    total = len(envelope.results)

    for i, result in enumerate(envelope.results, start=1):
        confidence = score_to_confidence_label(result.score)

        lines.append(f"--- Result {i}/{total} ---")
        lines.append(f"PR #{result.pr_number}: {result.title}")
        lines.append(f"Score: {confidence}")
        lines.append(f"Repo: {result.repo}")
        lines.append(f"Author: {result.author}")
        if result.merged_at:
            lines.append(f"Merged: {result.merged_at}")
        if result.linked_issue:
            lines.append(f"Issue: {result.linked_issue}")

        # Files summary
        if result.files_truncated > 0:
            lines.append(
                f"Files: {', '.join(result.files)} (+{result.files_truncated} more)"
            )
        else:
            lines.append(f"Files: {', '.join(result.files) if result.files else '(none)'}")

        lines.append(f"PR: {result.pr_link}")

        if debug and result.p_sim is not None and result.s_sim is not None:
            lines.append(f"[debug] p_sim={result.p_sim:.4f}  s_sim={result.s_sim:.4f}")

        lines.append("")
        lines.append("Diff excerpt:")
        lines.append(result.diff_excerpt)
        lines.append("")

    return "\n".join(lines).rstrip()


def _render_no_matches(envelope: SearchResponseV1, debug: bool) -> str:
    """Render the no_matches_above_threshold text block.

    Per D-02: must explicitly state no precedent above threshold, frame
    best_below_threshold as a weak lead, and convey this is expected/common
    on novel tasks (absence of precedent is itself a useful signal).
    """
    lines: list[str] = [
        "No precedent found above the score threshold for this query.",
        "",
        "This is an expected, common signal on novel tasks — absence of a "
        "strong precedent means this problem may be genuinely new in this "
        "codebase, not that the search failed.",
    ]

    if envelope.best_below_threshold is not None:
        below = envelope.best_below_threshold
        confidence = score_to_confidence_label(below.score)
        lines.extend([
            "",
            "Closest near-miss (WEAK LEAD — not a precedent, use with caution):",
            f"  PR #{below.pr_number}: {below.title}",
            f"  Score: {confidence}",
            f"  Repo: {below.repo}",
            f"  PR: {below.pr_link}",
            "",
            "The score is below the configured threshold, which means semantic "
            "similarity to this query is low. Consider whether it is relevant "
            "before relying on it.",
        ])
    else:
        lines.extend([
            "",
            "No near-miss candidates found either (index may be empty or no PRs"
            " have been indexed yet).",
        ])

    return "\n".join(lines)
