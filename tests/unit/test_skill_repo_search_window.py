"""
tests/unit/test_skill_repo_search_window.py -- Unit test stubs for DEPTH-02 window params.

Tests the merged_before / merged_after signature contract on SkillRepo.search.
These stubs are WAVE-0 contracts; implementation lives in Plan 02.

All tests are marked xfail(strict=False) so they skip gracefully when the
parameters do not yet exist, without breaking the existing test suite.
"""

from __future__ import annotations

import inspect
from datetime import datetime
from typing import Optional

import pytest


# ---------------------------------------------------------------------------
# Test: ceiling (merged_before) parameter signature contract
# ---------------------------------------------------------------------------


class TestSearchWindowCeiling:
    """SkillRepo.search must accept a merged_before Optional[datetime] param (DEPTH-02)."""

    @pytest.mark.xfail(strict=False, reason="DEPTH-02 not implemented")
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

    @pytest.mark.xfail(strict=False, reason="DEPTH-02 not implemented")
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


# ---------------------------------------------------------------------------
# Test: backward compatibility when both params are None
# ---------------------------------------------------------------------------


class TestSearchWindowBackwardCompat:
    """Both params None must leave call signature backward-compatible."""

    @pytest.mark.xfail(strict=False, reason="DEPTH-02 not implemented")
    def test_both_none_does_not_change_signature_arity(self):
        """Existing callers pass no window params; the added None-defaults must be transparent."""
        from senrah.db.repos.skill import SkillRepo

        sig = inspect.signature(SkillRepo.search)
        params = sig.parameters
        # Both params must exist with None defaults
        assert "merged_before" in params, "merged_before missing from SkillRepo.search (DEPTH-02)"
        assert "merged_after" in params, "merged_after missing from SkillRepo.search (DEPTH-02)"
        for name in ("merged_before", "merged_after"):
            assert params[name].default is None, (
                f"{name} must default to None so existing callers are unaffected"
            )


# ---------------------------------------------------------------------------
# Test: floor-only (merged_after without ceiling) param acceptance
# ---------------------------------------------------------------------------


class TestSearchWindowFloorOnly:
    """merged_after alone (no merged_before) must be accepted by the signature."""

    @pytest.mark.xfail(strict=False, reason="DEPTH-02 not implemented")
    def test_merged_after_accepted_without_merged_before(self):
        """Pass merged_after=T but no merged_before; signature must accept this combination."""
        from senrah.db.repos.skill import SkillRepo

        sig = inspect.signature(SkillRepo.search)
        params = sig.parameters
        # Both params must exist and be keyword-capable
        assert "merged_before" in params, "merged_before missing from SkillRepo.search (DEPTH-02)"
        assert "merged_after" in params, "merged_after missing from SkillRepo.search (DEPTH-02)"
        for name in ("merged_before", "merged_after"):
            kind = params[name].kind
            assert kind in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            ), f"{name} must be keyword-accessible"
