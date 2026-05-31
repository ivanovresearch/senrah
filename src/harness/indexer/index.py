"""
harness.indexer.index — Indexer orchestrator.

The Indexer wires PRRepo (read unindexed PRs) → Embedder (truncate + embed) →
SkillRepo (write skills rows).

Responsibilities:
1. Read unindexed PRs from pull_requests via PRRepo.unindexed_prs.
2. Build problem text (title + body, truncated to problem_limit_tokens).
3. Build solution text (diff, truncated to diff_limit_tokens).
4. Batch all texts through embed_texts (AsyncOpenAI, no real calls in tests).
5. Write each skills row via SkillRepo.upsert_skill with model + version (D-08).
6. Per-PR error isolation: log to stderr and continue (T-02-04 pattern).

Boundary constraints (STATE.md):
- NO SQL, no pgvector operators.  All DB access via PRRepo and SkillRepo.
- Logging goes to stderr only via standard logging.
"""

from __future__ import annotations

import logging
import sys

import psycopg

from harness.config import EmbedConfig
from harness.db.repos.pr import PRRepo
from harness.db.repos.skill import SkillRepo
from harness.indexer.embedder import (
    build_problem_text,
    embed_texts,
    truncate_to_tokens,
)

logger = logging.getLogger(__name__)


class Indexer:
    """Orchestrates the index pipeline: pull_requests → skills table.

    Usage at the CLI composition root:
        indexer = Indexer(conn, cfg.embed)
        await indexer.run(repository_id)

    The Indexer is async because embed_texts uses AsyncOpenAI.
    For the CLI (sync context), wrap with asyncio.run().
    """

    def __init__(self, conn: psycopg.Connection, embed_cfg: EmbedConfig) -> None:
        self._conn = conn
        self._embed_cfg = embed_cfg

    async def run(self, repository_id: int) -> int:
        """Index all unindexed PRs for a repository.

        Reads unindexed PRs, builds problem + solution texts, embeds in batches,
        and writes skills rows.  Per-PR errors are logged to stderr and do not
        abort the run (per-PR isolation pattern from T-02-04).

        Args:
            repository_id: The repository to index.

        Returns:
            Number of PRs successfully indexed.
        """
        pr_repo = PRRepo(self._conn)
        skill_repo = SkillRepo(self._conn)
        embed_cfg = self._embed_cfg

        # Read all PRs that don't have a skills row yet
        prs = pr_repo.unindexed_prs(repository_id)

        if not prs:
            logger.info("No unindexed PRs for repository_id=%d", repository_id)
            return 0

        logger.info(
            "Indexing %d unindexed PR(s) for repository_id=%d",
            len(prs),
            repository_id,
        )

        # Build all texts (problem + solution) for batched embedding
        # Order: [pr0_problem, pr0_solution, pr1_problem, pr1_solution, ...]
        # This interleaved layout lets us re-associate embeddings with PRs
        # in a single pass after the batch call.
        texts: list[str] = []
        for pr in prs:
            problem_text = truncate_to_tokens(
                build_problem_text(pr.title, pr.body or ""),
                embed_cfg.problem_limit_tokens,
            )
            solution_text = truncate_to_tokens(
                pr.diff or "",
                embed_cfg.diff_limit_tokens,
            )
            texts.append(problem_text)
            texts.append(solution_text)

        # Batch embed all texts (AsyncOpenAI under the hood; testable via patch)
        embeddings = await embed_texts(texts, model=embed_cfg.model)

        # Write skills rows — one per PR, two embeddings each
        indexed = 0
        for i, pr in enumerate(prs):
            problem_emb = embeddings[i * 2]
            solution_emb = embeddings[i * 2 + 1]

            try:
                skill_repo.upsert_skill(
                    pr_id=pr.id,  # type: ignore[arg-type]
                    problem_emb=problem_emb,
                    solution_emb=solution_emb,
                    model=embed_cfg.model,
                    version=embed_cfg.version,
                )
                indexed += 1
            except Exception as exc:
                # Per-PR error isolation: log to stderr and continue.
                # This mirrors the Ingester pattern (T-02-04 / STATE.md).
                logger.error(
                    "Failed to write skill for PR id=%s: %s",
                    pr.id,
                    exc,
                )

        logger.info(
            "Indexed %d / %d PR(s) for repository_id=%d",
            indexed,
            len(prs),
            repository_id,
        )
        return indexed
