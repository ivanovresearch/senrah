"""
eval/temporal/run_temporal_eval.py -- Automated temporal-holdout hit-rate@k scorer.

Reads frozen params from manifest-temporal.json and frozen queries from
query-set.json, batch-embeds query texts (one API call), then calls
SkillRepo.search per query with:
  - repos=["dotnet/efcore"]           (D-10: restrict to efcore corpus)
  - merged_before=T                   (corpus ceiling from manifest)
  - merged_after=T - rung_floor_days  (corpus floor; None for deepest rung)
  - score_threshold=0.45              (frozen in manifest, asserted -- D-09)

Produces temporal-results-{tag}.json with hit_rate_at_5, ci_lo_5, ci_hi_5,
hit_rate_at_10, ci_lo_10, ci_hi_10, n_queries.

ASCII-only stdout (Windows cp1251 console).

Usage:
    python eval/temporal/run_temporal_eval.py --rung-days 0 --tag baseline
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

# psycopg3 async cannot run on Windows' default ProactorEventLoop (same
# policy switch as tests/conftest.py and the CLI entry point).
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

HERE = pathlib.Path(__file__).parent
MANIFEST = HERE / "manifest-temporal.json"
QUERY_SET = HERE / "query-set.json"
CLUSTERS_DEEP = pathlib.Path("eval/cluster/clusters-deep.json")


def _compute_relevant_set(
    pr_number: int,
    linked_issue: str | None,
    cluster_map: dict,
    corpus_prs: object,  # reserved for future use; pass None
) -> set[int]:
    """Compute the set of search-result PR numbers that are relevant to this query.

    A search result (candidate PR number) is relevant if it meets either criterion:
      1. Cluster match: the candidate PR is in the same cluster as the query PR
         (backport / cherry-pick family via clusters-deep.json).
      2. Linked-issue match: the candidate PR shares the same linked_issue string
         as the query PR (same GitHub issue fixed by multiple PRs).

    This function is pure (no DB, no I/O) and designed to be called inside the
    search results loop: it checks each returned result against these two criteria.
    corpus_prs is reserved for a future full-corpus relevance scan and is currently
    unused (pass None).

    Args:
        pr_number:    PR number of the query (post-T, NOT in corpus).
        linked_issue: Linked issue string of the query PR (e.g. "#42"), or None.
        cluster_map:  Parsed clusters-deep.json dict (from load_cluster_map).
        corpus_prs:   Reserved; pass None (unused in current implementation).

    Returns:
        Set of PR numbers that are relevant according to the criteria above.
        IMPORTANT: this is determined per search result -- callers must pass
        candidate PR metadata into the function. In the scorer loop, results
        from SkillRepo.search are checked individually via:
            any(r.number in relevant for r in results)
        where relevant is computed once per query from the cluster/issue criteria.

    Implementation note for the scorer loop:
        The scorer builds relevant as the set of PR numbers for which EITHER:
          - cluster_of(result.number, cluster_map) == cluster_of(pr_number, cluster_map)
            AND the cluster is multi-member (not a singleton)
          - result.linked_issue == linked_issue (when linked_issue is not None)
        This function encapsulates that logic for testability.
    """
    from eval.cluster.grouping import cluster_of

    relevant: set[int] = set()

    # Determine the query PR's cluster id
    query_cluster = cluster_of(pr_number, cluster_map)
    # A singleton maps to itself -- do not treat self-cluster as a match signal
    # unless the cluster genuinely has multiple members.
    query_in_multi = _is_multi_member_cluster(pr_number, cluster_map)

    # We do not have a list of candidate PR numbers at call time in the scorer --
    # instead, the scorer calls this function to obtain the query's "relevant key"
    # (cluster id + linked issue), and then checks each result individually.
    # For unit testing, callers may pass mock result objects or PR number lists
    # and call _is_relevant_result() below.
    #
    # Return value semantics for the scorer:
    #   The function returns a frozenset-like marker tuple used by _is_relevant_result.
    #   For test fixtures, the caller simulates result objects with .number and
    #   .linked_issue attributes and calls _is_relevant_result directly.
    #
    # To keep the test interface clean we return an EMPTY set here; the scorer
    # uses _is_relevant_result() per result. The unit tests for
    # TestAnswerableDetection use _is_relevant_result with fixture result objects.
    #
    # However, the plan's test_temporal_split.py cases specify:
    #   _compute_relevant_set(...) returns non-empty set / empty set
    # so we need to actually evaluate against the corpus_prs argument or a
    # candidate list. When corpus_prs is a list of dicts (test fixture), evaluate
    # each candidate.

    if corpus_prs is not None:
        for candidate in corpus_prs:
            c_number = candidate["number"]
            c_linked = candidate.get("linked_issue")

            # Criterion 1: same multi-member cluster
            if query_in_multi:
                c_cluster = cluster_of(c_number, cluster_map)
                if c_cluster == query_cluster:
                    relevant.add(c_number)
                    continue

            # Criterion 2: shared linked_issue
            if linked_issue is not None and c_linked == linked_issue:
                relevant.add(c_number)

    return relevant


def _is_multi_member_cluster(pr_number: int, cluster_map: dict) -> bool:
    """Return True if pr_number belongs to a cluster with more than one member."""
    for cluster in cluster_map.get("clusters", []):
        if pr_number in cluster and len(cluster) > 1:
            return True
    return False


def _is_relevant_result(
    result_number: int,
    result_linked_issue: str | None,
    query_pr_number: int,
    query_linked_issue: str | None,
    cluster_map: dict,
) -> bool:
    """Return True if a search result is relevant to the query.

    Used inside the scorer's per-result check:
        hit = int(any(_is_relevant_result(r.number, r.linked_issue, ...) for r in results))

    Args:
        result_number:       PR number of the search result.
        result_linked_issue: linked_issue of the search result (from DB).
        query_pr_number:     PR number of the query (post-T).
        query_linked_issue:  linked_issue of the query PR.
        cluster_map:         Parsed clusters-deep.json.

    Returns:
        True if the result is relevant; False otherwise.
    """
    from eval.cluster.grouping import cluster_of

    # Criterion 1: shared multi-member cluster
    if _is_multi_member_cluster(query_pr_number, cluster_map):
        q_cluster = cluster_of(query_pr_number, cluster_map)
        r_cluster = cluster_of(result_number, cluster_map)
        if q_cluster == r_cluster:
            return True

    # Criterion 2: shared linked_issue
    if (
        query_linked_issue is not None
        and result_linked_issue is not None
        and result_linked_issue == query_linked_issue
    ):
        return True

    return False


async def _run(rung_floor_days: int, tag: str) -> None:
    from senrah.config import EnvSettings, find_config_file, load_yaml_config
    from senrah.db.pool import create_pool
    from senrah.db.repos.skill import SkillRepo
    from senrah.indexer.embedder import embed_texts, truncate_to_tokens
    from eval.cluster.grouping import load_cluster_map
    from eval.temporal.bootstrap_ci import bootstrap_hit_rate_ci

    load_dotenv(".env")
    env = EnvSettings()
    cfg = load_yaml_config(find_config_file())

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    query_set = json.loads(QUERY_SET.read_text(encoding="utf-8"))

    # Read frozen params from manifest (D-09 -- never re-read senrah.yaml for these)
    score_threshold = manifest["score_threshold"]   # 0.45
    assert score_threshold == 0.45, (
        f"score_threshold must be 0.45 per manifest, got {score_threshold}"
    )
    top_n_gate = manifest["top_n_gate"]             # 5
    top_n_diag = manifest["top_n_diagnostic"]       # 10
    bootstrap_B = manifest["bootstrap_B"]           # 2000
    bootstrap_seed = manifest["bootstrap_seed"]     # 42
    T_dt = datetime.fromisoformat(manifest["T"])
    if T_dt.tzinfo is None:
        T_dt = T_dt.replace(tzinfo=timezone.utc)

    # Load cluster map once (D-01 relevance, load-once pattern)
    cluster_map = load_cluster_map(CLUSTERS_DEEP)

    # Batch-embed all query texts upfront (one API call, mirror run_eval.py)
    queries = query_set["queries"]
    texts = [
        truncate_to_tokens(q["query"], cfg.embed.problem_limit_tokens)
        for q in queries
    ]
    vecs = await embed_texts(
        texts,
        model=cfg.embed.model,
        api_key=env.openai_api_key,
        base_url=cfg.embed.base_url,
    )

    pool = await create_pool(env.database_url)
    hits_at_gate: list[int] = []
    hits_at_diag: list[int] = []
    try:
        async with pool.connection() as conn:
            repo = SkillRepo(conn)
            for q, vec in zip(queries, vecs):
                merged_after = (
                    T_dt - timedelta(days=rung_floor_days)
                    if rung_floor_days > 0
                    else None
                )
                for top_n, hits_list in [
                    (top_n_gate, hits_at_gate),
                    (top_n_diag, hits_at_diag),
                ]:
                    results = await repo.search(
                        query_vec=vec,
                        top_n=top_n,
                        oversample_factor=cfg.search.oversample_factor,
                        score_threshold=score_threshold,   # 0.45 from manifest (D-09)
                        problem_weight=cfg.search.problem_weight,
                        solution_weight=cfg.search.solution_weight,
                        repos=["dotnet/efcore"],           # D-10
                        merged_before=T_dt,                # corpus ceiling
                        merged_after=merged_after,         # corpus floor (None = no floor)
                    )
                    hit = int(
                        any(
                            _is_relevant_result(
                                r.number,
                                r.linked_issue,
                                q["pr_number"],
                                q.get("linked_issue"),
                                cluster_map,
                            )
                            for r in results
                        )
                    )
                    hits_list.append(hit)
    finally:
        await pool.close()

    pt5, lo5, hi5 = bootstrap_hit_rate_ci(
        hits_at_gate, B=bootstrap_B, seed=bootstrap_seed
    )
    pt10, lo10, hi10 = bootstrap_hit_rate_ci(
        hits_at_diag, B=bootstrap_B, seed=bootstrap_seed
    )

    result = {
        "version": "temporal-holdout-v1",
        "tag": tag,
        "rung_floor_days": rung_floor_days,
        "T": manifest["T"],
        "n_queries": len(queries),
        "hit_rate_at_5": pt5,
        "ci_lo_5": lo5,
        "ci_hi_5": hi5,
        "hit_rate_at_10": pt10,
        "ci_lo_10": lo10,
        "ci_hi_10": hi10,
        "score_threshold": score_threshold,
    }
    out = HERE / f"temporal-results-{tag}.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    # ASCII-only output (Windows cp1251 console)
    print(
        f"[{tag}] n={len(queries)} hit@5={pt5:.3f} [{lo5:.3f},{hi5:.3f}]"
        f" hit@10={pt10:.3f} [{lo10:.3f},{hi10:.3f}]"
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Run temporal-holdout hit-rate@k scorer."
    )
    parser.add_argument(
        "--rung-days",
        type=int,
        default=0,
        dest="rung_days",
        help=(
            "Corpus floor depth in days relative to T "
            "(0 = deepest rung, no floor, full pre-T corpus)."
        ),
    )
    parser.add_argument(
        "--tag",
        type=str,
        default="baseline",
        help="Output tag -> temporal-results-<tag>.json",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.rung_days, args.tag))
