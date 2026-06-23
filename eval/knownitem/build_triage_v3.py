"""
eval/knownitem/build_triage_v3.py -- Stage-1 mechanical reclassification (EVAL-03).

Reads misses_at_10 from results-v2-575-reindexed.json and re-scores each
miss against the EVAL-01 cluster map.  A miss whose v2 top-1 result is a
member of the target PR's cluster is auto-reclassified as a hit (the
documented backport-miss class -- a cluster member was retrieved but the
old exact-title rule in the v2 manifest did not recognise it as relevant).

This is Stage 1 -- purely mechanical, no LLM, no human.
Stage 2 tags (final_tag) for the residual misses are filled by a human
checkpoint (Plan 02 Task 3).

Output: eval/knownitem/triage-v3.json
"""

from __future__ import annotations

import json
import pathlib

HERE = pathlib.Path(__file__).parent
CLUSTER_DIR = HERE.parent / "cluster"


def build_triage_v3(
    results_path: pathlib.Path | None = None,
    clusters_path: pathlib.Path | None = None,
    out_path: pathlib.Path | None = None,
) -> list[dict]:
    """Run Stage-1 reclassification and write triage-v3.json.

    Returns the list of triage rows (one per original miss).
    """
    if results_path is None:
        results_path = HERE / "results-v2-575-reindexed.json"
    if clusters_path is None:
        clusters_path = CLUSTER_DIR / "clusters.json"
    if out_path is None:
        out_path = HERE / "triage-v3.json"

    from eval.cluster.grouping import cluster_of, load_cluster_map

    results = json.loads(results_path.read_text(encoding="utf-8"))
    cluster_map = load_cluster_map(clusters_path)

    misses = results["misses_at_10"]  # list of target PR numbers

    # Build a lookup: target_pr -> top1 PR number (the highest-ranked result)
    per_query = {row["target_pr"]: row for row in results["per_query"]}

    triage_rows = []
    for target_pr in misses:
        row_data = per_query.get(target_pr, {})
        top1 = row_data.get("top1")

        # Stage-1 rule: if top1 belongs to the same cluster as target_pr,
        # the retriever DID find a cluster member but the v2 manifest didn't
        # recognise it as relevant.  Auto-reclassify as a hit.
        stage1_reclassified = False
        stage1_via_cluster: list[int] | None = None

        if top1 is not None:
            target_cid = cluster_of(target_pr, cluster_map)
            top1_cid = cluster_of(top1, cluster_map)
            if target_cid == top1_cid and top1 != target_pr:
                # top1 is a backport/cherry-pick of the target -- reclassify
                stage1_reclassified = True
                # Record the cluster members so the human can audit
                for cluster in cluster_map.get("clusters", []):
                    if target_pr in cluster:
                        stage1_via_cluster = cluster
                        break

        triage_rows.append(
            {
                "target_pr": target_pr,
                "stage1_reclassified": stage1_reclassified,
                "stage1_via_cluster": stage1_via_cluster,
                "top1_in_v2": top1,
                "final_tag": None,
                "note": "",
            }
        )

    out_path.write_text(json.dumps(triage_rows, indent=2), encoding="utf-8")
    return triage_rows


if __name__ == "__main__":
    rows = build_triage_v3()
    reclassified = [r for r in rows if r["stage1_reclassified"]]
    residual = [r for r in rows if not r["stage1_reclassified"]]
    print(f"Total misses: {len(rows)}")
    print(f"Stage-1 reclassified: {len(reclassified)}")
    print(f"Residual (need human tag): {len(residual)}")
    for r in reclassified:
        print(
            f"  PR {r['target_pr']} -> cluster {r['stage1_via_cluster']} "
            f"(top1={r['top1_in_v2']})"
        )
