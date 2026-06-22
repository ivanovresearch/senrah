"""
eval/cluster/unionfind.py — Pure stdlib union-find with path compression.

No I/O, no external dependencies — only Python stdlib.
Models the no-dep discipline of src/senrah/scoring.py.

Provides:
- UnionFind: parent-array union-find with path compression
  - find(i): return root of element i (with path compression)
  - union(a, b): merge the components containing a and b
  - components(): return list of lists, each a connected component
"""

from __future__ import annotations


class UnionFind:
    """Parent-array union-find (disjoint set) with path compression.

    Elements are integers in the range [0, n).
    """

    def __init__(self, n: int) -> None:
        """Initialise with n elements, each in its own component."""
        self._parent: list[int] = list(range(n))
        self._rank: list[int] = [0] * n

    def find(self, i: int) -> int:
        """Return the root of element i (path-compressing)."""
        while self._parent[i] != i:
            # Path compression: make every node on the path point to its grandparent.
            self._parent[i] = self._parent[self._parent[i]]
            i = self._parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        """Merge the components containing a and b (union by rank)."""
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        # Union by rank: attach smaller tree under the larger tree.
        if self._rank[ra] < self._rank[rb]:
            ra, rb = rb, ra
        self._parent[rb] = ra
        if self._rank[ra] == self._rank[rb]:
            self._rank[ra] += 1

    def components(self) -> list[list[int]]:
        """Return all connected components as lists of element ids.

        Returns a list of lists; each inner list is one component.
        The order of components and elements within each component is unspecified.
        """
        n = len(self._parent)
        if n == 0:
            return []
        groups: dict[int, list[int]] = {}
        for i in range(n):
            root = self.find(i)
            groups.setdefault(root, []).append(i)
        return list(groups.values())
