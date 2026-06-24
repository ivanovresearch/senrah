"""
eval/temporal/bootstrap_ci.py -- Percentile bootstrap CI for hit-rate metrics.

Pure function module; no I/O, no DB, no network. Safe to import at any time.

Algorithm:
  - Resample unit: per-query Bernoulli trial (each element of `hits` is 0 or 1).
  - B=2000 bootstrap resamples drawn with replacement (default; frozen in manifest).
  - Seed: numpy.random.default_rng(seed=42) -- reproducible without global RNG state.
    Do NOT use numpy.random.seed() (global side-effect, forbidden here).
  - CI bounds: percentile method via numpy.quantile (not BCa or studentised).
    alpha = (1 - ci) / 2; lo = quantile(alpha); hi = quantile(1 - alpha).
  - Point estimate: arithmetic mean of hits (not the mean of bootstrap samples).

Usage:
    from eval.temporal.bootstrap_ci import bootstrap_hit_rate_ci
    point, lo, hi = bootstrap_hit_rate_ci(hits, B=2000, seed=42, ci=0.95)
"""

from __future__ import annotations

import numpy as np


def bootstrap_hit_rate_ci(
    hits: list[int],
    B: int = 2000,
    seed: int = 42,
    ci: float = 0.95,
) -> tuple[float, float, float]:
    """Compute bootstrap percentile CI for a binary hit-rate.

    Args:
        hits: List of 0/1 integers (1 = hit, 0 = miss), one per query.
        B:    Number of bootstrap resamples (default 2000; frozen in manifest).
        seed: RNG seed for numpy.random.default_rng (default 42; frozen in manifest).
              Uses per-call default_rng, never numpy.random.seed() global state.
        ci:   Confidence level (default 0.95 for 95% interval).

    Returns:
        (point, lo, hi): 3-tuple of floats.
          point -- arithmetic mean of hits (hit rate).
          lo    -- lower percentile CI bound.
          hi    -- upper percentile CI bound.
    """
    rng = np.random.default_rng(seed)
    arr = np.array(hits, dtype=float)
    n = len(arr)
    point = float(arr.mean())
    samples = rng.choice(arr, size=(B, n), replace=True).mean(axis=1)
    alpha = (1.0 - ci) / 2.0
    lo = float(np.quantile(samples, alpha))
    hi = float(np.quantile(samples, 1.0 - alpha))
    return point, lo, hi
