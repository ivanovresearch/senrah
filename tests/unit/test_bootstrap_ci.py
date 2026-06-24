"""
tests/unit/test_bootstrap_ci.py -- Unit test stubs for DEPTH-04 bootstrap CI.

Tests the bootstrap_hit_rate_ci function for determinism and width monotonicity.
These are WAVE-0 stubs; the target module eval.temporal.bootstrap_ci is created
in Plan 05. All tests are skipped until that module exists.
"""

from __future__ import annotations

import pytest

try:
    from eval.temporal.bootstrap_ci import bootstrap_hit_rate_ci  # noqa: F401
    _BOOTSTRAP_AVAILABLE = True
except ImportError:
    _BOOTSTRAP_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _BOOTSTRAP_AVAILABLE,
    reason="eval.temporal.bootstrap_ci not yet created -- Plan 05",
)


# ---------------------------------------------------------------------------
# Test: determinism (same seed -> same bounds)
# ---------------------------------------------------------------------------


class TestBootstrapCIDeterminism:
    """bootstrap_hit_rate_ci must be deterministic given a fixed seed (DEPTH-04)."""

    def test_same_seed_produces_identical_result(self):
        """Calling bootstrap_hit_rate_ci twice with seed=42 must return identical (point, lo, hi)."""
        from eval.temporal.bootstrap_ci import bootstrap_hit_rate_ci

        hits = [1, 0, 1, 1, 0, 1, 0, 0, 1, 1]
        r1 = bootstrap_hit_rate_ci(hits, seed=42)
        r2 = bootstrap_hit_rate_ci(hits, seed=42)
        assert r1 == r2, (
            f"bootstrap_hit_rate_ci with seed=42 returned different results: {r1} vs {r2}"
        )

    def test_result_is_three_tuple(self):
        """Return value must be a (point, lo, hi) tuple of three floats."""
        from eval.temporal.bootstrap_ci import bootstrap_hit_rate_ci

        hits = [1, 0, 1, 1, 0]
        result = bootstrap_hit_rate_ci(hits, seed=42)
        assert len(result) == 3, f"Expected 3-tuple, got {len(result)}-tuple"
        point, lo, hi = result
        assert lo <= point <= hi, (
            f"Expected lo <= point <= hi, got {lo} <= {point} <= {hi}"
        )


# ---------------------------------------------------------------------------
# Test: CI width monotonicity (larger N -> narrower interval)
# ---------------------------------------------------------------------------


class TestBootstrapCIWidthMonotonicity:
    """CI width (hi - lo) must shrink as the number of observations N increases."""

    def test_width_smaller_with_large_n(self):
        """CI width with N=200 must be strictly less than CI width with N=20."""
        from eval.temporal.bootstrap_ci import bootstrap_hit_rate_ci

        # Use a 60% hit rate for both
        hits_small = [1, 0, 1, 0, 1, 1, 0, 1, 0, 1,
                      1, 0, 0, 1, 0, 1, 1, 0, 1, 0]  # N=20
        hits_large = hits_small * 10  # N=200, same 60% rate

        _, lo_small, hi_small = bootstrap_hit_rate_ci(hits_small, seed=42)
        _, lo_large, hi_large = bootstrap_hit_rate_ci(hits_large, seed=42)

        width_small = hi_small - lo_small
        width_large = hi_large - lo_large
        assert width_large < width_small, (
            f"CI width must shrink with larger N: "
            f"N=20 width={width_small:.4f}, N=200 width={width_large:.4f}"
        )
