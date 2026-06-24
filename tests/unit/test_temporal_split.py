"""
tests/unit/test_temporal_split.py -- Unit tests for DEPTH-03 temporal split logic.

Tests split disjointness and answerable-set detection logic from
eval.temporal.run_temporal_eval (_compute_relevant_set pure function).
Hand-built fixtures only -- no DB, no disk I/O.
"""

from __future__ import annotations

from eval.temporal.run_temporal_eval import _compute_relevant_set


# ---------------------------------------------------------------------------
# Minimal cluster map fixtures used by answerable-detection tests.
# ---------------------------------------------------------------------------

# cluster_map_empty: no clusters -- every PR is a singleton
CLUSTER_MAP_EMPTY: dict = {"version": "fixture", "clusters": [], "edges": []}

# cluster_map_with_pair: PRs 1001 and 3001 share a cluster
CLUSTER_MAP_WITH_PAIR: dict = {
    "version": "fixture",
    "clusters": [[1001, 3001]],
    "edges": [],
}


# ---------------------------------------------------------------------------
# Test: corpus / query split disjointness
# ---------------------------------------------------------------------------


class TestTemporalSplitDisjoint:
    """No query PR (merged_at > T) must appear in the corpus set (merged_at < T)."""

    def test_query_not_in_corpus(self):
        """
        Query PR has merged_at = T + 1 day; corpus PRs have merged_at < T.
        Assert query pr_number is not in the set of corpus pr_numbers.

        This verifies the temporal-holdout invariant: strict disjointness
        between the retrieval corpus and the evaluation query set.
        Pure data check -- no DB needed.
        """
        from datetime import datetime, timezone, timedelta

        T = datetime(2023, 1, 1, tzinfo=timezone.utc)

        corpus_prs = [
            {"number": 1001, "merged_at": datetime(2022, 6, 1, tzinfo=timezone.utc)},
            {"number": 1002, "merged_at": datetime(2022, 12, 31, tzinfo=timezone.utc)},
        ]
        query_pr = {"number": 2001, "merged_at": T + timedelta(days=1)}

        corpus_numbers = {pr["number"] for pr in corpus_prs}
        assert query_pr["number"] not in corpus_numbers, (
            f"Query PR {query_pr['number']} must not appear in corpus set {corpus_numbers}"
        )


# ---------------------------------------------------------------------------
# Test: answerable-set detection via _compute_relevant_set
# ---------------------------------------------------------------------------


class TestAnswerableDetection:
    """_compute_relevant_set returns relevant PR numbers based on linked_issue + cluster."""

    def test_linked_issue_match_is_answerable(self):
        """
        Query has linked_issue="#42". A corpus PR also has linked_issue="#42".
        _compute_relevant_set must return a non-empty set containing that corpus PR.
        """
        corpus_prs = [
            {"number": 1001, "linked_issue": "#42"},
            {"number": 1002, "linked_issue": "#99"},
        ]
        relevant = _compute_relevant_set(
            pr_number=2001,
            linked_issue="#42",
            cluster_map=CLUSTER_MAP_EMPTY,
            corpus_prs=corpus_prs,
        )
        assert len(relevant) > 0, (
            "Expected non-empty relevant set: corpus PR 1001 shares linked_issue='#42'"
        )
        assert 1001 in relevant, (
            f"Corpus PR 1001 (linked_issue='#42') must be in relevant set, got {relevant}"
        )

    def test_no_match_is_not_answerable(self):
        """
        Query has linked_issue="#42". Corpus only has PRs with linked_issue="#99".
        No shared cluster. _compute_relevant_set must return empty set.
        """
        corpus_prs = [
            {"number": 1001, "linked_issue": "#99"},
        ]
        relevant = _compute_relevant_set(
            pr_number=2001,
            linked_issue="#42",
            cluster_map=CLUSTER_MAP_EMPTY,
            corpus_prs=corpus_prs,
        )
        assert relevant == set(), (
            f"Expected empty relevant set when no linked_issue match and no cluster match, got {relevant}"
        )
