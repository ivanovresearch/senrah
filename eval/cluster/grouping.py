"""
eval/cluster/grouping.py -- Per-cluster hit and distractor counting (EVAL-02).

Pure module: no SQL, no I/O, no external dependencies (stdlib only).
Consumed by the known-item scorer (Plan 02) and the Phase 10/11 temporal
scorer; both pass in a parsed cluster map as an argument so the functions
stay importable and unit-testable without touching disk.

Public API
----------
cluster_of(pr_number, cluster_map) -> int
    Return the canonical cluster id for a PR number.  PRs not in any
    multi-member cluster map to their own singleton id (the PR number itself).

load_cluster_map(path) -> dict
    Load and parse clusters.json from *path*.  Returns the raw dict.
    This is the ONLY I/O helper; the counting functions below accept the
    parsed map so they stay pure.

collapse_per_cluster(ranked_numbers, relevant_set, cluster_map) -> dict
    Given a ranked list of PR numbers and the set of relevant PR numbers,
    return per-cluster counts of relevant hits and distractors.

    Rules (EVAL-02 / D-08):
      - One cluster contributes at most ONE "relevant" count, regardless of
        how many cluster members appear in ranked_numbers.
      - Distractors are also deduplicated per-cluster: one cluster
        contributes at most ONE "distractor" count.
      - A cluster that has at least one relevant member takes the "relevant"
        slot; additional members from the same cluster do NOT count as extra
        relevant hits.
"""

from __future__ import annotations

import json
import pathlib
from typing import Dict, Iterable, Set


def load_cluster_map(path: str | pathlib.Path) -> dict:
    """Load clusters.json from *path* and return the raw parsed dict."""
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def cluster_of(pr_number: int, cluster_map: dict) -> int:
    """Return the canonical cluster id for *pr_number*.

    The cluster id is the smallest PR number in the cluster (stable across
    runs because clusters.json is frozen and sorted).  PRs that appear only
    as singletons (their own cluster) map to their own number.

    Args:
        pr_number:   The PR number to look up.
        cluster_map: Parsed clusters.json dict (from load_cluster_map or a
                     hand-built fixture).

    Returns:
        An int cluster id.  For singletons this equals pr_number.
    """
    for cluster in cluster_map.get("clusters", []):
        if pr_number in cluster:
            return min(cluster)
    # Not found in any cluster list -- singleton
    return pr_number


def collapse_per_cluster(
    ranked_numbers: Iterable[int],
    relevant_set: Set[int],
    cluster_map: dict,
) -> Dict[str, int]:
    """Collapse a ranked result list to per-cluster hit and distractor counts.

    A cluster contributes at most ONE relevant count and at most ONE
    distractor count.  If a cluster has any relevant member in the ranked
    list it takes the relevant slot; it cannot also be a distractor.

    Args:
        ranked_numbers: Ordered iterable of PR numbers (top-1 first).
        relevant_set:   Set of PR numbers considered relevant for this query.
        cluster_map:    Parsed clusters.json dict.

    Returns:
        A dict with keys:
            "relevant":   number of distinct clusters with a relevant hit
            "distractor": number of distinct clusters with no relevant hit
            "per_pr_relevant":   raw per-PR relevant count (for divergence demo)
            "per_pr_distractor": raw per-PR distractor count
    """
    seen_cluster_ids: Set[int] = set()
    relevant_clusters: Set[int] = set()
    distractor_clusters: Set[int] = set()

    per_pr_relevant = 0
    per_pr_distractor = 0

    for pr in ranked_numbers:
        cid = cluster_of(pr, cluster_map)
        is_relevant = pr in relevant_set

        # Per-PR counters (no dedup)
        if is_relevant:
            per_pr_relevant += 1
        else:
            per_pr_distractor += 1

        # Per-cluster counters (deduplicated)
        if cid not in seen_cluster_ids:
            seen_cluster_ids.add(cid)
            if is_relevant:
                relevant_clusters.add(cid)
            else:
                distractor_clusters.add(cid)
        else:
            # Already seen this cluster; if a previous member was NOT
            # relevant but this one IS, promote the cluster to relevant.
            if is_relevant and cid not in relevant_clusters:
                relevant_clusters.add(cid)
                distractor_clusters.discard(cid)
            # A relevant cluster cannot become a distractor.

    return {
        "relevant": len(relevant_clusters),
        "distractor": len(distractor_clusters),
        "per_pr_relevant": per_pr_relevant,
        "per_pr_distractor": per_pr_distractor,
    }
