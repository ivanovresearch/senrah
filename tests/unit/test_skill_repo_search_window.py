"""
tests/unit/test_skill_repo_search_window.py -- Unit tests for DEPTH-02 window params.

Tests the merged_before / merged_after signature contract on SkillRepo.search,
and the filter-string assembly logic via _build_window_filters.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Optional

import pytest


# ---------------------------------------------------------------------------
# Test: ceiling (merged_before) parameter signature contract
# ---------------------------------------------------------------------------


class TestSearchWindowCeiling:
    """SkillRepo.search must accept a merged_before Optional[datetime] param (DEPTH-02)."""

    def test_search_signature_has_merged_before(self):
        """SkillRepo.search signature must include merged_before with Optional[datetime] default None."""
        from senrah.db.repos.skill import SkillRepo

        sig = inspect.signature(SkillRepo.search)
        params = sig.parameters
        assert "merged_before" in params, (
            "SkillRepo.search is missing the merged_before parameter (DEPTH-02)"
        )
        default = params["merged_before"].default
        assert default is None, (
            f"merged_before must default to None, got {default!r}"
        )

    def test_search_signature_has_merged_after(self):
        """SkillRepo.search signature must include merged_after with Optional[datetime] default None."""
        from senrah.db.repos.skill import SkillRepo

        sig = inspect.signature(SkillRepo.search)
        params = sig.parameters
        assert "merged_after" in params, (
            "SkillRepo.search is missing the merged_after parameter (DEPTH-02)"
        )
        default = params["merged_after"].default
        assert default is None, (
            f"merged_after must default to None, got {default!r}"
        )

    def test_before_filter_string_when_set(self):
        """_build_window_filters returns correct static SQL fragment for merged_before."""
        from senrah.db.repos.skill import _build_window_filters

        T = datetime(2025, 1, 1, tzinfo=timezone.utc)
        _, before_filter, _ = _build_window_filters(None, T, None)
        assert before_filter == "AND pr.merged_at < %(merged_before)s", (
            f"Expected static SQL fragment, got: {before_filter!r}"
        )

    def test_before_filter_empty_when_none(self):
        """_build_window_filters returns empty string for before_filter when merged_before is None."""
        from senrah.db.repos.skill import _build_window_filters

        _, before_filter, _ = _build_window_filters(None, None, None)
        assert before_filter == "", (
            f"Expected empty string when merged_before=None, got: {before_filter!r}"
        )


# ---------------------------------------------------------------------------
# Test: backward compatibility when both params are None
# ---------------------------------------------------------------------------


class TestSearchWindowBackwardCompat:
    """Both params None must leave call signature backward-compatible."""

    def test_both_none_does_not_change_signature_arity(self):
        """Existing callers pass no window params; the added None-defaults must be transparent."""
        from senrah.db.repos.skill import SkillRepo

        sig = inspect.signature(SkillRepo.search)
        params = sig.parameters
        assert "merged_before" in params, "merged_before missing from SkillRepo.search (DEPTH-02)"
        assert "merged_after" in params, "merged_after missing from SkillRepo.search (DEPTH-02)"
        for name in ("merged_before", "merged_after"):
            assert params[name].default is None, (
                f"{name} must default to None so existing callers are unaffected"
            )

    def test_both_none_no_extra_fragments(self):
        """With both params None, _build_window_filters returns empty filter strings."""
        from senrah.db.repos.skill import _build_window_filters

        repo_filter, before_filter, after_filter = _build_window_filters(None, None, None)
        assert repo_filter == "", f"Expected empty repo_filter, got: {repo_filter!r}"
        assert before_filter == "", f"Expected empty before_filter, got: {before_filter!r}"
        assert after_filter == "", f"Expected empty after_filter, got: {after_filter!r}"


# ---------------------------------------------------------------------------
# Test: floor-only (merged_after without ceiling) param acceptance
# ---------------------------------------------------------------------------


class TestSearchWindowFloorOnly:
    """merged_after alone (no merged_before) must be accepted by the signature."""

    def test_merged_after_accepted_without_merged_before(self):
        """Pass merged_after=T but no merged_before; signature must accept this combination."""
        from senrah.db.repos.skill import SkillRepo

        sig = inspect.signature(SkillRepo.search)
        params = sig.parameters
        assert "merged_before" in params, "merged_before missing from SkillRepo.search (DEPTH-02)"
        assert "merged_after" in params, "merged_after missing from SkillRepo.search (DEPTH-02)"
        for name in ("merged_before", "merged_after"):
            kind = params[name].kind
            assert kind in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            ), f"{name} must be keyword-accessible"

    def test_after_filter_string(self):
        """_build_window_filters returns correct static SQL fragment for merged_after."""
        from senrah.db.repos.skill import _build_window_filters

        some_dt = datetime(2024, 6, 1, tzinfo=timezone.utc)
        _, _, after_filter = _build_window_filters(None, None, some_dt)
        assert after_filter == "AND pr.merged_at >= %(merged_after)s", (
            f"Expected static SQL fragment, got: {after_filter!r}"
        )

    def test_after_filter_empty_when_none(self):
        """_build_window_filters returns empty string for after_filter when merged_after is None."""
        from senrah.db.repos.skill import _build_window_filters

        _, _, after_filter = _build_window_filters(None, None, None)
        assert after_filter == "", (
            f"Expected empty string when merged_after=None, got: {after_filter!r}"
        )
