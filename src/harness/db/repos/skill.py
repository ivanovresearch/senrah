"""
harness.db.repos.skill — SkillRepo data-access layer.

All SQL and pgvector operators live here — never in CLI, Indexer, or anywhere else
(STATE.md constraint: SQL confined to db/repos/).

Provides:
- SkillRepo.upsert_skill(pr_id, problem_emb, solution_emb, model, version)
  INSERT ... ON CONFLICT (pr_id, embedding_model, embedding_version) DO UPDATE
  Persists both vector embeddings + model + version per skills row (D-08).

- SkillRepo.search(query_vec, top_n, oversample_factor, score_threshold,
                   problem_weight, solution_weight, repos=None)
  Oversample ANN via <=> cosine (HNSW), Python re-rank by composite score,
  filter by threshold, return top_n SearchResult rows (D-09/D-10/D-11/STORE-03).

Security:
- T-03-03 / T-04-01: All SQL uses parameterized %(name)s placeholders — no f-string SQL.
  Vectors are cast via ::vector (pgvector type annotation in SQL); the actual
  serialisation is handled by register_vector (pgvector-python).
- T-04-02: <=> matches vector_cosine_ops index; other distance operators never used.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import psycopg

from harness.scoring import composite_score

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """A single search result returned by SkillRepo.search.

    Fields mirror the future MCP output (D-12): score, PR metadata,
    repo, author, files, diff excerpt source.
    """

    pr_id: int
    number: int
    title: str
    repo_name: str
    author: str
    merged_at: Optional[datetime]
    linked_issue: Optional[str]
    files_changed: list[str]
    diff: str
    problem_sim: float
    solution_sim: float
    score: float


class SkillRepo:
    """Data-access object for the skills table.

    Provides upsert_skill for the write path (Indexer) and search for
    the read path (CLI + future MCP server).

    All SQL is parameterized — no f-string interpolation (T-03-03 / T-04-01).
    All pgvector <=> operators are confined to this module (STATE.md).
    """

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    # upsert_skill — write path for Indexer
    # ------------------------------------------------------------------

    def upsert_skill(
        self,
        pr_id: int,
        problem_emb: list[float],
        solution_emb: list[float],
        model: str,
        version: str,
    ) -> None:
        """Insert or update a skills row for a given pull request.

        Conflict key: (pr_id, embedding_model, embedding_version) — the unique
        constraint from the migration.  On conflict, the embeddings are updated
        in place (supports re-embedding with the same model/version config).

        Vectors are serialised as PostgreSQL vector type via the ::vector cast.
        The pgvector-python register_vector call in pool.py wires the Python
        list[float] → vector serialization, but we add the ::vector cast as an
        explicit annotation for clarity and correctness.

        Args:
            pr_id: Foreign key to pull_requests.id.
            problem_emb: 1536-dim problem embedding (title + body).
            solution_emb: 1536-dim solution embedding (clean diff).
            model: Embedding model name (e.g. "text-embedding-3-small") from config.
            version: Embedding version string (e.g. "v1") from config.

        Raises:
            psycopg.Error: On database errors (caller handles per-PR error isolation).
        """
        self._conn.execute(
            """
            INSERT INTO skills (
                pr_id,
                problem_embedding,
                solution_embedding,
                embedding_model,
                embedding_version
            )
            VALUES (
                %(pr_id)s,
                %(prob_emb)s::vector,
                %(sol_emb)s::vector,
                %(model)s,
                %(version)s
            )
            ON CONFLICT (pr_id, embedding_model, embedding_version) DO UPDATE SET
                problem_embedding  = EXCLUDED.problem_embedding,
                solution_embedding = EXCLUDED.solution_embedding
            """,
            {
                "pr_id": pr_id,
                "prob_emb": problem_emb,
                "sol_emb": solution_emb,
                "model": model,
                "version": version,
            },
        )

    def index_stats(self) -> dict:
        """Aggregate index health for `harness status` (OPS-04).

        Returns:
            {"total_vectors": int (skills rows; each row holds two embeddings),
             "models": [(model, version, count), ...],
             "last_indexed_at": datetime | None}
        """
        total = self._conn.execute("SELECT count(*) FROM skills").fetchone()[0]
        models = self._conn.execute(
            """
            SELECT embedding_model, embedding_version, count(*)
            FROM skills GROUP BY 1, 2 ORDER BY 3 DESC
            """
        ).fetchall()
        last = self._conn.execute("SELECT max(created_at) FROM skills").fetchone()[0]
        return {
            "total_vectors": int(total),
            "models": [(m, v, int(c)) for m, v, c in models],
            "last_indexed_at": last,
        }

    def delete_for_repository(self, repository_id: int) -> int:
        """Delete ALL skills rows for a repository's pull requests.

        Used by `harness index --reindex` (INDEX-03): the conflict key on
        skills is (pr_id, embedding_model, embedding_version), so re-embedding
        under a NEW model/version would INSERT rows alongside the old ones and
        the search path (which does not filter by version) would return
        duplicates. A reindex therefore clears the repository's rows first,
        then re-embeds everything from the raw pull_requests store.

        Args:
            repository_id: The repository whose skills rows are removed.

        Returns:
            Number of rows deleted.
        """
        cur = self._conn.execute(
            """
            DELETE FROM skills
            USING pull_requests pr
            WHERE skills.pr_id = pr.id
              AND pr.repository_id = %(repository_id)s
            """,
            {"repository_id": repository_id},
        )
        return cur.rowcount

    # ------------------------------------------------------------------
    # search — read path for CLI + MCP Server (Plan 01-04)
    # ------------------------------------------------------------------

    async def search(
        self,
        query_vec: list[float],
        top_n: int,
        oversample_factor: int,
        score_threshold: float,
        problem_weight: float,
        solution_weight: float,
        repos: Optional[list[str]] = None,
    ) -> list[SearchResult]:
        """Oversample ANN search via <=> cosine distance, then Python re-rank.

        Algorithm (D-10):
        1. ANN query: fetch top_n × oversample_factor candidates ordered by
           problem_embedding <=> query_vec (cosine distance, HNSW index).
        2. Compute problem_sim and solution_sim as 1 - distance for each row.
        3. Apply composite_score(p_sim, s_sim, problem_weight, solution_weight).
        4. Filter out candidates below score_threshold.
        5. Sort remaining by score descending; return first top_n.

        When zero candidates pass score_threshold (D-11), this returns an empty
        list — the CLI layer handles the below-threshold hint.

        The query vector is used against BOTH embedding columns (symmetric
        retrieval: the same natural-language query is compared to both the
        problem description and the diff). RESEARCH query-embedding decision.

        Security:
        - T-04-01: query_vec and repos passed as psycopg3 bind params (%(vec)s /
          %(repos)s). No f-string interpolation in SQL.
        - T-04-02: <=> matches vector_cosine_ops HNSW index; cosine distance only.

        Args:
            query_vec: 1536-dim embedding vector for the search query.
            top_n: Maximum number of results to return.
            oversample_factor: Multiplier for the ANN fetch (D-10 default 5).
            score_threshold: Minimum composite score to include a result (D-11).
            problem_weight: Weight for problem similarity in composite score (D-09).
            solution_weight: Weight for solution similarity in composite score (D-09).
            repos: Optional list of repo names (e.g. ["owner/repo"]) to restrict
                   search to. None means all repos in the project (SEARCH-03 groundwork).

        Returns:
            List of SearchResult objects, sorted by score descending, at most top_n.
        """
        limit = top_n * oversample_factor

        # Build optional WHERE clause for repo filtering (SEARCH-03 groundwork).
        # This is safe: the clause is static text; repos is a bind parameter.
        repo_filter = "AND r.name = ANY(%(repos)s)" if repos is not None else ""

        # CRITICAL: Use <=> (cosine distance) — MUST match vector_cosine_ops index.
        # 1 - distance = cosine similarity (T-04-02 / Pitfall 1).
        # We compute BOTH p_sim and s_sim in SQL to avoid a second query.
        # The ORDER BY uses problem_embedding <=> only (ANN fast path);
        # re-ranking by composite score happens in Python after fetch (D-10).
        query = f"""
            SELECT
                sk.pr_id,
                pr.number,
                pr.title,
                r.name  AS repo_name,
                pr.author,
                pr.merged_at,
                pr.linked_issue,
                pr.files_changed,
                pr.diff,
                1 - (sk.problem_embedding  <=> %(vec)s::vector) AS p_sim,
                1 - (sk.solution_embedding <=> %(vec)s::vector) AS s_sim
            FROM   skills          sk
            JOIN   pull_requests   pr ON pr.id = sk.pr_id
            JOIN   repositories    r  ON r.id  = pr.repository_id
            WHERE  sk.problem_embedding IS NOT NULL
              AND  sk.solution_embedding IS NOT NULL
              {repo_filter}
            ORDER BY sk.problem_embedding <=> %(vec)s::vector
            LIMIT %(limit)s
        """

        params: dict = {"vec": query_vec, "limit": limit}
        if repos is not None:
            params["repos"] = repos

        async with self._conn.cursor() as cur:
            await cur.execute(query, params)
            rows = await cur.fetchall()

        # Python re-rank (D-10): compute composite score, filter, sort, cap at top_n.
        results: list[SearchResult] = []
        for row in rows:
            (
                pr_id, number, title, repo_name, author, merged_at,
                linked_issue, files_changed_raw, diff, p_sim_raw, s_sim_raw,
            ) = row

            p_sim = float(p_sim_raw)
            s_sim = float(s_sim_raw)
            score = composite_score(p_sim, s_sim, problem_weight, solution_weight)

            if score < score_threshold:
                continue

            # Deserialise files_changed: stored as JSONB (list[str]).
            if isinstance(files_changed_raw, list):
                files_changed = files_changed_raw
            elif isinstance(files_changed_raw, str):
                try:
                    files_changed = json.loads(files_changed_raw)
                except (json.JSONDecodeError, TypeError):
                    files_changed = []
            else:
                files_changed = []

            results.append(
                SearchResult(
                    pr_id=pr_id,
                    number=number,
                    title=title,
                    repo_name=repo_name,
                    author=author,
                    merged_at=merged_at,
                    linked_issue=linked_issue,
                    files_changed=files_changed,
                    diff=diff or "",
                    problem_sim=p_sim,
                    solution_sim=s_sim,
                    score=score,
                )
            )

        # Sort by score descending (D-10), return at most top_n.
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_n]
