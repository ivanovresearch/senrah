"""
tests/unit/test_bootstrap_ci.py -- Unit tests for DEPTH-04 bootstrap CI.

Tests the bootstrap_hit_rate_ci function for determinism (same seed -> same
result), point-estimate independence from seed, CI width monotonicity, and
boundary values (all-hits / all-misses).
"""

from __future__ import annotations

from eval.temporal.bootstrap_ci import bootstrap_hit_rate_ci


# ---------------------------------------------------------------------------
# Test: determinism (same seed -> same bounds)
# ---------------------------------------------------------------------------


class TestBootstrapCIDeterminism:
    """bootstrap_hit_rate_ci must be deterministic given a fixed seed (DEPTH-04)."""

    def test_same_seed_same_result(self):
        """Calling bootstrap_hit_rate_ci twice with seed=42 must return identical (point, lo, hi)."""
        hits = [1, 0, 1, 1, 0, 1, 0, 0, 1, 1]
        r1 = bootstrap_hit_rate_ci(hits, seed=42)
        r2 = bootstrap_hit_rate_ci(hits, seed=42)
        assert r1 == r2, (
            f"bootstrap_hit_rate_ci with seed=42 returned different results: {r1} vs {r2}"
        )

    def test_different_seed_may_differ(self):
        """Point estimate is seed-independent; CI bounds may differ across seeds."""
        hits = [1, 0, 1, 1, 0, 1, 0, 0, 1, 1]
        r1 = bootstrap_hit_rate_ci(hits, seed=42)
        r99 = bootstrap_hit_rate_ci(hits, seed=99)
        # Point estimate is arithmetic mean of hits -- deterministic regardless of seed
        assert r1[0] == r99[0], (
            f"Point estimate should be identical across seeds: {r1[0]} vs {r99[0]}"
        )


# ---------------------------------------------------------------------------
# Test: CI width monotonicity (larger N -> narrower interval)
# ---------------------------------------------------------------------------


class TestBootstrapCIWidthMonotonicity:
    """CI width (hi - lo) must shrink as the number of observations N increases."""

    def test_width_shrinks_with_n(self):
        """CI width with N=200 must be strictly less than CI width with N=20."""
        small = [1, 0] * 10    # N=20, 50% hit rate
        large = [1, 0] * 100   # N=200, same 50% hit rate
        _, lo_s, hi_s = bootstrap_hit_rate_ci(small, seed=42)
        _, lo_l, hi_l = bootstrap_hit_rate_ci(large, seed=42)
        assert (hi_s - lo_s) > (hi_l - lo_l), (
            f"CI width must shrink with larger N: "
            f"N=20 width={hi_s - lo_s:.4f}, N=200 width={hi_l - lo_l:.4f}"
        )


# ---------------------------------------------------------------------------
# Test: boundary point estimates
# ---------------------------------------------------------------------------


class TestBootstrapCIPointEstimate:
    """Point estimate at boundaries: all-hits -> 1.0, all-misses -> 0.0."""

    def test_all_hits_gives_point_one(self):
        """All queries are hits -> point estimate is 1.0."""
        hits = [1] * 50
        point, _, _ = bootstrap_hit_rate_ci(hits, seed=42)
        assert point == 1.0, f"Expected point=1.0 for all-hits, got {point}"

    def test_all_misses_gives_point_zero(self):
        """All queries are misses -> point estimate is 0.0."""
        hits = [0] * 50
        point, _, _ = bootstrap_hit_rate_ci(hits, seed=42)
        assert point == 0.0, f"Expected point=0.0 for all-misses, got {point}"
