"""
tests/unit/test_diff_normalize.py — Unit tests for diff normalization + similarity.

Covers:
- normalize_diff strips git/diff header lines, hunk headers, path lines
- normalize_diff keeps added/removed payload lines
- diff_similarity returns 1.0 for identical normalized diffs
- diff_similarity returns low value for disjoint diffs
"""

from __future__ import annotations


SAMPLE_DIFF_A = """\
diff --git a/src/Foo.cs b/src/Foo.cs
index abc1234..def5678 100644
--- a/src/Foo.cs
+++ b/src/Foo.cs
@@ -10,7 +10,7 @@ namespace Foo {
-    var x = 1;
+    var x = 2;
     return x;
"""

SAMPLE_DIFF_B_IDENTICAL_PAYLOAD = """\
diff --git a/src/Bar.cs b/src/Bar.cs
index 000111..222333 100644
--- a/src/Bar.cs
+++ b/src/Bar.cs
@@ -5,7 +5,7 @@ namespace Bar {
-    var x = 1;
+    var x = 2;
     return x;
"""

SAMPLE_DIFF_DISJOINT = """\
diff --git a/src/Baz.cs b/src/Baz.cs
index aabbcc..ddeeff 100644
--- a/src/Baz.cs
+++ b/src/Baz.cs
@@ -1,3 +1,3 @@
-    public void DoSomethingTotallyDifferent() {}
+    public void DoSomethingElseEntirely() {}
"""


class TestNormalizeDiff:
    """normalize_diff strips headers and keeps payload lines."""

    def test_strips_diff_git_header(self):
        from eval.cluster.detector import normalize_diff

        result = normalize_diff(SAMPLE_DIFF_A)
        assert "diff --git" not in result

    def test_strips_index_line(self):
        from eval.cluster.detector import normalize_diff

        result = normalize_diff(SAMPLE_DIFF_A)
        assert "index abc1234" not in result

    def test_strips_triple_minus_path(self):
        from eval.cluster.detector import normalize_diff

        result = normalize_diff(SAMPLE_DIFF_A)
        assert "--- a/src/Foo.cs" not in result

    def test_strips_triple_plus_path(self):
        from eval.cluster.detector import normalize_diff

        result = normalize_diff(SAMPLE_DIFF_A)
        assert "+++ b/src/Foo.cs" not in result

    def test_strips_hunk_header(self):
        from eval.cluster.detector import normalize_diff

        result = normalize_diff(SAMPLE_DIFF_A)
        assert "@@ -10,7 +10,7 @@" not in result

    def test_keeps_added_line(self):
        from eval.cluster.detector import normalize_diff

        result = normalize_diff(SAMPLE_DIFF_A)
        # The +    var x = 2; line should be present
        assert "+    var x = 2;" in result

    def test_keeps_removed_line(self):
        from eval.cluster.detector import normalize_diff

        result = normalize_diff(SAMPLE_DIFF_A)
        assert "-    var x = 1;" in result


class TestDiffSimilarity:
    """diff_similarity returns 1.0 for identical normalized diffs, low for disjoint."""

    def test_identical_normalized_diffs_return_1(self):
        """Same payload in different-file wrappers → similarity 1.0."""
        from eval.cluster.detector import diff_similarity

        assert diff_similarity(SAMPLE_DIFF_A, SAMPLE_DIFF_B_IDENTICAL_PAYLOAD) == 1.0

    def test_same_diff_returns_1(self):
        """Exact same diff → 1.0."""
        from eval.cluster.detector import diff_similarity

        assert diff_similarity(SAMPLE_DIFF_A, SAMPLE_DIFF_A) == 1.0

    def test_disjoint_diffs_return_low(self):
        """Completely different payloads → low similarity."""
        from eval.cluster.detector import diff_similarity

        score = diff_similarity(SAMPLE_DIFF_A, SAMPLE_DIFF_DISJOINT)
        assert score < 0.5, f"Expected low similarity, got {score}"

    def test_empty_diffs_return_1(self):
        """Two empty diffs → 1.0 (both normalize to empty string)."""
        from eval.cluster.detector import diff_similarity

        assert diff_similarity("", "") == 1.0
