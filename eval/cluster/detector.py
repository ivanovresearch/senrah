"""
eval/cluster/detector.py — Signal-to-edge extraction for backport/cherry-pick detection.

Signals extracted from the pull_requests table (DB-only first, D-01):
  1. Title convention (authoritative): _normalize_title equality + explicit Backport/Port refs
  2. Shared linked_issue (corroborating)
  3. Diff similarity (candidate-only — NEVER merges alone, D-02)
  4. Author + close-in-time across release branches (weak corroborating)

Merge rule (D-02, D-03):
  - A diff-similarity edge alone NEVER unions two PRs.
  - Merging requires corroboration: title-convention, shared linked_issue,
    author+close-in-time, or cached cherry-pick SHA from refetch.py.
  - Bias to under-merge: uncorroborated candidate pairs are left unmerged.

Diff normalization:
  - Strip git/diff header lines (diff --git, index …), hunk headers (@@ … @@),
    and path lines (+++ / ---).
  - Keep added/removed payload lines only.
  - similarity = difflib.SequenceMatcher(None, a, b).ratio()

DSN: use EnvSettings().database_url (ENV-only posture, RESEARCH §1).
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

# ── Title normalization ─────────────────────────────────────────────────────
# Verbatim from eval/knownitem/build_manifest.py — authoritative title-convention signal.
_BRANCH_PREFIX = re.compile(r"^\s*\[(release/[^\]]+|main)\]\s*", re.IGNORECASE)

# Explicit backport reference patterns in title or body
_BACKPORT_REF = re.compile(r"\b(?:backport|port)\s+of\s+#(\d+)", re.IGNORECASE)


def _normalize_title(title: str) -> str:
    """Strip branch prefixes, collapse whitespace, casefold.

    Verbatim copy of build_manifest.py::_normalize_title — kept identical
    so title-convention clustering matches the existing manifest rule exactly.
    """
    t, n = _BRANCH_PREFIX.subn("", title)
    while n:
        t, n = _BRANCH_PREFIX.subn("", t)
    return re.sub(r"\s+", " ", t).strip().casefold()


# ── Diff normalization + similarity ────────────────────────────────────────

# Lines to strip: diff --git, index …, --- a/…, +++ b/…, @@ … @@
_STRIP_LINE = re.compile(
    r"^(?:"
    r"diff\s+--git\s+"        # diff --git a/... b/...
    r"|index\s+[0-9a-f]"      # index abc..def 100644
    r"|---\s+[ab]/"           # --- a/path  or  --- /dev/null
    r"|\+\+\+\s+[ab]/"        # +++ b/path  or  +++ /dev/null
    r"|---\s+/dev/null"        # --- /dev/null
    r"|\+\+\+\s+/dev/null"     # +++ /dev/null
    r"|@@\s"                  # @@ -n,m +n,m @@ …
    r")",
    re.IGNORECASE,
)


def normalize_diff(patch: str) -> str:
    """Strip git/diff header, hunk-header, and path lines; keep payload lines.

    Payload lines are added lines (starting with '+'), removed lines (starting
    with '-'), and context lines (starting with ' '). Header/metadata lines are
    discarded so that identical changes applied to different files yield the
    same normalized representation.
    """
    kept: list[str] = []
    for line in patch.splitlines():
        if _STRIP_LINE.match(line):
            continue
        kept.append(line)
    return "\n".join(kept)


def diff_similarity(a: str, b: str) -> float:
    """Return difflib ratio of normalized diffs (0.0–1.0).

    D-02: this value alone NEVER triggers a union — it only marks a CANDIDATE
    edge that requires corroboration.
    """
    na = normalize_diff(a)
    nb = normalize_diff(b)
    return difflib.SequenceMatcher(None, na, nb).ratio()


# ── Edge types ──────────────────────────────────────────────────────────────

@dataclass
class Edge:
    """A pairwise edge between two PRs with provenance."""

    a: int
    b: int
    via: str  # e.g. "title-convention", "linked-issue", "author-time", "cherry-pick-sha"
    score: float = 0.0  # diff similarity score when applicable

    def sorted_pair(self) -> tuple[int, int]:
        return (min(self.a, self.b), max(self.a, self.b))


# ── build_edges ─────────────────────────────────────────────────────────────

# Author + close-in-time threshold: two PRs by the same author within this window
# on different branches = weak corroborating signal.
_TIME_WINDOW = timedelta(days=7)


def build_edges(
    rows: list[dict[str, Any]],
    sim_threshold: float = 0.92,
) -> tuple[list[Edge], list[Edge]]:
    """Extract corroborated and candidate-only edges from PR signal rows.

    Args:
        rows: list of dicts, each with keys matching pull_requests columns:
              number, title, body, diff, author, merged_at, linked_issue,
              files_changed (list[str] or None).
        sim_threshold: minimum difflib.ratio to consider a pair as diff-similar.
                       A diff-similar pair with NO corroboration is a candidate only.

    Returns:
        (corroborated_edges, candidate_only_edges)
        corroborated_edges: pairs that should be unioned (at least one non-diff signal).
        candidate_only_edges: diff-similar pairs with no corroboration (NOT unioned, D-02).
    """
    # Index rows by PR number for O(1) lookup.
    by_number: dict[int, dict[str, Any]] = {r["number"]: r for r in rows}
    numbers: list[int] = [r["number"] for r in rows]
    n = len(numbers)

    # Step 1: build corroborating signal sets.

    # 1a. Title-convention groups: normalized title → set of PR numbers.
    title_groups: dict[str, set[int]] = {}
    for r in rows:
        norm = _normalize_title(r["title"] or "")
        title_groups.setdefault(norm, set()).add(r["number"])

    # 1b. Explicit backport references from title+body.
    #     "Backport of #N" or "Port of #N" → edge (current PR, N).
    explicit_backport: list[tuple[int, int]] = []
    for r in rows:
        text = (r.get("title") or "") + " " + (r.get("body") or "")
        for m in _BACKPORT_REF.finditer(text):
            ref = int(m.group(1))
            if ref in by_number and ref != r["number"]:
                explicit_backport.append((r["number"], ref))

    # 1c. Shared linked_issue groups.
    issue_groups: dict[str, set[int]] = {}
    for r in rows:
        li = r.get("linked_issue")
        if li:
            issue_groups.setdefault(str(li), set()).add(r["number"])

    # Step 2: collect corroborating pairs (excluding self-pairs).
    corroborated_pairs: set[tuple[int, int]] = set()

    # Title-convention pairs (exact normalized-title match — multiple PRs same title).
    for members in title_groups.values():
        if len(members) < 2:
            continue
        sorted_m = sorted(members)
        for i in range(len(sorted_m)):
            for j in range(i + 1, len(sorted_m)):
                corroborated_pairs.add((sorted_m[i], sorted_m[j]))

    # Explicit backport reference pairs.
    for a, b in explicit_backport:
        pair = (min(a, b), max(a, b))
        corroborated_pairs.add(pair)

    # Shared linked_issue pairs.
    for members in issue_groups.values():
        if len(members) < 2:
            continue
        sorted_m = sorted(members)
        for i in range(len(sorted_m)):
            for j in range(i + 1, len(sorted_m)):
                corroborated_pairs.add((sorted_m[i], sorted_m[j]))

    # Step 3: author + close-in-time across release branches (weak corroborating).
    # Only pair PRs whose merged_at values differ by <= _TIME_WINDOW AND have the same author.
    # This is a weak signal; requires diff-similarity corroboration to union alone is
    # handled in the merge rule (it's added to corroborating set here, to be combined
    # with diff similarity in the diff-sim path).
    author_time_pairs: set[tuple[int, int]] = set()
    for i in range(n):
        for j in range(i + 1, n):
            ri, rj = rows[i], rows[j]
            if ri["author"] != rj["author"]:
                continue
            ti = ri.get("merged_at")
            tj = rj.get("merged_at")
            if ti is None or tj is None:
                continue
            # Normalize to aware datetime if needed.
            if isinstance(ti, str):
                ti = datetime.fromisoformat(ti)
            if isinstance(tj, str):
                tj = datetime.fromisoformat(tj)
            if abs(ti - tj) <= _TIME_WINDOW:
                pair = (min(ri["number"], rj["number"]), max(ri["number"], rj["number"]))
                author_time_pairs.add(pair)

    # Step 4: diff-similarity scan (files_changed pre-filter, D-02).
    diff_similar_pairs: dict[tuple[int, int], float] = {}
    for i in range(n):
        for j in range(i + 1, n):
            ri, rj = rows[i], rows[j]
            # files_changed pre-filter: only compute similarity for pairs sharing ≥1 file.
            fi = set(ri.get("files_changed") or [])
            fj = set(rj.get("files_changed") or [])
            if not (fi & fj):
                continue
            sim = diff_similarity(ri.get("diff") or "", rj.get("diff") or "")
            if sim >= sim_threshold:
                pair = (min(ri["number"], rj["number"]), max(ri["number"], rj["number"]))
                diff_similar_pairs[pair] = sim

    # Step 5: classify edges.
    corroborated_edges: list[Edge] = []
    candidate_only_edges: list[Edge] = []

    # First, emit corroborated edges from signal sets (not diff-similarity).
    for pair in corroborated_pairs:
        a, b = pair
        # Determine via provenance (most authoritative first).
        via_parts: list[str] = []
        norm_a = _normalize_title(by_number[a]["title"] or "")
        norm_b = _normalize_title(by_number[b]["title"] or "")
        if norm_a == norm_b:
            via_parts.append("title-convention")
        if (a, b) in [(min(x, y), max(x, y)) for x, y in explicit_backport]:
            via_parts.append("explicit-backport-ref")
        li_a = by_number[a].get("linked_issue")
        li_b = by_number[b].get("linked_issue")
        if li_a and li_b and str(li_a) == str(li_b):
            via_parts.append("linked-issue")
        via = "+".join(via_parts) if via_parts else "corroborated"
        sim = diff_similar_pairs.get(pair, 0.0)
        corroborated_edges.append(Edge(a=a, b=b, via=via, score=sim))

    # Diff-similar pairs: classify as corroborated (if also in any corroborating set) or candidate.
    for pair, sim in diff_similar_pairs.items():
        a, b = pair
        is_corroborated = (
            pair in corroborated_pairs
            or pair in author_time_pairs
        )
        if is_corroborated and pair not in corroborated_pairs:
            # Only in author_time (weak) — still counts as corroborated.
            via_parts = ["author-time"]
            norm_a = _normalize_title(by_number[a]["title"] or "")
            norm_b = _normalize_title(by_number[b]["title"] or "")
            if norm_a == norm_b:
                via_parts.append("title-convention")
            via = "+".join(via_parts)
            corroborated_edges.append(Edge(a=a, b=b, via=via, score=sim))
        elif not is_corroborated:
            # Diff-similar only — candidate, NOT merged (D-02).
            candidate_only_edges.append(Edge(a=a, b=b, via="diff-similarity-only", score=sim))

    return corroborated_edges, candidate_only_edges
