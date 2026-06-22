"""
tests/unit/test_detector.py — Unit tests for eval/cluster/detector.py build_edges.

Covers:
- D-02: diff-similarity-only pair (0.99) with NO corroboration → NOT merged (separate components)
- D-02 promotion: diff-similar pair + shared linked_issue → merged
- D-02 promotion: explicit 'Backport of #N' in title → merged
- D-03: under-merge bias — uncorroborated candidate stays in candidate_only_edges, not corroborated
- refetch path is mocked (no live network calls)
"""

from __future__ import annotations

from datetime import datetime, timezone


def _make_rows(*overrides_list: dict) -> list[dict]:
    """Build minimal PR rows for fixture tests."""
    base_rows = []
    for i, overrides in enumerate(overrides_list, start=1):
        base = {
            "number": i * 1000,
            "title": f"Fix something #{i * 1000}",
            "body": "",
            "diff": f"+    var x = {i};",
            "author": "contributor",
            "merged_at": datetime(2024, 1, i, tzinfo=timezone.utc),
            "linked_issue": None,
            "files_changed": [],
        }
        base.update(overrides)
        base_rows.append(base)
    return base_rows


class TestDiffSimilarityOnlyNeverMerges:
    """D-02: a pair with diff-similarity >= threshold and NO other signal → candidate only."""

    def test_high_sim_no_corroboration_stays_separate(self):
        """diff_sim 0.99 + no corroboration ⇒ stays in candidate_only_edges, NOT corroborated."""
        from eval.cluster.detector import build_edges

        # Nearly identical diffs, different titles, same shared file, no linked issue.
        SHARED_FILE = "src/Foo.cs"
        PAYLOAD = "+    var x = 2;\n-    var x = 1;"
        rows = [
            {
                "number": 37674,
                "title": "Fix bug in Foo",
                "body": "",
                "diff": (
                    "diff --git a/src/Foo.cs b/src/Foo.cs\n"
                    "index abc..def 100644\n"
                    "--- a/src/Foo.cs\n"
                    "+++ b/src/Foo.cs\n"
                    "@@ -1,2 +1,2 @@\n"
                ) + PAYLOAD,
                "author": "alice",
                "merged_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
                "linked_issue": None,
                "files_changed": [SHARED_FILE],
            },
            {
                "number": 99999,
                "title": "A completely different title about something else",
                "body": "No backport references here.",
                "diff": (
                    "diff --git a/src/Foo.cs b/src/Foo.cs\n"
                    "index 111..222 100644\n"
                    "--- a/src/Foo.cs\n"
                    "+++ b/src/Foo.cs\n"
                    "@@ -3,2 +3,2 @@\n"
                ) + PAYLOAD,
                "author": "bob",
                "merged_at": datetime(2024, 6, 1, tzinfo=timezone.utc),
                "linked_issue": None,
                "files_changed": [SHARED_FILE],
            },
        ]
        corroborated, candidates = build_edges(rows, sim_threshold=0.5)

        # The pair should NOT be in corroborated edges.
        corroborated_pairs = {(e.a, e.b) for e in corroborated}
        assert (37674, 99999) not in corroborated_pairs, (
            "A diff-similarity-only pair must NOT be in corroborated edges (D-02)"
        )

        # The pair SHOULD appear as a candidate.
        candidate_pairs = {(e.a, e.b) for e in candidates}
        assert (37674, 99999) in candidate_pairs, (
            "A diff-similar uncorroborated pair must appear in candidate_only_edges"
        )

    def test_high_sim_verifies_are_in_separate_components(self):
        """Build union-find over corroborated edges; diff-sim-only pair stays in separate components."""
        from eval.cluster.detector import build_edges
        from eval.cluster.unionfind import UnionFind

        SHARED_FILE = "src/Bar.cs"
        PAYLOAD = "+    var y = 42;"
        rows = [
            {
                "number": 100,
                "title": "Change something",
                "body": "",
                "diff": "diff --git a/src/Bar.cs b/src/Bar.cs\n--- a/src/Bar.cs\n+++ b/src/Bar.cs\n@@ -1,1 +1,1 @@\n" + PAYLOAD,
                "author": "x",
                "merged_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
                "linked_issue": None,
                "files_changed": [SHARED_FILE],
            },
            {
                "number": 200,
                "title": "Rename something else",
                "body": "",
                "diff": "diff --git a/src/Bar.cs b/src/Bar.cs\n--- a/src/Bar.cs\n+++ b/src/Bar.cs\n@@ -2,1 +2,1 @@\n" + PAYLOAD,
                "author": "y",
                "merged_at": datetime(2024, 8, 1, tzinfo=timezone.utc),
                "linked_issue": None,
                "files_changed": [SHARED_FILE],
            },
        ]
        corroborated, _ = build_edges(rows, sim_threshold=0.5)

        # Build union-find over corroborated edges only.
        numbers = [100, 200]
        idx = {n: i for i, n in enumerate(numbers)}
        uf = UnionFind(n=2)
        for e in corroborated:
            if e.a in idx and e.b in idx:
                uf.union(idx[e.a], idx[e.b])

        # The two PRs must remain in separate components.
        assert uf.find(idx[100]) != uf.find(idx[200]), (
            "diff-sim-only pair must remain in separate components (D-02)"
        )


class TestCorroboratedEdgesMerge:
    """Corroborated signals DO produce union edges."""

    def test_diff_sim_plus_shared_linked_issue_merges(self):
        """diff-sim ≥ threshold + shared linked_issue → corroborated edge (D-02 promotion)."""
        from eval.cluster.detector import build_edges

        SHARED_FILE = "src/Baz.cs"
        PAYLOAD = "+    var z = 3;\n-    var z = 0;"
        rows = [
            {
                "number": 10,
                "title": "Fix null ref in Baz",
                "body": "Fixes #555",
                "diff": "diff --git a/src/Baz.cs b/src/Baz.cs\n--- a/src/Baz.cs\n+++ b/src/Baz.cs\n@@ -1,2 +1,2 @@\n" + PAYLOAD,
                "author": "dev",
                "merged_at": datetime(2024, 2, 1, tzinfo=timezone.utc),
                "linked_issue": "#555",
                "files_changed": [SHARED_FILE],
            },
            {
                "number": 20,
                "title": "[release/8.0] Fix null ref in Baz",
                "body": "Fixes #555",
                "diff": "diff --git a/src/Baz.cs b/src/Baz.cs\n--- a/src/Baz.cs\n+++ b/src/Baz.cs\n@@ -5,2 +5,2 @@\n" + PAYLOAD,
                "author": "dev",
                "merged_at": datetime(2024, 2, 2, tzinfo=timezone.utc),
                "linked_issue": "#555",
                "files_changed": [SHARED_FILE],
            },
        ]
        corroborated, _ = build_edges(rows, sim_threshold=0.5)

        corroborated_pairs = {(e.a, e.b) for e in corroborated}
        assert (10, 20) in corroborated_pairs, (
            "diff-sim + shared linked_issue must produce a corroborated edge"
        )

    def test_normalized_title_match_merges(self):
        """Normalized title equality → corroborated edge (title-convention signal)."""
        from eval.cluster.detector import build_edges

        rows = [
            {
                "number": 37674,
                "title": "Fix query plan regression in Foo",
                "body": "",
                "diff": "+    line A",
                "author": "dev",
                "merged_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
                "linked_issue": None,
                "files_changed": [],
            },
            {
                "number": 38066,
                "title": "[release/8.0] Fix query plan regression in Foo",
                "body": "",
                "diff": "+    line B",
                "author": "dev",
                "merged_at": datetime(2024, 1, 5, tzinfo=timezone.utc),
                "linked_issue": None,
                "files_changed": [],
            },
        ]
        corroborated, _ = build_edges(rows)

        corroborated_pairs = {(e.a, e.b) for e in corroborated}
        assert (37674, 38066) in corroborated_pairs, (
            "Normalized title match must produce a corroborated edge"
        )

    def test_explicit_backport_ref_merges(self):
        """Explicit 'Backport of #N' in title → corroborated edge."""
        from eval.cluster.detector import build_edges

        rows = [
            {
                "number": 500,
                "title": "Add caching to Qux",
                "body": "",
                "diff": "+    cache = True;",
                "author": "dev",
                "merged_at": datetime(2024, 3, 1, tzinfo=timezone.utc),
                "linked_issue": None,
                "files_changed": [],
            },
            {
                "number": 501,
                "title": "[release/9.0] Backport of #500",
                "body": "Backport of #500 for the 9.0 branch.",
                "diff": "+    cache = True;",
                "author": "dev",
                "merged_at": datetime(2024, 3, 2, tzinfo=timezone.utc),
                "linked_issue": None,
                "files_changed": [],
            },
        ]
        corroborated, _ = build_edges(rows)

        corroborated_pairs = {(e.a, e.b) for e in corroborated}
        assert (500, 501) in corroborated_pairs, (
            "Explicit 'Backport of #N' must produce a corroborated edge"
        )

    def test_port_of_ref_merges(self):
        """Explicit 'Port of #N' in body → corroborated edge."""
        from eval.cluster.detector import build_edges

        rows = [
            {
                "number": 600,
                "title": "Optimize reader",
                "body": "",
                "diff": "+    fast = True;",
                "author": "dev",
                "merged_at": datetime(2024, 4, 1, tzinfo=timezone.utc),
                "linked_issue": None,
                "files_changed": [],
            },
            {
                "number": 601,
                "title": "[main] Optimize reader",
                "body": "Port of #600",
                "diff": "+    fast = True;",
                "author": "dev",
                "merged_at": datetime(2024, 4, 3, tzinfo=timezone.utc),
                "linked_issue": None,
                "files_changed": [],
            },
        ]
        corroborated, _ = build_edges(rows)

        corroborated_pairs = {(e.a, e.b) for e in corroborated}
        assert (600, 601) in corroborated_pairs, (
            "Explicit 'Port of #N' must produce a corroborated edge"
        )


class TestUnderMergeBias:
    """D-03: uncorroborated candidate pairs are NOT merged."""

    def test_lone_candidate_not_in_corroborated(self):
        """A diff-similar pair with no title/issue/author corroboration stays in candidates."""
        from eval.cluster.detector import build_edges

        SHARED_FILE = "src/Widget.cs"
        # Two PRs with very different titles, different authors, far apart in time,
        # no linked issue — only their diffs are similar.
        rows = [
            {
                "number": 1,
                "title": "Alpha feature implementation",
                "body": "",
                "diff": "+    doAlpha();",
                "author": "alice",
                "merged_at": datetime(2023, 1, 1, tzinfo=timezone.utc),
                "linked_issue": None,
                "files_changed": [SHARED_FILE],
            },
            {
                "number": 2,
                "title": "Beta unrelated change",
                "body": "No relation to Alpha.",
                "diff": "+    doAlpha();",
                "author": "bob",
                "merged_at": datetime(2024, 6, 1, tzinfo=timezone.utc),  # 17 months later
                "linked_issue": None,
                "files_changed": [SHARED_FILE],
            },
        ]
        corroborated, candidates = build_edges(rows, sim_threshold=0.5)

        corroborated_pairs = {(e.a, e.b) for e in corroborated}
        candidate_pairs = {(e.a, e.b) for e in candidates}

        assert (1, 2) not in corroborated_pairs, (
            "Under-merge bias: diff-sim-only pair must not be in corroborated edges (D-03)"
        )
        assert (1, 2) in candidate_pairs, (
            "Diff-similar uncorroborated pair must appear in candidate_only_edges"
        )


class TestRefetchModuleExists:
    """refetch.py must exist and have the cherry-pick scan function."""

    def test_refetch_module_importable(self):
        """eval.cluster.refetch must be importable."""
        import eval.cluster.refetch  # noqa: F401

    def test_refetch_has_fetch_commits_function(self):
        """refetch must expose a fetch_commits function."""
        from eval.cluster import refetch

        assert hasattr(refetch, "fetch_commits"), (
            "refetch.py must expose fetch_commits(pr_number, ...)"
        )

    def test_no_github_token_literal_in_refetch(self):
        """GITHUB_TOKEN must NOT appear as a literal in refetch.py."""
        import pathlib

        src = (pathlib.Path(__file__).parent.parent.parent / "eval" / "cluster" / "refetch.py").read_text()
        # The string "GITHUB_TOKEN" is fine as an env var key; the literal token value must not appear.
        # We check that no ghp_* or github_pat_ tokens are hardcoded.
        import re

        assert not re.search(r"ghp_[A-Za-z0-9]{36}", src), "No real GitHub token in refetch.py"
        assert not re.search(r"github_pat_[A-Za-z0-9_]+", src), "No real GitHub PAT in refetch.py"

    def test_no_github_token_literal_in_detector(self):
        """GITHUB_TOKEN must NOT appear as a real token literal in detector.py."""
        import pathlib
        import re

        src = (pathlib.Path(__file__).parent.parent.parent / "eval" / "cluster" / "detector.py").read_text()
        assert not re.search(r"ghp_[A-Za-z0-9]{36}", src), "No real GitHub token in detector.py"
        assert not re.search(r"github_pat_[A-Za-z0-9_]+", src), "No real GitHub PAT in detector.py"
