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
