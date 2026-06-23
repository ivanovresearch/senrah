"""
eval/knownitem/build_manifest.py — freeze the known-item evaluation manifest.

Known-item retrieval eval (designed 2026-06-11, ratified after the A/B run):
for every corpus PR with a linked issue, the QUERY is the issue's title+body
(the "symptom language" — never embedded anywhere in the index; the problem
embedding is built from the PR's own title+body) and the single relevant item
is that PR. Label precision is 1.0 by construction; no auxiliary label source.

Anti-leak rules applied here:
- all `#NNNNN` tokens are stripped from the query (PR bodies embed "Fixes #N",
  so issue numbers are real, if weak, leak tokens);
- issues that turn out to be pull requests or are inaccessible are skipped
  and recorded in the manifest header.

Backport rule (v2): a fix and its release-branch backport are the SAME
solution under different PR numbers. relevant(X) = {X} ∪ {corpus PRs whose
normalized title — `[release/…]`/`[main]` prefixes stripped, whitespace
collapsed, casefolded — equals X's normalized title}. Without this rule a
retrieval that returns the backport of the target is scored as a miss
(observed in the v1 baseline: target 37674 "missed" to its own backport
38066).

Backport rule (v3): same as v2 but relevant_prs sourced from the fuzzy
cluster map (eval/cluster/clusters.json) instead of exact title groups.
The v3 path reuses v2 query text — no GitHub issue re-fetch needed.

Output:
  v2: eval/knownitem/manifest.json
  v3: eval/knownitem/manifest-v3.json
Re-running this script after corpus changes produces a NEW manifest version
(corpus fingerprint is recorded), old results stay comparable only within
their manifest version.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import time

import httpx
import psycopg
from dotenv import load_dotenv

DSN = "postgresql://harness:harness@localhost:5432/harness"
OUT = pathlib.Path(__file__).parent / "manifest.json"
OUT_V3 = pathlib.Path(__file__).parent / "manifest-v3.json"
CLUSTERS_PATH = pathlib.Path(__file__).parent.parent / "cluster" / "clusters.json"
TRIAGE_V3_PATH = pathlib.Path(__file__).parent / "triage-v3.json"

# `[release/10.0]`, `[main]`, `[release/9.0-staging]`… — possibly stacked.
_BRANCH_PREFIX = re.compile(r"^\s*\[(release/[^\]]+|main)\]\s*", re.IGNORECASE)


def _normalize_title(title: str) -> str:
    """Strip branch prefixes, collapse whitespace, casefold."""
    t, n = _BRANCH_PREFIX.subn("", title)
    while n:
        t, n = _BRANCH_PREFIX.subn("", t)
    return re.sub(r"\s+", " ", t).strip().casefold()


def build_v3(
    v2_manifest_path: pathlib.Path | None = None,
    clusters_path: pathlib.Path | None = None,
    triage_path: pathlib.Path | None = None,
    out_path: pathlib.Path | None = None,
) -> dict:
    """Build the v3-knownitem-deduped manifest without re-fetching GitHub issues.

    v3 reuses v2 query text verbatim. Only relevant_prs is recomputed from the
    fuzzy cluster map (clusters.json) instead of exact title groups. EVAL-03
    corrections (collapsed duplicates + label-error fixes) are applied and
    recorded.

    No GitHub API call is made on the v3 path (network caveat — RESEARCH SS5).

    Args:
        v2_manifest_path: Path to the v2 manifest.json (defaults to OUT).
        clusters_path:    Path to clusters.json (defaults to CLUSTERS_PATH).
        triage_path:      Path to triage-v3.json (defaults to TRIAGE_V3_PATH).
        out_path:         Path to write manifest-v3.json (defaults to OUT_V3).

    Returns:
        The manifest dict (also written to out_path).
    """
    v2_manifest_path = v2_manifest_path or OUT
    clusters_path = clusters_path or CLUSTERS_PATH
    triage_path = triage_path or TRIAGE_V3_PATH
    out_path = out_path or OUT_V3

    # Load v2 manifest — reuse query text, issue, merged_at verbatim.
    v2 = json.loads(v2_manifest_path.read_text(encoding="utf-8"))
    v2_queries = v2["queries"]
    v2_skipped = v2.get("skipped", [])

    # Load the fuzzy cluster map (EVAL-01 artifact).
    from eval.cluster.grouping import cluster_of, load_cluster_map

    cluster_map = load_cluster_map(clusters_path)
    clusters_raw = cluster_map.get("clusters", [])
    cluster_fingerprint_hash = cluster_map.get("corpus_fingerprint", {}).get("hash", "")

    # Build a lookup: pr_number -> full cluster members (sorted).
    # cluster_of() returns the minimum member (cluster id); we need the full set.
    def _cluster_members(pr_number: int) -> list[int]:
        """Return all members of the cluster containing pr_number (sorted)."""
        for cluster in clusters_raw:
            if pr_number in cluster:
                return sorted(cluster)
        # Singleton: just the PR itself.
        return [pr_number]

    # Load EVAL-03 corrections from triage-v3.json.
    triage_rows = json.loads(triage_path.read_text(encoding="utf-8"))

    # Identify label-error removals (final_tag == "label-error"): targets to remove.
    label_error_targets: set[int] = {
        row["target_pr"]
        for row in triage_rows
        if row.get("final_tag") == "label-error"
    }

    # Identify duplicate collapses (final_tag == "duplicate"): cluster was missing in v2.
    # These targets stay in the manifest; relevant_prs now includes cluster members.
    duplicate_targets: dict[int, list[int]] = {}
    for row in triage_rows:
        if row.get("final_tag") == "duplicate":
            cluster = row.get("stage1_via_cluster") or []
            duplicate_targets[row["target_pr"]] = sorted(cluster)

    # Build corrections list for the manifest (one entry per change).
    corrections: list[dict] = []
    for row in triage_rows:
        target = row["target_pr"]
        tag = row.get("final_tag")
        if tag == "duplicate":
            corrections.append({
                "type": "collapsed-duplicate",
                "target_pr": target,
                "cluster_members": duplicate_targets[target],
                "note": row.get("note", ""),
            })
        elif tag == "label-error":
            corrections.append({
                "type": "label-error-removal",
                "target_pr": target,
                "note": row.get("note", ""),
            })

    # Build v3 queries: reuse v2 query text, recompute relevant_prs from cluster map.
    queries_v3: list[dict] = []
    for q in v2_queries:
        target_pr = q["target_pr"]

        # Skip targets with label-error (the only legitimate v2->v3 shrinkage).
        if target_pr in label_error_targets:
            continue

        # Relevant set = all cluster members of this target.
        relevant = _cluster_members(target_pr)

        queries_v3.append({
            "target_pr": target_pr,
            "relevant_prs": relevant,
            "issue": q["issue"],
            "merged_at": q["merged_at"],
            "query": q["query"],  # verbatim — no re-fetch
        })

    # Manifest corpus fingerprint: v3 uses the cluster-map corpus (575 PRs).
    cluster_corpus = cluster_map.get("corpus_fingerprint", {})

    manifest = {
        "version": "v3-knownitem-deduped",
        "corpus": {
            "prs": cluster_corpus.get("prs", 0),
            "min_merged": cluster_corpus.get("min_merged", ""),
            "max_merged": cluster_corpus.get("max_merged", ""),
        },
        "corpus_fingerprint": {
            "hash": cluster_fingerprint_hash,
            "source": "eval/cluster/clusters.json",
        },
        "rules": (
            "query = linked issue title+body, #NNNNN stripped (reused from v2 manifest, no re-fetch); "
            "relevant = all cluster members from eval/cluster/clusters.json (fuzzy backport rule); "
            "corrections from triage-v3.json applied and recorded; "
            "ranking metrics only (threshold decoupled per protocol)"
        ),
        "skipped": v2_skipped,
        "corrections": corrections,
        "queries": queries_v3,
    }

    out_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def main() -> None:
    load_dotenv(".env")
    token = os.environ["GITHUB_TOKEN"]
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }

    conn = psycopg.connect(DSN)
    rows = conn.execute(
        "SELECT number, linked_issue, merged_at FROM pull_requests "
        "WHERE linked_issue IS NOT NULL ORDER BY merged_at"
    ).fetchall()
    all_titles = conn.execute("SELECT number, title FROM pull_requests").fetchall()
    corpus = conn.execute(
        "SELECT count(*), min(merged_at)::date::text, max(merged_at)::date::text "
        "FROM pull_requests"
    ).fetchone()
    conn.close()

    # Backport groups: normalized title -> set of PR numbers.
    title_groups: dict[str, set[int]] = {}
    for number, title in all_titles:
        title_groups.setdefault(_normalize_title(title), set()).add(number)

    queries = []
    skipped = []
    with httpx.Client(headers=headers, timeout=30) as client:
        for pr_number, linked, merged_at in rows:
            issue_num = int(linked.lstrip("#"))
            r = client.get(
                f"https://api.github.com/repos/dotnet/efcore/issues/{issue_num}"
            )
            if r.status_code != 200:
                skipped.append({"pr": pr_number, "issue": issue_num, "reason": f"http {r.status_code}"})
                continue
            issue = r.json()
            if "pull_request" in issue:
                skipped.append({"pr": pr_number, "issue": issue_num, "reason": "linked ref is a PR"})
                continue
            title = issue.get("title") or ""
            body = issue.get("body") or ""
            query = re.sub(r"#\d+", "", f"{title}\n\n{body}").strip()
            if len(query) < 30:
                skipped.append({"pr": pr_number, "issue": issue_num, "reason": "issue text too short"})
                continue
            target_title = next(t for n, t in all_titles if n == pr_number)
            relevant = sorted(title_groups[_normalize_title(target_title)])
            queries.append(
                {
                    "target_pr": pr_number,
                    "relevant_prs": relevant,  # target + its backports/original
                    "issue": issue_num,
                    "merged_at": merged_at.isoformat(),
                    "query": query,
                }
            )
            time.sleep(0.3)

    manifest = {
        "version": "v2-knownitem-backports",
        "corpus": {"prs": corpus[0], "min_merged": corpus[1], "max_merged": corpus[2]},
        "rules": "query = linked issue title+body, #NNNNN stripped; "
        "relevant = {target_pr} + same-normalized-title corpus PRs (backport rule); "
        "ranking metrics only (threshold decoupled per protocol)",
        "skipped": skipped,
        "queries": queries,
    }
    OUT.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"queries: {len(queries)}, skipped: {len(skipped)}, corpus: {corpus}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "v3":
        manifest = build_v3()
        print(
            f"v3 manifest: queries={len(manifest['queries'])}, "
            f"corrections={len(manifest['corrections'])}, "
            f"fingerprint={manifest['corpus_fingerprint']['hash'][:16]}..."
        )
    else:
        main()
