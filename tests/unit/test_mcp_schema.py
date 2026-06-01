"""
tests/unit/test_mcp_schema.py — Unit tests for MCP _v1 schema and formatting helpers.

Tests cover:
- score_to_confidence_label: band monotonicity, score appears in label
- PRResultV1, BelowThresholdV1, SearchResponseV1 model validation
- SearchResponseV1.model_json_schema() succeeds (outputSchema source)
- model_dump(mode="json") is JSON-serializable (no datetime objects)
- fmt_files_mcp: cap at 6 + omitted count
- fmt_diff_excerpt_mcp: head-truncation + marker
- build_envelope: ok vs no_matches branches, pr_link derivation, debug gating
- render_text_response: ok text has confidence signal; no-matches text frames weak lead
"""
# Note: formatting tests (TestFmtFilesMcp, TestFmtDiffExcerptMcp, TestBuildEnvelope,
# TestRenderTextResponse) use imports from harness.mcp.formatting which is implemented
# in Task 3 of this plan.

from __future__ import annotations

import json
from datetime import datetime

import pytest

from harness.mcp.schema import (
    BelowThresholdV1,
    PRResultV1,
    SearchResponseV1,
    score_to_confidence_label,
)


# ---------------------------------------------------------------------------
# score_to_confidence_label — D-01 single source of truth
# ---------------------------------------------------------------------------


class TestScoreToConfidenceLabel:
    def test_low_score_contains_score_value(self):
        label = score_to_confidence_label(0.30)
        # The numeric score must appear in the returned string (D-01)
        assert "0.30" in label or "0.3" in label

    def test_mid_score_contains_score_value(self):
        label = score_to_confidence_label(0.55)
        assert "0.55" in label

    def test_high_score_contains_score_value(self):
        label = score_to_confidence_label(0.72)
        assert "0.72" in label or "0.7" in label

    def test_monotonic_bands_low_lt_mid(self):
        # Different bands must produce different labels
        low_label = score_to_confidence_label(0.30)
        mid_label = score_to_confidence_label(0.55)
        assert low_label != mid_label

    def test_monotonic_bands_mid_lt_high(self):
        mid_label = score_to_confidence_label(0.55)
        high_label = score_to_confidence_label(0.72)
        assert mid_label != high_label

    def test_low_ne_high(self):
        assert score_to_confidence_label(0.30) != score_to_confidence_label(0.72)

    def test_pure_same_input_same_output(self):
        # Pure function: same input → same output
        assert score_to_confidence_label(0.50) == score_to_confidence_label(0.50)

    def test_low_score_has_weak_qualifier(self):
        label = score_to_confidence_label(0.30)
        assert any(word in label.lower() for word in ("weak", "low", "poor"))

    def test_high_score_has_strong_qualifier(self):
        label = score_to_confidence_label(0.72)
        # High score should mention practical ceiling or strength
        assert any(
            word in label.lower()
            for word in ("strong", "high", "near", "ceiling", "practical")
        )


# ---------------------------------------------------------------------------
# PRResultV1 model validation
# ---------------------------------------------------------------------------


class TestPRResultV1:
    def _make_result(self, **overrides):
        defaults = dict(
            pr_number=42,
            title="Add async retry logic",
            score=0.65,
            repo="owner/repo",
            author="alice",
            merged_at="2024-01-15T12:00:00",
            linked_issue=None,
            files=["src/retry.py"],
            files_truncated=0,
            pr_link="https://github.com/owner/repo/pull/42",
            diff_excerpt="+ def retry():\n+    pass",
        )
        defaults.update(overrides)
        return PRResultV1(**defaults)

    def test_valid_construction(self):
        result = self._make_result()
        assert result.pr_number == 42
        assert result.score == 0.65

    def test_p_sim_s_sim_default_none(self):
        result = self._make_result()
        assert result.p_sim is None
        assert result.s_sim is None

    def test_p_sim_s_sim_set_when_debug(self):
        result = self._make_result(p_sim=0.72, s_sim=0.58)
        assert result.p_sim == 0.72
        assert result.s_sim == 0.58

    def test_merged_at_optional_none(self):
        result = self._make_result(merged_at=None)
        assert result.merged_at is None

    def test_merged_at_is_string_not_datetime(self):
        result = self._make_result(merged_at="2024-01-15T12:00:00")
        assert isinstance(result.merged_at, str)

    def test_model_dump_json_serializable(self):
        result = self._make_result()
        dumped = result.model_dump(mode="json")
        # Should not raise
        serialized = json.dumps(dumped)
        assert serialized  # non-empty


# ---------------------------------------------------------------------------
# BelowThresholdV1 model validation
# ---------------------------------------------------------------------------


class TestBelowThresholdV1:
    def _make_below(self, **overrides):
        defaults = dict(
            pr_number=10,
            title="Refactor cache layer",
            score=0.22,
            repo="owner/repo",
            pr_link="https://github.com/owner/repo/pull/10",
        )
        defaults.update(overrides)
        return BelowThresholdV1(**defaults)

    def test_valid_construction(self):
        below = self._make_below()
        assert below.pr_number == 10
        assert below.score == 0.22

    def test_model_dump_json_serializable(self):
        below = self._make_below()
        dumped = below.model_dump(mode="json")
        serialized = json.dumps(dumped)
        assert serialized


# ---------------------------------------------------------------------------
# SearchResponseV1 model validation
# ---------------------------------------------------------------------------


class TestSearchResponseV1:
    def test_ok_status_empty_results(self):
        r = SearchResponseV1(status="ok", results=[])
        assert r.status == "ok"
        assert r.results == []
        assert r.best_below_threshold is None

    def test_ok_status_with_results(self):
        pr = PRResultV1(
            pr_number=1,
            title="T",
            score=0.7,
            repo="o/r",
            author="bob",
            merged_at=None,
            linked_issue=None,
            files=[],
            files_truncated=0,
            pr_link="https://github.com/o/r/pull/1",
            diff_excerpt="",
        )
        r = SearchResponseV1(status="ok", results=[pr])
        assert len(r.results) == 1

    def test_no_matches_status(self):
        below = BelowThresholdV1(
            pr_number=5,
            title="Near miss",
            score=0.15,
            repo="o/r",
            pr_link="https://github.com/o/r/pull/5",
        )
        r = SearchResponseV1(
            status="no_matches_above_threshold",
            results=[],
            best_below_threshold=below,
        )
        assert r.status == "no_matches_above_threshold"
        assert r.best_below_threshold is not None
        assert r.best_below_threshold.pr_number == 5

    def test_model_json_schema_succeeds(self):
        schema = SearchResponseV1.model_json_schema()
        assert isinstance(schema, dict)
        assert "properties" in schema or "$defs" in schema or "title" in schema

    def test_model_dump_mode_json_serializable(self):
        r = SearchResponseV1(status="ok", results=[])
        dumped = r.model_dump(mode="json")
        serialized = json.dumps(dumped)
        assert '"status"' in serialized

    def test_populated_response_json_serializable(self):
        pr = PRResultV1(
            pr_number=99,
            title="Big PR",
            score=0.80,
            repo="org/proj",
            author="carol",
            merged_at="2025-03-20T08:30:00",
            linked_issue="https://github.com/org/proj/issues/200",
            files=["a.py", "b.py"],
            files_truncated=3,
            pr_link="https://github.com/org/proj/pull/99",
            diff_excerpt="diff text here",
            p_sim=0.85,
            s_sim=0.75,
        )
        r = SearchResponseV1(status="ok", results=[pr])
        dumped = r.model_dump(mode="json")
        serialized = json.dumps(dumped)
        parsed = json.loads(serialized)
        assert parsed["results"][0]["pr_number"] == 99
        # merged_at must be a string (Pitfall 4 — no datetime objects in JSON)
        assert isinstance(parsed["results"][0]["merged_at"], str)


# ---------------------------------------------------------------------------
# Formatting helpers — fmt_files_mcp, fmt_diff_excerpt_mcp
# ---------------------------------------------------------------------------

from harness.mcp.formatting import (  # noqa: E402 — after schema imports
    build_envelope,
    fmt_diff_excerpt_mcp,
    fmt_files_mcp,
    render_text_response,
)


class TestFmtFilesMcp:
    def test_empty_files_returns_empty_list_and_zero(self):
        files, omitted = fmt_files_mcp([])
        assert files == []
        assert omitted == 0

    def test_six_files_no_omission(self):
        inputs = [f"file{i}.py" for i in range(6)]
        files, omitted = fmt_files_mcp(inputs)
        assert files == inputs
        assert omitted == 0

    def test_seven_files_caps_at_six(self):
        inputs = [f"file{i}.py" for i in range(7)]
        files, omitted = fmt_files_mcp(inputs)
        assert len(files) == 6
        assert omitted == 1

    def test_nine_files_omits_three(self):
        inputs = [str(i) for i in range(9)]
        files, omitted = fmt_files_mcp(inputs)
        assert len(files) == 6
        assert omitted == 3

    def test_returns_first_six(self):
        inputs = [f"file{i}.py" for i in range(9)]
        files, _ = fmt_files_mcp(inputs)
        assert files == inputs[:6]

    def test_exactly_six_returns_correct(self):
        # Regression: exactly 6 should have 0 omitted
        inputs = list("abcdef")
        files, omitted = fmt_files_mcp(inputs)
        assert len(files) == 6
        assert omitted == 0


class TestFmtDiffExcerptMcp:
    def test_short_diff_unchanged(self):
        diff = "some short diff"
        result = fmt_diff_excerpt_mcp(diff, limit=100)
        assert result == diff

    def test_exact_limit_unchanged(self):
        diff = "x" * 100
        result = fmt_diff_excerpt_mcp(diff, limit=100)
        assert "truncated" not in result

    def test_over_limit_truncated(self):
        diff = "x" * 200
        result = fmt_diff_excerpt_mcp(diff, limit=100)
        assert "truncated" in result

    def test_truncated_starts_with_first_limit_chars(self):
        diff = "A" * 50 + "B" * 200
        result = fmt_diff_excerpt_mcp(diff, limit=50)
        assert result.startswith("A" * 50)

    def test_empty_diff_placeholder(self):
        result = fmt_diff_excerpt_mcp("", limit=2000)
        # Empty diff should return a clear placeholder, not empty string
        assert result  # non-empty
        assert len(result) > 0

    def test_marker_present_when_truncated(self):
        diff = "y" * 1000
        result = fmt_diff_excerpt_mcp(diff, limit=10)
        assert "[..." in result or "truncated" in result.lower()


# ---------------------------------------------------------------------------
# build_envelope — ok vs no_matches, pr_link derivation, debug gating
# ---------------------------------------------------------------------------

from datetime import datetime  # noqa: E402

from harness.db.repos.skill import SearchResult  # noqa: E402


def _make_search_result(
    pr_id: int = 1,
    number: int = 42,
    title: str = "Test PR",
    repo_name: str = "owner/repo",
    author: str = "alice",
    merged_at: datetime | None = None,
    linked_issue: str | None = None,
    files_changed: list[str] | None = None,
    diff: str = "diff content",
    problem_sim: float = 0.70,
    solution_sim: float = 0.60,
    score: float = 0.66,
) -> SearchResult:
    return SearchResult(
        pr_id=pr_id,
        number=number,
        title=title,
        repo_name=repo_name,
        author=author,
        merged_at=merged_at,
        linked_issue=linked_issue,
        files_changed=files_changed or ["src/main.py"],
        diff=diff,
        problem_sim=problem_sim,
        solution_sim=solution_sim,
        score=score,
    )


class TestBuildEnvelope:
    def test_ok_status_when_results(self):
        result = _make_search_result()
        envelope = build_envelope([result], best=None, debug=False, output_diff_limit=2000)
        assert envelope.status == "ok"
        assert len(envelope.results) == 1
        assert envelope.best_below_threshold is None

    def test_no_matches_status_when_empty(self):
        best = _make_search_result(score=0.20)
        envelope = build_envelope([], best=best, debug=False, output_diff_limit=2000)
        assert envelope.status == "no_matches_above_threshold"
        assert envelope.results == []
        assert envelope.best_below_threshold is not None

    def test_no_matches_best_below_threshold_pr_number(self):
        # best_below_threshold.pr_number ← SearchResult.number (D-02)
        best = _make_search_result(number=77, score=0.15)
        envelope = build_envelope([], best=best, debug=False, output_diff_limit=2000)
        assert envelope.best_below_threshold.pr_number == 77

    def test_pr_link_derivation(self):
        result = _make_search_result(number=99, repo_name="myorg/myrepo")
        envelope = build_envelope([result], best=None, debug=False, output_diff_limit=2000)
        assert envelope.results[0].pr_link == "https://github.com/myorg/myrepo/pull/99"

    def test_merged_at_iso_string(self):
        dt = datetime(2024, 6, 1, 12, 30, 0)
        result = _make_search_result(merged_at=dt)
        envelope = build_envelope([result], best=None, debug=False, output_diff_limit=2000)
        assert envelope.results[0].merged_at == "2024-06-01T12:30:00"

    def test_merged_at_none_when_none(self):
        result = _make_search_result(merged_at=None)
        envelope = build_envelope([result], best=None, debug=False, output_diff_limit=2000)
        assert envelope.results[0].merged_at is None

    def test_debug_false_hides_p_sim_s_sim(self):
        result = _make_search_result(problem_sim=0.80, solution_sim=0.60)
        envelope = build_envelope([result], best=None, debug=False, output_diff_limit=2000)
        assert envelope.results[0].p_sim is None
        assert envelope.results[0].s_sim is None

    def test_debug_true_exposes_p_sim_s_sim(self):
        result = _make_search_result(problem_sim=0.80, solution_sim=0.60)
        envelope = build_envelope([result], best=None, debug=True, output_diff_limit=2000)
        assert envelope.results[0].p_sim == pytest.approx(0.80, abs=1e-6)
        assert envelope.results[0].s_sim == pytest.approx(0.60, abs=1e-6)

    def test_files_capped_at_six(self):
        files = [f"f{i}.py" for i in range(9)]
        result = _make_search_result(files_changed=files)
        envelope = build_envelope([result], best=None, debug=False, output_diff_limit=2000)
        assert len(envelope.results[0].files) == 6
        assert envelope.results[0].files_truncated == 3

    def test_diff_excerpt_truncated(self):
        result = _make_search_result(diff="x" * 5000)
        envelope = build_envelope([result], best=None, debug=False, output_diff_limit=100)
        assert "truncated" in envelope.results[0].diff_excerpt

    def test_empty_results_no_best_none_status(self):
        envelope = build_envelope([], best=None, debug=False, output_diff_limit=2000)
        assert envelope.status == "no_matches_above_threshold"
        assert envelope.best_below_threshold is None


# ---------------------------------------------------------------------------
# render_text_response — ok text + no-matches text framing
# ---------------------------------------------------------------------------


class TestRenderTextResponse:
    def test_ok_text_contains_confidence_label(self):
        result = _make_search_result(score=0.72)
        envelope = build_envelope([result], best=None, debug=False, output_diff_limit=2000)
        text = render_text_response(envelope, debug=False)
        # Text must contain a confidence signal (D-01)
        assert "0.72" in text

    def test_ok_text_contains_pr_number(self):
        result = _make_search_result(number=123)
        envelope = build_envelope([result], best=None, debug=False, output_diff_limit=2000)
        text = render_text_response(envelope, debug=False)
        assert "123" in text

    def test_no_matches_text_states_no_precedent(self):
        best = _make_search_result(score=0.20)
        envelope = build_envelope([], best=best, debug=False, output_diff_limit=2000)
        text = render_text_response(envelope, debug=False)
        # Must explicitly state no precedent above threshold (D-02)
        assert "threshold" in text.lower() or "no" in text.lower()

    def test_no_matches_text_frames_weak_lead(self):
        best = _make_search_result(score=0.20)
        envelope = build_envelope([], best=best, debug=False, output_diff_limit=2000)
        text = render_text_response(envelope, debug=False)
        # Text must frame the near-miss as a weak lead, not a precedent (D-02)
        assert any(
            phrase in text.lower()
            for phrase in ("weak", "lead", "near-miss", "near miss", "below threshold")
        )

    def test_no_matches_text_conveys_expected_signal(self):
        best = _make_search_result(score=0.20)
        envelope = build_envelope([], best=best, debug=False, output_diff_limit=2000)
        text = render_text_response(envelope, debug=False)
        # Absence of precedent is expected/common on novel tasks (D-02)
        assert any(
            word in text.lower()
            for word in ("expected", "common", "novel", "absence", "signal", "precedent")
        )

    def test_ok_text_no_fenced_code(self):
        result = _make_search_result()
        envelope = build_envelope([result], best=None, debug=False, output_diff_limit=2000)
        text = render_text_response(envelope, debug=False)
        assert "```" not in text  # no fenced code blocks (plain text per spec)
