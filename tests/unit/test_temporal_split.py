"""
tests/unit/test_temporal_split.py -- Unit test stubs for DEPTH-03 temporal split logic.

Tests split disjointness and answerable-set detection logic from
eval.temporal.define_split (pure helper functions). These are WAVE-0 stubs;
the target module is created in Plan 05. All tests are skipped until that
module exists.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(
    reason="define_split pure helpers not yet created -- Plan 05"
)


# ---------------------------------------------------------------------------
# Test: corpus / query split disjointness
# ---------------------------------------------------------------------------


class TestTemporalSplitDisjoint:
    """No query PR (merged_at > T) must appear in the corpus set (merged_at < T)."""

    def test_query_prs_not_in_corpus(self):
        """
        Given a fixture with known corpus PRs (merged_at < T) and query PRs
        (merged_at > T), assert no query PR number appears in the corpus set.

        This verifies the core temporal-holdout invariant: strict disjointness
        between the retrieval corpus and the evaluation query set.
        """
        from datetime import datetime, timezone

        T = datetime(2023, 1, 1, tzinfo=timezone.utc)

        # Corpus PRs: merged before T
        corpus_prs = [
            {"number": 1001, "merged_at": datetime(2022, 6, 1, tzinfo=timezone.utc)},
            {"number": 1002, "merged_at": datetime(2022, 12, 31, tzinfo=timezone.utc)},
        ]

        # Query PRs: merged after T
        query_prs = [
            {"number": 2001, "merged_at": datetime(2023, 3, 1, tzinfo=timezone.utc)},
            {"number": 2002, "merged_at": datetime(2023, 6, 1, tzinfo=timezone.utc)},
        ]

        corpus_numbers = {pr["number"] for pr in corpus_prs}
        query_numbers = {pr["number"] for pr in query_prs}

        intersection = corpus_numbers & query_numbers
        assert intersection == set(), (
            f"Query PRs {intersection} appear in the corpus set -- temporal leak!"
        )


# ---------------------------------------------------------------------------
# Test: answerable-set detection
# ---------------------------------------------------------------------------


class TestAnswerableDetection:
    """A query PR is answerable if and only if a corpus PR shares its linked_issue."""

    def test_query_with_matching_corpus_issue_is_answerable(self):
        """
        A query PR whose linked_issue matches a corpus PR's linked_issue is answerable.
        """
        corpus_prs = [
            {"number": 1001, "linked_issue": "#42"},
            {"number": 1002, "linked_issue": "#99"},
        ]
        query_pr = {"number": 2001, "linked_issue": "#42"}

        corpus_issues = {pr["linked_issue"] for pr in corpus_prs if pr.get("linked_issue")}
        is_answerable = query_pr.get("linked_issue") in corpus_issues

        assert is_answerable is True, (
            f"Query PR #{query_pr['number']} with linked_issue {query_pr['linked_issue']!r} "
            f"should be answerable (corpus contains that issue)"
        )

    def test_query_without_matching_corpus_issue_is_not_answerable(self):
        """
        A query PR whose linked_issue has no match in the corpus is not answerable.
        """
        corpus_prs = [
            {"number": 1001, "linked_issue": "#42"},
        ]
        query_pr = {"number": 2001, "linked_issue": "#999"}

        corpus_issues = {pr["linked_issue"] for pr in corpus_prs if pr.get("linked_issue")}
        is_answerable = query_pr.get("linked_issue") in corpus_issues

        assert is_answerable is False, (
            f"Query PR #{query_pr['number']} with linked_issue {query_pr['linked_issue']!r} "
            f"should NOT be answerable (corpus does not contain that issue)"
        )
