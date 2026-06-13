"""
eval/knownitem/run_eval.py — run the known-item baseline against the live corpus.

Reads manifest.json, batch-embeds all queries (one API call), runs
SkillRepo.search per query with score_threshold=0.0 (ranking metrics are
threshold-free BY PROTOCOL — the threshold is tuned separately, never here),
and reports recall@1, recall@5, recall@10 and MRR@10 for the target PR.

Weights come from senrah.yaml (the live config) and are PRINTED into the
results so every run records what it measured. Output:
eval/knownitem/results-<tag>.json + a summary line to stdout.

Usage: python eval/knownitem/run_eval.py <tag>
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys

from dotenv import load_dotenv

# psycopg3 async cannot run on Windows' default ProactorEventLoop (same
# policy switch as tests/conftest.py and the CLI entry point).
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

HERE = pathlib.Path(__file__).parent


async def _run(tag: str) -> None:
    from senrah.config import EnvSettings, find_config_file, load_yaml_config
    from senrah.db.pool import create_pool
    from senrah.db.repos.skill import SkillRepo
    from senrah.indexer.embedder import embed_texts, truncate_to_tokens

    load_dotenv(".env")
    env = EnvSettings()
    cfg = load_yaml_config(find_config_file())

    manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    queries = manifest["queries"]

    # Symmetric with the index side: queries truncated like problem texts.
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
    per_query = []
    try:
        async with pool.connection() as conn:
            repo = SkillRepo(conn)
            for q, vec in zip(queries, vecs):
                results = await repo.search(
                    query_vec=vec,
                    top_n=10,
                    oversample_factor=cfg.search.oversample_factor,
                    score_threshold=0.0,  # ranking metrics are threshold-free
                    problem_weight=cfg.search.problem_weight,
                    solution_weight=cfg.search.solution_weight,
                )
                # v2 backport rule: any PR in the relevant set is a hit
                # (v1 manifests lack relevant_prs — fall back to the target).
                relevant = set(q.get("relevant_prs", [q["target_pr"]]))
                rank = next(
                    (i + 1 for i, r in enumerate(results) if r.number in relevant),
                    None,
                )
                per_query.append(
                    {
                        "target_pr": q["target_pr"],
                        "rank": rank,
                        "top1": results[0].number if results else None,
                        "top1_score": round(results[0].score, 3) if results else None,
                    }
                )
    finally:
        await pool.close()

    n = len(per_query)
    r_at = lambda k: sum(1 for p in per_query if p["rank"] and p["rank"] <= k) / n
    mrr = sum(1 / p["rank"] for p in per_query if p["rank"]) / n
    summary = {
        "tag": tag,
        "manifest_version": manifest["version"],
        "corpus": manifest["corpus"],
        "weights": {
            "problem_weight": cfg.search.problem_weight,
            "solution_weight": cfg.search.solution_weight,
            "oversample_factor": cfg.search.oversample_factor,
        },
        "n_queries": n,
        "recall_at_1": round(r_at(1), 3),
        "recall_at_5": round(r_at(5), 3),
        "recall_at_10": round(r_at(10), 3),
        "mrr_at_10": round(mrr, 3),
        "misses_at_10": [p["target_pr"] for p in per_query if p["rank"] is None],
        "per_query": per_query,
    }
    out = HERE / f"results-{tag}.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(
        f"[{tag}] n={n} recall@1={summary['recall_at_1']} recall@5={summary['recall_at_5']} "
        f"recall@10={summary['recall_at_10']} MRR@10={summary['mrr_at_10']} "
        f"misses@10={len(summary['misses_at_10'])}"
    )


if __name__ == "__main__":
    asyncio.run(_run(sys.argv[1] if len(sys.argv) > 1 else "baseline"))
