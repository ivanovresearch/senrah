"""
tests/unit/test_unionfind.py — Unit tests for eval/cluster/unionfind.py.

Covers:
- Union correctness (transitive merge, disjoint roots)
- components() grouping
- Structural constraint: unionfind.py must import only stdlib
"""

from __future__ import annotations

import importlib.util
import pathlib
import re


class TestUnionFind:
    """Correctness tests for pure union-find."""

    def test_union_then_find_same_root(self):
        """union(a, b) → find(a) == find(b)."""
        from eval.cluster.unionfind import UnionFind

        uf = UnionFind(n=5)
        uf.union(0, 1)
        assert uf.find(0) == uf.find(1)

    def test_disjoint_elements_have_distinct_roots(self):
        """Elements never unioned must remain in separate components."""
        from eval.cluster.unionfind import UnionFind

        uf = UnionFind(n=4)
        uf.union(0, 1)
        # 2 and 3 are untouched
        assert uf.find(2) != uf.find(0)
        assert uf.find(3) != uf.find(0)
        assert uf.find(2) != uf.find(3)

    def test_transitive_union(self):
        """union(a,b) + union(b,c) → find(a) == find(c)."""
        from eval.cluster.unionfind import UnionFind

        uf = UnionFind(n=5)
        uf.union(0, 1)
        uf.union(1, 2)
        assert uf.find(0) == uf.find(2)

    def test_components_groups_correctly(self):
        """components() returns lists of grouped ids matching union calls."""
        from eval.cluster.unionfind import UnionFind

        uf = UnionFind(n=5)
        uf.union(0, 1)
        uf.union(2, 3)
        # 4 is alone
        comps = uf.components()
        # Convert to frozensets for comparison
        comp_sets = [frozenset(c) for c in comps]
        assert frozenset({0, 1}) in comp_sets
        assert frozenset({2, 3}) in comp_sets
        assert frozenset({4}) in comp_sets
        assert len(comps) == 3

    def test_union_self_is_idempotent(self):
        """union(a, a) does not crash and element stays in its component."""
        from eval.cluster.unionfind import UnionFind

        uf = UnionFind(n=3)
        uf.union(0, 0)
        uf.union(0, 1)
        assert uf.find(0) == uf.find(1)

    def test_components_empty(self):
        """UnionFind with n=0 returns empty components list."""
        from eval.cluster.unionfind import UnionFind

        uf = UnionFind(n=0)
        assert uf.components() == []

    def test_components_all_separate(self):
        """No unions → each element is its own component."""
        from eval.cluster.unionfind import UnionFind

        uf = UnionFind(n=3)
        comps = uf.components()
        assert len(comps) == 3
        for c in comps:
            assert len(c) == 1


class TestUnionFindModuleConstraints:
    """Structural: unionfind.py must only import from stdlib."""

    def test_only_stdlib_imports(self):
        """unionfind.py must not import any third-party package."""
        spec = importlib.util.spec_from_file_location(
            "eval.cluster.unionfind",
            pathlib.Path(__file__).parent.parent.parent / "eval" / "cluster" / "unionfind.py",
        )
        assert spec is not None and spec.origin is not None
        src_text = pathlib.Path(spec.origin).read_text(encoding="utf-8")
        import_lines = [
            line.strip()
            for line in src_text.splitlines()
            if re.match(r"^\s*(import|from)\s+", line)
        ]
        forbidden = ["psycopg", "httpx", "openai", "pgvector", "pydantic", "yaml", "requests"]
        for pkg in forbidden:
            for line in import_lines:
                assert not re.search(rf"\b{re.escape(pkg)}\b", line), (
                    f"unionfind.py must not import {pkg!r} (found: {line!r})"
                )
