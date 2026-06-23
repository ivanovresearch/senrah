"""
eval/cluster/build_clusters.py — Frozen hash-pinned clusters.json writer.

Loads the full PR signal surface from pull_requests, runs build_edges + union-find,
calibrates the diff-similarity threshold against the corpus, and emits
eval/cluster/clusters.json with a sha256 corpus fingerprint.

Output schema:
  {
    "version": "v3",
    "corpus_fingerprint": {
      "prs": <int>,
      "min_merged": "<date>",
      "max_merged": "<date>",
      "hash": "<sha256 of sorted (number, title, merged_at) tuples>"
    },
    "params": {
      "sim_threshold": <float>,
      "metric": "difflib.ratio",
      "signals": [...],
      "calibration_notes": "..."
    },
    "clusters": [[pr1, pr2, ...], ...],
    "edges": [{"a": ..., "b": ..., "via": ..., "score": ...}],
    "refetched_pairs": [[a, b]]
  }

Threshold calibration method (RESEARCH §2):
  1. Take _normalize_title exact groups as known-positive backport pairs.
  2. Sample known-distinct pairs (different normalized titles, different files).
  3. Pick threshold in the clean separation gap.

DSN sourcing: EnvSettings().database_url (ENV-only posture).

Usage:
  python -m eval.cluster.build_clusters
  python -m eval.cluster.build_clusters --dry-run   # skip refetch
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

import psycopg

from eval.cluster.detector import _normalize_title, build_edges, diff_similarity
from eval.cluster.unionfind import UnionFind

OUT = pathlib.Path(__file__).parent / "clusters.json"


def load_signal_surface(dsn: str) -> tuple[list[dict], dict]:
    """Load the full PR signal surface from pull_requests.

    Returns:
        (rows, corpus_meta) where corpus_meta has prs, min_merged, max_merged.
    """
    conn = psycopg.connect(dsn)
    try:
        rows_raw = conn.execute(
            "SELECT number, title, body, diff, author, merged_at, linked_issue, files_changed "
            "FROM pull_requests ORDER BY merged_at"
        ).fetchall()
        corpus = conn.execute(
            "SELECT count(*), min(merged_at)::date::text, max(merged_at)::date::text "
            "FROM pull_requests"
        ).fetchone()
    finally:
        conn.close()

    rows = [
        {
            "number": r[0],
            "title": r[1] or "",
            "body": r[2] or "",
            "diff": r[3] or "",
            "author": r[4] or "",
            "merged_at": r[5],
            "linked_issue": r[6],
            "files_changed": r[7] if r[7] is not None else [],
        }
        for r in rows_raw
    ]
    corpus_meta = {
        "prs": corpus[0],
        "min_merged": corpus[1],
        "max_merged": corpus[2],
    }
    return rows, corpus_meta


def compute_corpus_fingerprint(rows: list[dict], corpus_meta: dict) -> str:
    """Compute sha256 over sorted (number, title, merged_at) tuples (D-06 hash-pin).

    The hash deterministically identifies the exact corpus this cluster map was
    built over. Rebuilding over the same rows yields an identical hash.
    """
    # Sort by number for determinism.
    tuples = sorted(
        (r["number"], r["title"] or "", str(r["merged_at"] or ""))
        for r in rows
    )
    raw = json.dumps(tuples, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def calibrate_threshold(rows: list[dict]) -> tuple[float, str]:
    """Calibrate diff-similarity threshold using the corpus (RESEARCH §2).

    Method:
      1. Take _normalize_title exact groups as known-positive backport pairs.
      2. Sample known-distinct pairs (different normalized title, no shared files).
      3. Compute diff similarity for both groups and pick the threshold in the
         clean separation gap.

    Returns:
        (sim_threshold, calibration_notes)
    """
    # Step 1: build known-positive pairs from title groups.
    title_groups: dict[str, list[int]] = {}
    by_number = {r["number"]: r for r in rows}
    for r in rows:
        norm = _normalize_title(r["title"])
        title_groups.setdefault(norm, []).append(r["number"])

    positive_sims: list[float] = []
    for members in title_groups.values():
        if len(members) < 2:
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = by_number[members[i]], by_number[members[j]]
                # Only compute if they share a file (meaningful comparison).
                fa = set(a.get("files_changed") or [])
                fb = set(b.get("files_changed") or [])
                if fa & fb:
                    sim = diff_similarity(a["diff"], b["diff"])
                    positive_sims.append(sim)

    # Step 2: sample known-distinct pairs.
    distinct_sims: list[float] = []
    numbers = list(by_number.keys())
    # Use a deterministic sample: take every 20th pair from sorted number list.
    step = max(1, len(numbers) // 30)
    sampled = numbers[::step][:30]
    for i in range(len(sampled)):
        for j in range(i + 1, len(sampled)):
            a, b = by_number[sampled[i]], by_number[sampled[j]]
            norm_a = _normalize_title(a["title"])
            norm_b = _normalize_title(b["title"])
            if norm_a == norm_b:
                continue  # Skip — these are actually related
            fa = set(a.get("files_changed") or [])
            fb = set(b.get("files_changed") or [])
            if not (fa & fb):
                continue  # No shared files — skip (pre-filter)
            sim = diff_similarity(a["diff"], b["diff"])
            distinct_sims.append(sim)

    # Step 3: choose threshold.
    if positive_sims and distinct_sims:
        pos_min = min(positive_sims)
        dist_max = max(distinct_sims)
        if pos_min > dist_max:
            # Clean gap: set threshold at midpoint.
            threshold = (pos_min + dist_max) / 2.0
            threshold = round(threshold, 3)
            notes = (
                f"Calibrated from corpus: "
                f"{len(positive_sims)} known-positive pairs (min sim={pos_min:.3f}), "
                f"{len(distinct_sims)} known-distinct pairs (max sim={dist_max:.3f}). "
                f"Clean separation gap [{dist_max:.3f}, {pos_min:.3f}]; "
                f"threshold set at midpoint {threshold:.3f}."
            )
        else:
            # Overlapping distributions — use a conservative high threshold.
            threshold = 0.92
            notes = (
                f"No clean gap found: pos_min={pos_min:.3f}, dist_max={dist_max:.3f}. "
                f"Using conservative default threshold={threshold}."
            )
    elif positive_sims:
        # Only positives available — use high threshold.
        threshold = 0.92
        notes = (
            f"{len(positive_sims)} positive pairs available, no distinct sample. "
            f"Using conservative default threshold={threshold}."
        )
    else:
        threshold = 0.92
        notes = "Insufficient calibration data; using conservative default threshold=0.92."

    return threshold, notes


def build_cluster_artifact(
    dsn: str,
    *,
    dry_run: bool = False,
    use_refetch: bool = True,
    out: pathlib.Path = OUT,
) -> dict:
    """Build and write clusters.json over the full corpus.

    Args:
        dsn: PostgreSQL DSN.
        dry_run: If True, skip the refetch step (for testing without GITHUB_TOKEN).
        use_refetch: If True, attempt to corroborate ambiguous candidates via
                     cached commit-message re-fetch.
        out: Output path for clusters.json.

    Returns:
        The artifact dict (also written to out).
    """
    rows, corpus_meta = load_signal_surface(dsn)
    corpus_fingerprint_hash = compute_corpus_fingerprint(rows, corpus_meta)

    # Calibrate threshold.
    sim_threshold, calibration_notes = calibrate_threshold(rows)

    # Build edges.
    corroborated_edges, candidate_edges = build_edges(rows, sim_threshold=sim_threshold)

    # Optional: promote candidates via cached commit-message refetch.
    refetched_pairs: list[list[int]] = []
    if use_refetch and not dry_run and candidate_edges:
        try:
            from eval.cluster.refetch import find_cherry_pick_corroboration
            import os

            if "GITHUB_TOKEN" in os.environ:
                for edge in candidate_edges:
                    if find_cherry_pick_corroboration(edge.a, edge.b):
                        # Promote to corroborated.
                        edge.via = "cherry-pick-sha"
                        corroborated_edges.append(edge)
                        refetched_pairs.append([edge.a, edge.b])
        except Exception:
            pass  # Refetch is best-effort; never block the main build

    # Build union-find over corroborated edges.
    numbers = [r["number"] for r in rows]
    idx = {n: i for i, n in enumerate(numbers)}
    uf = UnionFind(n=len(numbers))
    for edge in corroborated_edges:
        if edge.a in idx and edge.b in idx:
            uf.union(idx[edge.a], idx[edge.b])

    # Extract clusters: groups of PR numbers (only non-trivial or all).
    raw_components = uf.components()
    clusters: list[list[int]] = [
        sorted(numbers[i] for i in comp)
        for comp in raw_components
    ]
    # Sort clusters for determinism (by first element).
    clusters.sort(key=lambda c: c[0])

    # Build artifact.
    artifact = {
        "version": "v3",
        "corpus_fingerprint": {
            "prs": corpus_meta["prs"],
            "min_merged": corpus_meta["min_merged"],
            "max_merged": corpus_meta["max_merged"],
            "hash": corpus_fingerprint_hash,
        },
        "params": {
            "sim_threshold": sim_threshold,
            "metric": "difflib.ratio",
            "signals": [
                "title-convention",
                "explicit-backport-ref",
                "linked-issue",
                "author-time+diff-sim",
                "cherry-pick-sha (cached refetch)",
            ],
            "calibration_notes": calibration_notes,
        },
        "clusters": clusters,
        "edges": [
            {"a": e.a, "b": e.b, "via": e.via, "score": round(e.score, 4)}
            for e in corroborated_edges
        ],
        "refetched_pairs": refetched_pairs,
    }

    out.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")
    return artifact


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build frozen clusters.json from the PR corpus.")
    parser.add_argument("--dry-run", action="store_true", help="Skip GitHub refetch step.")
    parser.add_argument("--dsn", default=None, help="PostgreSQL DSN (default: EnvSettings().database_url).")
    args = parser.parse_args(argv)

    if args.dsn:
        dsn = args.dsn
    else:
        from senrah.config import EnvSettings
        dsn = EnvSettings().database_url

    artifact = build_cluster_artifact(dsn, dry_run=args.dry_run)
    n_clusters = len(artifact["clusters"])
    multi_member = sum(1 for c in artifact["clusters"] if len(c) > 1)
    print(
        f"clusters.json written: {artifact['corpus_fingerprint']['prs']} PRs, "
        f"{n_clusters} clusters ({multi_member} with >=2 members), "
        f"{len(artifact['edges'])} edges, "
        f"hash={artifact['corpus_fingerprint']['hash'][:16]}..."
    )


if __name__ == "__main__":
    main()
