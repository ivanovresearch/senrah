"""
harness.db.repos.skill — SkillRepo data-access layer.

All SQL and pgvector operators live here — never in CLI, Indexer, or anywhere else
(STATE.md constraint: SQL confined to db/repos/).

Provides:
- SkillRepo.upsert_skill(pr_id, problem_emb, solution_emb, model, version)
  INSERT ... ON CONFLICT (pr_id, embedding_model, embedding_version) DO UPDATE
  Persists both vector embeddings + model + version per skills row (D-08).

The search() method (SkillRepo.search) is added in Plan 01-04.

Security:
- T-03-03: All SQL uses parameterized %(name)s placeholders — no f-string SQL.
  Vectors are cast via ::vector (pgvector type annotation in SQL); the actual
  serialization is handled by register_vector (pgvector-python).
"""

from __future__ import annotations

import psycopg


class SkillRepo:
    """Data-access object for the skills table.

    Provides upsert_skill for the write path (Indexer).
    The read path (search) is added in Plan 01-04.

    All SQL is parameterized — no f-string interpolation (T-03-03).
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
