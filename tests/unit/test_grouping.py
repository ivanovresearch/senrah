"""
tests/unit/test_grouping.py -- Unit tests for eval.cluster.grouping (EVAL-02).

Tests cover:
  - cluster_of: singleton, known cluster member, multi-member cluster
  - collapse_per_cluster: per-PR vs per-cluster DIVERGENCE (the key fixture)
  - stdlib-only import constraint (mirrors test_scoring.py discipline)
"""

from __future__ import annotations

import importlib
import importlib.util
import pathlib
import re


# ---------------------------------------------------------------------------
# Minimal hand-built cluster map fixture used by all tests.
# Cluster layout:
#   cluster A  = [100, 101]   (two-member backport pair)
#   cluster B  = [200, 201, 202]  (three-member backport chain)
#   singleton  = [300]
# ---------------------------------------------------------------------------
FIXTURE_MAP = {
    "version": "fixture",
    "clusters": [
        [100, 101],
        [200, 201, 202],
        [300],
    ],
    "edges": [],
}


class TestClusterOf:
    """cluster_of(pr, cluster_map) -> cluster_id"""

    def test_known_first_member_returns_min(self):
        from eval.cluster.grouping import cluster_of

        # min([100, 101]) = 100
        assert cluster_of(100, FIXTURE_MAP) == 100

    def test_known_second_member_returns_min(self):
        from eval.cluster.grouping import cluster_of

        assert cluster_of(101, FIXTURE_MAP) == 100

    def test_three_member_cluster_all_map_to_min(self):
        from eval.cluster.grouping import cluster_of

        assert cluster_of(200, FIXTURE_MAP) == 200
        assert cluster_of(201, FIXTURE_MAP) == 200
        assert cluster_of(202, FIXTURE_MAP) == 200

    def test_singleton_maps_to_self(self):
        from eval.cluster.grouping import cluster_of

        assert cluster_of(300, FIXTURE_MAP) == 300

    def test_unknown_pr_is_singleton(self):
        """A PR not in clusters.json maps to its own number (singleton rule)."""
        from eval.cluster.grouping import cluster_of

        assert cluster_of(9999, FIXTURE_MAP) == 9999

    def test_empty_cluster_list(self):
        from eval.cluster.grouping import cluster_of

        empty_map = {"clusters": []}
        assert cluster_of(42, empty_map) == 42


class TestCollapsePerCluster:
    """collapse_per_cluster divergence fixture (EVAL-02 success criterion 2)."""

    def test_divergence_two_cluster_members_in_top_k(self):
        """
        DIVERGENCE fixture: top-k contains BOTH members of cluster A [100, 101].
        Per-PR: 2 relevant.
        Per-cluster: 1 relevant (only ONE cluster hit, not two).
        """
        from eval.cluster.grouping import collapse_per_cluster

        ranked = [100, 101, 300]  # both cluster A members in top-3
        relevant = {100, 101}     # both are relevant

        result = collapse_per_cluster(ranked, relevant, FIXTURE_MAP)

        # Per-PR counts raw (no dedup)
        assert result["per_pr_relevant"] == 2, (
            "Per-PR: both 100 and 101 are relevant so count should be 2"
        )
        # Per-cluster counts deduplicated -- THE KEY ASSERTION
        assert result["relevant"] == 1, (
            "Per-cluster: both 100 and 101 belong to the same cluster, "
            "so per-cluster relevant count MUST be 1 (not 2)"
        )

    def test_divergence_distractor_dedup(self):
        """
        Two distractors from the same cluster count as ONE distractor cluster.
        """
        from eval.cluster.grouping import collapse_per_cluster

        ranked = [200, 201, 100]  # 200+201 same cluster (distractors), 100 relevant
        relevant = {100}

        result = collapse_per_cluster(ranked, relevant, FIXTURE_MAP)

        assert result["per_pr_distractor"] == 2  # two individual distractor PRs
        assert result["distractor"] == 1          # but only ONE distractor cluster
        assert result["relevant"] == 1

    def test_single_member_hit_no_divergence(self):
        """When top-k has only one member of a cluster, per-PR == per-cluster."""
        from eval.cluster.grouping import collapse_per_cluster

        ranked = [100, 300]  # only one member of cluster A
        relevant = {100}

        result = collapse_per_cluster(ranked, relevant, FIXTURE_MAP)

        assert result["per_pr_relevant"] == 1
        assert result["relevant"] == 1

    def test_miss_returns_zero_relevant(self):
        from eval.cluster.grouping import collapse_per_cluster

        ranked = [300]
        relevant = {100}  # 100 not in top-k

        result = collapse_per_cluster(ranked, relevant, FIXTURE_MAP)

        assert result["relevant"] == 0
        assert result["per_pr_relevant"] == 0

    def test_empty_ranked_list(self):
        from eval.cluster.grouping import collapse_per_cluster

        result = collapse_per_cluster([], {100}, FIXTURE_MAP)
        assert result["relevant"] == 0
        assert result["distractor"] == 0

    def test_cluster_promoted_to_relevant(self):
        """
        If a cluster's first seen member is a distractor but a later member IS
        relevant, the cluster is promoted to relevant (not double-counted).
        """
        from eval.cluster.grouping import collapse_per_cluster

        # 101 appears first (distractor because relevant={100}), then 100 (relevant)
        ranked = [101, 100]
        relevant = {100}

        result = collapse_per_cluster(ranked, relevant, FIXTURE_MAP)

        # The cluster [100, 101] should be "relevant" (not distractor)
        assert result["relevant"] == 1
        assert result["distractor"] == 0
        # Per-PR: 101 is distractor, 100 is relevant
        assert result["per_pr_relevant"] == 1
        assert result["per_pr_distractor"] == 1

    def test_full_three_member_cluster_relevant(self):
        """All three members of cluster B relevant -- still one cluster hit."""
        from eval.cluster.grouping import collapse_per_cluster

        ranked = [200, 201, 202]
        relevant = {200, 201, 202}

        result = collapse_per_cluster(ranked, relevant, FIXTURE_MAP)

        assert result["per_pr_relevant"] == 3
        assert result["relevant"] == 1


class TestGroupingModuleConstraints:
    """Structural constraints: stdlib-only imports, no I/O in counting fns."""

    def test_stdlib_only_imports(self):
        """grouping.py must not import anything outside stdlib + __future__."""
        spec = importlib.util.find_spec("eval.cluster.grouping")
        assert spec is not None, "eval.cluster.grouping not importable"
        src_text = pathlib.Path(spec.origin).read_text(encoding="utf-8")

        import_lines = [
            line.strip()
            for line in src_text.splitlines()
            if re.match(r"^\s*(import|from)\s+", line)
        ]
        forbidden = [
            "openai", "tiktoken", "psycopg", "pgvector",
            "yaml", "requests", "httpx", "sqlalchemy",
            "numpy", "pandas", "scipy",
        ]
        for pkg in forbidden:
            for line in import_lines:
                assert not re.search(rf"\b{re.escape(pkg)}\b", line), (
                    f"grouping.py must not import {pkg} (found: {line!r})"
                )

    def test_no_sql_in_counting_functions(self):
        """counting functions must contain no SQL keywords."""
        spec = importlib.util.find_spec("eval.cluster.grouping")
        assert spec is not None
        src_text = pathlib.Path(spec.origin).read_text(encoding="utf-8")

        # Only the load_cluster_map helper may touch disk (pathlib/json).
        # The counting functions must not contain SQL.
        sql_markers = ["SELECT ", "INSERT ", "UPDATE ", "DELETE ", "psycopg.connect"]
        for marker in sql_markers:
            assert marker not in src_text, (
                f"grouping.py must not contain SQL (found: {marker!r})"
            )

    def test_cluster_of_importable_without_disk(self):
        """cluster_of and collapse_per_cluster must be importable (no side effects)."""
        from eval.cluster.grouping import cluster_of, collapse_per_cluster  # noqa: F401
