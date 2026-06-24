"""
eval/temporal/define_split.py -- Derive the temporal holdout split and freeze query-set.json.

Temporal holdout split (DEPTH-03 / D-01 to D-05):
  - Corpus  = all efcore PRs with merged_at <  T
  - Queries = efcore PRs with merged_at >= T AND linked_issue IS NOT NULL
  - T       = max(merged_at) - T_days (default 365 days)

Answerable query definition (D-01):
  A query PR is answerable if its linked issue also appears as a linked_issue in
  at least one corpus PR (same-issue linkage), OR if the query PR belongs to the
  same cluster as at least one corpus PR (fuzzy backport match via clusters-deep.json).

Query text (D-03):
  Linked-issue title+body fetched from GitHub API at define_split time and frozen
  into query-set.json. This is consistent with the Phase 9 known-item eval approach
  (build_manifest.py pattern) and ensures the query text is never re-fetched after
  the split is frozen.

D-05 N-gate: after calling define_split(), print n_answerable. If N < 100, print
  a nudge hint. The human checkpoint in Plan 04 decides whether to nudge T.

Anti-leak rules:
  - All `#NNNNN` tokens stripped from query text (same as build_manifest.py).
  - Issue refs that resolve to pull_request objects are skipped.
  - GitHub tokens sourced from environment only (never written to output).

Do NOT run this script until the deep corpus ingest (Task 1 of Plan 03) has
completed. The script reads pull_requests rows; it requires the full multi-year
efcore history to derive a valid split.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import sys
import time
from datetime import timedelta

import httpx
import psycopg
from dotenv import load_dotenv

from senrah.config import EnvSettings
from eval.cluster.grouping import cluster_of, load_cluster_map

HERE = pathlib.Path(__file__).parent
CLUSTERS_DEEP = pathlib.Path("eval/cluster/clusters-deep.json")
DEFAULT_OUT = HERE / "query-set.json"
EFCORE_REPO = "dotnet/efcore"


def define_split(
    dsn: str,
    clusters_path: pathlib.Path,
    out_path: pathlib.Path,
    github_token: str,
    T_days: int = 365,
) -> dict:
    """Derive temporal holdout split and freeze query-set.json.

    Steps:
      1. Connect to DB (sync psycopg).
      2. Compute max_merged = max merged_at across efcore pull_requests.
      3. T = max_merged - timedelta(days=T_days).
      4. query_prs = efcore PRs where merged_at > T AND linked_issue IS NOT NULL.
      5. corpus_linked_set = distinct linked_issues from efcore PRs where merged_at < T.
      6. Load cluster_map from clusters_path.
      7. Build pr_to_cluster dict (only multi-member clusters per D-01).
      8. corpus_pr_set = PR numbers where merged_at < T.
      9. Fetch linked-issue text for each query PR via GitHub API (0.3s sleep).
     10. Determine answerable = linked-issue text present AND
         (linked_issue in corpus_linked_set OR cluster match).
     11. Build result dict with version, T, T_days_offset, n_answerable,
         cluster_map_file, corpus_fingerprint_hash, queries.
     12. Write to out_path and return result.

    Args:
        dsn:            PostgreSQL DSN (from EnvSettings().database_url).
        clusters_path:  Path to clusters-deep.json (produced by build_clusters.py --out).
        out_path:       Output path for query-set.json.
        github_token:   GitHub personal access token (read-only; from ENV only).
        T_days:         Days to subtract from max_merged to derive T (default 365).

    Returns:
        The result dict (also written to out_path).
    """
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github+json",
    }

    # --- 1. Connect and query DB --------------------------------------------------
    conn = psycopg.connect(dsn)

    # --- 2. max_merged_at ---------------------------------------------------------
    row = conn.execute(
        """
        SELECT max(pr.merged_at)
        FROM pull_requests pr
        JOIN repositories r ON r.id = pr.repository_id
        WHERE r.name = %s
        """,
        (EFCORE_REPO,),
    ).fetchone()
    if row is None or row[0] is None:
        conn.close()
        raise RuntimeError(
            "No efcore pull_requests found in DB. "
            "Run 'senrah ingest --scope all -' first (Task 1 of Plan 03)."
        )
    max_merged = row[0]

    # --- 3. T = max_merged - T_days -----------------------------------------------
    # LEAKAGE CHECK: PRRepo.upsert DOES update merged_at on conflict (pr.py lines 88-96).
    # Safe: GitHub never changes merged_at post-merge (immutable after merge event).
    # Split correctness: corpus = merged_at < T uses the stored immutable timestamp.
    # Query text = linked-issue text fetched NOW and frozen into query-set.json.
    # A PR body edited after T cannot contaminate the pre-T corpus because:
    # (a) we embed linked-issue text (not PR body), and
    # (b) issue text is frozen into query-set.json at define_split time, never re-fetched.
    T = max_merged - timedelta(days=T_days)

    # --- 4. Query PRs (merged after T with linked_issue) --------------------------
    query_rows = conn.execute(
        """
        SELECT pr.number, pr.linked_issue, pr.merged_at
        FROM pull_requests pr
        JOIN repositories r ON r.id = pr.repository_id
        WHERE r.name = %s
          AND pr.merged_at > %s
          AND pr.linked_issue IS NOT NULL
        ORDER BY pr.merged_at
        """,
        (EFCORE_REPO, T),
    ).fetchall()

    # --- 5. Corpus linked_issue set (merged before T) -----------------------------
    corpus_linked_rows = conn.execute(
        """
        SELECT DISTINCT pr.linked_issue
        FROM pull_requests pr
        JOIN repositories r ON r.id = pr.repository_id
        WHERE r.name = %s
          AND pr.merged_at < %s
          AND pr.linked_issue IS NOT NULL
        """,
        (EFCORE_REPO, T),
    ).fetchall()
    corpus_linked_set: set[str] = {r[0] for r in corpus_linked_rows}

    # --- 8. Corpus PR set (all PR numbers merged before T) -----------------------
    corpus_pr_rows = conn.execute(
        """
        SELECT pr.number
        FROM pull_requests pr
        JOIN repositories r ON r.id = pr.repository_id
        WHERE r.name = %s
          AND pr.merged_at < %s
        """,
        (EFCORE_REPO, T),
    ).fetchall()
    corpus_pr_set: set[int] = {r[0] for r in corpus_pr_rows}

    # Corpus fingerprint: sha256 of sorted corpus PR numbers for reproducibility.
    corpus_fingerprint_hash = hashlib.sha256(
        ",".join(str(n) for n in sorted(corpus_pr_set)).encode()
    ).hexdigest()

    conn.close()

    # --- 6. Load cluster map ------------------------------------------------------
    cluster_map = load_cluster_map(clusters_path)
    clusters_raw = cluster_map.get("clusters", [])
    cluster_fingerprint_hash = cluster_map.get("corpus_fingerprint", {}).get("hash", "")

    # --- 7. Build pr_to_cluster (multi-member clusters only, per D-01) ------------
    # cluster_of() returns the min member as cluster id; only multi-member clusters
    # contribute a cluster match for answerable detection.
    multi_member_prs: set[int] = set()
    for cluster in clusters_raw:
        if len(cluster) > 1:
            multi_member_prs.update(cluster)

    def _cluster_members(pr_number: int) -> list[int]:
        """Return all members of the cluster containing pr_number (sorted)."""
        for cluster in clusters_raw:
            if pr_number in cluster:
                return sorted(cluster)
        return [pr_number]

    def _has_corpus_cluster_match(pr_number: int) -> bool:
        """True if any cluster member of pr_number is in corpus_pr_set."""
        if pr_number not in multi_member_prs:
            return False
        for member in _cluster_members(pr_number):
            if member != pr_number and member in corpus_pr_set:
                return True
        return False

    # --- 9. Fetch linked-issue text via GitHub API --------------------------------
    queries: list[dict] = []
    skipped: list[dict] = []

    with httpx.Client(headers=headers, timeout=30) as client:
        for pr_number, linked_issue, merged_at in query_rows:
            issue_num = int(linked_issue.lstrip("#"))
            r = client.get(
                f"https://api.github.com/repos/{EFCORE_REPO}/issues/{issue_num}"
            )
            if r.status_code != 200:
                skipped.append({
                    "pr": pr_number,
                    "issue": issue_num,
                    "reason": f"http {r.status_code}",
                })
                time.sleep(0.3)
                continue
            issue = r.json()
            if "pull_request" in issue:
                skipped.append({
                    "pr": pr_number,
                    "issue": issue_num,
                    "reason": "linked ref is a PR",
                })
                time.sleep(0.3)
                continue
            title = issue.get("title") or ""
            body = issue.get("body") or ""
            # Strip #NNNNN refs (anti-leak: issue numbers are weak leak tokens).
            query_text = re.sub(r"#\d+", "", f"{title}\n\n{body}").strip()
            if len(query_text) < 30:
                skipped.append({
                    "pr": pr_number,
                    "issue": issue_num,
                    "reason": "issue text too short",
                })
                time.sleep(0.3)
                continue

            # --- 10. Answerable check (D-01) --------------------------------------
            in_corpus_linked = linked_issue in corpus_linked_set
            cluster_match = _has_corpus_cluster_match(pr_number)
            is_answerable = in_corpus_linked or cluster_match
            if in_corpus_linked:
                match_type = "linked-issue"
            elif cluster_match:
                match_type = "cluster"
            else:
                match_type = "none"

            queries.append({
                "pr_number": pr_number,
                "linked_issue": linked_issue,
                "merged_at": merged_at.isoformat(),
                "query": query_text,
                "is_answerable": is_answerable,
                "match_type": match_type,
            })
            time.sleep(0.3)

    # --- 11. Build result dict ----------------------------------------------------
    answerable = [q for q in queries if q["is_answerable"]]
    n_answerable = len(answerable)

    result = {
        "version": "v1-temporal-split",
        "T": T.isoformat(),
        "T_days_offset": T_days,
        "max_merged": max_merged.isoformat(),
        "n_query_prs": len(query_rows),
        "n_answerable": n_answerable,
        "cluster_map_file": str(clusters_path),
        "cluster_fingerprint_hash": cluster_fingerprint_hash,
        "corpus_fingerprint_hash": corpus_fingerprint_hash,
        "corpus_pr_count": len(corpus_pr_set),
        "skipped": skipped,
        "queries": queries,
    }

    # --- 12. Write to out_path ----------------------------------------------------
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> None:
    load_dotenv(".env")

    parser = argparse.ArgumentParser(
        description=(
            "Derive temporal holdout split and freeze query-set.json. "
            "Requires completed deep-corpus ingest (Plan 03 Task 1)."
        )
    )
    parser.add_argument(
        "--out",
        type=pathlib.Path,
        default=DEFAULT_OUT,
        help="Output path for query-set.json (default: eval/temporal/query-set.json)",
    )
    parser.add_argument(
        "--clusters",
        type=pathlib.Path,
        default=CLUSTERS_DEEP,
        help="Path to clusters-deep.json (default: eval/cluster/clusters-deep.json)",
    )
    parser.add_argument(
        "--T-days",
        type=int,
        default=365,
        dest="T_days",
        help="Days to subtract from max_merged to derive T (default: 365)",
    )
    args = parser.parse_args(argv)

    github_token = os.environ.get("GITHUB_TOKEN", "")
    if not github_token:
        print("ERROR: GITHUB_TOKEN not set in environment.", file=sys.stderr)
        sys.exit(1)

    dsn = EnvSettings().database_url

    result = define_split(
        dsn=dsn,
        clusters_path=args.clusters,
        out_path=args.out,
        github_token=github_token,
        T_days=args.T_days,
    )

    n = result["n_answerable"]
    T_str = result["T"]
    # ASCII-only output (Windows cp1251 console).
    print(f"T = {T_str} (offset -{args.T_days} days from max_merged={result['max_merged']})")
    print(f"n_answerable = {n} (of {result['n_query_prs']} query PRs, {result['corpus_pr_count']} corpus PRs)")
    print(f"query-set written to: {args.out}")

    # D-05 N-gate hint: researcher decides; no auto-nudge.
    if n < 100:
        print(
            f"WARNING: n_answerable={n} < 100. "
            "Nudge T to -455 days and rerun."
        )


if __name__ == "__main__":
    main()
