"""Initial schema: vector extension, four tables, and HNSW indexes.

Revision ID: 0001
Revises: (none)
Create Date: 2026-05-31

Implements STORE-01 / STORE-02 / STORE-03:
- CREATE EXTENSION IF NOT EXISTS vector  (must be first)
- projects, repositories, pull_requests, skills tables
- HNSW indexes on both embedding columns using vector_cosine_ops
  (must match <=> cosine-distance operator — Pitfall 1)
"""

from __future__ import annotations

from alembic import op

# revision identifiers
revision: str = "0001"
down_revision: str | None = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Install pgvector extension (must precede any vector() column DDL)
    # ------------------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ------------------------------------------------------------------
    # 2. projects — top-level project grouping
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE projects (
            id         SERIAL PRIMARY KEY,
            name       TEXT NOT NULL UNIQUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    # ------------------------------------------------------------------
    # 3. repositories — source repos within a project
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE repositories (
            id         SERIAL PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES projects(id),
            type       TEXT NOT NULL,
            name       TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(project_id, name)
        )
        """
    )

    # ------------------------------------------------------------------
    # 4. pull_requests — raw PR content (diff + files stored here)
    # STORE-02: diff TEXT and files_changed JSONB required
    # Pitfall 5: files_changed stored at ingest time (not re-fetched at read time)
    # Open Question 1: JSONB is self-describing, psycopg3 serializes list[str] natively
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE pull_requests (
            id            SERIAL PRIMARY KEY,
            repository_id INTEGER NOT NULL REFERENCES repositories(id),
            number        INTEGER NOT NULL,
            title         TEXT NOT NULL,
            body          TEXT,
            diff          TEXT,
            author        TEXT,
            merged_at     TIMESTAMPTZ,
            linked_issue  TEXT,
            files_changed JSONB,
            content_hash  TEXT,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(repository_id, number)
        )
        """
    )

    # ------------------------------------------------------------------
    # 5. skills — embeddings per PR + model metadata
    # STORE-01: problem_embedding + solution_embedding, embedding_model, embedding_version
    # UNIQUE constraint prevents duplicate embeddings for the same model/version per PR
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE skills (
            id                 SERIAL PRIMARY KEY,
            pr_id              INTEGER NOT NULL REFERENCES pull_requests(id),
            problem_embedding  vector(1536),
            solution_embedding vector(1536),
            embedding_model    TEXT NOT NULL,
            embedding_version  TEXT NOT NULL,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(pr_id, embedding_model, embedding_version)
        )
        """
    )

    # ------------------------------------------------------------------
    # 6. HNSW indexes on both embedding columns
    # CRITICAL: vector_cosine_ops MUST match the <=> operator used in queries
    # (Pitfall 1: operator-class mismatch → silent Seq Scan, not an error)
    # m=16, ef_construction=64: recommended defaults for <100K vectors (Open Question 3)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE INDEX idx_skills_problem_emb ON skills
            USING hnsw (problem_embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
        """
    )
    op.execute(
        """
        CREATE INDEX idx_skills_solution_emb ON skills
            USING hnsw (solution_embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
        """
    )


def downgrade() -> None:
    # Drop tables in reverse dependency order; CASCADE handles FK references.
    # We do NOT drop the vector extension — other schemas may use it.
    op.execute("DROP TABLE IF EXISTS skills CASCADE")
    op.execute("DROP TABLE IF EXISTS pull_requests CASCADE")
    op.execute("DROP TABLE IF EXISTS repositories CASCADE")
    op.execute("DROP TABLE IF EXISTS projects CASCADE")
